"""Protections natives (stop-loss hébergés), pannes pendant la modification, ventes dupliquées,
fonds réservés par les ordres conditionnels, exécutions partielles. Échange = SIMULATEUR (pas Bitvavo)."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.execution.engine import OrderRequest
from backend.execution.protection import ProtectionManager
from backend.market_data.models import MarketInfo
from tests.conftest import BTC_RAW

QTY = D("0.0008")


@pytest.fixture
def btc_tp() -> MarketInfo:
    return MarketInfo.from_bitvavo({**BTC_RAW, "orderTypes": ["market", "limit", "stopLoss", "stopLossLimit",
                                                              "takeProfit", "takeProfitLimit"]})


@pytest.fixture
def held(sim, engine):
    """Position de QTY BTC détenue (achetée hors moteur pour isoler la protection)."""
    sim.available["BTC"] = QTY
    sim.last_trade["BTC-EUR"] = D(50000)
    return QTY


@pytest.fixture
def pm(engine):
    return ProtectionManager(engine)


async def test_stop_protected_only_after_exchange_confirmation(pm, sim, held, engine):
    st0 = await pm.status("BTC-EUR", held)
    assert not st0.protected and st0.state == "NONE"
    st = await pm.ensure("BTC-EUR", held, D(48000))
    assert st.protected and st.state == "CONFIRMED" and st.trigger == D(48000)
    # base réservée par l'ordre conditionnel : plus rien de disponible pour une autre vente
    assert sim.available["BTC"] == 0 and sim.in_order["BTC"] == held


async def test_stop_not_protected_while_submission_ambiguous(pm, sim, held, engine, clock):
    sim.fail_next = "timeout_before_accept"
    st = await pm.ensure("BTC-EUR", held, D(48000))
    assert not st.protected and st.state == "PENDING"
    clock.advance(16_000)
    await engine.recover()  # NOT_PLACED après expiration de la fenêtre
    engine.g.resume("t", {"ok": True})
    st = await pm.ensure("BTC-EUR", held, D(48000))  # nouvelle pose, cette fois confirmée
    assert st.protected


async def test_stop_trigger_rounded_up_never_widened(pm, sim, held):
    st = await pm.ensure("BTC-EUR", held, D("48000.4"))  # 5 chiffres significatifs -> 48001, pas 48000
    assert st.trigger == D(48001)


async def test_raise_stop_in_place(pm, sim, held):
    await pm.ensure("BTC-EUR", held, D(48000))
    st = await pm.ensure("BTC-EUR", held, D(49000))
    assert st.protected and st.trigger == D(49000)
    assert sim.update_calls == 1 and sim.create_calls == 1  # modification en place, pas d'annuler/recréer


async def test_stop_never_lowered(pm, sim, held):
    await pm.ensure("BTC-EUR", held, D(49000))
    st = await pm.ensure("BTC-EUR", held, D(45000))
    assert st.trigger == D(49000) and sim.update_calls == 0


async def test_crash_during_stop_modification_before_applied(pm, sim, held):
    await pm.ensure("BTC-EUR", held, D(48000))
    sim.fail_next_update = "timeout_before"
    st = await pm.ensure("BTC-EUR", held, D(49000))
    # la relecture montre l'ANCIEN déclencheur : toujours protégé, au niveau confirmé par l'échange
    assert st.protected and st.trigger == D(48000)


async def test_crash_during_stop_modification_after_applied(pm, sim, held):
    await pm.ensure("BTC-EUR", held, D(48000))
    sim.fail_next_update = "timeout_after"
    st = await pm.ensure("BTC-EUR", held, D(49000))
    assert st.protected and st.trigger == D(49000)


async def test_modification_outcome_unknown_means_not_protected(pm, sim, held, engine):
    await pm.ensure("BTC-EUR", held, D(48000))
    real_get, real_update = sim.get_order, sim.update_order

    async def down(*a, **k):
        from backend.market_data.bitvavo_rest import OrderStatusUnknown
        raise OrderStatusUnknown("échange injoignable")

    async def update_then_network_down(body):
        sim.get_order = down  # le réseau tombe juste après l'envoi de la modification
        sim.fail_next_update = "timeout_after"
        return await real_update(body)

    sim.update_order = update_then_network_down
    row = engine.db.one("SELECT id FROM intents WHERE order_type='stopLoss'")
    assert await engine.update_trigger(row["id"], D(49000)) == "unknown"
    sim.get_order = real_get  # réseau revenu
    st = await pm.status("BTC-EUR", held)
    assert not st.protected and st.state == "UNCERTAIN" and "modification en cours" in st.reason
    sim.update_order = real_update
    assert await engine.update_trigger(row["id"], D(49000)) == "updated"  # même valeur absolue : idempotent
    st = await pm.status("BTC-EUR", held)
    assert st.protected and st.trigger == D(49000)


async def test_stop_execution_has_no_guaranteed_price(pm, sim, held, engine, db):
    await pm.ensure("BTC-EUR", held, D(48000))
    # krach : le carnet saute sous le déclencheur
    sim.set_book("BTC-EUR", [(D(45000), D("0.0005")), (D(44000), D(1))], [(D(45100), D(1))])
    sim.check_triggers("BTC-EUR")
    sim.last_trade["BTC-EUR"] = D(45000)
    sim.check_triggers("BTC-EUR")
    await engine.refresh_open()
    fills = db.q("SELECT * FROM fills WHERE side='sell'")
    assert fills and all(D(f["price"]) < D(48000) for f in fills)  # glissement : exécuté SOUS le stop
    assert sum(D(f["amount"]) for f in fills) == held


async def test_stop_limit_can_remain_unfilled_in_gap(engine, sim, held):
    pm = ProtectionManager(engine, order_type="stopLossLimit")
    await pm.ensure("BTC-EUR", held, D(48000))  # limite à 98 % du déclencheur
    sim.set_book("BTC-EUR", [(D(40000), D(1))], [(D(40100), D(1))])
    sim.last_trade["BTC-EUR"] = D(40000)
    sim.check_triggers("BTC-EUR")
    st = await pm.status("BTC-EUR", held)
    assert not st.protected and st.state == "TRIGGERED"  # déclenché mais NON exécuté : la position reste exposée
    assert sim.available.get("BTC", D(0)) + sim.in_order["BTC"] == held


async def test_duplicate_sell_blocked_while_stop_active(pm, engine, held):
    await pm.ensure("BTC-EUR", held, D(48000))
    r = await engine.submit(OrderRequest("BTC-EUR", "sell", "market", amount=held, is_entry=False, is_protective=True,
                                         position_qty=held), D(0))
    assert not r.accepted and any("dupliquée" in x for x in r.reasons)


async def test_two_exit_sells_second_vetoed(engine, sim, held):
    sim.set_book("BTC-EUR", [(D(49990), D("0.0001"))], [(D(50010), D(1))])  # carnet mince : vente partielle possible
    req = dict(is_entry=False, is_protective=True, position_qty=held)
    r1 = await engine.submit(OrderRequest("BTC-EUR", "sell", "limit", amount=held, price=D(50500), **req), D(0))
    assert r1.accepted and r1.status == "new"
    r2 = await engine.submit(OrderRequest("BTC-EUR", "sell", "market", amount=held, **req), D(0))
    assert not r2.accepted and sim.create_calls == 1


async def test_sell_without_position_qty_refused(engine, held):
    r = await engine.submit(OrderRequest("BTC-EUR", "sell", "market", amount=held, is_entry=False), D(0))
    assert not r.accepted


async def test_exit_cancels_stop_first_then_sells(pm, engine, sim, held, db):
    await pm.ensure("BTC-EUR", held, D(48000))
    ok, _ = await pm.release_for_exit("BTC-EUR")
    assert ok and sim.in_order["BTC"] == 0 and sim.available["BTC"] == held
    r = await engine.submit(OrderRequest("BTC-EUR", "sell", "market", amount=held, is_entry=False, is_protective=True,
                                         position_qty=held, ref_price=D(49990)), D(0))
    assert r.accepted and r.status == "filled"


async def test_exit_race_stop_fills_during_cancel(pm, engine, sim, held):
    await pm.ensure("BTC-EUR", held, D(48000))
    real_cancel = sim.cancel_order

    async def cancel_after_trigger(market, order_id, operator_id):
        sim.set_book("BTC-EUR", [(D(47000), D(1))], [(D(47100), D(1))])  # référence = meilleur bid
        sim.check_triggers("BTC-EUR")  # le stop s'exécute juste avant l'annulation
        return await real_cancel(market, order_id, operator_id)

    sim.cancel_order = cancel_after_trigger
    ok, why = await pm.release_for_exit("BTC-EUR")
    assert not ok and "déjà vendue" in why  # aucune seconde vente


async def test_cancel_timeout_then_reread(pm, engine, sim, held):
    await pm.ensure("BTC-EUR", held, D(48000))
    sim.fail_next_cancel = "timeout_after"
    ok, _ = await pm.release_for_exit("BTC-EUR")
    assert ok  # la relecture tranche : annulé


async def test_partial_stop_quantity_is_reprotected(pm, engine, sim, held):
    await pm.ensure("BTC-EUR", held, D(48000))
    sim.available["BTC"] = sim.available.get("BTC", D(0)) + D("0.0005")  # achat complémentaire
    bigger = held + D("0.0005")
    st = await pm.status("BTC-EUR", bigger)
    assert not st.protected and st.state == "PARTIAL"
    st = await pm.ensure("BTC-EUR", bigger, D(48000))
    assert st.protected and st.covered_qty == bigger


async def test_position_below_minimum_cannot_be_protected(pm, sim):
    sim.available["BTC"] = D("0.00005")
    st = await pm.ensure("BTC-EUR", D("0.00005"), D(48000))
    assert not st.protected and "minimums" in st.reason


async def test_take_profit_native(db, guardian, clock, btc_tp):
    from backend.execution.engine import ExecutionEngine
    from backend.execution.simulator import SimulatedExchange
    sim = SimulatedExchange([btc_tp], {"EUR": D(0), "BTC": QTY}, now_ms=clock.now_ms)
    sim.set_book("BTC-EUR", [(D(55000), D(1))], [(D(55010), D(1))])
    ex = ExecutionEngine(db, sim, guardian, {"BTC-EUR": btc_tp}, "simulation", 1, clock.now_ms)
    r = await ex.submit(OrderRequest("BTC-EUR", "sell", "takeProfit", amount=QTY, trigger_amount=D(54000),
                                     is_entry=False, is_protective=True, position_qty=QTY), D(0))
    assert r.status == "awaitingTrigger"
    sim.last_trade["BTC-EUR"] = D(54500)
    sim.check_triggers("BTC-EUR")
    await ex.refresh_open()
    assert db.one("SELECT status FROM intents WHERE id=?", (r.intent_id,))["status"] == "filled"


async def test_conditional_buy_refused(engine, held):
    r = await engine.submit(OrderRequest("BTC-EUR", "buy", "stopLoss", amount=held, trigger_amount=D(51000)), D(100))
    assert not r.accepted
