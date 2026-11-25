"""Flux WebSocket Bitvavo : ticker, bougies, carnet avec contrôle de nonce et resynchronisation.

Le carnet est initialisé par snapshot REST ; toute rupture de séquence déclenche une resync.
Les mises à jour reçues pendant la resync sont mises en tampon puis rejouées si leur nonce suit.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.logging import get_logger
from .bitvavo_rest import BitvavoREST
from .models import BookSequenceError, OrderBook

log = get_logger("bitvavo.ws")


class BookManager:
    def __init__(self, rest: BitvavoREST) -> None:
        self.rest = rest
        self.books: dict[str, OrderBook] = {}
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self.resyncs = 0

    async def resync(self, market: str) -> None:
        self.resyncs += 1
        self._buffers[market] = []
        snap = await self.rest.book(market, depth=100)
        ob = self.books.setdefault(market, OrderBook(market))
        ob.load_snapshot(snap, int(time.time() * 1000))
        for upd in sorted(self._buffers.pop(market, []), key=lambda u: int(u["nonce"])):
            if int(upd["nonce"]) > ob.nonce:
                ob.apply_update(upd, int(time.time() * 1000))

    async def on_update(self, upd: dict[str, Any]) -> None:
        m = upd["market"]
        if m in self._buffers:
            self._buffers[m].append(upd)
            return
        ob = self.books.get(m)
        if ob is None or not ob.synced:
            await self.resync(m)
            return
        try:
            ob.apply_update(upd, int(time.time() * 1000))
        except BookSequenceError as e:
            log.warning("rupture de séquence carnet", extra={"data": {"market": m, "err": str(e)}})
            await self.resync(m)


class BitvavoWS:
    def __init__(self, url: str, on_event: Callable[[dict[str, Any]], Awaitable[None]],
                 heartbeat_s: float = 30.0) -> None:
        self.url = url
        self.on_event = on_event
        self.heartbeat_s = heartbeat_s
        self.subscriptions: list[dict[str, Any]] = []
        self.connected = False
        self.last_msg_ms = 0
        self._stop = asyncio.Event()

    def subscribe(self, name: str, markets: list[str], **extra: Any) -> None:
        ch: dict[str, Any] = {"name": name, "markets": markets}
        ch.update(extra)
        self.subscriptions.append(ch)

    async def run_forever(self) -> None:
        import websockets

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=self.heartbeat_s, ping_timeout=10) as ws:
                    self.connected = True
                    backoff = 1.0
                    if self.subscriptions:
                        await ws.send(json.dumps({"action": "subscribe", "channels": self.subscriptions}))
                    async for raw in ws:
                        self.last_msg_ms = int(time.time() * 1000)
                        msg = json.loads(raw)
                        await self.on_event(msg)
            except Exception as e:  # noqa: BLE001 — reconnexion sur toute erreur
                log.warning("WS déconnecté", extra={"data": {"err": repr(e), "backoff": backoff}})
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    def stop(self) -> None:
        self._stop.set()

    def is_stale(self, max_silence_ms: int) -> bool:
        return not self.connected or int(time.time() * 1000) - self.last_msg_ms > max_silence_ms
