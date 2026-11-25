"""Protections hébergées chez l'échange (stop-loss natifs Bitvavo).

Règles :
- Une position n'est PROTÉGÉE que si un ordre stop de vente a été relu chez l'échange avec le statut
  `awaitingTrigger`, le déclencheur attendu et une quantité couvrant la position. Une simple réponse
  d'acceptation, un timeout ou une modification en cours ne suffisent pas.
- Un stop n'est jamais abaissé (on ne l'élargit jamais pour éviter une perte) ; il peut être relevé.
- Pas d'OCO natif supposé : stop et take-profit natifs sur la même quantité se disputeraient le solde
  réservé ; la politique retenue est stop natif sur toute la position, prises de bénéfices gérées par le
  moteur (annulation du stop -> vente -> nouveau stop sur le reliquat).
- LIMITES : un stopLoss déclenché devient un ordre au marché (glissement, gaps : AUCUN prix garanti) ;
  un stopLossLimit peut ne jamais être exécuté si le prix traverse sa limite ; pendant une panne de
  Bitvavo ou une suspension de marché, aucun stop ne s'exécute.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..core.logging import get_logger
from ..core.num import ZERO, D
from ..market_data.bitvavo_rest import BitvavoError, OrderStatusUnknown
from .engine import ExecutionEngine, OrderRequest

log = get_logger("protection")


@dataclass
class ProtectionStatus:
    market: str
    protected: bool
    state: str  # NONE | PENDING | CONFIRMED | UNCERTAIN | TRIGGERED
    trigger: Decimal | None = None
    covered_qty: Decimal = ZERO
    intent_id: str | None = None
    reason: str = ""


class ProtectionManager:
    def __init__(self, ex: ExecutionEngine, order_type: str = "stopLoss", trigger_reference: str = "bestBid") -> None:
        self.ex = ex
        self.order_type = order_type
        self.trigger_reference = trigger_reference

    def _stop_rows(self, market: str) -> list[Any]:
        return self.ex.db.q("SELECT * FROM intents WHERE mode=? AND market=? AND side='sell' AND order_type IN "
                            "('stopLoss','stopLossLimit') AND status IN ('PENDING','SUBMITTING','UNKNOWN','new',"
                            "'partiallyFilled','awaitingTrigger') ORDER BY ts DESC", (self.ex.mode, market))

    async def status(self, market: str, position_qty: Decimal) -> ProtectionStatus:
        """Relit le stop CHEZ L'ÉCHANGE. Seul ce résultat fait foi."""
        rows = self._stop_rows(market)
        if not rows:
            return ProtectionStatus(market, False, "NONE", reason="aucun stop")
        r = rows[0]
        det = json.loads(r["details"] or "{}")
        if r["status"] in ("PENDING", "SUBMITTING", "UNKNOWN") or not r["exchange_order_id"]:
            return ProtectionStatus(market, False, "PENDING", intent_id=r["id"], reason="stop non confirmé")
        try:
            o = await self.ex.ex.get_order(market, order_id=r["exchange_order_id"])
        except (BitvavoError, OrderStatusUnknown) as e:
            return ProtectionStatus(market, False, "UNCERTAIN", intent_id=r["id"], reason=f"relecture impossible : {e}")
        self.ex._apply_order(r["id"], o)
        if o.get("status") != "awaitingTrigger":
            return ProtectionStatus(market, False, "TRIGGERED" if o.get("status") in ("filled", "partiallyFilled", "new")
                                    else "NONE", intent_id=r["id"], reason=f"stop {o.get('status')}")
        live_trigger = D(o.get("triggerAmount") or o.get("triggerPrice") or "0")
        qty = D(o.get("amountRemaining") or o.get("amount") or "0")
        if det.get("pending_trigger"):
            return ProtectionStatus(market, False, "UNCERTAIN", live_trigger, qty, r["id"], "modification en cours")
        m = self.ex.markets[market]
        if qty < m.round_amount(position_qty) - m.min_order_base:
            return ProtectionStatus(market, False, "PARTIAL", live_trigger, qty, r["id"], "quantité couverte insuffisante")
        return ProtectionStatus(market, True, "CONFIRMED", live_trigger, qty, r["id"], "stop confirmé par l'échange")

    async def ensure(self, market: str, position_qty: Decimal, desired_trigger: Decimal) -> ProtectionStatus:
        """Pose un stop si aucun n'existe, le relève si le déclencheur désiré est plus haut, puis relit."""
        st = await self.status(market, position_qty)
        m = self.ex.markets[market]
        if st.state == "NONE":
            qty = m.round_amount(position_qty)
            if qty < m.min_order_base or qty * desired_trigger < m.min_order_quote:
                return ProtectionStatus(market, False, "NONE", reason="position sous les minimums : stop impossible")
            res = await self.ex.submit(OrderRequest(market, "sell", self.order_type, amount=qty,
                                                    trigger_amount=desired_trigger,
                                                    trigger_reference=self.trigger_reference,
                                                    price=desired_trigger * D("0.98") if self.order_type == "stopLossLimit" else None,
                                                    is_entry=False, is_protective=True, position_qty=position_qty,
                                                    purpose="stop"), ZERO)
            if not res.accepted:
                log.warning("pose du stop refusée", extra={"data": {"market": market, "reasons": res.reasons}})
            return await self.status(market, position_qty)
        if st.state == "PARTIAL":
            # quantité couverte insuffisante (ex. achat complémentaire) : annuler puis reposer sur toute la position.
            ok, why = await self.release_for_exit(market)
            if not ok:
                return ProtectionStatus(market, False, "UNCERTAIN", reason=why)
            return await self.ensure(market, position_qty, max(desired_trigger, st.trigger or ZERO))
        if st.state == "CONFIRMED" and st.trigger is not None and desired_trigger > st.trigger and st.intent_id:
            outcome = await self.ex.update_trigger(st.intent_id, desired_trigger)
            log.info("relèvement du stop", extra={"data": {"market": market, "outcome": outcome}})
            return await self.status(market, position_qty)
        return st

    async def release_for_exit(self, market: str) -> tuple[bool, str]:
        """Avant une vente volontaire : annule le stop et CONFIRME l'annulation (les jetons réservés
        doivent être libérés). Si le stop s'est exécuté entre-temps, la position a déjà été vendue."""
        for r in self._stop_rows(market):
            if not r["exchange_order_id"]:
                return False, "stop en cours de pose : sortie reportée"
            status = await self.ex.cancel(r["id"])
            if status == "filled":
                return False, "le stop a été exécuté pendant l'annulation : position déjà vendue"
            if status not in ("canceled", "expired", "canceledMarketProtection"):
                return False, f"annulation du stop non confirmée ({status})"
        return True, "stop annulé et confirmé"
