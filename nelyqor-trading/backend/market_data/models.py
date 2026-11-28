"""Modèles de données marché (métadonnées, bougies, carnet)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core.num import (
    ZERO,
    D,
    ceil_significant,
    ceil_to_step,
    floor_decimals,
    floor_significant,
    floor_to_step,
)


@dataclass(frozen=True)
class MarketInfo:
    market: str
    base: str
    quote: str
    status: str  # trading | halted | auction | ...
    min_order_base: Decimal
    min_order_quote: Decimal
    max_order_base: Decimal | None = None
    max_order_quote: Decimal | None = None
    price_precision: int | None = None  # chiffres significatifs (ancien format)
    tick_size: Decimal | None = None  # nouveau format
    quantity_decimals: int = 8
    notional_decimals: int = 2
    order_types: tuple[str, ...] = ("market", "limit")
    fee_category: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def from_bitvavo(cls, d: dict[str, Any]) -> MarketInfo:
        def opt(key: str) -> Decimal | None:
            v = d.get(key)
            return D(v) if v not in (None, "") else None

        return cls(
            market=d["market"],
            base=d.get("base", d["market"].split("-")[0]),
            quote=d.get("quote", d["market"].split("-")[1]),
            status=str(d.get("status", "unknown")),
            min_order_base=D(d.get("minOrderInBaseAsset", "0")),
            min_order_quote=D(d.get("minOrderInQuoteAsset", "0")),
            max_order_base=opt("maxOrderInBaseAsset"),
            max_order_quote=opt("maxOrderInQuoteAsset"),
            price_precision=int(d["pricePrecision"]) if d.get("pricePrecision") not in (None, "") else None,
            tick_size=opt("tickSize"),
            quantity_decimals=int(d.get("quantityDecimals", 8)),
            notional_decimals=int(d.get("notionalDecimals", 2)),
            order_types=tuple(d.get("orderTypes", ["market", "limit"])),
            fee_category=d.get("feeCategory"),
            raw=d,
        )

    @property
    def is_trading(self) -> bool:
        return self.status == "trading"

    def round_price(self, p: Decimal, up: bool = False) -> Decimal:
        """Vers le bas par défaut ; `up=True` pour les déclencheurs de stop de vente (ne jamais élargir un stop)."""
        if self.tick_size:
            return ceil_to_step(p, self.tick_size) if up else floor_to_step(p, self.tick_size)
        if self.price_precision:
            return ceil_significant(p, self.price_precision) if up else floor_significant(p, self.price_precision)
        return p

    def round_amount(self, a: Decimal) -> Decimal:
        """Toujours vers le bas : ne jamais dépasser un plafond de risque par arrondi."""
        return floor_decimals(a, self.quantity_decimals)

    def round_quote(self, q: Decimal) -> Decimal:
        return floor_decimals(q, self.notional_decimals)

    def supports(self, order_type: str) -> bool:
        return order_type in self.order_types


@dataclass(frozen=True)
class Candle:
    ts: int  # ouverture, ms UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def from_bitvavo(cls, row: list[Any]) -> Candle:
        return cls(int(row[0]), D(row[1]), D(row[2]), D(row[3]), D(row[4]), D(row[5]))


class BookSequenceError(Exception):
    pass


@dataclass
class OrderBook:
    """Carnet local maintenu par snapshot REST + mises à jour WS avec contrôle de nonce."""

    market: str
    nonce: int = -1
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    ts_market: int = 0
    ts_received: int = 0
    synced: bool = False

    def load_snapshot(self, snap: dict[str, Any], received_ms: int) -> None:
        self.nonce = int(snap["nonce"])
        self.bids = {D(p): D(a) for p, a in snap.get("bids", [])}
        self.asks = {D(p): D(a) for p, a in snap.get("asks", [])}
        self.ts_market = int(snap.get("timestamp", received_ms))
        self.ts_received = received_ms
        self.synced = True

    def apply_update(self, upd: dict[str, Any], received_ms: int) -> None:
        """Applique une mise à jour ; lève BookSequenceError si un nonce manque (resync requis)."""
        n = int(upd["nonce"])
        if not self.synced:
            raise BookSequenceError("carnet non initialisé")
        if n <= self.nonce:
            return  # doublon / ancienne mise à jour
        if n != self.nonce + 1:
            self.synced = False
            raise BookSequenceError(f"nonce attendu {self.nonce + 1}, reçu {n}")
        for side, book in (("bids", self.bids), ("asks", self.asks)):
            for p, a in upd.get(side, []):
                price, amt = D(p), D(a)
                if amt == ZERO:
                    book.pop(price, None)
                else:
                    book[price] = amt
        self.nonce = n
        self.ts_market = int(upd.get("timestamp", received_ms))
        self.ts_received = received_ms

    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    def mid(self) -> Decimal | None:
        b, a = self.best_bid(), self.best_ask()
        return (b + a) / 2 if b is not None and a is not None else None

    def spread_bps(self) -> Decimal | None:
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None or b <= 0:
            return None
        return (a - b) / ((a + b) / 2) * 10000

    def is_crossed(self) -> bool:
        b, a = self.best_bid(), self.best_ask()
        return b is not None and a is not None and b >= a

    def levels(self, side: str, n: int = 50) -> list[tuple[Decimal, Decimal]]:
        if side == "asks":
            return sorted(self.asks.items())[:n]
        return sorted(self.bids.items(), reverse=True)[:n]

    def estimate_fill(self, side: str, amount_base: Decimal | None = None, amount_quote: Decimal | None = None
                      ) -> tuple[Decimal, Decimal, bool]:
        """Prix moyen estimé en balayant la profondeur. Renvoie (base, quote, complètement_rempli)."""
        levels = self.levels("asks" if side == "buy" else "bids", 1000)
        got_base, spent_quote = ZERO, ZERO
        for price, amt in levels:
            if amount_base is not None:
                take = min(amt, amount_base - got_base)
            else:
                assert amount_quote is not None
                take = min(amt, (amount_quote - spent_quote) / price)
            got_base += take
            spent_quote += take * price
            if (amount_base is not None and got_base >= amount_base) or (
                amount_quote is not None and spent_quote >= amount_quote - D("1e-12")
            ):
                return got_base, spent_quote, True
        return got_base, spent_quote, False

    def depth_quote(self, side: str, within_bps: Decimal) -> Decimal:
        m = self.mid()
        if m is None:
            return ZERO
        tot = ZERO
        for p, a in self.levels(side, 1000):
            if abs(p - m) / m * 10000 > within_bps:
                break
            tot += p * a
        return tot
