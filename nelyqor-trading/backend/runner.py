"""Service moteur (QUANT + GUARDIAN + EXECUTION) : seul processus qui peut détenir les clés Bitvavo.

Boucle : commandes de contrôle (toujours en premier) -> marchés (10 min) -> tickers (5 s) -> reprise des
intentions ambiguës -> suivi des ordres -> protections natives -> bougies -> décisions (60 s) ->
réconciliation (réel, 5 min) -> instantané d'equity (1/min) -> heartbeat.

Deux exécuteurs coexistent : SIMULATION (papier, toujours actif, comparaison permanente) et RÉEL (créé si
des clés existent). Les entrées réelles exigent l'état REEL_AUTORISE ; la gestion des positions réelles
existantes (stops natifs, sorties) continue même si le réel est désactivé ou en pause.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from .accounting.ledger import ExternalFlow, Fill, Ledger
from .accounting.reconcile import reconcile
from .core.clock import Clock, new_id
from .core.config import HardLimits, Settings, load_strategy_params
from .core.logging import get_logger, register_secret, setup_logging
from .core.num import ZERO, D, fmt
from .execution.engine import ExecutionEngine, InstanceLock, OrderRequest
from .execution.protection import ProtectionManager
from .execution.simulator import SimulatedExchange
from .features import indicators as ind
from .guardian.guardian import Guardian, IntentCheck, State
from .market_data.bitvavo_rest import BitvavoError, BitvavoREST
from .market_data.models import Candle, MarketInfo
from .market_data.universe import MarketState, check_eligibility, diff_markets, spread_bps
from .notify.notifier import Notifier
from .portfolio.decision import DecisionEngine, HeldPosition
from .research.registry import Registry
from .storage.db import Database
from .strategies.base import build_strategies

log = get_logger("engine")
INTERVAL = "1h"
INTERVAL_MS = 3_600_000
COMMAND_TTL_MS = 10 * 60_000  # une commande non traitée en 10 min expire (sauf pause / arrêt d'urgence)
ALWAYS_VALID_COMMANDS = {"pause", "emergency_stop"}
ENTRY_LIMIT_TTL_MS = 15 * 60_000
RECON_OVERLAP_MS = 7 * 86_400_000


class Engine:
    def __init__(self, settings: Settings, db: Database, rest: Any, clock: Clock | None = None,
                 limits: HardLimits | None = None, state_key: bytes | None = None,
                 command_key: bytes | None = None, store_path: Path | None = None) -> None:
        self.s, self.db, self.rest = settings, db, rest
        self.clock = clock or Clock()
        self.limits = limits or HardLimits.load()
        self.params = load_strategy_params()
        from .core.engine_store import EngineStore

        self.store = EngineStore(store_path, state_key) if (store_path is not None and state_key) else None
        self.guardian = Guardian(db, self.limits, settings.real_trading_server_flag, settings.role, self.clock.now_ms,
                                 state_key=state_key, store=self.store)
        self._realloc: dict[str, dict[str, Any]] = {"simulation": {}, "real": {}}  # en mémoire du moteur seulement
        self.registry = Registry(db, self.clock.now_ms, settings.role)
        self.notifier = Notifier(db, self.clock.now_ms)
        self.markets: dict[str, MarketInfo] = {}
        self.states: dict[str, MarketState] = {}
        self.candles: dict[str, pd.DataFrame] = {}
        sim = self.params["simulation"]
        self.fee_taker, self.fee_maker = D(sim["fee_taker"]), D(sim["fee_maker"])
        self.strategies = build_strategies(self.params)
        self.decider = DecisionEngine(self.strategies, self.limits, self.fee_taker, self.fee_maker,
                                      self.params.get("risk_weights"))
        self.paper: SimulatedExchange | None = None
        self.paper_exec: ExecutionEngine | None = None
        self.real_exec: ExecutionEngine | None = None
        self.real_exchange: Any = None
        self.protect: dict[str, ProtectionManager] = {}
        self.last_markets_ms = 0
        self.last_decision_ms = 0
        self.last_recon_ms = 0
        self.last_ticker_ms = 0
        self.last_data_ms = 0
        self.last_snapshot_ms = 0
        self.last_fee_refresh_ms = 0
        self.command_key = command_key  # vérifie les commandes sensibles signées par le propriétaire

    # ------------------------------------------------------------ état réel protégé
    def _kv_get(self, key: str, default: Any, mode: str) -> Any:
        """Données qui pilotent des ordres RÉELS (stops, périmètre exclu) : magasin du moteur si disponible."""
        if mode == "real" and self.store is not None:
            return self.store.get(key, default)
        return self.db.kv_get(key, default)

    def _kv_set(self, key: str, value: Any, mode: str) -> None:
        if mode == "real" and self.store is not None:
            self.store.set(key, value)
        self.db.kv_set(key, value, self.clock.now_ms())  # copie d'affichage (PWA)

    # ------------------------------------------------------------ compat : exécuteur « principal »
    @property
    def exec(self) -> ExecutionEngine | None:
        """Exécuteur des NOUVELLES entrées : réel si REEL_AUTORISE, sinon simulation."""
        if self.guardian.state == State.REAL and self.real_exec is not None:
            return self.real_exec
        return self.paper_exec

    def execs(self) -> list[ExecutionEngine]:
        return [e for e in (self.paper_exec, self.real_exec) if e is not None]

    # ------------------------------------------------------------ démarrage
    def start(self) -> None:
        now = self.clock.now_ms()
        self.db.kv_set("hard_limits_sha256", self.limits.sha256, now)
        self.db.kv_set("strategy_params_version", self.params.get("version"), now)
        self.guardian.init_state()
        self.guardian.verify_integrity()
        # Redémarrage alors que le réel était actif : passer par la réconciliation avant toute entrée.
        if self.guardian.state == State.REAL:
            self.guardian.enter_reconciliation("redémarrage : réconciliation obligatoire")
        # Commandes interrompues par un crash : jamais rejouées automatiquement.
        self.db.exec("UPDATE control_commands SET status='done', processed_ms=?, result=? WHERE status='processing'",
                     (now, json.dumps({"ok": False, "reasons": ["interrompue par un redémarrage : non rejouée"]}, ensure_ascii=False)))
        for s in self.strategies:
            self.registry.register(s.name, self.params["version"], self.params[s.name])

    def attach_real_exchange(self, ex: Any) -> None:
        self.real_exchange = ex
        if self.markets:
            self._init_real_exec()

    def _init_real_exec(self) -> None:
        if self.real_exchange is None or self.real_exec is not None:
            return
        self.real_exec = ExecutionEngine(self.db, self.real_exchange, self.guardian, self.markets, "real",
                                         self.s.bitvavo_operator_id, self.clock.now_ms, taker_fee=self.fee_taker)
        self.protect["real"] = ProtectionManager(self.real_exec)

    # ------------------------------------------------------------ données
    async def refresh_markets(self) -> None:
        raw = await self.rest.markets()
        new = {m.market: m for m in (MarketInfo.from_bitvavo(d) for d in raw)}
        changes = diff_markets(self.markets, new) if self.markets else {"listed": list(new), "delisted": [],
                                                                         "status_changed": []}
        now = self.clock.now_ms()
        with self.db.tx() as c:
            for m in new.values():
                c.execute("INSERT INTO markets(market,base,quote,status,meta,first_seen_ms,last_seen_ms) VALUES(?,?,?,?,?,?,?)"
                          " ON CONFLICT(market) DO UPDATE SET status=excluded.status, meta=excluded.meta,"
                          " last_seen_ms=excluded.last_seen_ms, delisted_ms=NULL",
                          (m.market, m.base, m.quote, m.status, json.dumps(m.raw), now, now))
            for d in changes["delisted"]:
                c.execute("UPDATE markets SET delisted_ms=? WHERE market=?", (now, d))
        # mise à jour EN PLACE : les exécuteurs partagent ce dictionnaire
        self.markets.clear()
        self.markets.update(new)
        for k, m in new.items():
            if k in self.states:
                self.states[k].info = m
            else:
                self.states[k] = MarketState(m)
        if self.paper is None:
            self._init_paper()
        else:
            self.paper.markets = dict(new)
        self._init_real_exec()
        self.last_markets_ms = now

    def _init_paper(self) -> None:
        self.paper = SimulatedExchange(list(self.markets.values()), {"EUR": self.s.initial_capital_eur},
                                       self.fee_maker, self.fee_taker, self.clock.now_ms)
        self.paper_exec = ExecutionEngine(self.db, self.paper, self.guardian, self.markets, "simulation",
                                          self.s.bitvavo_operator_id, self.clock.now_ms, taker_fee=self.fee_taker)
        self.protect["simulation"] = ProtectionManager(self.paper_exec)
        self._replay_paper_state()

    def _replay_paper_state(self) -> None:
        """Après redémarrage : reconstitue les soldes papier depuis le journal. Les ordres papier ouverts ne
        survivent pas à un redémarrage du simulateur : ils sont marqués annulés (les stops seront reposés)."""
        assert self.paper is not None
        led = self._ledger("simulation")
        self.paper.available = {"EUR": led.s.cash.get("EUR", ZERO)}
        for sym, p in led.s.positions.items():
            if p.qty > 0:
                self.paper.available[sym] = p.qty
        self.db.exec("UPDATE intents SET status='canceled', reserved_quote='0', reserved_base='0', "
                     "last_error='simulateur redémarré' WHERE mode='simulation' AND status IN "
                     "('new','partiallyFilled','awaitingTrigger','PENDING','SUBMITTING','UNKNOWN')")

    async def refresh_tickers(self) -> None:
        now = self.clock.now_ms()
        books = await self.rest.ticker_book()
        vols = {t["market"]: t for t in await self.rest.ticker_24h()} if now - self.last_ticker_ms > 60_000 else {}
        for b in books:
            st = self.states.get(b["market"])
            if st is None:
                continue
            st.best_bid = D(b["bid"]) if b.get("bid") else None
            st.best_ask = D(b["ask"]) if b.get("ask") else None
            st.ts_received_ms = now
            st.ts_market_ms = int(b.get("timestamp", now))
            if st.best_bid and st.best_ask and self.paper:
                self.paper.set_book(b["market"], [(st.best_bid, b.get("bidSize") or "0")],
                                    [(st.best_ask, b.get("askSize") or "0")])
                self.paper.check_triggers(b["market"])  # stops papier évalués sur le meilleur bid réel
        for m, t in vols.items():
            st = self.states.get(m)
            if st:
                st.last_price = D(t["last"]) if t.get("last") else st.last_price
                st.quote_volume_24h = D(t.get("volumeQuote") or 0)
        if vols:
            self.last_ticker_ms = now

    async def refresh_candles(self, markets: list[str], limit: int = 500) -> None:
        now = self.clock.now_ms()
        for m in markets:
            rows = await self.rest.candles(m, INTERVAL, limit=limit)
            cs = [Candle.from_bitvavo(r) for r in rows]
            cs = [c for c in cs if c.ts + INTERVAL_MS <= now]  # bougie en cours exclue : pas de fuite
            with self.db.tx() as c:
                c.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)",
                              [(m, INTERVAL, k.ts, fmt(k.open), fmt(k.high), fmt(k.low), fmt(k.close), fmt(k.volume),
                                now, "bitvavo") for k in cs])
            if cs:
                df = pd.DataFrame([{"ts": k.ts, "open": float(k.open), "high": float(k.high), "low": float(k.low),
                                    "close": float(k.close), "volume": float(k.volume)} for k in cs]).set_index("ts")
                df = df[~df.index.duplicated(keep="last")].sort_index()
                self.candles[m] = df

    async def refresh_fees(self) -> None:
        """Frais réels du compte (palier) : remplacent les hypothèses de configuration pour le RÉEL."""
        ex = self.real_exchange
        if ex is None or not getattr(ex, "has_credentials", True):
            return
        try:
            acc = await ex.account()
            fees = acc.get("fees", {})
            if fees.get("taker") and fees.get("maker"):
                taker, maker = D(fees["taker"]), D(fees["maker"])
                if self.real_exec is not None:
                    self.real_exec.taker_fee = taker
                self.db.kv_set("real_fees", {"taker": fmt(taker), "maker": fmt(maker), "ts": self.clock.now_ms()})
        except (BitvavoError, Exception) as e:  # noqa: BLE001
            log.warning("actualisation des frais impossible", extra={"data": {"err": repr(e)}})
        self.last_fee_refresh_ms = self.clock.now_ms()

    async def on_ws_event(self, msg: dict[str, Any]) -> None:
        """Événement privé order/fill : relecture REST immédiate (le REST reste la source de vérité)."""
        if msg.get("event") in ("order", "fill") and self.real_exec is not None:
            self.db.kv_set("ws_last_event", {"ts": self.clock.now_ms(), "event": msg.get("event")})
            await self.real_exec.refresh_open()

    async def on_ws_reconnect(self) -> None:
        """Après une coupure du flux : événements possiblement perdus -> suivi des ordres + réconciliation."""
        if self.real_exec is not None:
            await self.real_exec.refresh_open()
            if self.held_positions("real") or self.guardian.state == State.REAL:
                await self._reconcile_real()

    # ------------------------------------------------------------ portefeuille
    def _ledger(self, mode: str) -> Ledger:
        """Rejoue fills et flux externes dans l'ORDRE CHRONOLOGIQUE (un dépôt de crypto suivi d'une vente
        doit être appliqué avant la vente). Mis en cache tant qu'aucun fill/flux n'a changé (le rejeu complet
        à chaque appel croissait avec l'historique)."""
        sig = self.db.one("SELECT (SELECT COUNT(*) FROM fills WHERE mode=?) a, (SELECT COALESCE(MAX(rowid),0) FROM fills "
                          "WHERE mode=?) b, (SELECT COUNT(*)||'-'||COALESCE(SUM(identified),0)||'-'||"
                          "COALESCE(GROUP_CONCAT(value_eur),'') FROM external_flows WHERE mode=?) c", (mode, mode, mode))
        key = (mode, sig["a"], sig["b"], sig["c"]) if sig else None
        cache = getattr(self, "_ledger_cache", {})
        if key is not None and cache.get(mode, (None,))[0] == key:
            return copy.deepcopy(cache[mode][1])
        led = self._build_ledger(mode)
        cache[mode] = (key, copy.deepcopy(led))
        self._ledger_cache = cache
        return led

    def _build_ledger(self, mode: str) -> Ledger:
        led = Ledger(self.s.initial_capital_eur if mode == "simulation" else ZERO)
        events: list[tuple[int, int, Any]] = []
        for f in self.db.q("SELECT * FROM fills WHERE mode=?", (mode,)):
            events.append((int(f["ts"]), 1, Fill(f["ts"], f["market"], f["side"], D(f["amount"]), D(f["price"]),
                                                 D(f["fee"]), f["fee_currency"],
                                                 D(f["fee_eur"]) if f["fee_eur"] else None, f["id"])))
        for fl in self.db.q("SELECT * FROM external_flows WHERE mode=? AND identified=1", (mode,)):
            events.append((int(fl["ts"]), 0, ExternalFlow(fl["ts"], fl["kind"], fl["symbol"], D(fl["amount"]),
                                                          D(fl["value_eur"]))))
        for _, kind, ev in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == 1:
                led.apply_fill(ev)
            else:
                led.apply_flow(ev)
        return led

    def prices_eur(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for st in self.states.values():
            if st.info.quote == "EUR":
                px = st.best_bid or st.last_price
                if px:
                    out[st.info.base] = px
        return out

    def _first_fill_ts(self, mode: str, market: str) -> int:
        r = self.db.one("SELECT MIN(ts) t FROM fills WHERE mode=? AND market=? AND side='buy'", (mode, market))
        return int(r["t"]) if r and r["t"] else self.clock.now_ms()

    def held_positions(self, mode: str) -> dict[str, HeldPosition]:
        led = self._ledger(mode)
        meta = self._kv_get(f"position_meta_{mode}", {}, mode)
        excluded = set(self._kv_get("real_excluded_symbols", [], "real")) if mode == "real" else set()
        out = {}
        for sym, p in led.s.positions.items():
            mk = f"{sym}-EUR"
            m = self.markets.get(mk)
            if p.qty <= 0 or sym in excluded:
                continue
            md = meta.get(mk, {})
            # méta absente (crash, position importée) : jamais de « durée max » ni de stop nul par défaut
            entry_ts = int(md.get("entry_ts") or self._first_fill_ts(mode, mk))
            stop = D(md.get("stop") or "0")
            if stop <= 0 and mk in self.candles and m is not None:
                df = self.candles[mk]
                a = float(ind.atr(df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float))[-1])
                stop = D(str(max(0.0, float(df["close"].iloc[-1]) - 2.5 * a)))
            out[mk] = HeldPosition(mk, p.qty, p.avg_cost, entry_ts, md.get("strategy", "?"), stop,
                                   int((self.clock.now_ms() - entry_ts) // INTERVAL_MS))
        return out

    # ------------------------------------------------------------ commandes de contrôle (depuis l'API)
    async def process_commands(self) -> None:
        now = self.clock.now_ms()
        for cmd in self.db.q("SELECT * FROM control_commands WHERE status='pending' ORDER BY ts"):
            # marquage « processing » AVANT exécution : en cas de crash, la commande n'est pas rejouée ;
            # prise atomique : si une autre boucle/instance l'a déjà prise, elle n'est jamais exécutée deux fois
            with self.db.tx() as cx:
                taken = cx.execute("UPDATE control_commands SET status='processing' WHERE id=? AND status='pending'",
                                   (cmd["id"],)).rowcount
            if taken != 1:
                continue
            payload = json.loads(cmd["payload"] or "{}")
            actor = payload.get("actor", "owner")
            c = cmd["command"]
            res: dict[str, Any]
            try:
                if c not in ALWAYS_VALID_COMMANDS and now - int(cmd["ts"]) > COMMAND_TTL_MS:
                    res = {"ok": False, "reasons": ["commande expirée (non traitée dans les 10 minutes)"]}
                elif c == "pause":
                    self.guardian.pause(actor, payload.get("reason", "pause manuelle"))
                    res = {"ok": True}
                elif c == "emergency_stop":
                    self.guardian.emergency_stop(actor)
                    res = {"ok": True, "note": "nouvelles entrées bloquées ; positions et protections conservées"}
                elif c == "resume":
                    to_real = self.guardian.resume_state == State.REAL
                    why = self._verify_signed("resume_real", payload) if to_real else None
                    if why is not None:
                        # reprise vers le RÉEL = réactivation : signature du propriétaire exigée (une API
                        # compromise ne peut pas réarmer le réel après un arrêt d'urgence)
                        res = {"ok": False, "needs_signature": "resume_real",
                               "reasons": [f"reprise vers le mode réel : {why}"]}
                    else:
                        v = self.guardian.resume(actor, await self.preflight(for_real=to_real),
                                                 expected_target=State.REAL if to_real else self.guardian.resume_state)
                        res = {"ok": v.allowed, "reasons": v.reasons}
                elif c == "activate_real":
                    why = self._verify_signed(c, payload)
                    signed = payload.get("sig_fields") or {}
                    pf = await self.preflight(for_real=True)
                    # seuls les champs SIGNÉS par le propriétaire comptent (pas ceux ajoutés par l'API)
                    pf.update({k: bool(signed.get(k)) for k in ("declare_retrait_desactive", "declare_ip_fixe",
                                                                "declare_2fa", "accepte_risque_perte_totale")})
                    pf["commande_signee_par_le_proprietaire"] = why is None
                    v = self.guardian.activate_real(actor, str(signed.get("confirmation", "")),
                                                    bool(payload.get("password_ok")) and why is None, pf)
                    res = {"ok": v.allowed, "reasons": v.reasons + ([why] if why else [])}
                    if v.allowed:
                        self.notifier.notify("incident", "MODE RÉEL ACTIVÉ par le propriétaire", dedup_key=new_id())
                elif c == "deactivate_real":
                    self.guardian.deactivate_real(actor)
                    res = {"ok": True, "note": "plus d'entrée réelle ; positions réelles restantes toujours protégées"}
                elif c == "liquidation_estimate":
                    res = {"ok": True, "estimate": self.liquidation_estimate()}
                elif c == "liquidation_confirm":
                    res = await self.liquidate(payload)
                elif c == "ack_flow":
                    res = self.ack_flow(payload["flow_id"], payload.get("note", "rapproché manuellement"))
                else:
                    res = {"ok": False, "reasons": ["commande inconnue"]}
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "reasons": [f"{type(e).__name__}: {e}"]}
            self.db.exec("UPDATE control_commands SET status='done', processed_ms=?, result=? WHERE id=?",
                         (self.clock.now_ms(), json.dumps(res, default=str, ensure_ascii=False), cmd["id"]))

    def _verify_signed(self, command: str, payload: dict[str, Any]) -> str | None:
        from .core.command_sig import verify

        used = self.store.get("used_command_nonces", []) if self.store else self.db.kv_get("used_command_nonces", [])
        why = verify(self.command_key, command, payload, self.clock.now_ms(), set(used),
                     not_before_ms=self.guardian.revoked_ms)
        if why is None:
            used = (used + [payload["sig_nonce"]])[-2000:]
            if self.store:
                self.store.set("used_command_nonces", used)
            else:
                self.db.kv_set("used_command_nonces", used, self.clock.now_ms())
        return why

    def ack_flow(self, flow_id: str, note: str) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM external_flows WHERE id=?", (flow_id,))
        if r is None:
            return {"ok": False, "reasons": ["flux inconnu"]}
        px = self.prices_eur()
        amt = D(r["amount"])
        val = amt if r["symbol"] == "EUR" else amt * px.get(r["symbol"], ZERO)
        if r["symbol"] != "EUR" and val <= 0:
            return {"ok": False, "reasons": ["valorisation EUR indisponible : réessayer quand le prix est connu"]}
        self.db.exec("UPDATE external_flows SET identified=1, value_eur=?, note=? WHERE id=?", (fmt(val), note, flow_id))
        return {"ok": True}

    async def preflight(self, for_real: bool) -> dict[str, bool]:
        now = self.clock.now_ms()
        fresh = [st for st in self.states.values() if now - st.ts_received_ms <= self.limits.max_stale_ms]
        pf = {
            "donnees_marche_fraiches": bool(fresh),
            "aucun_ordre_inconnu": self.db.one("SELECT 1 FROM intents WHERE status IN ('UNKNOWN','SUBMITTING')") is None,
            "aucun_flux_non_identifie": self.db.one("SELECT 1 FROM external_flows WHERE identified=0") is None,
            "integrite_etat": self.guardian.verify_integrity() if self.guardian.integrity_enabled else not for_real,
        }
        if for_real:
            pf["cles_api_presentes"] = self.real_exchange is not None and getattr(self.real_exchange, "has_credentials", False)
            from .core.attest import verify_proof
            from .core.config import ROOT

            why_t = verify_proof(self.db.kv_get("acceptance_proof"), self.guardian._key, ROOT, now)
            self.db.kv_set("acceptance_proof_check", {"ts": now, "raisons": why_t}, now)
            pf["tests_acceptation_ok"] = not why_t
            pf["reconciliation_ok"] = await self._reconcile_real() if self.real_exchange else False
        return pf

    async def _reconcile_real(self) -> bool:
        """Compare le journal réel aux soldes Bitvavo ; enregistre les flux identifiés, signale les autres.
        Premier rapprochement : les soldes existants sont enregistrés comme solde d'ouverture (EUR) ; les
        autres actifs déjà détenus sont EXCLUS du périmètre du robot (jamais vendus par lui)."""
        ex = self.real_exchange
        if ex is None:
            return False
        try:
            bal = await ex.balance()
            since = int(self.db.kv_get("recon_since_ms", 0))
            start = max(0, since - RECON_OVERLAP_MS) if since else None
            deps = await ex.deposit_history(start)
            wds = await ex.withdrawal_history(start)
        except (BitvavoError, Exception) as e:  # noqa: BLE001
            log.error("réconciliation impossible", extra={"data": {"err": repr(e)}})
            return False
        now = self.clock.now_ms()

        def key(prefix: str, d: dict[str, Any]) -> str:
            return f"{prefix}-{d.get('txId') or d.get('timestamp')}-{d.get('symbol')}-{d.get('amount')}"

        if not self._kv_get("real_opening_done", False, "real"):
            # Les mouvements antérieurs sont déjà dans le solde d'ouverture : on les marque comme intégrés.
            with self.db.tx() as c:
                for prefix, lst in (("dep", deps), ("wd", wds)):
                    for d in lst:
                        c.execute("INSERT OR IGNORE INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,"
                                  "identified,note) VALUES(?,?,?,?,?,?,?,?,1,?)",
                                  (key(prefix, d), int(d.get("timestamp", now)), "preopening",
                                   "deposit" if prefix == "dep" else "withdrawal", d.get("symbol"), str(d.get("amount")),
                                   "0", "bitvavo_history", "antérieur à l'ouverture : inclus dans le solde d'ouverture"))
            self._record_opening(bal, now)
            self.db.kv_set("recon_since_ms", now, now)
            self.last_recon_ms = now
            return True
        excluded = set(self._kv_get("real_excluded_symbols", [], "real"))
        led = self._ledger("real")
        expected = {k: v for k, v in led.s.cash.items() if k not in excluded}
        expected.update({s: p.qty for s, p in led.s.positions.items() if s not in excluded})
        bal = [b for b in bal if b["symbol"] not in excluded]
        known = {r["id"] for r in self.db.q("SELECT id FROM external_flows WHERE mode IN ('real','preopening')")}

        new_deps = [d for d in deps if key("dep", d) not in known and d.get("symbol") not in excluded]
        new_wds = [w for w in wds if key("wd", w) not in known and w.get("symbol") not in excluded]
        res = reconcile(expected, bal, new_deps, new_wds)
        px = self.prices_eur()
        with self.db.tx() as c:
            for fl in res.identified_flows:
                is_dep = fl["kind"] == "deposit"
                # un retrait sort du compte montant + frais de retrait
                amt = D(fl["amount"]) + (ZERO if is_dep else D(fl.get("fee", 0)))
                val = amt if fl["symbol"] == "EUR" else amt * px.get(fl["symbol"], ZERO)
                c.execute("INSERT OR IGNORE INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,identified)"
                          " VALUES(?,?,?,?,?,?,?,?,1)", (key("dep" if is_dep else "wd", fl), int(fl.get("timestamp", now)),
                                                         "real", fl["kind"], fl["symbol"], fmt(amt), fmt(val),
                                                         "bitvavo_history"))
            # un seul écart ouvert par actif (mis à jour), jamais une ligne par cycle
            c.execute("DELETE FROM external_flows WHERE mode='real' AND identified=0")
            for sym, amt in res.unidentified.items():
                c.execute("INSERT INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,identified,note)"
                          " VALUES(?,?,?,?,?,?,?,?,0,?)", (f"unk-{sym}", now, "real", "deposit" if amt > 0 else "withdrawal",
                                                           sym, fmt(abs(amt)), "0", "ecart_solde", "mouvement non identifié"))
        self.db.kv_set("recon_since_ms", now, now)
        self.last_recon_ms = now
        if not res.ok:
            self.guardian.pause("reconciliation", "mouvement manuel non identifié : rapprochement requis")
            self.notifier.notify("flux_non_identifie", f"Écart de solde non identifié : {res.unidentified}")
        return res.ok

    def _record_opening(self, bal: list[dict[str, Any]], now: int) -> None:
        excluded = []
        with self.db.tx() as c:
            for b in bal:
                total = D(b.get("available", 0)) + D(b.get("inOrder", 0))
                if total <= 0:
                    continue
                if b["symbol"] == "EUR":
                    c.execute("INSERT OR IGNORE INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,identified,note)"
                              " VALUES(?,?,?,?,?,?,?,?,1,?)", ("opening-EUR", now, "real", "deposit", "EUR", fmt(total),
                                                               fmt(total), "solde_ouverture", "capital affecté au robot"))
                else:
                    excluded.append(b["symbol"])
        self._kv_set("real_excluded_symbols", excluded, "real")
        self._kv_set("real_opening_done", True, "real")

    # ------------------------------------------------------------ décisions
    def _record_decision(self, mode: str, p: Any, accepted: bool, reasons: list[str], hypothetical: bool) -> None:
        self.db.exec("INSERT INTO decisions(id,ts,mode,market,strategy,action,accepted,reasons,details,hypothetical)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (new_id(), self.clock.now_ms(), mode, p.market, p.strategy, p.action, int(accepted),
                      json.dumps(reasons, ensure_ascii=False), json.dumps({"risk": p.risk, "confidence": p.confidence,
                                                                           "edge": p.edge, **p.details}, default=str),
                      int(hypothetical)))

    async def manage_positions(self, ex: ExecutionEngine) -> None:
        """Protections natives + sorties pour les positions du mode de `ex` (permis même en pause)."""
        mode = ex.mode
        led = self._ledger(mode)
        px = self.prices_eur()
        equity = led.value_eur(px)
        cash = led.s.cash.get("EUR", ZERO)
        meta = self._kv_get(f"position_meta_{mode}", {}, mode)
        statuses = {}
        for mk, h in self.held_positions(mode).items():
            st, df = self.states.get(mk), self.candles.get(mk)
            pm = self.protect[mode]
            if st is None:
                continue
            p = self.decider.evaluate_exit(st.info, st, df, h) if df is not None else None
            realloc = self._realloc[mode]
            if (mk in realloc and self.clock.now_ms() - int(realloc[mk].get("ts", 0)) < 3_600_000
                    and self.guardian.state in (State.SIMULATION, State.REAL)):
                from .portfolio.decision import Proposal
                p = Proposal(mk, "vendre", h.strategy, amount_base=st.info.round_amount(h.qty),
                             reasons=[f"réallocation au profit de {realloc[mk]['au_profit_de']}"])
                realloc.pop(mk)
            if p is not None and p.action == "vendre" and p.amount_base > 0:
                ok, why = await pm.release_for_exit(mk)
                if not ok:
                    self._record_decision(mode, p, False, p.reasons + [why], False)
                    continue
                h2 = self.held_positions(mode).get(mk)
                if h2 is None:
                    meta.pop(mk, None)
                    continue
                qty = st.info.round_amount(h2.qty)
                r = await ex.submit(OrderRequest(mk, "sell", "market", amount=qty, strategy=h.strategy, is_entry=False,
                                                 is_protective=True, position_qty=h2.qty, purpose="sortie",
                                                 ref_price=st.best_bid,
                                                 check=self._check(st, "sell", qty * (st.best_bid or ZERO), False,
                                                                   equity, cash, ZERO, mode)), cash)
                self._record_decision(mode, p, r.accepted, p.reasons + r.reasons, False)
                if r.accepted:
                    meta.pop(mk, None)
                continue
            if h.stop > 0:
                ps = await pm.ensure(mk, h.qty, h.stop)
                statuses[mk] = {"protege": ps.protected, "etat": ps.state, "declencheur": fmt(ps.trigger) if ps.trigger
                                else None, "quantite_couverte": fmt(ps.covered_qty), "raison": ps.reason}
        self._kv_set(f"position_meta_{mode}", meta, mode)
        self.db.kv_set(f"protections_{mode}", statuses, self.clock.now_ms())
        unprotected = [m for m, s in statuses.items() if not s["protege"]]
        if mode == "real" and unprotected:
            self.notifier.notify("arret_protection", f"Positions réelles NON protégées : {unprotected}",
                                 dedup_key=f"unprot-{','.join(sorted(unprotected))}")

    async def decide(self) -> None:
        ex = self.exec
        assert ex is not None
        mode = ex.mode
        now = self.clock.now_ms()
        led = self._ledger(mode)
        px = self.prices_eur()
        equity = led.value_eur(px)
        cash = led.s.cash.get("EUR", ZERO)
        held = self.held_positions(mode)
        exposure = sum((h.qty * px.get(h.market.split("-")[0], ZERO) for h in held.values()), ZERO)
        meta = self._kv_get(f"position_meta_{mode}", {}, mode)
        returns = {m: df["close"].pct_change() for m, df in self.candles.items()}
        scanner = []
        for mk, st in self.states.items():
            el = check_eligibility(st, self.limits, now, self.s.quote_currency)
            row: dict[str, Any] = {"market": mk, "eligible": el.eligible, "reasons": el.reasons,
                                   "spread_bps": float(spread_bps(st.best_bid, st.best_ask) or 0),
                                   "volume_24h_eur": float(st.quote_volume_24h)}
            df = self.candles.get(mk)
            if el.eligible and df is not None:
                ob = self.paper.books.get(mk) if self.paper else None
                depth = ob.depth_quote("asks", D(50)) if ob else ZERO
                held_rets = {h: returns[h] for h in held if h in returns and h != mk}
                props = self.decider.evaluate_entry(st.info, st, df, equity, cash, exposure, held.get(mk), depth,
                                                    held_returns=held_rets)
                for p in props:
                    row.setdefault("propositions", []).append({"strategy": p.strategy, "action": p.action,
                                                               "risk": p.risk, "confidence": p.confidence,
                                                               "edge": p.edge, "reasons": p.reasons,
                                                               "label": "estimation non calibrée"})
                    if p.action != "acheter":
                        if p.strategy != "-":
                            self._record_decision(mode, p, False, p.reasons, True)
                            if p.details.get("contrainte_liante") == "disponible_apres_reserve" and held:
                                self._consider_reallocation(mode, p, held)
                        continue
                    chk = self._check(st, "buy", p.amount_quote, True, equity, cash, exposure + p.amount_quote, mode)
                    chk.reinforcement_of_loser = mk in held and (st.last_price or ZERO) < held[mk].avg_cost
                    req = OrderRequest(mk, "buy", p.order_type,
                                       amount_quote=p.amount_quote if p.order_type == "market" else None,
                                       amount=(p.amount_quote / p.limit_price) if p.order_type == "limit" and p.limit_price else None,
                                       price=p.limit_price, post_only=p.post_only, strategy=p.strategy, check=chk,
                                       purpose="entree")
                    r = await ex.submit(req, cash)
                    self._record_decision(mode, p, r.accepted, p.reasons + r.reasons, False)
                    if r.accepted:
                        meta[mk] = {"entry_ts": now, "strategy": p.strategy, "stop": str(p.details.get("stop", 0))}
                        cash -= p.amount_quote
                        exposure += p.amount_quote
                    break  # une seule entrée par marché et par cycle
            scanner.append(row)
        self._kv_set(f"position_meta_{mode}", meta, mode)
        self.db.kv_set("scanner", {"ts": now, "rows": scanner}, now)
        self.last_decision_ms = now

    def _consider_reallocation(self, mode: str, p: Any, held: dict[str, HeldPosition]) -> None:
        """Opportunité bloquée faute de liquidités : libérer la position la plus faible si le gain net
        (après frais de changement, hystérésis et incertitude) est positif. La vente se fait au cycle suivant
        par manage_positions (stop natif annulé et confirmé d'abord)."""
        from .portfolio.actions import ActionEstimate, pick_reallocation
        from .portfolio.decision import forward_outcomes

        n = int(p.details.get("n_signaux_independants") or 0)
        if n < 20 or self.guardian.state not in (State.SIMULATION, State.REAL):
            return  # en pause/incident : vendre sans pouvoir racheter serait une rotation nuisible
        cand = ActionEstimate(p.market, float(p.details.get("esperance_nette", 0.0)),
                              float(p.details.get("ecart_type", 1.0)) / max(1, n) ** 0.5, n)
        ests = {}
        for mk, h in held.items():
            strat = next((s for s in self.strategies if s.name == h.strategy), None)
            df = self.candles.get(mk)
            if strat is None or df is None:
                continue
            sig = strat.signals(df)
            rt = float(2 * self.fee_taker)
            r = forward_outcomes(df, sig.entry, min(24, sig.max_bars)) - rt
            if len(r) >= 20:
                ests[mk] = ActionEstimate(mk, float(r.mean()), float(r.std(ddof=1) / len(r) ** 0.5), len(r))
        victim, gain = pick_reallocation(ests, cand, switch_cost=float(4 * self.fee_taker))
        if victim:
            self._realloc[mode][victim] = {"au_profit_de": p.market, "gain_borne_basse": gain.borne_basse if gain else None,
                                           "ts": self.clock.now_ms()}
            self.db.kv_set(f"realloc_{mode}_affichage", self._realloc[mode], self.clock.now_ms())

    def _check(self, st: MarketState, side: str, amount_q: Decimal, entry: bool, equity: Decimal, cash: Decimal,
               exposure_after: Decimal, mode: str) -> IntentCheck:
        held = self.held_positions(mode).get(st.info.market)
        pos_after = (held.qty * (st.best_bid or ZERO) if held else ZERO) + (amount_q if entry else ZERO)
        return IntentCheck(st.info.market, side, "market", amount_q, entry, not entry,
                           data_age_ms=self.clock.now_ms() - st.ts_received_ms, market_trading=st.info.is_trading,
                           spread_bps=spread_bps(st.best_bid, st.best_ask), anomaly=st.anomaly_score,
                           min_order_quote=st.info.min_order_quote, available_quote=cash, equity=equity,
                           exposure_after=exposure_after, position_after=pos_after, mode=mode)

    # ------------------------------------------------------------ liquidation manuelle
    def _liq_mode(self) -> str:
        """La liquidation vise le réel dès qu'un exécuteur réel existe et détient des positions."""
        if self.real_exec is not None and self.held_positions("real"):
            return "real"
        return "simulation"

    def liquidation_estimate(self) -> dict[str, Any]:
        mode = self._liq_mode()
        out: list[dict[str, Any]] = []
        total = ZERO
        for mk, h in self.held_positions(mode).items():
            st = self.states.get(mk)
            bid = st.best_bid if st else None
            if bid is None or st is None:
                out.append({"market": mk, "qty": fmt(h.qty), "estimation": None, "note": "prix indisponible"})
                continue
            got_b, got_q, full = (self.paper.books[mk].estimate_fill("sell", amount_base=h.qty)
                                  if self.paper and mk in self.paper.books else (h.qty, h.qty * bid, False))
            gross = got_q if full else h.qty * bid
            fee = gross * self.fee_taker
            dust = gross < st.info.min_order_quote
            out.append({"market": mk, "qty": fmt(h.qty), "brut_eur": fmt(gross), "frais_eur": fmt(fee),
                        "net_eur": fmt(gross - fee), "poussiere_invendable": dust,
                        "profondeur_suffisante_au_meilleur_prix": full})
            if not dust:
                total += gross - fee
        est = {"id": new_id(), "ts": self.clock.now_ms(), "mode": mode, "lignes": out, "net_total_eur": fmt(total),
               "avertissement": "Estimation sur le carnet connu ; le glissement réel peut être supérieur."}
        self.db.kv_set("liquidation_estimate", est, self.clock.now_ms())
        return est

    async def liquidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        est = self.db.kv_get("liquidation_estimate")
        if not est or est["id"] != payload.get("estimate_id") or self.clock.now_ms() - est["ts"] > 120_000:
            return {"ok": False, "reasons": ["estimation absente, déjà utilisée ou expirée (2 min) : recommencer"]}
        if est.get("mode") == "real" or self.command_key is not None:
            why = self._verify_signed("liquidation_confirm", payload)
            signed = payload.get("sig_fields") or {}
            if why or signed.get("estimate_id") != est["id"] or signed.get("confirmation") != "LIQUIDER":
                return {"ok": False, "reasons": [why or "champs signés incohérents"]}
        elif not payload.get("password_ok") or payload.get("confirmation") != "LIQUIDER":
            return {"ok": False, "reasons": ["confirmation invalide"]}
        self.db.kv_set("liquidation_estimate", None, self.clock.now_ms())  # usage unique
        await self.refresh_tickers()  # jamais de vente sur des prix périmés
        mode = est["mode"]
        ex = self.real_exec if mode == "real" else self.paper_exec
        assert ex is not None
        results = []
        for mk in self.held_positions(mode):
            st = self.states.get(mk)
            if st is None:
                continue
            ok, why = await self.protect[mode].release_for_exit(mk)
            if not ok:
                results.append({"market": mk, "status": "REPORTÉ", "reasons": [why]})
                continue
            h2 = self.held_positions(mode).get(mk)
            if h2 is None:
                continue
            qty = st.info.round_amount(h2.qty)
            r = await ex.submit(OrderRequest(mk, "sell", "market", amount=qty, strategy="liquidation_manuelle",
                                             is_entry=False, is_protective=True, position_qty=h2.qty,
                                             purpose="liquidation", ref_price=st.best_bid,
                                             check=self._check(st, "sell", qty * (st.best_bid or ZERO), False,
                                                               ZERO, ZERO, ZERO, mode)), ZERO)
            results.append({"market": mk, "status": r.status, "reasons": r.reasons})
        return {"ok": True, "mode": mode, "resultats": results}

    # ------------------------------------------------------------ snapshot / santé
    def snapshot(self, force: bool = False) -> None:
        now = self.clock.now_ms()
        if force or now - self.last_snapshot_ms >= 60_000:  # 1/min : ~525k lignes/an/mode au plus
            px = self.prices_eur()
            for ex in self.execs():
                led = self._ledger(ex.mode)
                v = led.value_eur(px)
                cash = led.s.cash.get("EUR", ZERO)
                self.db.exec("INSERT OR REPLACE INTO equity_snapshots VALUES(?,?,?,?,?)",
                             (now, ex.mode, fmt(v), fmt(cash), fmt(v - cash)))
            self.last_snapshot_ms = now
        self.heartbeat(data_ok=True)

    def heartbeat(self, data_ok: bool) -> None:
        """Signe de vie du moteur, distinct de la fraîcheur des données de marché."""
        ex = self.exec
        mode = ex.mode if ex else "simulation"
        self.db.kv_set("engine_heartbeat", {"ts": self.clock.now_ms(), "mode": mode, "pid": os.getpid(),
                                            "n_markets": len(self.markets), "data_ok": data_ok,
                                            "last_data_ms": self.last_data_ms,
                                            "real_exec": self.real_exec is not None}, self.clock.now_ms())

    async def step(self) -> None:
        now = self.clock.now_ms()
        # Les commandes de contrôle (arrêt d'urgence, pause) passent AVANT tout accès réseau.
        await self.process_commands()
        if not self.guardian.verify_integrity():
            self.notifier.notify("securite", "Falsification de l'état du Guardian détectée : INCIDENT")
        try:
            if now - self.last_markets_ms > 600_000:
                await self.refresh_markets()
            await self.refresh_tickers()
        except Exception:
            self.heartbeat(data_ok=False)
            raise
        self.last_data_ms = now
        await self.process_commands()
        for ex in self.execs():
            await ex.recover()
            await ex.refresh_open()
            await ex.cancel_stale_entries(ENTRY_LIMIT_TTL_MS)
        if self.real_exec is not None and now - self.last_fee_refresh_ms > 3_600_000:
            await self.refresh_fees()
        if now - self.last_decision_ms >= 60_000:
            eligible = [m for m, st in self.states.items() if check_eligibility(st, self.limits, now).eligible]
            held = set(self.held_positions("simulation"))
            if self.real_exec is not None:
                held |= set(self.held_positions("real"))
            wanted = list(dict.fromkeys(sorted(held) + eligible))  # positions détenues TOUJOURS rafraîchies
            stale = [m for m in wanted if m not in self.candles or
                     int(self.candles[m].index[-1]) + 2 * INTERVAL_MS <= now]
            if stale:
                await self.refresh_candles(stale[:20])
            for ex in self.execs():
                await self.manage_positions(ex)
            await self.decide()
        if self.real_exec is not None and now - self.last_recon_ms > 300_000 and (
                self.guardian.state == State.REAL or self.held_positions("real")):
            await self._reconcile_real()
        if self.guardian.state == State.RECONCILIATION and self.real_exchange is not None:
            if await self._reconcile_real():
                self.guardian.resume("engine", await self.preflight(for_real=True))
        self.snapshot()


def _load_state_key(s: Settings) -> bytes | None:
    k = s.read_secret("engine_state_key")
    if not k:
        log.warning("secrets/engine_state_key absent : intégrité de l'état non signée, réel impossible")
        return None
    register_secret(k)
    return k.encode()


async def main() -> None:  # pragma: no cover - point d'entrée de production
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    s = Settings()
    lock = InstanceLock(Path(s.db_path).with_suffix(".lock"))
    lock.acquire()
    db = Database(s.db_path)
    key, secret = s.read_secret("bitvavo_api_key"), s.read_secret("bitvavo_api_secret")
    public = BitvavoREST(s.bitvavo_rest_url)
    ck = s.read_secret("owner_command_key")
    if ck:
        register_secret(ck)
    store = Path(os.environ.get("NELYQOR_ENGINE_STATE_DIR", "/state")) / "etat_moteur.json"
    eng = Engine(s, db, public, state_key=_load_state_key(s), command_key=bytes.fromhex(ck) if ck else None,
                 store_path=store)
    tg_token, tg_chat = s.read_secret("telegram_bot_token"), os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        register_secret(tg_token)
        eng.notifier = Notifier(db, eng.clock.now_ms, tg_token, tg_chat)
    if key and secret:
        real = BitvavoREST(s.bitvavo_rest_url, key, secret)
        try:
            await real.sync_time()
        except Exception:  # noqa: BLE001
            log.warning("synchronisation de l'horloge impossible")
        eng.attach_real_exchange(real)
    eng.start()
    if key and secret:
        from .market_data.bitvavo_ws import AccountStream

        await eng.refresh_markets()
        eur = [m for m, i in eng.markets.items() if i.quote == "EUR"]
        stream = AccountStream(s.bitvavo_ws_url, key, secret, eur, eng.on_ws_event, eng.on_ws_reconnect)
        asyncio.create_task(stream.run_forever())  # noqa: RUF006
    failures = 0
    while True:
        try:
            await eng.step()
            failures = 0
        except Exception as e:  # noqa: BLE001
            failures += 1
            log.exception("erreur de boucle", extra={"data": {"failures": failures}})
            if failures >= 5:
                eng.guardian.pause("engine", f"erreurs répétées : {type(e).__name__}")
                eng.notifier.notify("panne_prolongee", f"Moteur en erreur répétée : {type(e).__name__}")
            for _ in range(min(60, 2 ** failures) // 5 + 1):
                try:
                    await eng.process_commands()
                except Exception:  # noqa: BLE001
                    log.exception("commandes non traitées")
                await asyncio.sleep(5)
        await asyncio.sleep(5)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
