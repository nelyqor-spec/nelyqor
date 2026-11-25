"""Ordres : minimums, précision, fonds insuffisants, post-only, fill partiel, timeout après acceptation,
aucune répétition aveugle, annulation/fill concurrent."""
from __future__ import annotations

from decimal import Decimal as D

from backend.execution.engine import OrderRequest
from backend.guardian.guardian import IntentCheck, State
from backend.market_data.models import MarketInfo
from tests.conftest import BTC_RAW


def chk(amount: str, entry: bool = True, avail: str = "100", **kw):
    base = dict(market="BTC-EUR", side="buy", order_type="market", amount_quote=D(amount), is_entry=entry,
                spread_bps=D(4), min_order_quote=D(5), available_quote=D(avail), equity=D(100),
                exposure_after=D(amount), position_after=D(amount))
    base.update(kw)
    return IntentCheck(**base)


async def test_market_buy_fills_and_records_fees(engine, sim, db):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("30"), check=chk("30")), D(100))
    assert r.accepted and r.status == "filled"
    fills = db.q("SELECT * FROM fills")
    assert len(fills) == 2  # balaye 2 niveaux : 0.0005 @50010 puis le reste @50020
    assert {f["price"] for f in fills} == {"50010", "50020"}
    assert all(D(f["fee"]) > 0 and f["fee_currency"] == "EUR" for f in fills)
    assert sim.available["EUR"] >= D("70") - D("0.01")
    assert sim.create_calls == 1


async def test_minimum_refused(engine, sim):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("4.99"), check=chk("4.99")), D(100))
    assert not r.accepted and r.status == "VETOED"
    assert any("minimum" in x for x in r.reasons)
    assert sim.create_calls == 0


def test_precision_rounds_down():
    m = MarketInfo.from_bitvavo(BTC_RAW)
    assert m.round_amount(D("0.123456789")) == D("0.12345678")
    assert m.round_price(D("50017.9")) == D("50017")
    assert m.round_quote(D("10.019")) == D("10.01")
    m2 = MarketInfo.from_bitvavo({**BTC_RAW, "pricePrecision": None, "tickSize": "0.5"})
    assert m2.round_price(D("100.74")) == D("100.5")


async def test_insufficient_funds_vetoed(engine, sim):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("30"),
                                         check=chk("30", avail="10")), D(10))
    assert not r.accepted and "fonds insuffisants" in r.reasons


async def test_post_only_crossing_is_canceled(engine, sim, db):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "limit", amount=D("0.0002"), price=D("50015"),
                                         post_only=True, check=chk("10")), D(100))
    assert r.status == "canceledPostOnly"
    assert sim.available["EUR"] == D("100")  # réservation libérée
    assert db.q("SELECT * FROM fills") == []
    row = db.one("SELECT reserved_quote FROM intents WHERE id=?", (r.intent_id,))
    assert row["reserved_quote"] == "0"


async def test_partial_fill_then_complete(engine, sim, db):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "limit", amount=D("0.0004"), price=D("49995"),
                                         post_only=True, check=chk("20")), D(100))
    assert r.status == "new"
    sim.on_trade("BTC-EUR", D("49990"), D("0.0004"))  # moitié du volume -> 0.0002
    await engine.refresh_open()
    row = db.one("SELECT status FROM intents WHERE id=?", (r.intent_id,))
    assert row["status"] == "partiallyFilled"
    sim.on_trade("BTC-EUR", D("49990"), D("0.001"))
    await engine.refresh_open()
    assert db.one("SELECT status FROM intents WHERE id=?", (r.intent_id,))["status"] == "filled"
    fills = db.q("SELECT * FROM fills WHERE intent_id=?", (r.intent_id,))
    assert sum(D(f["amount"]) for f in fills) == D("0.0004")
    assert all(f["taker"] == 0 for f in fills)  # maker


async def test_touch_does_not_fill_limit(engine, sim):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "limit", amount=D("0.0002"), price=D("49995"),
                                         post_only=True, check=chk("10")), D(100))
    sim.on_trade("BTC-EUR", D("49995"), D("1"))  # toucher n'est pas traverser
    await engine.refresh_open()
    assert sim.orders[sim.by_client[engine.db.one("SELECT client_order_id c FROM intents")["c"]]].status == "new"
    assert r.accepted


