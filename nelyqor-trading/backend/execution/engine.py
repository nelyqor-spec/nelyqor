"""EXECUTION : seul composant autorisé à soumettre des ordres.

Cycle : intention persistée -> réservation atomique -> veto Guardian -> envoi -> suivi des fills -> réconciliation.
- clientOrderId UUID + operatorId sur chaque ordre.
- Timeout / statut inconnu : recherche par clientOrderId et dans les trades AVANT toute nouvelle soumission ;
  ambiguïté persistante => pause des entrées, jamais de répétition aveugle.
- Montants en Decimal ; fills dédupliqués par identifiant.
"""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from ..core.clock import new_id
from ..core.logging import get_logger
from ..core.num import ZERO, D, fmt
from ..guardian.guardian import Guardian, IntentCheck
from ..market_data.bitvavo_rest import BitvavoError, OrderStatusUnknown
from ..market_data.models import MarketInfo
from ..storage.db import Database

log = get_logger("execution")

OPEN_STATES = {"new", "partiallyFilled", "awaitingTrigger"}
FINAL_STATES = {"filled", "canceled", "canceledAuction", "canceledSelfTradePrevention", "canceledIOC", "canceledFOK",
                "canceledMarketProtection", "canceledPostOnly", "expired", "rejected"}


class Exchange(Protocol):
    async def create_order(self, body: dict[str, Any]) -> dict[str, Any]: ...
    async def get_order(self, market: str, order_id: str | None = None, client_order_id: str | None = None
                        ) -> dict[str, Any]: ...
    async def cancel_order(self, market: str, order_id: str, operator_id: int) -> dict[str, Any]: ...
    async def trades(self, market: str, limit: int = 500, start: int | None = None) -> list[dict[str, Any]]: ...
    async def balance(self) -> list[dict[str, Any]]: ...
    async def open_orders(self, market: str | None = None) -> list[dict[str, Any]]: ...


