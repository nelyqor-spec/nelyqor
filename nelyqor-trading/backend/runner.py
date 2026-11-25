"""Service moteur (QUANT + GUARDIAN + EXECUTION) : seul processus qui peut détenir les clés Bitvavo.

Boucle : marchés (10 min) -> tickers/carnets (5 s) -> bougies (à la clôture) -> commandes de contrôle ->
décisions (60 s) -> suivi des ordres -> réconciliation (réel) -> snapshot d'equity -> heartbeat.
Démarrage : verrou exclusif, chargement des bornes (hash journalisé), réconciliation avant toute entrée.
"""
from __future__ import annotations

import asyncio
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
from .core.logging import get_logger, setup_logging
from .core.num import ZERO, D, fmt
from .execution.engine import ExecutionEngine, InstanceLock, OrderRequest
from .execution.simulator import SimulatedExchange
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


class Engine:
    def __init__(self, settings: Settings, db: Database, rest: Any, clock: Clock | None = None,
                 limits: HardLimits | None = None) -> None:
        self.s, self.db, self.rest = settings, db, rest
        self.clock = clock or Clock()
        self.limits = limits or HardLimits.load()
        self.params = load_strategy_params()
        self.guardian = Guardian(db, self.limits, settings.real_trading_server_flag, settings.role, self.clock.now_ms)
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
        self.exec: ExecutionEngine | None = None
        self.real_exchange: Any = None
        self.last_markets_ms = 0
        self.last_decision_ms = 0
        self.last_recon_ms = 0
        self.last_ticker_ms = 0
        self.last_data_ms = 0

    # ------------------------------------------------------------ démarrage
    def start(self) -> None:
        self.db.kv_set("hard_limits_sha256", self.limits.sha256, self.clock.now_ms())
        self.db.kv_set("strategy_params_version", self.params.get("version"), self.clock.now_ms())
        if self.db.kv_get("guardian_state") is None:
            self.db.kv_set("guardian_state", State.SIMULATION.value, self.clock.now_ms())
        # Redémarrage alors que le réel était actif : passer par la réconciliation avant toute entrée.
        if self.guardian.state == State.REAL:
            self.db.kv_set("resume_state", State.REAL.value, self.clock.now_ms())
            self.db.kv_set("guardian_state", State.RECONCILIATION.value, self.clock.now_ms())
            self.db.kv_set("guardian_reason", "redémarrage : réconciliation obligatoire", self.clock.now_ms())
        for s in self.strategies:
            self.registry.register(s.name, self.params["version"], self.params[s.name])

    def mode(self) -> str:
        return "real" if self.guardian.state in (State.REAL,) or (
            self.guardian.state in (State.PAUSE, State.RECONCILIATION, State.INCIDENT)
            and self.db.kv_get("resume_state") == State.REAL.value) else "simulation"

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
        self.markets = new
        for k, m in new.items():
            if k in self.states:
                self.states[k].info = m
            else:
                self.states[k] = MarketState(m)
        if self.paper is None:
            self._init_paper()
        else:
            self.paper.markets = dict(new)
        self.last_markets_ms = now

    def _init_paper(self) -> None:
        self.paper = SimulatedExchange(list(self.markets.values()), {"EUR": self.s.initial_capital_eur},
                                       self.fee_maker, self.fee_taker, self.clock.now_ms)
        self.exec = ExecutionEngine(self.db, self.paper, self.guardian, self.markets, "simulation",
                                    self.s.bitvavo_operator_id, self.clock.now_ms)
        self._replay_paper_state()

    def _replay_paper_state(self) -> None:
        """Après redémarrage : reconstitue les soldes papier depuis les fills persistés (journal = source de vérité)."""
        assert self.paper is not None
        led = self._ledger("simulation")
        self.paper.available = {"EUR": led.s.cash.get("EUR", ZERO)}
        for sym, p in led.s.positions.items():
            if p.qty > 0:
                self.paper.available[sym] = p.qty

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
            # on exclut la bougie en cours (non clôturée) : pas de fuite intrabougie
            cs = [c for c in cs if c.ts + INTERVAL_MS <= now]
            with self.db.tx() as c:
                c.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)",
                              [(m, INTERVAL, k.ts, fmt(k.open), fmt(k.high), fmt(k.low), fmt(k.close), fmt(k.volume),
                                now, "bitvavo") for k in cs])
            if cs:
                df = pd.DataFrame([{"ts": k.ts, "open": float(k.open), "high": float(k.high), "low": float(k.low),
                                    "close": float(k.close), "volume": float(k.volume)} for k in cs]).set_index("ts")
                self.candles[m] = df.sort_index()

    # ------------------------------------------------------------ portefeuille
    def _ledger(self, mode: str) -> Ledger:
        led = Ledger(self.s.initial_capital_eur if mode == "simulation" else ZERO)
        for f in self.db.q("SELECT * FROM fills WHERE mode=? ORDER BY ts", (mode,)):
            led.apply_fill(Fill(f["ts"], f["market"], f["side"], D(f["amount"]), D(f["price"]), D(f["fee"]),
                                f["fee_currency"], D(f["fee_eur"]) if f["fee_eur"] else None, f["id"]))
        for fl in self.db.q("SELECT * FROM external_flows WHERE mode=? AND identified=1 ORDER BY ts", (mode,)):
            led.apply_flow(ExternalFlow(fl["ts"], fl["kind"], fl["symbol"], D(fl["amount"]), D(fl["value_eur"])))
        return led

    def prices_eur(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for _k, st in self.states.items():
            if st.info.quote == "EUR":
                px = st.best_bid or st.last_price
                if px:
                    out[st.info.base] = px
        return out

    def held_positions(self, mode: str) -> dict[str, HeldPosition]:
        led = self._ledger(mode)
        meta = self.db.kv_get(f"position_meta_{mode}", {})
        out = {}
        for sym, p in led.s.positions.items():
            if p.qty <= 0:
                continue
            mk = f"{sym}-EUR"
            md = meta.get(mk, {})
            out[mk] = HeldPosition(mk, p.qty, p.avg_cost, int(md.get("entry_ts", 0)), md.get("strategy", "?"),
                                   D(md.get("stop", "0")), int((self.clock.now_ms() - int(md.get("entry_ts", 0))) // INTERVAL_MS))
        return out

    # ------------------------------------------------------------ commandes de contrôle (depuis l'API)
    async def process_commands(self) -> None:
        for cmd in self.db.q("SELECT * FROM control_commands WHERE status='pending' ORDER BY ts"):
            payload = json.loads(cmd["payload"] or "{}")
            actor = payload.get("actor", "owner")
            res: dict[str, Any]
            try:
                c = cmd["command"]
                if c == "pause":
                    self.guardian.pause(actor, payload.get("reason", "pause manuelle"))
                    res = {"ok": True}
                elif c == "emergency_stop":
                    self.guardian.emergency_stop(actor)
                    res = {"ok": True, "note": "nouvelles entrées bloquées ; positions et protections conservées"}
                elif c == "resume":
                    v = self.guardian.resume(actor, await self.preflight(for_real=False))
                    res = {"ok": v.allowed, "reasons": v.reasons}
                elif c == "activate_real":
                    pf = await self.preflight(for_real=True)
                    pf.update({k: bool(payload.get(k)) for k in ("declare_retrait_desactive", "declare_ip_fixe",
                                                                 "declare_2fa", "accepte_risque_perte_totale")})
                    v = self.guardian.activate_real(actor, payload.get("confirmation", ""), bool(payload.get("password_ok")), pf)
                    res = {"ok": v.allowed, "reasons": v.reasons}
                    if v.allowed:
                        await self._switch_to_real()
                elif c == "deactivate_real":
                    self.guardian.transition(State.SIMULATION, actor, "retour manuel en simulation")
                    res = {"ok": True}
                elif c == "liquidation_estimate":
                    res = {"ok": True, "estimate": self.liquidation_estimate()}
                elif c == "liquidation_confirm":
                    res = await self.liquidate(payload)
                elif c == "ack_flow":
                    self.db.exec("UPDATE external_flows SET identified=1, note=? WHERE id=?",
                                 (payload.get("note", "rapproché manuellement"), payload["flow_id"]))
                    res = {"ok": True}
                else:
                    res = {"ok": False, "reasons": ["commande inconnue"]}
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "reasons": [f"{type(e).__name__}: {e}"]}
            self.db.exec("UPDATE control_commands SET status='done', processed_ms=?, result=? WHERE id=?",
                         (self.clock.now_ms(), json.dumps(res, default=str, ensure_ascii=False), cmd["id"]))

    async def preflight(self, for_real: bool) -> dict[str, bool]:
        now = self.clock.now_ms()
        fresh = [st for st in self.states.values() if now - st.ts_received_ms <= self.limits.max_stale_ms]
        pf = {
            "donnees_marche_fraiches": bool(fresh),
            "aucun_ordre_inconnu": self.db.one("SELECT 1 FROM intents WHERE status IN ('UNKNOWN','SUBMITTING')") is None,
            "aucun_flux_non_identifie": self.db.one("SELECT 1 FROM external_flows WHERE identified=0") is None,
        }
        if for_real:
            pf["cles_api_presentes"] = self.real_exchange is not None and getattr(self.real_exchange, "has_credentials", False)
            pf["tests_acceptation_ok"] = bool(self.db.kv_get("acceptance_tests_ok", False))
            pf["reconciliation_ok"] = await self._reconcile_real() if self.real_exchange else False
        return pf

    async def _switch_to_real(self) -> None:
        assert self.real_exchange is not None
        self.exec = ExecutionEngine(self.db, self.real_exchange, self.guardian, self.markets, "real",
                                    self.s.bitvavo_operator_id, self.clock.now_ms)
        self.notifier.notify("incident", "MODE RÉEL ACTIVÉ par le propriétaire", dedup_key=new_id())

    async def _reconcile_real(self) -> bool:
        """Compare le journal réel aux soldes Bitvavo ; enregistre les flux identifiés, signale les autres."""
        ex = self.real_exchange
        if ex is None:
            return False
        try:
            bal = await ex.balance()
            since = int(self.db.kv_get("recon_since_ms", 0))
            deps = await ex.deposit_history(since or None)
            wds = await ex.withdrawal_history(since or None)
        except (BitvavoError, Exception) as e:  # noqa: BLE001
            log.error("réconciliation impossible", extra={"data": {"err": repr(e)}})
            return False
        led = self._ledger("real")
        expected = dict(led.s.cash)
        expected.update({s: p.qty for s, p in led.s.positions.items()})
        known = {r["id"] for r in self.db.q("SELECT id FROM external_flows WHERE mode='real'")}
        new_deps = [d for d in deps if f"dep-{d.get('txId', d.get('timestamp'))}" not in known]
        new_wds = [w for w in wds if f"wd-{w.get('txId', w.get('timestamp'))}" not in known]
        res = reconcile(expected, bal, new_deps, new_wds)
        px = self.prices_eur()
        with self.db.tx() as c:
            for fl in res.identified_flows:
                key = ("dep-" if fl["kind"] == "deposit" else "wd-") + str(fl.get("txId", fl.get("timestamp")))
                amt = D(fl["amount"])
                val = amt if fl["symbol"] == "EUR" else amt * px.get(fl["symbol"], ZERO)
                c.execute("INSERT OR IGNORE INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,identified)"
                          " VALUES(?,?,?,?,?,?,?,?,1)", (key, int(fl.get("timestamp", self.clock.now_ms())), "real",
                                                         fl["kind"], fl["symbol"], fmt(amt), fmt(val), "bitvavo_history"))
            for sym, amt in res.unidentified.items():
                c.execute("INSERT OR IGNORE INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,identified,note)"
                          " VALUES(?,?,?,?,?,?,?,?,0,?)", (f"unk-{sym}-{self.clock.now_ms()}", self.clock.now_ms(), "real",
                                                           "deposit" if amt > 0 else "withdrawal", sym, fmt(abs(amt)), "0",
                                                           "ecart_solde", "mouvement non identifié"))
        self.db.kv_set("recon_since_ms", self.clock.now_ms(), self.clock.now_ms())
        self.last_recon_ms = self.clock.now_ms()
        if not res.ok:
            self.guardian.pause("reconciliation", "mouvement manuel non identifié : rapprochement requis")
            self.notifier.notify("flux_non_identifie", f"Écart de solde non identifié : {res.unidentified}")
        return res.ok

    # ------------------------------------------------------------ décisions
    def _record_decision(self, mode: str, p: Any, accepted: bool, reasons: list[str], hypothetical: bool) -> None:
        self.db.exec("INSERT INTO decisions(id,ts,mode,market,strategy,action,accepted,reasons,details,hypothetical)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (new_id(), self.clock.now_ms(), mode, p.market, p.strategy, p.action, int(accepted),
                      json.dumps(reasons, ensure_ascii=False), json.dumps({"risk": p.risk, "confidence": p.confidence,
                                                                           "edge": p.edge, **p.details}, default=str),
                      int(hypothetical)))

    async def decide(self) -> None:
        assert self.exec is not None
        mode = self.exec.mode
        now = self.clock.now_ms()
        led = self._ledger(mode)
        px = self.prices_eur()
        equity = led.value_eur(px)
        cash = led.s.cash.get("EUR", ZERO)
        held = self.held_positions(mode)
        exposure = sum((h.qty * px.get(h.market.split("-")[0], ZERO) for h in held.values()), ZERO)
        meta = self.db.kv_get(f"position_meta_{mode}", {})
        scanner = []
        # 1) sorties d'abord (permises même en pause)
        for mk, h in held.items():
            st, df = self.states.get(mk), self.candles.get(mk)
            if st is None or df is None:
                continue
            p = self.decider.evaluate_exit(st.info, st, df, h)
            if p.action == "vendre" and p.amount_base > 0:
                r = await self.exec.submit(OrderRequest(mk, "sell", "market", amount=p.amount_base, strategy=h.strategy,
                                                        is_entry=False, is_protective=True,
                                                        check=self._check(st, "sell", p.amount_base * (st.best_bid or ZERO),
                                                                          False, equity, cash, exposure, mode)), cash)
                self._record_decision(mode, p, r.accepted, p.reasons + r.reasons, False)
                if r.accepted:
                    meta.pop(mk, None)
        # 2) entrées
        for mk, st in self.states.items():
            el = check_eligibility(st, self.limits, now, self.s.quote_currency)
            row: dict[str, Any] = {"market": mk, "eligible": el.eligible, "reasons": el.reasons,
                                   "spread_bps": float(spread_bps(st.best_bid, st.best_ask) or 0),
                                   "volume_24h_eur": float(st.quote_volume_24h)}
            df = self.candles.get(mk)
            if el.eligible and df is not None:
                ob = self.paper.books.get(mk) if self.paper else None
                depth = ob.depth_quote("asks", D(50)) if ob else ZERO
                props = self.decider.evaluate_entry(st.info, st, df, equity, cash, exposure, held.get(mk), depth)
                for p in props:
                    row.setdefault("propositions", []).append({"strategy": p.strategy, "action": p.action,
                                                               "risk": p.risk, "confidence": p.confidence,
                                                               "edge": p.edge, "reasons": p.reasons,
                                                               "label": "estimation non calibrée"})
                    if p.action != "acheter":
                        if p.strategy != "-":
                            self._record_decision(mode, p, False, p.reasons, True)
                        continue
                    chk = self._check(st, "buy", p.amount_quote, True, equity, cash, exposure + p.amount_quote, mode)
                    chk.reinforcement_of_loser = mk in held and (st.last_price or ZERO) < held[mk].avg_cost
                    req = OrderRequest(mk, "buy", p.order_type,
                                       amount_quote=p.amount_quote if p.order_type == "market" else None,
                                       amount=(p.amount_quote / p.limit_price) if p.order_type == "limit" and p.limit_price else None,
                                       price=p.limit_price, post_only=p.post_only, strategy=p.strategy, check=chk)
                    r = await self.exec.submit(req, cash)
                    self._record_decision(mode, p, r.accepted, p.reasons + r.reasons, False)
                    if r.accepted:
                        meta[mk] = {"entry_ts": now, "strategy": p.strategy, "stop": str(p.details.get("stop", 0))}
                        cash -= p.amount_quote
                        exposure += p.amount_quote
                    break  # une seule entrée par marché et par cycle
            scanner.append(row)
        self.db.kv_set(f"position_meta_{mode}", meta, now)
        self.db.kv_set("scanner", {"ts": now, "rows": scanner}, now)
        self.last_decision_ms = now

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
    def liquidation_estimate(self) -> dict[str, Any]:
        assert self.exec is not None
        mode = self.exec.mode
        out: list[dict[str, Any]] = []
        total = ZERO
        for mk, h in self.held_positions(mode).items():
            st = self.states.get(mk)
            bid = st.best_bid if st else None
            if bid is None:
                out.append({"market": mk, "qty": fmt(h.qty), "estimation": None, "note": "prix indisponible"})
                continue
            gross = h.qty * bid
            fee = gross * self.fee_taker
            dust = gross < st.info.min_order_quote if st else True
            out.append({"market": mk, "qty": fmt(h.qty), "brut_eur": fmt(gross), "frais_eur": fmt(fee),
                        "net_eur": fmt(gross - fee), "poussiere_invendable": dust})
            if not dust:
                total += gross - fee
        est = {"id": new_id(), "ts": self.clock.now_ms(), "mode": mode, "lignes": out, "net_total_eur": fmt(total),
               "avertissement": "Estimation au meilleur bid ; le glissement réel peut être supérieur."}
        self.db.kv_set("liquidation_estimate", est, self.clock.now_ms())
        return est

    async def liquidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self.exec is not None
        est = self.db.kv_get("liquidation_estimate")
        if not est or est["id"] != payload.get("estimate_id") or self.clock.now_ms() - est["ts"] > 120_000:
            return {"ok": False, "reasons": ["estimation absente ou expirée (2 min) : recommencer"]}
        if not payload.get("password_ok") or payload.get("confirmation") != "LIQUIDER":
            return {"ok": False, "reasons": ["confirmation invalide"]}
        await self.refresh_tickers()  # jamais de vente sur des prix périmés
        results = []
        for mk, h in self.held_positions(self.exec.mode).items():
            st = self.states.get(mk)
            if st is None:
                continue
            r = await self.exec.submit(OrderRequest(mk, "sell", "market", amount=st.info.round_amount(h.qty),
                                                    strategy="liquidation_manuelle", is_entry=False, is_protective=True,
                                                    check=self._check(st, "sell", h.qty * (st.best_bid or ZERO), False,
                                                                      ZERO, ZERO, ZERO, self.exec.mode)), ZERO)
            results.append({"market": mk, "status": r.status, "reasons": r.reasons})
        return {"ok": True, "resultats": results}

    # ------------------------------------------------------------ snapshot / santé
    def snapshot(self) -> None:
        assert self.exec is not None
        mode = self.exec.mode
        led = self._ledger(mode)
        px = self.prices_eur()
        v = led.value_eur(px)
        cash = led.s.cash.get("EUR", ZERO)
        self.db.exec("INSERT OR REPLACE INTO equity_snapshots VALUES(?,?,?,?,?)",
                     (self.clock.now_ms(), mode, fmt(v), fmt(cash), fmt(v - cash)))
        self.heartbeat(data_ok=True)

    def heartbeat(self, data_ok: bool) -> None:
        """Signe de vie du moteur, distinct de la fraîcheur des données de marché."""
        mode = self.exec.mode if self.exec else "simulation"
        self.db.kv_set("engine_heartbeat", {"ts": self.clock.now_ms(), "mode": mode, "pid": os.getpid(),
                                            "n_markets": len(self.markets), "data_ok": data_ok,
                                            "last_data_ms": self.last_data_ms}, self.clock.now_ms())

    async def step(self) -> None:
        now = self.clock.now_ms()
        # Les commandes de contrôle (arrêt d'urgence, pause) passent AVANT tout accès réseau :
        # elles doivent être prises en compte même si les données de marché sont indisponibles.
        await self.process_commands()
        try:
            if now - self.last_markets_ms > 600_000:
                await self.refresh_markets()
            await self.refresh_tickers()
        except Exception:
            self.heartbeat(data_ok=False)
            raise
        self.last_data_ms = now
        await self.process_commands()
        assert self.exec is not None
        await self.exec.refresh_open()
        if now - self.last_decision_ms >= 60_000:
            eligible = [m for m, st in self.states.items() if check_eligibility(st, self.limits, now).eligible]
            stale = [m for m in eligible if m not in self.candles or
                     int(self.candles[m].index[-1]) + 2 * INTERVAL_MS <= now]
            if stale:
                await self.refresh_candles(stale[:20])
            await self.decide()
        if self.exec.mode == "real" and now - self.last_recon_ms > 300_000:
            await self._reconcile_real()
        if self.guardian.state == State.RECONCILIATION and self.real_exchange is not None:
            if await self._reconcile_real():
                self.guardian.resume("engine", await self.preflight(for_real=False))
        self.snapshot()


async def main() -> None:  # pragma: no cover - point d'entrée de production
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    s = Settings()
    lock = InstanceLock(Path(s.db_path).with_suffix(".lock"))
    lock.acquire()
    db = Database(s.db_path)
    key, secret = s.read_secret("bitvavo_api_key"), s.read_secret("bitvavo_api_secret")
    public = BitvavoREST(s.bitvavo_rest_url)
    eng = Engine(s, db, public)
    tg_token, tg_chat = s.read_secret("telegram_bot_token"), os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        from .core.logging import register_secret

        register_secret(tg_token)
        eng.notifier = Notifier(db, eng.clock.now_ms, tg_token, tg_chat)
    if key and secret:
        eng.real_exchange = BitvavoREST(s.bitvavo_rest_url, key, secret)
    eng.start()
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
            # attente fractionnée : les commandes de contrôle restent traitées pendant le backoff
            for _ in range(min(60, 2 ** failures) // 5 + 1):
                try:
                    await eng.process_commands()
                except Exception:  # noqa: BLE001
                    log.exception("commandes non traitées")
                await asyncio.sleep(5)
        await asyncio.sleep(5)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
