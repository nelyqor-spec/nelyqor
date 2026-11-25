"""Conformité du simulateur avec les exemples de réponses des SDK OFFICIELS Bitvavo (node-bitvavo-api,
README, 2025) : champs d'un ordre, d'un fill REST, des frais. (Champs relevés dans la documentation des SDK ;
le format réel reste à confirmer en paper trading sur le serveur.)"""
from __future__ import annotations

from decimal import Decimal as D

ORDER_FIELDS_SDK = {"orderId", "market", "created", "updated", "status", "side", "orderType", "amount",
                    "amountRemaining", "price", "onHold", "onHoldCurrency", "filledAmount", "filledAmountQuote",
                    "feePaid", "feeCurrency", "fills", "selfTradePrevention", "visible", "timeInForce", "postOnly",
                    "clientOrderId"}
REST_FILL_FIELDS_SDK = {"id", "timestamp", "amount", "price", "taker", "fee", "feeCurrency", "settled"}
TRADE_FIELDS_SDK = {"id", "timestamp", "market", "side", "amount", "price", "taker", "fee", "feeCurrency", "settled",
                    "clientOrderId"}


async def test_order_and_fill_shapes(sim):
    o = await sim.create_order({"market": "BTC-EUR", "side": "buy", "orderType": "limit", "amount": "0.0002",
                                "price": "49000", "clientOrderId": "c1", "operatorId": 1})
    assert ORDER_FIELDS_SDK <= set(o), ORDER_FIELDS_SDK - set(o)
    assert D(o["onHold"]) == D("0.0002") * 49000 * D("1.0025")  # réservation frais inclus, comme dans le SDK
    m = await sim.create_order({"market": "BTC-EUR", "side": "buy", "orderType": "market", "amountQuote": "20",
                                "clientOrderId": "c2", "operatorId": 1})
    assert all(REST_FILL_FIELDS_SDK <= set(f) for f in m["fills"])
    trades = await sim.trades("BTC-EUR")
    assert all(TRADE_FIELDS_SDK <= set(t) for t in trades)


async def test_fee_response_shape(sim):
    acc = await sim.account()
    assert {"taker", "maker", "volume"} <= set(acc["fees"])