class InstanceLock:
    """Verrou exclusif : empêche deux moteurs de trader simultanément sur la même base."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            os.close(fd)
            raise RuntimeError("Une autre instance du moteur est déjà active") from e
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self.fd = fd

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


@dataclass
class OrderRequest:
    market: str
    side: str
    order_type: str  # market | limit
    amount: Decimal | None = None
    amount_quote: Decimal | None = None
    price: Decimal | None = None
    post_only: bool = False
    strategy: str = ""
    is_entry: bool = True
    is_protective: bool = False
    check: IntentCheck | None = None


@dataclass
class SubmitResult:
    intent_id: str
    status: str
    accepted: bool
    reasons: list[str]
    order: dict[str, Any] | None = None


class ExecutionEngine:
    def __init__(self, db: Database, exchange: Exchange, guardian: Guardian, markets: dict[str, MarketInfo],
                 mode: str, operator_id: int, now_ms: Any, fee_eur_rates: dict[str, Decimal] | None = None) -> None:
        if mode not in ("simulation", "real"):
            raise ValueError(mode)
        self.db, self.ex, self.g, self.markets = db, exchange, guardian, markets
        self.mode = mode
        self.operator_id = operator_id
        self._now = now_ms
        self.fee_eur_rates = fee_eur_rates or {"EUR": D(1)}

    # ---------- réservations internes
    def reserved_quote(self) -> Decimal:
        rows = self.db.q("SELECT reserved_quote FROM intents WHERE mode=? AND status IN "
                         "('PENDING','SUBMITTING','UNKNOWN','new','partiallyFilled','awaitingTrigger')", (self.mode,))
        return sum((D(r["reserved_quote"] or 0) for r in rows), ZERO)

    def _build_body(self, req: OrderRequest, cid: str, m: MarketInfo) -> dict[str, Any]:
        body: dict[str, Any] = {"market": req.market, "side": req.side, "orderType": req.order_type,
                                "clientOrderId": cid, "operatorId": self.operator_id}
        if req.amount is not None:
            body["amount"] = fmt(m.round_amount(req.amount))
        if req.amount_quote is not None:
            body["amountQuote"] = fmt(m.round_quote(req.amount_quote))
        if req.order_type == "limit":
            assert req.price is not None
            body["price"] = fmt(m.round_price(req.price))
            body["timeInForce"] = "GTC"
            if req.post_only:
                body["postOnly"] = True
        return body

    async def submit(self, req: OrderRequest, available_quote: Decimal) -> SubmitResult:
        m = self.markets.get(req.market)
        if m is None:
            return SubmitResult("", "REFUSED", False, ["marché inconnu"])
        if self._has_unresolved():
            self.g.pause("execution", "ordre à l'état inconnu non résolu")
            return SubmitResult("", "REFUSED", False, ["un ordre est à l'état inconnu : aucune nouvelle soumission"])
        est_quote = req.amount_quote if req.amount_quote is not None else (req.amount or ZERO) * (req.price or ZERO)
        chk = req.check or IntentCheck(req.market, req.side, req.order_type, est_quote, req.is_entry,
                                       req.is_protective, market_trading=m.is_trading,
                                       min_order_quote=m.min_order_quote, available_quote=available_quote,
                                       mode=self.mode)
        chk.mode = self.mode
        chk.order_type_supported = m.supports(req.order_type)
        chk.available_quote = available_quote - self.reserved_quote() if req.side == "buy" else available_quote
        intent_id, cid = new_id(), new_id()
        reserve_q = est_quote * (1 + D("0.0025")) if req.side == "buy" and req.order_type != "market" else (
            est_quote if req.side == "buy" else ZERO)
        verdict = self.g.check(chk)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO intents(id,ts,mode,market,side,order_type,amount,amount_quote,price,post_only,strategy,"
                "reserved_quote,reserved_base,status,client_order_id,updated_ms,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (intent_id, self._now(), self.mode, req.market, req.side, req.order_type,
                 fmt(req.amount) if req.amount is not None else None,
                 fmt(req.amount_quote) if req.amount_quote is not None else None,
                 fmt(req.price) if req.price is not None else None, int(req.post_only), req.strategy,
                 fmt(reserve_q) if verdict.allowed else "0", "0",
                 "PENDING" if verdict.allowed else "VETOED", cid, self._now(),
                 json.dumps({"veto": verdict.reasons}, ensure_ascii=False)))
        if not verdict.allowed:
            return SubmitResult(intent_id, "VETOED", False, verdict.reasons)
        body = self._build_body(req, cid, m)
        self._set(intent_id, status="SUBMITTING")
        try:
            resp = await self.ex.create_order(body)
        except OrderStatusUnknown as e:
            self._set(intent_id, status="UNKNOWN", last_error=str(e))
            log.error("statut d'ordre inconnu", extra={"data": {"intent": intent_id, "err": str(e)}})
            resolved = await self.resolve_unknown(intent_id)
            return SubmitResult(intent_id, resolved, resolved not in ("UNKNOWN", "NOT_PLACED"), [str(e)])
        except BitvavoError as e:
            self._set(intent_id, status="rejected", last_error=str(e), reserved_quote="0")
            return SubmitResult(intent_id, "rejected", False, [e.message])
        self._apply_order(intent_id, resp)
        return SubmitResult(intent_id, resp.get("status", "?"), True, [], resp)

    def _has_unresolved(self) -> bool:
        return self.db.one("SELECT 1 FROM intents WHERE mode=? AND status IN ('UNKNOWN','SUBMITTING') LIMIT 1",
                           (self.mode,)) is not None

    def _set(self, intent_id: str, **fields: Any) -> None:
        fields["updated_ms"] = self._now()
        cols = ",".join(f"{k}=?" for k in fields)
        self.db.exec(f"UPDATE intents SET {cols} WHERE id=?", (*fields.values(), intent_id))  # noqa: S608

    def _apply_order(self, intent_id: str, o: dict[str, Any]) -> None:
        status = o.get("status", "new")
        with self.db.tx() as c:
            row = c.execute("SELECT market, side FROM intents WHERE id=?", (intent_id,)).fetchone()
            for f in o.get("fills", []):
                fee = D(f.get("fee", "0"))
                cur = f.get("feeCurrency", "EUR")
                rate = self.fee_eur_rates.get(cur)
                fee_eur = fee * rate if rate is not None else None
                c.execute(
                    "INSERT OR IGNORE INTO fills(id,intent_id,exchange_order_id,ts,market,side,amount,price,fee,fee_currency,"
                    "fee_eur,taker,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f["id"], intent_id, o.get("orderId"), int(f.get("timestamp", self._now())), row["market"],
                     row["side"], f["amount"], f["price"], fmt(fee), cur,
                     fmt(fee_eur) if fee_eur is not None else None, int(bool(f.get("taker"))), self.mode))
            reserve = "0" if status in FINAL_STATES else None
            if reserve is not None:
                c.execute("UPDATE intents SET status=?, exchange_order_id=?, reserved_quote='0', updated_ms=? WHERE id=?",
                          (status, o.get("orderId"), self._now(), intent_id))
            else:
                c.execute("UPDATE intents SET status=?, exchange_order_id=?, updated_ms=? WHERE id=?",
                          (status, o.get("orderId"), self._now(), intent_id))

    async def resolve_unknown(self, intent_id: str, attempts: int = 3) -> str:
        """Recherche l'ordre par clientOrderId puis dans les trades. Ne resoumet jamais."""
        row = self.db.one("SELECT * FROM intents WHERE id=?", (intent_id,))
        assert row is not None
        not_found = 0
        for _ in range(attempts):
            try:
                o = await self.ex.get_order(row["market"], client_order_id=row["client_order_id"])
                self._apply_order(intent_id, o)
                return str(o.get("status"))
            except BitvavoError as e:
                if e.code == 240 or e.status == 404:
                    not_found += 1
                    continue
            except OrderStatusUnknown:
                continue
        # Dernière vérification : trades portant ce clientOrderId
        try:
            trades = await self.ex.trades(row["market"])
            if any(t.get("clientOrderId") == row["client_order_id"] for t in trades):
                self._set(intent_id, status="UNKNOWN", last_error="trades trouvés sans ordre : réconciliation requise")
                self.g.pause("execution", "trades sans ordre identifiable")
                return "UNKNOWN"
        except (BitvavoError, OrderStatusUnknown):
            pass
        if not_found == attempts:
            # Absence confirmée à chaque requête : l'ordre n'a vraisemblablement pas été placé.
            # On libère la réservation, on ne resoumet PAS automatiquement, et on demande une vérification.
            self._set(intent_id, status="NOT_PLACED", reserved_quote="0", last_error="introuvable après timeout")
            self.g.pause("execution", "ordre introuvable après timeout : vérification avant reprise")
            return "NOT_PLACED"
        self.g.pause("execution", "statut d'ordre ambigu persistant")
        return "UNKNOWN"

    async def refresh_open(self) -> list[str]:
        """Suit les ordres ouverts (fills partiels, expirations, annulations)."""
        changed = []
        for row in self.db.q("SELECT id, market, exchange_order_id, status FROM intents WHERE mode=? AND status IN "
                             "('new','partiallyFilled','awaitingTrigger')", (self.mode,)):
            try:
                o = await self.ex.get_order(row["market"], order_id=row["exchange_order_id"])
            except (BitvavoError, OrderStatusUnknown):
                continue
            if o.get("status") != row["status"] or o.get("fills"):
                self._apply_order(row["id"], o)
                changed.append(row["id"])
        return changed

    async def cancel(self, intent_id: str) -> str:
        """Annule puis relit l'ordre : gère la course annulation / remplissage."""
        row = self.db.one("SELECT * FROM intents WHERE id=?", (intent_id,))
        if row is None or not row["exchange_order_id"]:
            return "introuvable"
        try:
            await self.ex.cancel_order(row["market"], row["exchange_order_id"], self.operator_id)
        except BitvavoError as e:
            log.info("annulation refusée (probablement déjà rempli)", extra={"data": {"err": e.message}})
        o = await self.ex.get_order(row["market"], order_id=row["exchange_order_id"])
        self._apply_order(intent_id, o)
        return str(o.get("status"))

    def list_intents(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.q("SELECT * FROM intents WHERE mode=? ORDER BY ts DESC LIMIT ?",
                                           (self.mode, limit))]
