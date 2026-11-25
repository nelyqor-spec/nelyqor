"""Données : métadonnées marchés, carnet et resynchronisation, données obsolètes refusées, limites API, backoff."""
from __future__ import annotations

from decimal import Decimal as D

import httpx
import pytest

from backend.market_data.bitvavo_rest import BitvavoError, BitvavoREST, OrderStatusUnknown, RateBudget
from backend.market_data.bitvavo_ws import BookManager
from backend.market_data.models import BookSequenceError, MarketInfo, OrderBook
from backend.market_data.universe import MarketState, check_eligibility, diff_markets
from tests.conftest import BTC_RAW


def test_book_sequence_and_gap_detection():
    ob = OrderBook("BTC-EUR")
    ob.load_snapshot({"nonce": 10, "bids": [["100", "1"]], "asks": [["101", "2"]]}, 0)
    ob.apply_update({"nonce": 11, "bids": [["100", "0"], ["99", "3"]], "asks": []}, 1)
    assert ob.best_bid() == D(99) and ob.nonce == 11
    ob.apply_update({"nonce": 11, "bids": [["50", "1"]]}, 2)  # doublon ignoré
    assert ob.best_bid() == D(99)
    with pytest.raises(BookSequenceError):
        ob.apply_update({"nonce": 13, "bids": []}, 3)
    assert not ob.synced


class FakeRest:
    def __init__(self):
        self.calls = 0

    async def book(self, market, depth=100):
        self.calls += 1
        return {"nonce": 100, "bids": [["10", "1"]], "asks": [["11", "1"]]}


async def test_book_manager_resyncs_on_gap():
    fr = FakeRest()
    bm = BookManager(fr)  # type: ignore[arg-type]
    await bm.on_update({"market": "X-EUR", "nonce": 5})  # pas de carnet -> snapshot
    assert fr.calls == 1 and bm.books["X-EUR"].nonce == 100
    await bm.on_update({"market": "X-EUR", "nonce": 101, "bids": [["10.5", "1"]]})
    assert bm.books["X-EUR"].best_bid() == D("10.5")
    await bm.on_update({"market": "X-EUR", "nonce": 105})  # trou -> resync
    assert fr.calls == 2 and bm.resyncs == 2


def _state(**kw):
    st = MarketState(MarketInfo.from_bitvavo(BTC_RAW), best_bid=D(100), best_ask=D("100.05"),
                     quote_volume_24h=D(1_000_000), ts_received_ms=1000, ts_market_ms=1000)
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def test_stale_data_refused(limits):
    assert check_eligibility(_state(), limits, now_ms=2000).eligible
    el = check_eligibility(_state(), limits, now_ms=1000 + limits.max_stale_ms + 1)
    assert not el.eligible and "donnée périmée" in el.reasons


def test_illiquid_wide_crossed_halted_refused(limits):
    assert "liquidité insuffisante" in check_eligibility(_state(quote_volume_24h=D(10)), limits, 1500).reasons
    assert any("spread" in r for r in check_eligibility(_state(best_ask=D(110)), limits, 1500).reasons)
    assert "carnet croisé" in check_eligibility(_state(best_ask=D(99)), limits, 1500).reasons
    halted = _state()
    halted.info = MarketInfo.from_bitvavo({**BTC_RAW, "status": "halted"})
    assert not check_eligibility(halted, limits, 1500).eligible


def test_delisting_detected():
    a = {"A-EUR": MarketInfo.from_bitvavo({**BTC_RAW, "market": "A-EUR"}),
         "B-EUR": MarketInfo.from_bitvavo({**BTC_RAW, "market": "B-EUR"})}
    b = {"A-EUR": MarketInfo.from_bitvavo({**BTC_RAW, "market": "A-EUR", "status": "halted"}),
         "C-EUR": MarketInfo.from_bitvavo({**BTC_RAW, "market": "C-EUR"})}
    d = diff_markets(a, b)
    assert d == {"listed": ["C-EUR"], "delisted": ["B-EUR"], "status_changed": ["A-EUR"]}


def test_rate_budget():
    b = RateBudget(limit=1000, reserve=100)
    b.remaining, b.reset_at_ms = 120, 10_000
    assert b.wait_ms(25, now_ms=5000) == 5000
    assert b.wait_ms(10, now_ms=5000) == 0
    assert b.wait_ms(25, now_ms=11_000) == 0


async def test_get_retries_with_backoff_and_reads_headers(monkeypatch):
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    n = {"c": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["c"] += 1
        if n["c"] < 3:
            return httpx.Response(429, json={"errorCode": 110, "error": "rate"})
        return httpx.Response(200, json={"time": 123}, headers={"bitvavo-ratelimit-remaining": "990",
                                                                 "bitvavo-ratelimit-resetat": "1"})

    c = BitvavoREST("https://x/v2", transport=httpx.MockTransport(handler))
    assert await c.time() == 123 and n["c"] == 3 and c.budget.remaining == 990
    await c.aclose()


async def _nosleep(*a, **k):
    return None


async def test_post_timeout_raises_unknown_not_retried():
    n = {"c": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["c"] += 1
        raise httpx.ReadTimeout("t", request=req)

    c = BitvavoREST("https://x/v2", "k" * 10, "s" * 10, transport=httpx.MockTransport(handler))
    with pytest.raises(OrderStatusUnknown):
        await c.create_order({"market": "BTC-EUR"})
    assert n["c"] == 1  # pas de répétition aveugle d'un POST
    await c.aclose()


async def test_http_error_parsed():
    c = BitvavoREST("https://x/v2", transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"errorCode": 217, "error": "Minimum order size"})))
    with pytest.raises(BitvavoError) as e:
        await c.markets()
    assert e.value.code == 217
    await c.aclose()
