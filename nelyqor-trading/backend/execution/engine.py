"""EXECUTION : seul composant autorisé à soumettre des ordres.

Cycle : intention persistée -> réservation atomique -> veto Guardian -> envoi -> suivi des fills -> réconciliation.
- clientOrderId UUID + operatorId sur chaque ordre.
- Timeout / statut inconnu : recherche par clientOrderId et dans les trades AVANT toute nouvelle soumission ;
  ambiguïté persistante => pause des entrées, jamais de répétition aveugle.
- Un ordre n'est déclaré « NOT_PLACED » qu'après expiration de la fenêtre d'accès signée (Bitvavo rejette
  toute requête dont l'horodatage dépasse la fenêtre) : avant, il pourrait encore apparaître.
- Anti-vente dupliquée : la quantité vendable = position - ventes actives (ordres conditionnels compris),
  vérifiée dans la même transaction que la création de l'intention.
- Montants en Decimal ; arrondis vers le bas AVANT les contrôles ; fills dédupliqués par identifiant.
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
from ..guardian.guardian import CONDITIONAL_TYPES, Guardian, IntentCheck
from ..market_data.bitvavo_rest import BitvavoError, OrderStatusUnknown
from ..market_data.models import MarketInfo
from ..storage.db import Database

log = get_logger("execution")

OPEN_STATES = {"new", "partiallyFilled", "awaitingTrigger"}
ACTIVE_STATES = {"PENDING", "SUBMITTING", "UNKNOWN", *OPEN_STATES}
FINAL_STATES = {"filled", "canceled", "canceledAuction", "canceledSelfTradePrevention", "canceledIOC", "canceledFOK",
                "canceledMarketProtection", "canceledPostOnly", "expired", "rejected"}
_ACTIVE_SQL = "('PENDING','SUBMITTING','UNKNOWN','new','partiallyFilled','awaitingTrigger')"


class Exchange(Protocol):
    async def create_order(self, body: dict[str, Any]) -> dict[str, Any]: ...
    async def get_order(self, market: str, order_id: str | None = None, client_order_id: str | None = None
                        ) -> dict[str, Any]: ...
    async def update_order(self, body: dict[str, Any]) -> dict[str, Any]: ...
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
    order_type: str  # market | limit | stopLoss | stopLossLimit | takeProfit | takeProfitLimit
    amount: Decimal | None = None
    amount_quote: Decimal | None = None
    price: Decimal | None = None
    post_only: bool = False
    strategy: str = ""
    is_entry: bool = True
    is_protective: bool = False
    check: IntentCheck | None = None
    trigger_amount: Decimal | None = None
    trigger_reference: str = "lastTrade"  # lastTrade | bestBid | bestAsk | midPrice
    position_qty: Decimal | None = None  # requis pour toute vente (anti-vente dupliquée)
    purpose: str = ""  # entree | sortie | stop | take_profit | liquidation
    ref_price: Decimal | None = None  # prix de référence (meilleur bid/ask) pour valoriser un ordre au marché


@dataclass
class SubmitResult:
    intent_id: str
    status: str
    accepted: bool
    reasons: list[str]
    order: dict[str, Any] | None = None


class ExecutionEngine:
    def __init__(self, db: Database, exchange: Exchange, guardian: Guardian, markets: dict[str, MarketInfo],
                 mode: str, operator_id: int, now_ms: Any, fee_eur_rates: dict[str, Decimal] | None = None,
                 taker_fee: Decimal = D("0.0025"), access_window_ms: int = 10_000) -> None:
        if mode not in ("simulation", "real"):
            raise ValueError(mode)
        self.db, self.ex, self.g, self.markets = db, exchange, guardian, markets
        self.mode = mode
        self.operator_id = operator_id
        self._now = now_ms
        self.fee_eur_rates = fee_eur_rates or {"EUR": D(1)}
        self.taker_fee = taker_fee
        self.access_window_ms = access_window_ms

    # ---------- réservations internes
    def reserved_quote(self) -> Decimal:
        rows = self.db.q(f"SELECT reserved_quote FROM intents WHERE mode=? AND status IN {_ACTIVE_SQL}",  # noqa: S608
                         (self.mode,))
        return sum((D(r["reserved_quote"] or 0) for r in rows), ZERO)

    def active_sell_base(self, market: str, exclude_intent: str | None = None) -> Decimal:
        """Quantité de base déjà engagée dans des ventes actives (y compris stops et take-profits)."""
        rows = self.db.q(f"SELECT id, reserved_base FROM intents WHERE mode=? AND market=? AND side='sell' "  # noqa: S608
                         f"AND status IN {_ACTIVE_SQL}", (self.mode, market))
        return sum((D(r["reserved_base"] or 0) for r in rows if r["id"] != exclude_intent), ZERO)

    def _build_body(self, req: OrderRequest, cid: str, m: MarketInfo) -> dict[str, Any]:
        body: dict[str, Any] = {"market": req.market, "side": req.side, "orderType": req.order_type,
                                "clientOrderId": cid, "operatorId": self.operator_id}
        if req.amount is not None:
            body["amount"] = fmt(req.amount)
        if req.amount_quote is not None:
            body["amountQuote"] = fmt(req.amount_quote)
        if req.order_type in ("limit", "stopLossLimit", "takeProfitLimit"):
            assert req.price is not None
            body["price"] = fmt(req.price)
            body["timeInForce"] = "GTC"
            if req.post_only:
                body["postOnly"] = True
        if req.order_type in CONDITIONAL_TYPES:
            assert req.trigger_amount is not None
            body.update({"triggerType": "price", "triggerReference": req.trigger_reference,
                         "triggerAmount": fmt(req.trigger_amount)})
        return body

    def _round(self, req: OrderRequest, m: MarketInfo) -> None:
        """Arrondis vers le bas AVANT tout contrôle (jamais d'arrondi qui dépasse un plafond).
        Stop de vente : le déclencheur est arrondi vers le HAUT (ne jamais élargir un stop)."""
        if req.amount is not None:
            req.amount = m.round_amount(req.amount)
        if req.amount_quote is not None:
            req.amount_quote = m.round_quote(req.amount_quote)
        if req.price is not None:
            req.price = m.round_price(req.price, up=(req.side == "sell" and req.order_type == "stopLossLimit"))
        if req.trigger_amount is not None:
            req.trigger_amount = m.round_price(req.trigger_amount, up=req.order_type.startswith("stopLoss"))

    async def submit(self, req: OrderRequest, available_quote: Decimal) -> SubmitResult:
        m = self.markets.get(req.market)
        if m is None:
            return SubmitResult("", "REFUSED", False, ["marché inconnu"])
        if self._has_unresolved():
            self.g.pause("execution", "ordre à l'état inconnu non résolu")
            return SubmitResult("", "REFUSED", False, ["un ordre est à l'état inconnu : aucune nouvelle soumission"])
        self._round(req, m)
        ref_price = req.price or req.trigger_amount or req.ref_price or ZERO
        est_quote = req.amount_quote if req.amount_quote is not None else (req.amount or ZERO) * ref_price
        chk = req.check or IntentCheck(req.market, req.side, req.order_type, est_quote, req.is_entry,
                                       req.is_protective, market_trading=m.is_trading,
                                       min_order_quote=m.min_order_quote, available_quote=available_quote,
                                       mode=self.mode)
        chk.mode = self.mode
        chk.order_type = req.order_type
        chk.order_type_supported = m.supports(req.order_type)
        chk.min_order_base = m.min_order_base
        if req.side == "buy":
            reserve_q = est_quote if req.order_type == "market" else est_quote * (1 + self.taker_fee)
            reserve_b = ZERO
        else:
            reserve_q = ZERO
            reserve_b = req.amount if req.amount is not None else ZERO
            if req.amount is None:
                return SubmitResult("", "REFUSED", False, ["une vente doit préciser la quantité de base"])
            if req.position_qty is None:
                return SubmitResult("", "REFUSED", False, ["vente sans quantité de position connue : refusée"])
            chk.sell_amount_base = req.amount
        intent_id, cid = new_id(), new_id()
        with self.db.tx() as c:  # contrôle + création atomiques (anti-double vente / double réservation)
            if req.side == "buy":
                chk.available_quote = available_quote - self._reserved_quote_tx(c)
            else:
                chk.base_free_for_sale = (req.position_qty or ZERO) - self._active_sell_tx(c, req.market)
            verdict = self.g.check(chk)
            c.execute(
                "INSERT INTO intents(id,ts,mode,market,side,order_type,amount,amount_quote,price,post_only,strategy,"
                "reserved_quote,reserved_base,status,client_order_id,updated_ms,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (intent_id, self._now(), self.mode, req.market, req.side, req.order_type,
                 fmt(req.amount) if req.amount is not None else None,
                 fmt(req.amount_quote) if req.amount_quote is not None else None,
                 fmt(req.price) if req.price is not None else None, int(req.post_only), req.strategy,
                 fmt(reserve_q) if verdict.allowed else "0", fmt(reserve_b) if verdict.allowed else "0",
                 "PENDING" if verdict.allowed else "VETOED", cid, self._now(),
                 json.dumps({"veto": verdict.reasons, "purpose": req.purpose,
                             "trigger": fmt(req.trigger_amount) if req.trigger_amount is not None else None,
                             "original_reserve_quote": fmt(reserve_q)}, ensure_ascii=False)))
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
            self._set(intent_id, status="rejected", last_error=str(e), reserved_quote="0", reserved_base="0")
            return SubmitResult(intent_id, "rejected", False, [e.message])
        if resp.get("clientOrderId") not in (None, cid):
            # l'échange renvoie un autre identifiant client : incohérence grave
            self._set(intent_id, status="UNKNOWN", last_error="clientOrderId incohérent dans la réponse")
            self.g.pause("execution", "identifiant d'ordre incohérent")
            return SubmitResult(intent_id, "UNKNOWN", False, ["clientOrderId incohérent"])
        self._apply_order(intent_id, resp)
        return SubmitResult(intent_id, resp.get("status", "?"), True, [], resp)

    @staticmethod
    def _sum(rows: list[Any], col: str) -> Decimal:
        return sum((D(r[col] or 0) for r in rows), ZERO)

    def _reserved_quote_tx(self, c: Any) -> Decimal:
        return self._sum(c.execute(f"SELECT reserved_quote FROM intents WHERE mode=? AND status IN {_ACTIVE_SQL}",  # noqa: S608
                                   (self.mode,)).fetchall(), "reserved_quote")

    def _active_sell_tx(self, c: Any, market: str) -> Decimal:
        return self._sum(c.execute(f"SELECT reserved_base FROM intents WHERE mode=? AND market=? AND side='sell' "  # noqa: S608
                                   f"AND status IN {_ACTIVE_SQL}", (self.mode, market)).fetchall(), "reserved_base")

    def _has_unresolved(self) -> bool:
        return self.db.one("SELECT 1 FROM intents WHERE mode=? AND status IN ('UNKNOWN','SUBMITTING','PENDING') LIMIT 1",
                           (self.mode,)) is not None

    def _set(self, intent_id: str, **fields: Any) -> None:
        fields["updated_ms"] = self._now()
        cols = ",".join(f"{k}=?" for k in fields)
        self.db.exec(f"UPDATE intents SET {cols} WHERE id=?", (*fields.values(), intent_id))  # noqa: S608

    def _apply_order(self, intent_id: str, o: dict[str, Any]) -> None:
        status = o.get("status", "new")
        with self.db.tx() as c:
            row = c.execute("SELECT market, side, amount, details, client_order_id FROM intents WHERE id=?",
                            (intent_id,)).fetchone()
            if o.get("clientOrderId") not in (None, row["client_order_id"]):
                raise RuntimeError("réponse d'ordre ne correspondant pas à l'intention (clientOrderId)")
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
            if status in FINAL_STATES:
                rq, rb = "0", "0"
            else:
                det = json.loads(row["details"] or "{}")
                filled_q = D(o.get("filledAmountQuote", "0"))
                filled_b = D(o.get("filledAmount", "0"))
                if row["side"] == "buy":
                    orig = D(det.get("original_reserve_quote") or "0")
                    rq, rb = fmt(max(ZERO, orig - filled_q * (1 + self.taker_fee))), "0"
                else:
                    amt = D(o["amount"]) if o.get("amount") else D(row["amount"] or "0")
                    rq, rb = "0", fmt(max(ZERO, amt - filled_b))
            c.execute("UPDATE intents SET status=?, exchange_order_id=COALESCE(?, exchange_order_id), reserved_quote=?, "
                      "reserved_base=?, updated_ms=? WHERE id=?",
                      (status, o.get("orderId"), rq, rb, self._now(), intent_id))

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
        try:
            trades = await self.ex.trades(row["market"])
            if any(t.get("clientOrderId") == row["client_order_id"] for t in trades):
                self._set(intent_id, status="UNKNOWN", last_error="trades trouvés sans ordre : réconciliation requise")
                self.g.pause("execution", "trades sans ordre identifiable")
                return "UNKNOWN"
        except (BitvavoError, OrderStatusUnknown):
            pass
        window_expired = self._now() - int(row["ts"]) > self.access_window_ms + 5_000
        if not_found == attempts and window_expired:
            # Absence confirmée APRÈS expiration de la fenêtre d'accès signée : la requête ne peut plus être
            # acceptée par l'échange. Réservation libérée ; AUCUNE resoumission automatique.
            self._set(intent_id, status="NOT_PLACED", reserved_quote="0", reserved_base="0",
                      last_error="introuvable après timeout et expiration de la fenêtre d'accès")
            self.g.pause("execution", "ordre introuvable après timeout : vérification avant reprise")
            return "NOT_PLACED"
        self._set(intent_id, status="UNKNOWN", last_error="introuvable pour l'instant ; nouvelle recherche planifiée")
        self.g.pause("execution", "statut d'ordre ambigu")
        return "UNKNOWN"

    async def recover(self) -> list[str]:
        """Au démarrage et à chaque cycle : intentions restées PENDING/SUBMITTING/UNKNOWN (crash, timeout)."""
        out = []
        for row in self.db.q("SELECT id, status FROM intents WHERE mode=? AND status IN ('PENDING','SUBMITTING','UNKNOWN')",
                             (self.mode,)):
            if row["status"] == "PENDING":
                # PENDING = jamais envoyé (crash entre la persistance et l'envoi) : on l'abandonne proprement
                self._set(row["id"], status="ABANDONED", reserved_quote="0", reserved_base="0",
                          last_error="interrompu avant envoi")
                out.append("ABANDONED")
            else:
                out.append(await self.resolve_unknown(row["id"]))
        return out

    async def refresh_open(self) -> list[str]:
        """Suit les ordres ouverts (fills partiels, déclenchements, expirations, annulations)."""
        changed = []
        for row in self.db.q("SELECT id, market, exchange_order_id, status FROM intents WHERE mode=? AND status IN "
                             "('new','partiallyFilled','awaitingTrigger')", (self.mode,)):
            try:
                o = await self.ex.get_order(row["market"], order_id=row["exchange_order_id"])
            except OrderStatusUnknown:
                continue
            except BitvavoError as e:
                if e.code == 240 or e.status == 404:
                    self._set(row["id"], status="UNKNOWN", last_error="ordre ouvert introuvable chez l'échange")
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
        except OrderStatusUnknown:
            pass  # la relecture ci-dessous tranche
        o = await self.ex.get_order(row["market"], order_id=row["exchange_order_id"])
        self._apply_order(intent_id, o)
        return str(o.get("status"))

    async def update_trigger(self, intent_id: str, new_trigger: Decimal, new_price: Decimal | None = None) -> str:
        """Modifie le déclencheur d'un ordre conditionnel non déclenché (PUT /order, en place).
        En cas de panne pendant la modification : relecture de l'ordre pour savoir quel déclencheur est actif.
        Renvoie 'updated' | 'unchanged' | 'unknown' | le statut final si l'ordre n'est plus modifiable."""
        row = self.db.one("SELECT * FROM intents WHERE id=?", (intent_id,))
        if row is None or row["status"] != "awaitingTrigger":
            return "non_modifiable"
        m = self.markets[row["market"]]
        new_trigger = m.round_price(new_trigger, up=row["order_type"].startswith("stopLoss"))
        body: dict[str, Any] = {"market": row["market"], "orderId": row["exchange_order_id"],
                                "triggerAmount": fmt(new_trigger), "operatorId": self.operator_id}
        if new_price is not None and row["order_type"] in ("stopLossLimit", "takeProfitLimit"):
            body["price"] = fmt(m.round_price(new_price, up=True))
        det = json.loads(row["details"] or "{}")
        det["pending_trigger"] = fmt(new_trigger)
        self._set(intent_id, details=json.dumps(det))
        try:
            o = await self.ex.update_order(body)
        except (OrderStatusUnknown, BitvavoError) as e:
            try:
                o = await self.ex.get_order(row["market"], order_id=row["exchange_order_id"])
            except (OrderStatusUnknown, BitvavoError):
                self._set(intent_id, last_error=f"modification ambiguë : {e}")
                return "unknown"
        self._apply_order(intent_id, o)
        live = o.get("triggerAmount") or o.get("triggerPrice")
        det.pop("pending_trigger", None)
        if live is not None and D(live) == new_trigger:
            det["trigger"] = fmt(new_trigger)
            self._set(intent_id, details=json.dumps(det))
            return "updated"
        if o.get("status") != "awaitingTrigger":
            self._set(intent_id, details=json.dumps(det))
            return str(o.get("status"))
        self._set(intent_id, details=json.dumps(det))
        return "unchanged"

    async def cancel_stale_entries(self, ttl_ms: int) -> list[str]:
        """Annule les ordres limités d'ENTRÉE non exécutés après ttl (libère les fonds réservés)."""
        out = []
        for row in self.db.q("SELECT id, ts, details FROM intents WHERE mode=? AND side='buy' AND order_type='limit' "
                             "AND status IN ('new','partiallyFilled')", (self.mode,)):
            if self._now() - int(row["ts"]) > ttl_ms:
                out.append(await self.cancel(row["id"]))
        return out

    def list_intents(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.q("SELECT * FROM intents WHERE mode=? ORDER BY ts DESC LIMIT ?",
                                           (self.mode, limit))]
