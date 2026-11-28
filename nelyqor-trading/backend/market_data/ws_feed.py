"""Flux WebSocket PUBLIC Bitvavo branché au moteur (phase 8) : horodatage de l'échange et santé du flux.

Pourquoi : `/ticker/book` (REST), la source de prix du moteur, ne porte AUCUN horodatage de l'échange (mesuré :
426/426 marchés). Le canal `book` du WebSocket, lui, est horodaté par l'échange (en NANOSECONDES, mesuré au banc
45949068420) et numéroté (nonce). Ce module tient, pour un ensemble borné de marchés (détenus + plus liquides), un
carnet local synchronisé (instantané REST + mises à jour WS, contrôle de nonce) et en déduit :

- l'âge de la dernière donnée de l'ÉCHANGE (et non l'heure de réception) ;
- la latence échange → réception, corrigée du décalage d'horloge mesuré sur /v2/time ;
- un diagnostic par marché, qui distingue un MARCHÉ IMMOBILE d'un FLUX DÉFAILLANT :
    * `ok`         : flux vivant, carnet synchronisé, mise à jour récente ;
    * `immobile`   : flux vivant (d'autres marchés bougent), carnet synchronisé, aucun changement récent sur ce marché,
                     et le carnet REST concorde : c'est un marché calme, PAS une panne ;
    * `desynchro`  : rupture de nonce non encore résorbée, reconnexion en cours, ou instantané impossible ;
    * `incoherent` : le meilleur prix REST et le meilleur prix WS divergent durablement ;
    * `flux_coupe` : plus aucun message WS depuis `feed_silence_ms` (connexion morte ou figée) ;
    * `retard`     : l'échange horodate ses messages avec un retard anormal (après correction d'horloge).

Le WebSocket est un GARDE-FOU : la source de prix du moteur reste le REST (comportement validé). Un marché `desynchro`,
`incoherent` ou `retard` est refusé à l'entrée ; un flux coupé est signalé (alerte) et le moteur se replie sur les
règles REST (fraîcheur de réception, détection d'un flux REST figé). Aucune clé, aucun canal privé, aucun ordre.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core.logging import get_logger
from .models import BookSequenceError, OrderBook

log = get_logger("bitvavo.ws.public")

OK, IMMOBILE, DESYNCHRO, INCOHERENT, FLUX_COUPE, RETARD = "ok", "immobile", "desynchro", "incoherent", "flux_coupe", "retard"
BLOCKING = {DESYNCHRO, INCOHERENT, RETARD}


def to_ms(raw: object) -> int:
    """Horodatage de l'échange en ms ; Bitvavo envoie des ns (carnet WS), parfois des ms (REST). 0 si invalide."""
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, str):
        if not raw.isdigit():
            return 0
        raw = int(raw)
    if not isinstance(raw, int | float) or raw <= 0:
        return 0
    v = int(raw)
    if v > 10**17:
        return v // 1_000_000
    if v > 10**14:
        return v // 1_000
    return v


@dataclass
class MarketFeed:
    market: str
    book: OrderBook
    last_exchange_ms: int = 0   # horodatage échange de la dernière mise à jour appliquée
    last_received_ms: int = 0   # heure locale de réception de cette mise à jour
    gaps: int = 0               # ruptures de nonce constatées
    resyncs: int = 0
    resync_pending: bool = True  # pas encore d'instantané (ou trou / reconnexion)
    resync_failed_ms: int = 0
    disagree_since_ms: int = 0  # désaccord REST / WS en cours depuis


@dataclass
class FeedConfig:
    max_markets: int = 30
    feed_silence_ms: int = 15_000      # aucun message WS au-delà : flux coupé
    active_ms: int = 10_000            # mise à jour du marché plus récente que ça : « ok » ; sinon « immobile »
    max_exchange_delay_ms: int = 5_000  # retard échange → réception (corrigé de l'horloge) au-delà : « retard »
    disagree_bps: Decimal = Decimal(50)  # écart de meilleur prix REST / WS au-delà : désaccord
    disagree_ms: int = 30_000          # désaccord durable au-delà : « incohérent »
    snapshot_depth: int = 100
    resync_retry_ms: int = 5_000       # après un instantané en échec : pas de nouvel essai avant ce délai (limite d'API)
    latency_window: int = 500


@dataclass
class FeedStats:
    connections: int = 0
    reconnects: int = 0
    messages: int = 0
    book_updates: int = 0
    gaps: int = 0
    resyncs: int = 0
    resync_failures: int = 0
    invalid_messages: int = 0
    latencies_ms: deque[int] = field(default_factory=lambda: deque(maxlen=500))


