"""Service moteur (QUANT + GUARDIAN + EXECUTION) : seul processus qui peut détenir les clés Bitvavo.

Boucle : commandes de contrôle (toujours en premier) -> marchés (10 min) -> tickers (5 s) -> reprise des
intentions ambiguës -> suivi des ordres -> protections natives -> bougies -> décisions (60 s) ->
réconciliation (réel, 5 min) -> instantané d'equity (1/min) -> heartbeat.

Deux exécuteurs coexistent : SIMULATION (papier, toujours actif, comparaison permanente) et RÉEL (créé si
des clés existent). Les entrées réelles exigent l'état REEL_AUTORISE ; la gestion des positions réelles
existantes (stops natifs, sorties) continue même si le réel est désactivé ou en pause.

Phase 5 — bases séparées (backend/storage/channels.py) : le moteur possède `engine.db` (volume réservé), LIT les
commandes de l'API dans `commandes.db` (lecture seule, contenu non fiable, prise dans son propre journal) et
PUBLIE une copie d'affichage dans `affichage.db`, qu'il ne relit jamais. Un achat RÉEL est dimensionné sur le
capital AUTHENTIFIÉ (soldes Bitvavo recoupés avec le journal : backend/accounting/authenticated.py).
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from .accounting.authenticated import AuthCapital, authenticate_capital, unavailable
from .accounting.ledger import ExternalFlow, Fill, Ledger
from .accounting.reconcile import reconcile
from .core.clock import Clock, new_id
from .core.config import HardLimits, Settings, load_strategy_params
from .core.disk import CRITIQUE, OK, DiskThresholds, free_mb, inodes_exhausted, is_disk_full_error, next_level
from .core.logging import get_logger, register_secret, setup_logging
from .core.num import ZERO, D, fmt
from .execution.engine import ExecutionEngine, InstanceLock, OrderRequest, SubmitResult
from .execution.protection import ProtectionManager
from .execution.simulator import SimulatedExchange
from .features import indicators as ind
from .guardian.guardian import Guardian, IntentCheck, State
from .market_data.bitvavo_rest import BitvavoError, BitvavoREST
from .market_data.models import Candle, MarketInfo
from .market_data.universe import (
    MarketState,
    check_eligibility,
    data_age_ms,
    diff_markets,
    parse_exchange_ts,
    spread_bps,
)
from .notify.notifier import Notifier
from .portfolio.decision import DecisionEngine, HeldPosition
from .research.registry import Registry
from .storage.channels import MAX_NEW_PER_CYCLE, CommandInbox, DisplayPublisher, channel_writable
from .storage.db import Database
from .strategies.base import build_strategies

log = get_logger("engine")
INTERVAL = "1h"
INTERVAL_MS = 3_600_000
COMMAND_TTL_MS = 10 * 60_000  # une commande non traitée en 10 min expire (sauf pause / arrêt d'urgence)
ALWAYS_VALID_COMMANDS = {"pause", "emergency_stop"}
ENTRY_LIMIT_TTL_MS = 15 * 60_000
RECON_OVERLAP_MS = 7 * 86_400_000
COMMAND_FUTURE_TOLERANCE_MS = 60_000  # commande datée du futur (horloge de l'API) : refusée au-delà
ALWAYS_VALID_MAX_AGE_MS = 7 * 86_400_000  # pause / arrêt d'urgence : ignorés au-delà de 7 jours (anti-remplissage)
COMMAND_JOURNAL_RETENTION_MS = 8 * 86_400_000  # journal des commandes prises (moteur) : purge au-delà
COMMAND_SCAN_MS = 86_400_000  # fenêtre de lecture du canal : au-delà du TTL, la commande est journalisée « expirée »
CLOCK_SKEW_ALERT_MS = 5_000  # dérive d'horloge hôte/échange au-delà : alerte et réel refusé (D2)
CLOCK_SYNC_EVERY_MS = 600_000
PURGE_EVERY_MS = 86_400_000
PURGE_BATCH = 50_000  # lignes supprimées au plus par table et par purge (borne la durée du verrou)
OFFICIAL_REST_URL = "https://api.bitvavo.com/v2"
OFFICIAL_WS_PREFIX = "wss://ws.bitvavo.com/"
AUTH_REFRESH_MS = 5_000  # soldes relus chez Bitvavo à chaque cycle de décision (au plus une fois par 5 s)
KNOWN_COMMANDS = {"pause", "emergency_stop", "resume", "activate_real", "deactivate_real", "liquidation_estimate",
                  "liquidation_confirm", "ack_flow"}


class Engine:
    def __init__(self, settings: Settings, db: Database, rest: Any, clock: Clock | None = None,
                 limits: HardLimits | None = None, state_key: bytes | None = None,
                 command_key: bytes | None = None, store_path: Path | None = None,
                 commands_path: Path | None = None, display_path: Path | None = None) -> None:
        self.s, self.db, self.rest = settings, db, rest
        self.clock = clock or Clock()
        # canal d'ENTRÉE (lecture seule, contenu non fiable) et canal de SORTIE (écriture seule, jamais relu) :
        # aucun des deux n'est la base du moteur
        self.inbox = CommandInbox(commands_path or settings.commands_file)
        self.display = DisplayPublisher(db, display_path or settings.display_file, self.clock.now_ms)
        self._auth: AuthCapital | None = None
        self._auth_cache: tuple[int, list[dict[str, Any]]] | None = None
        self.limits = limits or HardLimits.load()
        self.params = load_strategy_params()
        from .core.engine_store import EngineStore

        self.store = EngineStore(store_path, state_key) if (store_path is not None and state_key) else None
        self.guardian = Guardian(db, self.limits, settings.real_trading_server_flag, settings.role, self.clock.now_ms,
                                 state_key=state_key, store=self.store)
        self._realloc: dict[str, dict[str, Any]] = {"simulation": {}, "real": {}}  # en mémoire du moteur seulement
        self._liq_estimate: dict[str, Any] | None = None
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
        self.clock_skew_ms = 0  # heure de l'échange − heure locale (D2) ; 0 tant que non mesuré
        self.clock_skew_known = False
        self.last_clock_sync_ms = -CLOCK_SYNC_EVERY_MS
        self.last_purge_ms = 0
        self._last_px: dict[str, Decimal] = {}  # dernier prix connu par actif (D5)
        self._open_orders_checked = False
        self.command_key = command_key  # vérifie les commandes sensibles signées par le propriétaire
        # phase 7 : surveillance du disque (backend/core/disk.py) ; en « critique », écritures non critiques suspendues
        self.disk_th = DiskThresholds.from_env()
        self.disk_level = OK
        self.disk_free_mb: float | None = None
        self.disk_full_errors = 0
        self._last_hb_ms = 0
        # contre-expertise phase 7 (D2) : après un refus d'écriture (ENOSPC / SQLITE_FULL) le niveau reste
        # « critique » au moins ce délai, même si statvfs voit de la place (inodes, quota, tmpfs) : pas d'oscillation
        self._enospc_until_ms = 0
        self.enospc_hold_ms = 10 * 60_000
        # (D3) fin du dernier cycle de boucle : la vigie ne doit pas masquer une boucle figée
        self.last_cycle_ms = 0
        self.frozen_after_ms = 10 * 60_000
        self._display_crit_ms = 0

    @property
    def writes_suspended(self) -> bool:
        """Disque presque plein : décisions, nouvelles entrées, instantanés, bougies hors positions et purge
        suspendus ; suivi des ordres, ventes, protections et battement de cœur continuent."""
        return self.disk_level == CRITIQUE

    def _disk_paths(self) -> list[Path]:
        out = [] if self.db.path == ":memory:" else [Path(self.db.path).parent]
        if self.store is not None:
            out.append(self.store.path.parent)
        return [p for p in out if p.exists()]

    def check_disk(self) -> str:
        """Mesure l'espace libre (base du moteur, magasin d'état) et applique les seuils ; notifie chaque passage
        en alerte / critique et la reprise. Ne lève jamais : une mesure impossible laisse le niveau inchangé."""
        paths = self._disk_paths()
        if not paths:
            return self.disk_level
        try:
            free = min(free_mb(p)[0] for p in paths)
            inodes_low = any(inodes_exhausted(p) for p in paths)
        except OSError as e:
            log.warning("mesure de l'espace disque impossible", extra={"data": {"err": repr(e)}})
            return self.disk_level
        self.disk_free_mb = free
        new = CRITIQUE if inodes_low else next_level(self.disk_level, free, self.disk_th)
        if new != CRITIQUE and self.clock.now_ms() < self._enospc_until_ms:
            new = CRITIQUE  # refus d'écriture récent : statvfs ne dit pas tout (inodes, quota) — D2
        self._set_disk_level(new)
        return self.disk_level

    def _set_disk_level(self, new: str) -> None:
        old, self.disk_level = self.disk_level, new
        if new == old:
            return
        free = f"{self.disk_free_mb:.0f} Mo libres" if self.disk_free_mb is not None else "espace inconnu"
        if new == CRITIQUE:
            log.error("DISQUE PRESQUE PLEIN : écritures non critiques suspendues", extra={"data": {"libre_mo": self.disk_free_mb}})
            self.notifier.notify("disque", f"Disque presque plein ({free}) : décisions et nouvelles entrées SUSPENDUES, "
                                 "protections et ventes maintenues. Libérer de l'espace (docs/RUNBOOKS.md).",
                                 dedup_key="disque-critique")  # clé STABLE : jamais de rafale (D2)
        elif new == OK and old == CRITIQUE or new != OK and old == OK:
            msg = (f"Espace disque revenu ({free}) : écritures reprises." if new == OK
                   else f"Espace disque faible ({free}) : libérer de l'espace avant saturation.")
            log.warning(msg)
            self.notifier.notify("disque", msg, dedup_key=f"disque-{new}")
        else:
            log.warning("niveau disque", extra={"data": {"de": old, "vers": new, "libre_mo": self.disk_free_mb}})

    def on_disk_full(self, e: BaseException) -> None:
        """Écriture refusée faute de place (SQLITE_FULL / ENOSPC) : la transaction est annulée par SQLite (base
        intacte) ; passage immédiat en « critique »."""
        self.disk_full_errors += 1
        self._enospc_until_ms = self.clock.now_ms() + self.enospc_hold_ms
        if self.disk_level != CRITIQUE:
            self._set_disk_level(CRITIQUE)
        log.error("écriture refusée : disque plein", extra={"data": {"err": repr(e)[:200], "n": self.disk_full_errors}})

    def _check_emergency_file(self) -> None:
        if self.store is None:
            return
        flag = self.store.path.parent / "ARRET_URGENCE"
        if flag.exists():
            try:
                stamp = flag.stat().st_mtime_ns
            except OSError:
                stamp = 0
            self.guardian.emergency_stop("ssh")
            try:
                flag.rename(flag.with_name(f"ARRET_URGENCE.applique-{self.clock.now_ms()}"))
            except OSError:
                pass  # le blocage en mémoire reste actif
            # une clé par DÉPÔT du drapeau (date du fichier) : si le renommage échoue, pas d'alerte toutes les 5 s
            self.notifier.notify("incident", "Arrêt d'urgence appliqué (canal SSH du moteur)",
                                 dedup_key=f"arret-urgence-ssh-{stamp}")

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
        self.last_cycle_ms = now  # D3 : une boucle qui ne termine jamais son PREMIER cycle est aussi détectée
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
            st.ts_market_ms = parse_exchange_ts(b.get("timestamp"))  # nul/non entier : inconnu (D9)
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
                    self._last_px[st.info.base] = px
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
    async def _safe_commands(self) -> None:
        try:
            await self.process_commands()
        except Exception as e:  # noqa: BLE001
            log.error("canal de commandes : échec isolé", extra={"data": {"err": repr(e)[:200]}})
            self.notifier.notify("securite", f"Canal de commandes en échec ({type(e).__name__}) : positions gérées, "
                                 "canal SSH disponible", dedup_key=f"canal-err-{self.clock.now_ms() // 3_600_000}")

    async def process_commands(self) -> None:
        """Commandes déposées par l'API dans `commandes.db` (lecture SEULE, contenu non fiable). La prise se fait
        dans le journal de la base du MOTEUR (INSERT OR IGNORE sur l'identifiant) : une commande n'est exécutée
        qu'une fois, même si l'API la remet « pending », la ré-insère ou si deux boucles la lisent."""
        now = self.clock.now_ms()
        processed = 0
        av_since = now - ALWAYS_VALID_MAX_AGE_MS
        taken = {r["id"] for r in self.db.q("SELECT id FROM control_commands WHERE ts >= ? OR command='emergency_stop'",
                                            (min(av_since, now - COMMAND_SCAN_MS),))}
        cands = self.inbox.candidates(now - COMMAND_SCAN_MS, ALWAYS_VALID_COMMANDS, taken=taken, always_valid_since=av_since)
        if len(cands) >= MAX_NEW_PER_CYCLE:
            self.notifier.notify("securite", "Canal de commandes saturé : au plus quelques commandes prises par cycle "
                                 "(arrêt d'urgence prioritaire ; canal SSH disponible)", dedup_key=f"canal-{now // 3_600_000}")
        if now - getattr(self, "_cmd_purge_ms", 0) > 3_600_000:
            self._cmd_purge_ms = now
            self.db.exec("DELETE FROM control_commands WHERE ts < ? AND status='done' AND command != 'emergency_stop'", (now - COMMAND_JOURNAL_RETENTION_MS,))
        for cmd in cands:
            # marquage « processing » AVANT exécution : en cas de crash, la commande n'est pas rejouée ;
            # prise atomique : si une autre boucle/instance l'a déjà prise, elle n'est jamais exécutée deux fois
            with self.db.tx() as cx:
                # journal du moteur : empreinte de la charge utile seulement (borne la croissance du disque)
                digest = "sha256:" + hashlib.sha256((cmd["payload"] or "").encode()).hexdigest()
                got = cx.execute("INSERT OR IGNORE INTO control_commands(id,ts,command,payload,status) "
                                 "VALUES(?,?,?,?,'processing')",
                                 (cmd["id"], cmd["ts"], cmd["command"], digest)).rowcount
            if got != 1:
                continue
            processed += 1
            c = cmd["command"]
            res: dict[str, Any]
            try:
                payload = json.loads(cmd["payload"] or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("charge utile invalide")
            except ValueError:
                payload = None
            actor = str(payload.get("actor", "owner"))[:32] if payload else "owner"
            try:
                if payload is None:
                    res = {"ok": False, "reasons": ["charge utile invalide ou trop volumineuse"]}
                elif c not in KNOWN_COMMANDS:
                    res = {"ok": False, "reasons": ["commande inconnue"]}
                elif c not in ALWAYS_VALID_COMMANDS and int(cmd["ts"]) > now + COMMAND_FUTURE_TOLERANCE_MS:
                    res = {"ok": False, "reasons": ["commande datée du futur : refusée"]}
                elif c not in ALWAYS_VALID_COMMANDS and now - int(cmd["ts"]) > COMMAND_TTL_MS:
                    res = {"ok": False, "reasons": ["commande expirée (non traitée dans les 10 minutes)"]}
                elif c == "pause":
                    self.guardian.pause(actor, payload.get("reason", "pause manuelle"))
                    res = {"ok": True}
                elif c == "emergency_stop":
                    self.guardian.emergency_stop(actor)
                    res = {"ok": True, "note": "nouvelles entrées bloquées ; positions et protections conservées"}
                elif c == "resume":
                    to_real = self.guardian.resume_state == State.REAL
                    sticky = bool(self.store is not None and self.store.get("falsification"))
                    why = self._verify_signed("resume_real", payload) if (to_real or sticky) else None
                    if why is not None:
                        # reprise vers le RÉEL = réactivation : signature du propriétaire exigée (une API
                        # compromise ne peut pas réarmer le réel après un arrêt d'urgence)
                        res = {"ok": False, "needs_signature": "resume_real",
                               "reasons": [f"reprise vers le mode réel : {why}"]}
                    else:
                        v = self.guardian.resume(actor, await self.preflight(for_real=to_real),
                                                 expected_target=State.REAL if to_real else self.guardian.resume_state)
                        res = {"ok": v.allowed, "reasons": v.reasons}
                        if v.allowed and sticky and self.store is not None:
                            self.store.set("falsification", None)  # levée par une reprise SIGNÉE seulement
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
                    res = self.ack_flow(str(payload["flow_id"]), str(payload.get("note", "rapproché manuellement"))[:500],
                                        payload)
                else:
                    res = {"ok": False, "reasons": ["commande inconnue"]}
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "reasons": [f"{type(e).__name__}: {e}"]}
            self.db.exec("UPDATE control_commands SET status='done', processed_ms=?, result=? WHERE id=?",
                         (self.clock.now_ms(), json.dumps(res, default=str, ensure_ascii=False), cmd["id"]))
        if processed:
            self.publish_display(force=True)  # résultat visible sans attendre le prochain instantané

    def publish_display(self, force: bool = False) -> None:
        """Copie d'affichage pour l'API. Un échec (disque plein…) ne doit jamais bloquer le moteur.
        Disque critique (D10) : la copie complète n'est republiée qu'une fois toutes les 5 min (ou sur commande),
        pour préserver la réserve des écritures critiques."""
        if self.writes_suspended and not force:
            now = self.clock.now_ms()
            if now - self._display_crit_ms < 300_000:
                return
            self._display_crit_ms = now
        try:
            self.display.publish(force=force)
        except Exception as e:  # noqa: BLE001
            log.warning("publication de l'affichage impossible", extra={"data": {"err": repr(e)}})

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

    def ack_flow(self, flow_id: str, note: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.db.one("SELECT * FROM external_flows WHERE id=?", (flow_id,))
        if r is None:
            return {"ok": False, "reasons": ["flux inconnu"]}
        if r["mode"] == "real" and self.command_key is None:
            return {"ok": False, "reasons": ["rapprochement d'un flux réel : clé de vérification des commandes absente"]}
        if r["mode"] == "real":
            # rapprocher un flux RÉEL modifie la comptabilité du compte : signature du propriétaire exigée
            why = self._verify_signed("ack_flow", payload or {})
            signed = (payload or {}).get("sig_fields") or {}
            if why is None and signed.get("flow_id") != flow_id:
                why = "champs signés incohérents (flow_id)"
            if why is not None:
                return {"ok": False, "needs_signature": "ack_flow", "reasons": [f"rapprochement d'un flux réel : {why}"]}
        px = self.prices_eur()
        amt = D(r["amount"])
        val = amt if r["symbol"] == "EUR" else amt * px.get(r["symbol"], ZERO)
        if r["symbol"] != "EUR" and val <= 0:
            return {"ok": False, "reasons": ["valorisation EUR indisponible : réessayer quand le prix est connu"]}
        self.db.exec("UPDATE external_flows SET identified=1, value_eur=?, note=? WHERE id=?", (fmt(val), note, flow_id))
        return {"ok": True}

    async def preflight(self, for_real: bool) -> dict[str, bool]:
        now = self.clock.now_ms()
        fresh = [st for st in self.states.values() if data_age_ms(st, now, self.clock_skew_ms) <= self.limits.max_stale_ms]
        pf = {
            "donnees_marche_fraiches": bool(fresh),
            "aucun_ordre_inconnu": self.db.one("SELECT 1 FROM intents WHERE status IN ('UNKNOWN','SUBMITTING')") is None,
            "aucun_flux_non_identifie": self.db.one("SELECT 1 FROM external_flows WHERE identified=0") is None,
            "integrite_etat": self.guardian.verify_integrity() if self.guardian.integrity_enabled else not for_real,
        }
        if for_real:
            # phase 5 : le réel exige que la base du moteur, le canal de commandes et l'affichage soient distincts
            pf["bases_separees"] = self.bases_separated()
            pf["horloge_synchronisee"] = self.clock_skew_known and abs(self.clock_skew_ms) <= CLOCK_SKEW_ALERT_MS
            pf["point_acces_bitvavo_officiel"] = self.official_endpoints()
            pf["aucun_ordre_ouvert_inconnu"] = not (self.db.kv_get("ordres_ouverts_rapprochement") or {}).get("inconnus")
            pf["cles_api_presentes"] = self.real_exchange is not None and getattr(self.real_exchange, "has_credentials", False)
            from .core.attest import verify_proof
            from .core.config import ROOT

            why_t = verify_proof(self.db.kv_get("acceptance_proof"), self.guardian._key, ROOT, now)
            self.db.kv_set("acceptance_proof_check", {"ts": now, "raisons": why_t}, now)
            pf["tests_acceptation_ok"] = not why_t
            pf["reconciliation_ok"] = await self._reconcile_real() if self.real_exchange else False
        return pf

    def official_endpoints(self) -> bool:
        """Réel : uniquement contre l'API OFFICIELLE de Bitvavo (D9) — une URL détournée par l'environnement
        enverrait les requêtes signées ailleurs."""
        return (self.s.bitvavo_rest_url.rstrip("/") == OFFICIAL_REST_URL
                and self.s.bitvavo_ws_url.startswith(OFFICIAL_WS_PREFIX))

    async def measure_clock_skew(self) -> None:
        """Décalage heure de l'échange − heure locale, mesuré sur /v2/time PUBLIC (D2). Au-delà de 5 s : alerte ;
        le réel est refusé (pré-requis `horloge_synchronisee`). Utilisé dans le calcul de l'âge des données."""
        timer = getattr(self.rest, "time", None)
        if timer is None:
            return
        t0 = self.clock.now_ms()
        try:
            server = int(await timer())
        except Exception as e:  # noqa: BLE001 — mesure impossible : jamais bloquant pour la boucle
            log.warning("mesure du décalage d'horloge impossible", extra={"data": {"err": repr(e)[:200]}})
            return
        t1 = self.clock.now_ms()
        self.clock_skew_ms = server - (t0 + t1) // 2
        self.clock_skew_known = True
        self.last_clock_sync_ms = t1
        self.db.kv_set("decalage_horloge", {"ms": self.clock_skew_ms, "ts": t1,
                                            "alerte": abs(self.clock_skew_ms) > CLOCK_SKEW_ALERT_MS}, t1)
        if abs(self.clock_skew_ms) > CLOCK_SKEW_ALERT_MS:
            self.notifier.notify("incident", f"Horloge du serveur décalée de {self.clock_skew_ms / 1000:.1f} s par "
                                 "rapport à Bitvavo : vérifier NTP (réel refusé)", dedup_key="decalage_horloge")

    async def check_open_orders(self, ex: ExecutionEngine) -> dict[str, Any]:
        """Rapproche les ordres OUVERTS chez l'échange (/ordersOpen) et le journal (D7) : un ordre inconnu du
        journal (créé hors du moteur, ou journal restauré trop ancien) est compté, signalé et bloque le réel."""
        now = self.clock.now_ms()
        orders = await ex.ex.open_orders()
        known = {r["exchange_order_id"] for r in self.db.q(
            "SELECT exchange_order_id FROM intents WHERE mode=? AND exchange_order_id IS NOT NULL", (ex.mode,))}
        unknown = [str(o.get("orderId")) for o in orders if str(o.get("orderId")) not in known]
        row = self.db.one("SELECT COUNT(*) n FROM intents WHERE mode=? AND status IN "
                          "('new','partiallyFilled','awaitingTrigger')", (ex.mode,))
        active = row["n"] if row else 0
        rep = {"ts": now, "mode": ex.mode, "ouverts_echange": len(orders), "actifs_journal": int(active),
               "inconnus": unknown[:50]}
        self.db.kv_set("ordres_ouverts_rapprochement", rep, now)
        if unknown:
            self.notifier.notify("ordre_inconnu", f"{len(unknown)} ordre(s) ouvert(s) chez Bitvavo inconnu(s) du "
                                 "journal : vérifier avant toute reprise du réel", dedup_key="ordres-ouverts-inconnus")
            if ex.mode == "real":
                self.guardian.pause("engine", "ordres ouverts inconnus du journal chez l'échange")
        return rep

    def purge_history(self) -> dict[str, int]:
        """Purge BORNÉE de engine.db (D8) : décisions, intentions VETOED, instantanés d'equity (au-delà de la
        rétention : un par jour conservé, pour les exports), bougies. Jamais les fills, flux, ordres actifs."""
        now = self.clock.now_ms()
        days = self.s.retention_days
        cut = {k: now - int(v) * 86_400_000 for k, v in days.items()}
        out: dict[str, int] = {}
        with self.db.tx() as c:
            out["decisions"] = c.execute(
                "DELETE FROM decisions WHERE rowid IN (SELECT rowid FROM decisions WHERE ts < ? LIMIT ?)",
                (cut["decisions"], PURGE_BATCH)).rowcount
            out["intents_vetoed"] = c.execute(
                "DELETE FROM intents WHERE rowid IN (SELECT rowid FROM intents WHERE status='VETOED' AND ts < ? LIMIT ?)",
                (cut["intents_vetoed"], PURGE_BATCH)).rowcount
            out["equity"] = c.execute(
                "DELETE FROM equity_snapshots WHERE rowid IN (SELECT rowid FROM equity_snapshots WHERE ts < ? AND "
                "ts NOT IN (SELECT MAX(ts) FROM equity_snapshots WHERE ts < ? GROUP BY mode, ts / 86400000) LIMIT ?)",
                (cut["equity"], cut["equity"], PURGE_BATCH)).rowcount
            out["candles"] = c.execute(
                "DELETE FROM candles WHERE rowid IN (SELECT rowid FROM candles WHERE ts < ? LIMIT ?)",
                (cut["candles"], PURGE_BATCH)).rowcount
        self.last_purge_ms = now
        self.db.kv_set("purge_historique", {"ts": now, "lignes": out, "retention_jours": days}, now)
        return out

    def bases_separated(self) -> bool:
        """Trois fichiers distincts et canal de commandes NON inscriptible par le
        moteur (montage :ro) ; un lien symbolique est refusé (contre-expertise sécurité phase 5, failles 5 et 7)."""
        if self.db.path == ":memory:":
            return False
        files = [Path(self.db.path), self.inbox.path, self.display.path]
        if any(f.is_symlink() for f in files):
            return False
        paths = {f.resolve() for f in files}
        return len(paths) == 3 and not channel_writable(self.inbox.path)

    # ------------------------------------------------------------ capital authentifié (phase 5)
    async def authenticated_capital(self) -> AuthCapital:
        """Soldes lus CHEZ BITVAVO par le moteur (requête signée avec ses clés), recoupés avec son journal.
        Jamais lu dans une base : ni la base d'affichage, ni le canal de commandes n'entrent dans ce calcul."""
        now = self.clock.now_ms()
        ex = self.real_exchange
        res: AuthCapital
        if ex is None:
            res = unavailable(now, "aucun compte réel attaché")
        else:
            cache = self._auth_cache
            if cache is None or now - cache[0] > AUTH_REFRESH_MS:
                try:
                    bal = await ex.balance()
                    if not isinstance(bal, list):
                        raise ValueError("réponse de solde inattendue")
                    cache = (self.clock.now_ms(), bal)
                    self._auth_cache = cache
                except Exception as e:  # noqa: BLE001 - erreur réseau, API, format : jamais d'achat sur l'inconnu
                    self._auth_cache = None
                    cache = None
                    log.error("soldes Bitvavo illisibles", extra={"data": {"err": repr(e)}})
                    res = unavailable(now, f"{type(e).__name__}")
            if cache is not None:
                led = self._ledger("real")
                excluded = set(self._kv_get("real_excluded_symbols", [], "real"))
                res = authenticate_capital(cache[1], cache[0], self.clock.now_ms(), dict(led.s.cash),
                                           {s: p.qty for s, p in led.s.positions.items()}, excluded,
                                           self.prices_eur(), self.limits)
        self._auth = res
        self.db.kv_set("capital_authentifie", res.as_display(), now)  # copie d'affichage
        if not res.ok:
            self.notifier.notify("securite", "Achats réels suspendus : " + "; ".join(res.reasons),
                                 dedup_key=f"auth-{res.reasons[0][:48]}")
        return res

    @staticmethod
    def _apply_auth(chk: IntentCheck, auth: AuthCapital | None) -> IntentCheck:
        if auth is not None and auth.ok:
            chk.authenticated_equity, chk.authenticated_cash, chk.authenticated_ts = (auth.equity_eur,
                                                                                      auth.eur_available, auth.ts)
        else:
            chk.authenticated_equity = chk.authenticated_cash = chk.authenticated_ts = None
            chk.authenticated_reasons = list(auth.reasons) if auth is not None else ["soldes non vérifiés"]
        return chk

    async def _reconcile_real(self) -> bool:
        """Compare le journal réel aux soldes Bitvavo ; enregistre les flux identifiés, signale les autres.
        Premier rapprochement : les soldes existants sont enregistrés comme solde d'ouverture (EUR) ; les
        autres actifs déjà détenus sont EXCLUS du périmètre du robot (jamais vendus par lui)."""
        ex = self.real_exchange
        if ex is None:
            return False
        try:
            bal = await ex.balance()
            self._auth_cache = (self.clock.now_ms(), list(bal))  # soldes frais : réutilisés pour le capital authentifié
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

        if (not self._kv_get("real_opening_done", False, "real") and self.store is not None
                and self.db.one("SELECT 1 FROM fills WHERE mode='real' LIMIT 1") is not None):
            # le magasin d'état a été perdu/réinitialisé : refaire l'ouverture exclurait les positions du robot
            self.guardian.incident("magasin d'état du moteur perdu alors que des transactions réelles existent : "
                                   "restaurer /state depuis la sauvegarde")
            if self.store is not None:
                self.store.set("falsification", "magasin perdu avec historique réel")
            return False
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
        """Protections natives + sorties pour les positions du mode de `ex` (permis même en pause).

        INVARIANT (phase 6) : à la sortie, toute position détenue a un stop confirmé par l'échange, ou figure dans
        `protections_<mode>` avec `protege: False` ET une notification `arret_protection` est émise. Une exception
        sur un marché (réseau, réponse inattendue) le marque non protégé ; les autres marchés sont traités, puis
        la première exception est relancée (comptage des pannes de la boucle)."""
        mode = ex.mode
        led = self._ledger(mode)
        px = self.prices_eur()
        equity = led.value_eur(px)
        cash = led.s.cash.get("EUR", ZERO)
        meta = self._kv_get(f"position_meta_{mode}", {}, mode)
        statuses: dict[str, dict[str, Any]] = {}
        first_error: Exception | None = None
        for mk, h in self.held_positions(mode).items():
            try:
                done = await self._manage_one(ex, mk, h, meta, statuses, equity, cash)
            except Exception as e:  # noqa: BLE001 — P1/P2 : jamais une position laissée « protégée » par défaut
                log.exception("gestion de position en échec", extra={"data": {"market": mk, "mode": mode}})
                first_error = first_error or e
                done = False
                statuses[mk] = self._unprotected("ERREUR", f"gestion en échec ({type(e).__name__}) : état du stop "
                                                 f"incertain ; {str(e)[:200]}")
            if done:
                meta.pop(mk, None)
        self._kv_set(f"position_meta_{mode}", meta, mode)
        self._publish_protections(mode, statuses)
        if first_error is not None:
            raise first_error

    @staticmethod
    def _unprotected(state: str, reason: str) -> dict[str, Any]:
        return {"protege": False, "etat": state, "declencheur": None, "quantite_couverte": "0", "raison": reason[:300]}

    def _publish_protections(self, mode: str, statuses: dict[str, dict[str, Any]]) -> None:
        """Publie l'état des protections ; toute position non protégée est NOTIFIÉE, en simulation comme en réel
        (P3 : la simulation n'était notifiée que pour une sortie refusée)."""
        self.db.kv_set(f"protections_{mode}", statuses, self.clock.now_ms())
        unprotected = sorted(m for m, s in statuses.items() if not s["protege"])
        if unprotected:
            label = "réelles" if mode == "real" else "simulées"
            self.notifier.notify("arret_protection", f"Positions {label} NON protégées : {unprotected}",
                                 dedup_key=f"unprot-{mode}-{','.join(unprotected)}")

    async def _manage_one(self, ex: ExecutionEngine, mk: str, h: HeldPosition, meta: dict[str, Any],
                          statuses: dict[str, dict[str, Any]], equity: Decimal, cash: Decimal) -> bool:
        """Une position : sortie éventuelle, puis protection. Renvoie True si la position est soldée."""
        mode = ex.mode
        st, df = self.states.get(mk), self.candles.get(mk)
        pm = self.protect[mode]
        if st is not None:
            p = self.decider.evaluate_exit(st.info, st, df, h) if df is not None else None
            realloc = self._realloc[mode]
            if (mk in realloc and self.clock.now_ms() - int(realloc[mk].get("ts", 0)) < 3_600_000
                    and self.guardian.state in (State.SIMULATION, State.REAL)):
                from .portfolio.decision import Proposal
                p = Proposal(mk, "vendre", h.strategy, amount_base=st.info.round_amount(h.qty),
                             reasons=[f"réallocation au profit de {realloc[mk]['au_profit_de']}"])
                realloc.pop(mk)
            if p is not None and p.action == "vendre" and p.amount_base > 0:
                status, accepted, reasons = await self._protective_exit(ex, mk, st, h.strategy, "sortie", equity,
                                                                        cash, h.stop)
                self._record_decision(mode, p, accepted, p.reasons + reasons, False)
                if accepted or status == "DEJA_VENDUE":
                    return True
                if mk not in self.held_positions(mode):
                    return False
                # sortie impossible : la position reste protégée (stop conservé ou REPOSÉ ci-dessous)
                if h.stop <= 0:
                    statuses[mk] = self._unprotected("SORTIE_REFUSEE", "; ".join(reasons))
                    return False
        if mk not in ex.markets:
            statuses[mk] = self._unprotected("MARCHE_INCONNU", "marché absent de la cote : aucun stop possible")
            return False
        # P5 : sans niveau de stop calculable (méta perdue, bougies indisponibles), le stop éventuel est RELU ;
        # sinon la position est explicitement signalée (jamais ignorée en silence)
        ps = await pm.ensure(mk, h.qty, h.stop) if h.stop > 0 else await pm.status(mk, h.qty)
        statuses[mk] = {"protege": ps.protected, "etat": ps.state, "declencheur": fmt(ps.trigger) if ps.trigger
                        else None, "quantite_couverte": fmt(ps.covered_qty),
                        "raison": ps.reason if h.stop > 0 or ps.protected else
                        f"niveau de stop inconnu (méta ou bougies absentes) ; {ps.reason}"}
        if st is None and not ps.protected:
            statuses[mk]["raison"] = f"aucune donnée de marché ; {statuses[mk]['raison']}"
        return False

    def _audit_protections(self) -> None:
        """Filet de sécurité appelé à la fin de CHAQUE cycle, y compris quand il se termine par une exception
        (P1, P4) : sans accès réseau, une position affichée « protégée » dont le stop n'est plus actif — journal
        (réel) ou simulateur papier (absent après un redémarrage tant qu'aucun cycle n'a abouti) — est marquée
        non protégée ; toute position non protégée est notifiée. Ne relève jamais une position en « protégée »."""
        modes = ["simulation"] + (["real"] if self.real_exec is not None else [])
        for mode in modes:
            try:
                held = self.held_positions(mode)
                statuses = dict(self.db.kv_get(f"protections_{mode}", {}) or {})
                changed = False
                for mk, h in held.items():
                    s = statuses.get(mk)
                    if s is not None and not s.get("protege"):
                        continue
                    if s is not None and self._stop_live(mode, mk, h.qty):
                        continue
                    statuses[mk] = self._unprotected(
                        "STOP_ABSENT" if s is not None else "NON_EVALUEE",
                        "aucun stop actif constaté en fin de cycle" if s is not None else
                        "position non évaluée par la gestion des protections")
                    changed = True
                for mk in [m for m in statuses if m not in held]:
                    statuses.pop(mk)
                    changed = True
                if changed or any(not s.get("protege") for s in statuses.values()):
                    self._publish_protections(mode, statuses)
            except Exception:  # noqa: BLE001 — le filet ne doit jamais masquer l'erreur d'origine
                log.exception("audit des protections impossible", extra={"data": {"mode": mode}})

    def _stop_live(self, mode: str, mk: str, qty: Decimal) -> bool:
        m = self.markets.get(mk)
        need = (m.round_amount(qty) - m.min_order_base) if m is not None else qty
        if mode == "simulation":
            if self.paper is None:  # simulateur pas encore reconstruit : ses ordres n'existent plus
                return False
            return any(o.market == mk and o.side == "sell" and o.order_type.startswith("stopLoss")
                       and o.status == "awaitingTrigger" and o.amount is not None and o.amount - o.filled_amount >= need
                       for o in self.paper.orders.values())
        rows = self.db.q("SELECT reserved_base FROM intents WHERE mode='real' AND market=? AND side='sell' AND "
                         "order_type IN ('stopLoss','stopLossLimit') AND status='awaitingTrigger' AND "
                         "exchange_order_id IS NOT NULL", (mk,))
        return any(D(r["reserved_base"] or 0) >= need for r in rows)

    async def decide(self) -> None:
        ex = self.exec
        assert ex is not None
        mode = ex.mode
        now = self.clock.now_ms()
        led = self._ledger(mode)
        px = self.prices_eur()
        equity = led.value_eur(px)
        cash = led.s.cash.get("EUR", ZERO)
        auth: AuthCapital | None = None
        if mode == "real":
            # phase 5 : dimensionnement sur min(journal, soldes Bitvavo authentifiés) ; écart ou soldes
            # invérifiables => equity/trésorerie nulles ET veto du Guardian (aucun achat ; ventes permises)
            auth = await self.authenticated_capital()
            equity, cash = auth.sizing(equity, cash)
        auth_spent = ZERO
        held = self.held_positions(mode)
        meta = self._kv_get(f"position_meta_{mode}", {}, mode)
        returns = {m: df["close"].pct_change() for m, df in self.candles.items()}
        scanner = []
        for mk, st in self.states.items():
            el = check_eligibility(st, self.limits, now, self.s.quote_currency, self.clock_skew_ms)
            row: dict[str, Any] = {"market": mk, "eligible": el.eligible, "reasons": el.reasons,
                                   "spread_bps": float(spread_bps(st.best_bid, st.best_ask) or 0),
                                   "volume_24h_eur": float(st.quote_volume_24h)}
            df = self.candles.get(mk)
            if el.eligible and df is not None:
                ob = self.paper.books.get(mk) if self.paper else None
                depth = ob.depth_quote("asks", D(50)) if ob else ZERO
                held_rets = {h: returns[h] for h in held if h in returns and h != mk}
                # exposition ENGAGÉE pour le dimensionnement : positions du journal + achats encore en attente
                # (phase 6 ; l'exécuteur refait ce calcul de façon atomique au contrôle)
                committed = self._exposure(mode, px) + ex.pending_buy_quote()
                props = self.decider.evaluate_entry(st.info, st, df, equity, cash, committed, held.get(mk), depth,
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
                    # exposition RECALCULÉE depuis le journal (un achat rempli dans ce cycle y figure déjà) ;
                    # l'exécuteur ajoute les achats encore en attente dans sa transaction
                    chk = self._check(st, "buy", p.amount_quote, True, equity, cash,
                                      self._exposure(mode, px) + p.amount_quote, mode)
                    if mode == "real":
                        self._apply_auth(chk, auth)
                        if chk.authenticated_cash is not None:
                            chk.authenticated_cash -= auth_spent  # achats déjà acceptés dans ce cycle
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
                        auth_spent += p.amount_quote
                    break  # une seule entrée par marché et par cycle
            scanner.append(row)
        self._kv_set(f"position_meta_{mode}", meta, mode)
        self.db.kv_set("scanner", {"ts": now, "rows": scanner}, now)
        self.last_decision_ms = now

    def _exposure(self, mode: str, px: dict[str, Decimal]) -> Decimal:
        """Valeur des positions DÉTENUES (journal des fills), hors ordres en attente. Position sans prix actuel
        (marché suspendu, absent du ticker) : dernier prix connu, sinon coût moyen — jamais 0 (D5)."""
        total = ZERO
        for h in self.held_positions(mode).values():
            base = h.market.split("-")[0]
            total += h.qty * (px.get(base) or self._last_px.get(base) or h.avg_cost)
        return total

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
                           data_age_ms=data_age_ms(st, self.clock.now_ms(), self.clock_skew_ms), market_trading=st.info.is_trading,
                           spread_bps=spread_bps(st.best_bid, st.best_ask), anomaly=st.anomaly_score,
                           min_order_quote=st.info.min_order_quote, available_quote=cash, equity=equity,
                           exposure_after=exposure_after, position_after=pos_after, mode=mode)

    async def _protective_exit(self, ex: ExecutionEngine, mk: str, st: MarketState, strategy: str, purpose: str,
                               equity: Decimal, cash: Decimal, stop: Decimal) -> tuple[str, bool, list[str]]:
        """Vente de protection au marché SANS jamais laisser la position nue (contre-expertise phase 6, D1) :
        1. la vente est évaluée par le Guardian AVANT d'annuler le stop hébergé : refusée => stop conservé ;
        2. le stop est annulé (annulation confirmée), puis la vente est soumise ;
        3. vente refusée ou en échec après l'annulation => stop REPOSÉ immédiatement et notification."""
        mode = ex.mode
        h = self.held_positions(mode).get(mk)
        if h is None:
            return "DEJA_VENDUE", False, ["position déjà soldée"]
        qty = st.info.round_amount(h.qty)

        def check() -> IntentCheck:
            c = self._check(st, "sell", qty * (st.best_bid or ZERO), False, equity, cash, ZERO, mode)
            c.order_type = "market"
            c.order_type_supported = st.info.supports("market")
            c.min_order_base = st.info.min_order_base
            c.sell_amount_base = qty
            return c
        pre = self.guardian.check(check())
        if not pre.allowed:
            return "NON_TENTEE", False, ["sortie refusée AVANT annulation du stop (stop conservé)", *pre.reasons]
        ok, why = await self.protect[mode].release_for_exit(mk)
        if not ok:
            return "REPORTEE", False, [why]
        h2 = self.held_positions(mode).get(mk)
        if h2 is None:
            return "DEJA_VENDUE", False, ["le stop a été exécuté pendant l'annulation"]
        qty = st.info.round_amount(h2.qty)
        try:
            r = await ex.submit(OrderRequest(mk, "sell", "market", amount=qty, strategy=strategy, is_entry=False,
                                             is_protective=True, position_qty=h2.qty, purpose=purpose,
                                             ref_price=st.best_bid, check=check()), cash)
        except Exception as e:  # noqa: BLE001 — P1 : vente en échec réseau APRÈS l'annulation du stop
            # (intention restée SUBMITTING/UNKNOWN : reprise par `recover` au cycle suivant, jamais resoumise)
            r = SubmitResult("", "ERREUR", False, [f"vente en échec ({type(e).__name__}) : {str(e)[:150]}"])
        if r.accepted:
            return r.status, True, r.reasons
        h3 = self.held_positions(mode).get(mk)
        reasons = list(r.reasons)
        if h3 is not None:
            # notifiée AVANT la repose (qui peut elle-même échouer) : la position est nue à cet instant
            self.notifier.notify("arret_protection", f"Sortie {mode} {mk} refusée après annulation du stop : "
                                 f"{'; '.join(r.reasons)[:200]}", dedup_key=f"sortie-{mode}-{mk}")
            if stop > 0:
                ps = await self.protect[mode].ensure(mk, h3.qty, stop)
                reasons.append(f"stop reposé : {ps.state}" if ps.protected else f"stop NON reposé : {ps.reason}")
        return r.status, False, reasons

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
        self._liq_estimate = est  # référence : mémoire du moteur (la base n'en a qu'une copie d'affichage)
        self.db.kv_set("liquidation_estimate", est, self.clock.now_ms())
        return est

    async def liquidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        est = self._liq_estimate
        if not est or est["id"] != payload.get("estimate_id") or self.clock.now_ms() - est["ts"] > 120_000:
            return {"ok": False, "reasons": ["estimation absente, déjà utilisée ou expirée (2 min) : recommencer"]}
        if est.get("mode") == "real" or self.command_key is not None:
            why = self._verify_signed("liquidation_confirm", payload)
            signed = payload.get("sig_fields") or {}
            if why or signed.get("estimate_id") != est["id"] or signed.get("confirmation") != "LIQUIDER" or (
                    "mode" in signed and signed.get("mode") != est["mode"]):
                return {"ok": False, "reasons": [why or "champs signés incohérents"]}
        elif not payload.get("password_ok") or payload.get("confirmation") != "LIQUIDER":
            return {"ok": False, "reasons": ["confirmation invalide"]}
        self._liq_estimate = None  # usage unique
        self.db.kv_set("liquidation_estimate", None, self.clock.now_ms())
        await self.refresh_tickers()  # jamais de vente sur des prix périmés
        mode = est["mode"]
        ex = self.real_exec if mode == "real" else self.paper_exec
        assert ex is not None
        results = []
        for mk in self.held_positions(mode):
            st = self.states.get(mk)
            if st is None:
                continue
            h = self.held_positions(mode).get(mk)
            status, _, reasons = await self._protective_exit(ex, mk, st, "liquidation_manuelle", "liquidation",
                                                             ZERO, ZERO, h.stop if h else ZERO)
            results.append({"market": mk, "status": status, "reasons": reasons})
        return {"ok": True, "mode": mode, "resultats": results}

    # ------------------------------------------------------------ snapshot / santé
    def snapshot(self, force: bool = False) -> None:
        now = self.clock.now_ms()
        if (force or now - self.last_snapshot_ms >= 60_000) and not self.writes_suspended:  # 1/min
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
        self._last_hb_ms = self.clock.now_ms()
        ex = self.exec
        mode = ex.mode if ex else "simulation"
        reelles = 0
        if self.real_exec is not None:
            try:  # D1 : exposition RÉELLE encore gérée même si les nouvelles entrées passent par la simulation
                reelles = len(self.held_positions("real"))
            except Exception:  # noqa: BLE001
                reelles = -1  # inconnu : l'affichage doit prendre la prudence (RÉEL)
        frozen = self.loop_frozen()
        self.db.kv_set("engine_heartbeat", {"ts": self.clock.now_ms(), "mode": mode, "pid": os.getpid(),
                                            "n_markets": len(self.markets), "data_ok": data_ok,
                                            "last_data_ms": self.last_data_ms,
                                            "real_exec": self.real_exec is not None,
                                            "positions_reelles_ouvertes": reelles,
                                            "last_cycle_ms": self.last_cycle_ms, "boucle_figee": frozen,
                                            "disque": {"niveau": self.disk_level, "libre_mo": self.disk_free_mb,
                                                       "ecritures_suspendues": self.writes_suspended}},
                         self.clock.now_ms())
        self.publish_display()  # l'API ne voit que cette copie (affichage.db, montée :ro dans l'API)

    def vigil_once(self) -> None:
        """Vigie (phase 7, tâche asyncio parallèle à la boucle) : si la boucle est bloquée dans un appel réseau
        (Bitvavo sans réponse : 5 tentatives × 10 s par requête, soit plus de 2 min par cycle — mesuré), le drapeau
        d'arrêt d'urgence SSH est quand même appliqué et le battement de cœur continue (sinon l'API et le contrôle
        de santé déclaraient le moteur mort). Synchrone : jamais entrelacé au milieu d'une transaction. Ne lève jamais."""
        now = self.clock.now_ms()
        try:
            self._check_emergency_file()
        except Exception:  # noqa: BLE001
            log.exception("vigie : drapeau d'arrêt d'urgence non lu")
        if self.loop_frozen():
            self.notifier.notify("panne_prolongee", "Boucle du moteur figée depuis plus de 10 min : aucune décision ni "
                                 "audit des protections. Redémarrer le moteur (docs/RUNBOOKS.md).",
                                 dedup_key=f"boucle-figee-{self.last_cycle_ms}")
        if now - self._last_hb_ms < 10_000:
            return
        try:
            self.heartbeat(data_ok=now - self.last_data_ms <= self.limits.max_stale_ms)
        except Exception as e:  # noqa: BLE001
            if is_disk_full_error(e):
                self.on_disk_full(e)
            else:
                log.exception("vigie : battement de cœur non écrit")

    def loop_frozen(self) -> bool:
        """Aucun cycle de boucle terminé depuis `frozen_after_ms` (D3) ; faux avant le premier cycle."""
        return self.last_cycle_ms > 0 and self.clock.now_ms() - self.last_cycle_ms > self.frozen_after_ms

    async def vigil(self, every_s: float = 5.0) -> None:  # pragma: no cover - boucle de production
        while True:
            await asyncio.sleep(every_s)
            self.vigil_once()

    async def idle_tick(self) -> None:
        """Attente après une erreur de boucle (échange injoignable, données refusées…) : l'arrêt d'urgence SSH et les
        commandes restent traités, et le battement de cœur continue (phase 7 : sans lui, une panne prolongée de
        Bitvavo faisait passer le moteur pour mort jusqu'à 65 s). Ne lève jamais."""
        try:
            self._check_emergency_file()
        except Exception:  # noqa: BLE001
            log.exception("drapeau d'arrêt d'urgence non lu")
        try:
            await self.process_commands()
        except Exception:  # noqa: BLE001
            log.exception("commandes non traitées")
        try:
            self.heartbeat(data_ok=False)
        except Exception as e:  # noqa: BLE001
            if is_disk_full_error(e):
                self.on_disk_full(e)
            else:
                log.exception("battement de cœur non écrit")

    async def step(self) -> None:
        try:
            await self._step()
        finally:
            self._audit_protections()  # invariant de protection, même si le cycle échoue (P1, P4)
            self.last_cycle_ms = self.clock.now_ms()  # cycle terminé (même en erreur) : la boucle n'est pas figée

    async def _step(self) -> None:
        now = self.clock.now_ms()
        self.check_disk()  # phase 7 : avant toute écriture du cycle
        # Arrêt d'urgence par SSH : fichier déposé dans le répertoire d'état du MOTEUR (l'API ne peut ni le
        # créer ni le neutraliser, contrairement à une ligne de control_commands)
        self._check_emergency_file()
        # Les commandes de contrôle (arrêt d'urgence, pause) passent AVANT tout accès réseau. Un échec du canal
        # (API compromise) ne doit JAMAIS empêcher la gestion des positions et des protections.
        await self._safe_commands()
        if not self.guardian.verify_integrity():
            self.notifier.notify("securite", "Falsification de l'état du Guardian détectée : INCIDENT")
        if now - self.last_clock_sync_ms >= CLOCK_SYNC_EVERY_MS:
            await self.measure_clock_skew()
        try:
            if now - self.last_markets_ms > 600_000:
                await self.refresh_markets()
            await self.refresh_tickers()
        except Exception:
            self.heartbeat(data_ok=False)
            raise
        self.last_data_ms = now
        await self._safe_commands()
        if self.real_exec is not None and not self._open_orders_checked:
            try:  # premier cycle après démarrage : ordres ouverts chez l'échange vs journal (D7)
                await self.check_open_orders(self.real_exec)
                self._open_orders_checked = True
            except Exception as e:  # noqa: BLE001
                log.warning("rapprochement des ordres ouverts impossible", extra={"data": {"err": repr(e)[:200]}})
        if now - self.last_purge_ms >= PURGE_EVERY_MS and not self.writes_suspended:
            self.purge_history()
        for ex in self.execs():
            await ex.recover()
            await ex.refresh_open()
            await ex.cancel_stale_entries(ENTRY_LIMIT_TTL_MS)
        if self.real_exec is not None and now - self.last_fee_refresh_ms > 3_600_000:
            await self.refresh_fees()
        if now - self.last_decision_ms >= 60_000 and self.writes_suspended:
            # disque presque plein : positions et protections seulement (aucune décision, aucune entrée)
            held_s = sorted(set(self.held_positions("simulation")) | (set(self.held_positions("real"))
                                                                      if self.real_exec is not None else set()))
            stale_h = [m for m in held_s if m not in self.candles or int(self.candles[m].index[-1]) + 2 * INTERVAL_MS <= now]
            if stale_h:
                await self.refresh_candles(stale_h[:20])
            for ex in self.execs():
                await self.manage_positions(ex)
            self.last_decision_ms = now
        elif now - self.last_decision_ms >= 60_000:
            eligible = [m for m, st in self.states.items()
                        if check_eligibility(st, self.limits, now, clock_skew_ms=self.clock_skew_ms).eligible]
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
    if not eng.bases_separated():
        log.error("base du moteur, canal de commandes et affichage NON distincts : mode réel impossible")
    tg_token, tg_chat = s.read_secret("telegram_bot_token"), os.environ.get("TELEGRAM_CHAT_ID")
    ntfy_url = s.read_secret("ntfy_url")  # phase 7 : canal gratuit (le nom du sujet est le secret)
    for sec in (tg_token, ntfy_url):
        if sec:
            register_secret(sec)
    if (tg_token and tg_chat) or ntfy_url:
        eng.notifier = Notifier(db, eng.clock.now_ms, tg_token if tg_chat else None, tg_chat, ntfy_url=ntfy_url)
    if key and secret and not eng.official_endpoints():
        log.error("clés présentes mais BITVAVO_REST_URL/WS_URL ne sont pas les points d'accès officiels : réel désactivé")
        key = secret = None
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
    asyncio.create_task(eng.vigil())  # noqa: RUF006 — arrêt d'urgence SSH et battement de cœur, boucle bloquée ou non
    failures = 0
    while True:
        try:
            await eng.step()
            failures = 0
        except Exception as e:  # noqa: BLE001
            if is_disk_full_error(e):
                # disque plein : transaction annulée (base intacte), écritures non critiques suspendues ; pas de
                # mise en pause du Guardian (son magasin peut être sur le même disque) : on réessaie au cycle suivant
                eng.on_disk_full(e)
                await asyncio.sleep(5)
                continue
            failures += 1
            log.exception("erreur de boucle", extra={"data": {"failures": failures}})
            if failures >= 5:
                eng.guardian.pause("engine", f"erreurs répétées : {type(e).__name__}")
                eng.notifier.notify("panne_prolongee", f"Moteur en erreur répétée : {type(e).__name__}")
            for _ in range(min(60, 2 ** failures) // 5 + 1):
                await eng.idle_tick()
                await asyncio.sleep(5)
        await asyncio.sleep(5)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