async def test_timeout_after_accept_no_blind_resubmit(engine, sim, db):
    sim.fail_next = "timeout_after_accept"
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("20"), check=chk("20")), D(100))
    # l'ordre a été retrouvé par clientOrderId, sans nouvelle soumission
    assert sim.create_calls == 1
    assert r.status == "filled"
    assert len(db.q("SELECT * FROM fills")) >= 1


async def test_timeout_before_accept_pauses_and_never_resubmits(engine, sim, db, guardian, clock):
    sim.fail_next = "timeout_before_accept"
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("20"), check=chk("20")), D(100))
    # Fenêtre d'accès non expirée : l'ordre pourrait encore apparaître -> UNKNOWN, rien n'est libéré
    assert r.status == "UNKNOWN" and not r.accepted
    assert sim.create_calls == 1
    assert guardian.state == State.PAUSE  # vérification requise avant reprise
    assert engine.reserved_quote() > 0
    clock.advance(16_000)  # fenêtre d'accès (10 s) + marge expirée
    assert await engine.recover() == ["NOT_PLACED"]
    assert engine.reserved_quote() == 0
    r2 = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("20"), check=chk("20")), D(100))
    assert not r2.accepted and sim.create_calls == 1


async def test_unknown_status_blocks_new_orders(engine, sim, db, guardian):
    async def boom(*a, **k):
        from backend.market_data.bitvavo_rest import OrderStatusUnknown
        raise OrderStatusUnknown("réseau")

    sim.fail_next = "timeout_after_accept"
    sim.get_order = boom  # la recherche échoue aussi : ambiguïté persistante
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("20"), check=chk("20")), D(100))
    assert r.status == "UNKNOWN"
    assert guardian.state == State.PAUSE
    r2 = await engine.submit(OrderRequest("BTC-EUR", "sell", "market", amount=D("0.0001"), is_entry=False,
                                          check=chk("5", entry=False, side="sell")), D(100))
    assert not r2.accepted and "inconnu" in r2.reasons[0]
    assert sim.create_calls == 1


async def test_cancel_fill_race(engine, sim, db):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "limit", amount=D("0.0002"), price=D("49995"),
                                         post_only=True, check=chk("10")), D(100))
    sim.on_trade("BTC-EUR", D("49000"), D("1"))  # rempli avant que l'annulation n'arrive
    status = await engine.cancel(r.intent_id)
    assert status == "filled"
    assert len(db.q("SELECT * FROM fills WHERE intent_id=?", (r.intent_id,))) == 1


async def test_fills_deduplicated(engine, sim, db):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("20"), check=chk("20")), D(100))
    n = len(db.q("SELECT * FROM fills"))
    engine._apply_order(r.intent_id, r.order)
    engine._apply_order(r.intent_id, r.order)
    assert len(db.q("SELECT * FROM fills")) == n


async def test_operator_and_client_id_sent(engine, sim):
    seen = {}
    orig = sim.create_order

    async def spy(body):
        seen.update(body)
        return await orig(body)

    sim.create_order = spy
    await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("20"), check=chk("20")), D(100))
    assert seen["operatorId"] == 42 and len(seen["clientOrderId"]) == 36


async def test_rejected_releases_reservation(engine, sim, db):
    sim.fail_next = "reject"
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "limit", amount=D("0.0002"), price=D("49000"),
                                         check=chk("10")), D(100))
    assert r.status == "rejected"
    assert engine.reserved_quote() == 0


async def test_reservation_counts_open_orders(engine, sim):
    await engine.submit(OrderRequest("BTC-EUR", "buy", "limit", amount=D("0.0015"), price=D("49000"),
                                     post_only=True, check=chk("73.5", equity=D(1000))), D(100))
    assert engine.reserved_quote() > D("73")
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("30"),
                                         check=chk("30", equity=D(1000))), D(100))
    assert not r.accepted and "fonds insuffisants" in r.reasons


async def test_position_limit_vetoes(engine, sim):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D("40"), check=chk("40")), D(100))
    assert not r.accepted and "dépasse la limite par position" in r.reasons
