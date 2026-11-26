"""Faux Bitvavo public (et privé minimal) pour les tests d'intégration du moteur. Données SYNTHÉTIQUES."""
from __future__ import annotations

from typing import Any

from backend.research.synthetic import synthetic_candles

from .conftest import BTC_RAW


class FakePublic:
    def __init__(self, clock: Any, n: int = 600, seed: int = 3) -> None:
        self.clock = clock
        self.df = synthetic_candles(n, seed, start_ms=clock.now_ms() - n * 3_600_000)
        self.fail = False
        self.markets_raw = [BTC_RAW, {**BTC_RAW, "market": "ETH-EUR", "base": "ETH"},
                            {**BTC_RAW, "market": "DEAD-EUR", "base": "DEAD", "status": "halted"}]

    def _px(self) -> float:
        return float(self.df["close"].iloc[-1])

    async def time(self) -> int:
        self._maybe_fail()
        return int(self.clock.now_ms()) + int(getattr(self, "skew_ms", 0))

    async def markets(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        return self.markets_raw

    async def ticker_book(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        p = self._px()
        return [{"market": m["market"], "bid": f"{p * 0.9999:.2f}", "bidSize": "2", "ask": f"{p * 1.0001:.2f}",
                 "askSize": "2", "timestamp": self.clock.now_ms()} for m in self.markets_raw]

    async def ticker_24h(self) -> list[dict[str, Any]]:
        self._maybe_fail()
        return [{"market": m["market"], "last": f"{self._px():.2f}", "volumeQuote": "5000000"} for m in self.markets_raw]

    async def candles(self, market: str, interval: str, limit: int = 500, **k: Any) -> list[list[Any]]:
        self._maybe_fail()
        d = self.df.tail(limit)
        return [[int(ts), str(r.open), str(r.high), str(r.low), str(r.close), str(r.volume)] for ts, r in d.iterrows()]

    def _maybe_fail(self) -> None:
        if self.fail:
            import httpx

            raise httpx.ConnectError("réseau coupé (simulé)")