class PublicBookFeed:
    def __init__(self, url: str, snapshot: Callable[[str, int], Awaitable[dict[str, Any]]],
                 now_ms: Callable[[], int] | None = None, cfg: FeedConfig | None = None) -> None:
        self.url = url
        self.snapshot = snapshot              # coroutine (marché, profondeur) -> instantané REST /{market}/book
        self.now = now_ms or (lambda: int(time.time() * 1000))
        self.cfg = cfg or FeedConfig()
        self.markets: dict[str, MarketFeed] = {}
        self.stats = FeedStats()
        self.connected = False
        self.last_msg_ms = 0
        self.clock_skew_ms = 0                # heure de l'échange − heure locale (fournie par le moteur, /v2/time)
        self._stop = asyncio.Event()
        self._resubscribe = asyncio.Event()
        self._ws: Any = None

    # ------------------------------------------------------------------ marchés suivis
    def set_markets(self, markets: list[str]) -> bool:
        """Fixe l'ensemble suivi (borné à `max_markets`). Vrai si l'ensemble a changé (ré-abonnement requis)."""
        wanted = list(dict.fromkeys(markets))[: self.cfg.max_markets]
        if set(wanted) == set(self.markets):
            return False
        self.markets = {m: self.markets.get(m) or MarketFeed(m, OrderBook(m)) for m in wanted}
        self._resubscribe.set()
        return True

    def covers(self, market: str) -> bool:
        return market in self.markets

    # ------------------------------------------------------------------ messages
    async def handle(self, msg: dict[str, Any]) -> None:
        now = self.now()
        self.last_msg_ms = now
        self.stats.messages += 1
        if msg.get("event") != "book":
            return
        mf = self.markets.get(str(msg.get("market")))
        if mf is None:
            return
        try:
            nonce = int(msg["nonce"])
        except (KeyError, TypeError, ValueError):
            self.stats.invalid_messages += 1
            return
        exch = to_ms(msg.get("timestamp"))
        self.stats.book_updates += 1
        if exch:
            self.stats.latencies_ms.append(now + self.clock_skew_ms - exch)
        upd = {"nonce": nonce, "bids": msg.get("bids", []), "asks": msg.get("asks", []), "timestamp": exch or now}
        if mf.resync_pending:
            await self.resync(mf)
            if mf.resync_pending:
                return
            if not self._apply(mf, upd, now, count_gap=False):
                return
        elif not self._apply(mf, upd, now, count_gap=True):
            # trou dans la séquence : instantané, puis nouvel essai UNIQUE avec ce message (plus ancien que
            # l'instantané : ignoré ; suivant immédiat : appliqué ; sinon le marché reste désynchronisé)
            await self.resync(mf)
            if mf.resync_pending or not self._apply(mf, upd, now, count_gap=False):
                return
        if exch and mf.book.nonce == nonce:
            mf.last_exchange_ms = exch
            mf.last_received_ms = now

    def _apply(self, mf: MarketFeed, upd: dict[str, Any], now: int, count_gap: bool) -> bool:
        try:
            mf.book.apply_update(upd, now)
            return True
        except BookSequenceError as e:
            if count_gap:
                mf.gaps += 1
                self.stats.gaps += 1
                log.warning("rupture de nonce du carnet WS", extra={"data": {"market": mf.market, "err": str(e)}})
            mf.resync_pending = True
            return False
        except Exception:  # noqa: BLE001 — prix illisible : carnet considéré désynchronisé
            self.stats.invalid_messages += 1
            mf.resync_pending = True
            return False

    async def resync(self, mf: MarketFeed) -> None:
        """Instantané REST : nouvelle base de nonce ; les mises à jour plus anciennes seront ignorées. Après un échec,
        aucun nouvel essai avant `resync_retry_ms` : un flux de 60 messages/s ne doit pas marteler l'API REST."""
        if mf.resync_failed_ms and self.now() - mf.resync_failed_ms < self.cfg.resync_retry_ms:
            return
        mf.resyncs += 1
        self.stats.resyncs += 1
        try:
            snap = await self.snapshot(mf.market, self.cfg.snapshot_depth)
            now = self.now()
            snap = {**snap, "timestamp": to_ms(snap.get("timestamp")) or now}
            mf.book.load_snapshot(snap, now)
        except Exception as e:  # noqa: BLE001 — Bitvavo injoignable : marché désynchronisé jusqu'au prochain essai
            mf.resync_pending = True
            mf.resync_failed_ms = self.now()
            self.stats.resync_failures += 1
            log.warning("instantané du carnet impossible", extra={"data": {"market": mf.market, "err": repr(e)[:160]}})
            return
        mf.resync_pending = False
        mf.resync_failed_ms = 0
        mf.last_received_ms = now
        mf.last_exchange_ms = int(snap["timestamp"])

    def on_disconnect(self) -> None:
        """Connexion perdue : la continuité des nonces est rompue pour TOUS les marchés."""
        self.connected = False
        for mf in self.markets.values():
            mf.resync_pending = True

    # ------------------------------------------------------------------ diagnostic
    def feed_alive(self, now: int | None = None) -> bool:
        now = self.now() if now is None else now
        return self.connected and now - self.last_msg_ms <= self.cfg.feed_silence_ms

    def exchange_delay_ms(self) -> int | None:
        """Retard médian échange → réception, corrigé du décalage d'horloge (ms)."""
        lat = list(self.stats.latencies_ms)[-50:]
        return int(statistics.median(lat)) if lat else None

    def status(self, market: str, rest_bid: Decimal | None = None, rest_ask: Decimal | None = None,
               now: int | None = None) -> str | None:
        """Diagnostic du marché (None s'il n'est pas suivi par le WebSocket)."""
        mf = self.markets.get(market)
        if mf is None:
            return None
        now = self.now() if now is None else now
        if not self.feed_alive(now):
            return FLUX_COUPE
        if mf.resync_pending or not mf.book.synced:
            return DESYNCHRO
        delay = self.exchange_delay_ms()
        if delay is not None and delay > self.cfg.max_exchange_delay_ms:
            return RETARD
        if rest_bid is not None and rest_ask is not None and self._disagrees(mf, rest_bid, rest_ask):
            if not mf.disagree_since_ms:
                mf.disagree_since_ms = now
            if now - mf.disagree_since_ms > self.cfg.disagree_ms:
                return INCOHERENT
        else:
            mf.disagree_since_ms = 0
        return OK if now - mf.last_received_ms <= self.cfg.active_ms else IMMOBILE

    def _disagrees(self, mf: MarketFeed, rest_bid: Decimal, rest_ask: Decimal) -> bool:
        wb, wa = mf.book.best_bid(), mf.book.best_ask()
        if wb is None or wa is None or rest_bid <= 0 or rest_ask <= 0:
            return False
        mid_w, mid_r = (wb + wa) / 2, (rest_bid + rest_ask) / 2
        return abs(mid_w - mid_r) / mid_r * 10_000 > self.cfg.disagree_bps

    def exchange_age_ms(self, market: str, now: int | None = None) -> int | None:
        """Âge, à l'heure de l'ÉCHANGE, de la dernière mise à jour reçue pour ce marché."""
        mf = self.markets.get(market)
        if mf is None or not mf.last_exchange_ms:
            return None
        now = self.now() if now is None else now
        return now + self.clock_skew_ms - mf.last_exchange_ms

    def summary(self, now: int | None = None) -> dict[str, Any]:
        now = self.now() if now is None else now
        counts: dict[str, int] = {}
        for m in self.markets:
            s = self.status(m, now=now) or "?"
            counts[s] = counts.get(s, 0) + 1
        lat = sorted(self.stats.latencies_ms)
        return {
            "connecte": self.connected, "vivant": self.feed_alive(now), "marches": len(self.markets),
            "statuts": counts, "dernier_message_age_ms": now - self.last_msg_ms if self.last_msg_ms else None,
            "retard_echange_mediane_ms": self.exchange_delay_ms(),
            "latence_p99_ms": lat[int(0.99 * (len(lat) - 1))] if lat else None,
            "connexions": self.stats.connections, "reconnexions": self.stats.reconnects,
            "messages": self.stats.messages, "ruptures_nonce": self.stats.gaps, "resynchronisations": self.stats.resyncs,
            "echecs_instantane": self.stats.resync_failures, "messages_invalides": self.stats.invalid_messages,
        }

    # ------------------------------------------------------------------ connexion
    async def _session(self, ws: Any) -> None:
        self._ws = ws
        self.connected = True
        self.last_msg_ms = self.now()
        self._resubscribe.clear()
        if self.markets:
            await ws.send(json.dumps({"action": "subscribe",
                                      "channels": [{"name": "book", "markets": sorted(self.markets)}]}))
        async for raw in ws:
            if self._resubscribe.is_set():
                return  # ensemble modifié : nouvelle session (nouvel abonnement, carnets resynchronisés)
            try:
                msg = json.loads(raw)
            except ValueError:
                self.stats.invalid_messages += 1
                continue
            if isinstance(msg, dict):
                await self.handle(msg)

    async def run_forever(self, max_connections: int | None = None) -> None:  # pragma: no cover - réseau réel
        import websockets

        backoff = 0.5
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=10, ping_timeout=10, max_size=2**22) as ws:
                    self.stats.connections += 1
                    if self.stats.connections > 1:
                        self.stats.reconnects += 1
                    backoff = 0.5
                    await self._session(ws)
            except Exception as e:  # noqa: BLE001 — reconnexion sur toute erreur
                log.warning("WS public déconnecté", extra={"data": {"err": repr(e)[:200], "backoff": backoff}})
            self.on_disconnect()
            if max_connections is not None and self.stats.connections >= max_connections:
                return
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    def stop(self) -> None:
        self._stop.set()
