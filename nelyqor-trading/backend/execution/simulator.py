"""Échange simulé au format des réponses Bitvavo (paper trading et tests).

Hypothèses de simulation (prudentes, documentées) :
- frais en EUR (devise de cotation), taker sur tout remplissage immédiat, maker sur ordre au repos ;
- un achat au marché en `amountQuote` inclut la commission dans le montant dépensé ;
- les remplissages balayent la profondeur du carnet (glissement réel du carnet simulé) ;
- un ordre limité au repos n'est rempli que si le prix TRAVERSE strictement sa limite (pas au toucher),
  et seulement pour une fraction `rest_fill_fraction` du volume échangé à ce prix ;
- `postOnly` qui croiserait le carnet est annulé (canceledPostOnly) ;
- injection de pannes : timeout après acceptation, refus, latence.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core.num import ZERO, D, fmt
from ..market_data.bitvavo_rest import BitvavoError, OrderStatusUnknown
from ..market_data.models import MarketInfo, OrderBook


@dataclass
class SimOrder:
    order_id: str
    client_order_id: str | None
    market: str
    side: str
    order_type: str
    amount: Decimal | None
    amount_quote: Decimal | None
    price: Decimal | None
    post_only: bool
    status: str = "new"
    filled_amount: Decimal = ZERO
    filled_quote: Decimal = ZERO
    fee_paid: Decimal = ZERO
    fills: list[dict[str, Any]] = field(default_factory=list)
    created: int = 0
    reserved_quote: Decimal = ZERO
    reserved_base: Decimal = ZERO

    def to_api(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "orderId": self.order_id, "market": self.market, "side": self.side, "orderType": self.order_type,
            "status": self.status, "filledAmount": fmt(self.filled_amount), "filledAmountQuote": fmt(self.filled_quote),
            "feePaid": fmt(self.fee_paid), "feeCurrency": "EUR", "fills": list(self.fills), "created": self.created,
            "postOnly": self.post_only, "updated": self.created,
        }
        if self.client_order_id:
            d["clientOrderId"] = self.client_order_id
        if self.amount is not None:
            d["amount"] = fmt(self.amount)
            d["amountRemaining"] = fmt(self.amount - self.filled_amount)
        if self.amount_quote is not None:
            d["amountQuote"] = fmt(self.amount_quote)
        if self.price is not None:
            d["price"] = fmt(self.price)
        return d


class SimulatedExchange:
    def __init__(self, markets: list[MarketInfo], balances: dict[str, Decimal], maker_fee: Decimal = D("0.0015"),
                 taker_fee: Decimal = D("0.0025"), now_ms: Any = None, rest_fill_fraction: Decimal = D("0.5")) -> None:
        self.markets = {m.market: m for m in markets}
        self.available: dict[str, Decimal] = {k: D(v) for k, v in balances.items()}
        self.in_order: dict[str, Decimal] = {}
        self.books: dict[str, OrderBook] = {}
        self.orders: dict[str, SimOrder] = {}
        self.by_client: dict[str, str] = {}
        self.maker_fee, self.taker_fee = maker_fee, taker_fee
        self._now = now_ms or (lambda: 0)
        self.rest_fill_fraction = rest_fill_fraction
        self.fail_next: str | None = None  # "timeout_after_accept" | "timeout_before_accept" | "reject"
        self.create_calls = 0
        self.withdraw_called = False

    # --- helpers
    def set_book(self, market: str, bids: list[tuple[Any, Any]], asks: list[tuple[Any, Any]], nonce: int = 1) -> None:
        ob = OrderBook(market)
        ob.load_snapshot({"nonce": nonce, "bids": [[str(p), str(a)] for p, a in bids],
                          "asks": [[str(p), str(a)] for p, a in asks]}, self._now())
        self.books[market] = ob

    def _bal(self, sym: str) -> Decimal:
        return self.available.get(sym, ZERO)

    def _move(self, sym: str, delta: Decimal) -> None:
        self.available[sym] = self._bal(sym) + delta
        if self.available[sym] < -D("1e-12"):
            raise AssertionError(f"solde négatif {sym}")

    def _reserve(self, sym: str, amt: Decimal) -> None:
        self._move(sym, -amt)
        self.in_order[sym] = self.in_order.get(sym, ZERO) + amt

    def _release(self, sym: str, amt: Decimal) -> None:
        self.in_order[sym] = self.in_order.get(sym, ZERO) - amt
        self._move(sym, amt)

    def _fill(self, o: SimOrder, amount: Decimal, price: Decimal, taker: bool) -> None:
        m = self.markets[o.market]
        rate = self.taker_fee if taker else self.maker_fee
        quote = amount * price
        fee = quote * rate
        if o.side == "buy":
            cost = quote + fee
            from_res = min(o.reserved_quote, cost)
            o.reserved_quote -= from_res
            self.in_order[m.quote] = self.in_order.get(m.quote, ZERO) - from_res
            if cost > from_res:
                self._move(m.quote, -(cost - from_res))
            self._move(m.base, amount)
        else:
            from_res = min(o.reserved_base, amount)
            o.reserved_base -= from_res
            self.in_order[m.base] = self.in_order.get(m.base, ZERO) - from_res
            if amount > from_res:
                self._move(m.base, -(amount - from_res))
            self._move(m.quote, quote - fee)
        o.filled_amount += amount
        o.filled_quote += quote
        o.fee_paid += fee
        o.fills.append({"id": str(uuid.uuid4()), "timestamp": self._now(), "amount": fmt(amount), "price": fmt(price),
                        "taker": taker, "fee": fmt(fee), "feeCurrency": m.quote, "settled": True})

    def _sweep(self, o: SimOrder, limit: Decimal | None) -> None:
        ob = self.books.get(o.market)
        if ob is None:
            return
        m = self.markets[o.market]
        levels = ob.levels("asks" if o.side == "buy" else "bids", 1000)
        for price, avail in levels:
            if limit is not None and ((o.side == "buy" and price > limit) or (o.side == "sell" and price < limit)):
                break
            if o.amount is not None:
                remaining = o.amount - o.filled_amount
            else:
                assert o.amount_quote is not None
                remaining = m.round_amount((o.amount_quote - o.filled_quote * (1 + self.taker_fee)) /
                                           (price * (1 + self.taker_fee)))
            take = m.round_amount(min(avail, remaining))
            if take <= 0:
                break
            self._fill(o, take, price, taker=True)
            book = ob.asks if o.side == "buy" else ob.bids
            book[price] = avail - take
            if book[price] <= 0:
                del book[price]

    # --- API compatible BitvavoREST
    async def get_markets(self) -> list[dict[str, Any]]:
        return [m.raw or {"market": m.market, "status": m.status} for m in self.markets.values()]

    async def balance(self) -> list[dict[str, Any]]:
        syms = set(self.available) | set(self.in_order)
        return [{"symbol": s, "available": fmt(self._bal(s)), "inOrder": fmt(self.in_order.get(s, ZERO))} for s in sorted(syms)]

    async def create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        self.create_calls += 1
        mode, self.fail_next = self.fail_next, None
        if mode == "timeout_before_accept":
            raise OrderStatusUnknown("timeout simulé avant acceptation")
        if mode == "reject":
            raise BitvavoError(400, 216, "Insufficient balance (simulé)")
        if "operatorId" not in body:
            raise BitvavoError(400, 203, "operatorId manquant")
        m = self.markets.get(body["market"])
        if m is None or not m.is_trading:
            raise BitvavoError(400, 205, "market not available")
        cid = body.get("clientOrderId")
        if cid and cid in self.by_client:
            raise BitvavoError(400, 207, "clientOrderId déjà utilisé")
        side, otype = body["side"], body["orderType"]
        if otype not in m.order_types:
            raise BitvavoError(400, 205, f"orderType {otype} indisponible")
        amount = D(body["amount"]) if "amount" in body else None
        amount_q = D(body["amountQuote"]) if "amountQuote" in body else None
        price = D(body["price"]) if "price" in body else None
        post_only = bool(body.get("postOnly", False))
        ob = self.books.get(m.market)
        ref = price if price is not None else (ob.best_ask() if side == "buy" else ob.best_bid()) if ob else None
        if ref is None:
            raise BitvavoError(400, 205, "pas de prix de référence")
        notional = amount_q if amount_q is not None else (amount or ZERO) * ref
        if notional < m.min_order_quote or (amount is not None and amount < m.min_order_base):
            raise BitvavoError(400, 217, "Minimum order size not met")
        o = SimOrder(str(uuid.uuid4()), cid, m.market, side, otype, amount, amount_q, price, post_only,
                     created=self._now())
        # réservation
        if side == "buy":
            need = (amount_q if amount_q is not None else notional * (1 + self.taker_fee))
            if self._bal(m.quote) < need:
                raise BitvavoError(400, 216, "Insufficient balance")
            o.reserved_quote = need
            self._reserve(m.quote, need)
        else:
            need_b = amount if amount is not None else (amount_q or ZERO) / ref
            if self._bal(m.base) < need_b:
                raise BitvavoError(400, 216, "Insufficient balance")
            o.reserved_base = need_b
            self._reserve(m.base, need_b)
        self.orders[o.order_id] = o
        if cid:
            self.by_client[cid] = o.order_id
        if otype == "market":
            self._sweep(o, None)
            self._finish_or_cancel_remaining(o, final=True)
        elif otype == "limit":
            crosses = ob is not None and price is not None and (
                (side == "buy" and ob.best_ask() is not None and price >= ob.best_ask())  # type: ignore[operator]
                or (side == "sell" and ob.best_bid() is not None and price <= ob.best_bid()))  # type: ignore[operator]
            if post_only and crosses:
                o.status = "canceledPostOnly"
                self._release_all(o)
            else:
                if crosses:
                    self._sweep(o, price)
                self._finish_or_cancel_remaining(o, final=False)
        else:
            o.status = "awaitingTrigger"
        if mode == "timeout_after_accept":
            raise OrderStatusUnknown("timeout simulé après acceptation")
        return o.to_api()

    def _release_all(self, o: SimOrder) -> None:
        m = self.markets[o.market]
        if o.reserved_quote > 0:
            self._release(m.quote, o.reserved_quote)
            o.reserved_quote = ZERO
        if o.reserved_base > 0:
            self._release(m.base, o.reserved_base)
            o.reserved_base = ZERO

    def _finish_or_cancel_remaining(self, o: SimOrder, final: bool) -> None:
        done = (o.amount is not None and o.filled_amount >= o.amount) or (
            o.amount_quote is not None and o.filled_quote * (1 + self.taker_fee) >= o.amount_quote * D("0.999"))
        if done:
            o.status = "filled"
            self._release_all(o)
        elif final:
            o.status = "filled" if o.filled_amount > 0 else "canceledMarketProtection"
            self._release_all(o)
        else:
            o.status = "partiallyFilled" if o.filled_amount > 0 else "new"

    async def get_order(self, market: str, order_id: str | None = None, client_order_id: str | None = None
                        ) -> dict[str, Any]:
        oid = order_id or (self.by_client.get(client_order_id) if client_order_id else None)
        if not oid or oid not in self.orders:
            raise BitvavoError(404, 240, "No order found")
        return self.orders[oid].to_api()

    async def open_orders(self, market: str | None = None) -> list[dict[str, Any]]:
        return [o.to_api() for o in self.orders.values() if o.status in ("new", "partiallyFilled", "awaitingTrigger")
                and (market is None or o.market == market)]

    async def cancel_order(self, market: str, order_id: str, operator_id: int) -> dict[str, Any]:
        o = self.orders.get(order_id)
        if o is None:
            raise BitvavoError(404, 240, "No order found")
        if o.status not in ("new", "partiallyFilled", "awaitingTrigger"):
            raise BitvavoError(400, 240, f"Order already {o.status}")
        o.status = "canceled"
        self._release_all(o)
        return {"orderId": order_id}

    async def trades(self, market: str, limit: int = 500, start: int | None = None) -> list[dict[str, Any]]:
        out = []
        for o in self.orders.values():
            if o.market != market:
                continue
            for f in o.fills:
                out.append({**f, "orderId": o.order_id, "clientOrderId": o.client_order_id, "market": market,
                            "side": o.side})
        return out[-limit:]

    async def withdraw(self, *a: Any, **k: Any) -> None:  # pragma: no cover - doit rester inaccessible
        self.withdraw_called = True
        raise PermissionError("Retraits interdits")

    def on_trade(self, market: str, price: Decimal, volume: Decimal) -> None:
        """Simule un trade de marché : remplit les ordres limités au repos traversés strictement."""
        for o in list(self.orders.values()):
            if o.market != market or o.status not in ("new", "partiallyFilled") or o.price is None:
                continue
            if (o.side == "buy" and price < o.price) or (o.side == "sell" and price > o.price):
                assert o.amount is not None
                rem = o.amount - o.filled_amount
                take = self.markets[market].round_amount(min(rem, volume * self.rest_fill_fraction))
                if take > 0:
                    self._fill(o, take, o.price, taker=False)
                    self._finish_or_cancel_remaining(o, final=False)

    def external_deposit(self, sym: str, amount: Decimal) -> None:
        self._move(sym, amount)
