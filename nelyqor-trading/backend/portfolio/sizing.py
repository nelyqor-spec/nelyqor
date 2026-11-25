"""Dimensionnement : applique simultanément toutes les contraintes. Peut renvoyer 0 ; ne force jamais un minimum."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..core.config import HardLimits
from ..core.num import ZERO, D
from ..market_data.models import MarketInfo
from .risk import position_cap


@dataclass
class SizingResult:
    amount_quote: Decimal
    reasons: list[str] = field(default_factory=list)
    binding: str = ""


def size_buy(*, equity: Decimal, available_quote: Decimal, current_exposure: Decimal, position_value: Decimal,
             risk: float, confidence: float, limits: HardLimits, market: MarketInfo, fee_rate: Decimal,
             depth_quote: Decimal, stress_loss_frac: Decimal, max_stress_loss_eur: Decimal,
             optimizer_target: Decimal | None = None, reserve_fraction: Decimal | None = None) -> SizingResult:
    reasons: list[str] = []
    reserve = equity * max(limits.min_reserve_fraction, reserve_fraction or ZERO)
    caps: dict[str, Decimal] = {
        "disponible_apres_reserve": available_quote - reserve,
        "plafond_risque": position_cap(equity, risk, confidence) - position_value,
        "max_position": equity * limits.max_position_fraction - position_value,
        "exposition_totale": equity * limits.max_total_exposure_fraction - current_exposure,
        "max_ordre": limits.max_order_eur,
        "profondeur": depth_quote * D("0.1"),  # ne pas consommer plus de 10 % de la profondeur proche
    }
    if stress_loss_frac > 0:
        caps["perte_stress"] = max_stress_loss_eur / stress_loss_frac
    if optimizer_target is not None:
        caps["optimiseur"] = optimizer_target
    if market.max_order_quote:
        caps["max_bitvavo"] = market.max_order_quote
    binding = min(caps, key=lambda k: caps[k])
    amt = max(ZERO, caps[binding])
    # les frais sont prélevés sur le disponible : garder la marge pour la commission
    amt = min(amt, (available_quote - reserve) / (1 + fee_rate)) if available_quote > reserve else ZERO
    amt = market.round_quote(max(ZERO, amt))
    if risk > 45:
        reasons.append(f"risque {risk:.0f}/100 > 45 : montant réduit par le plafond")
    if amt < market.min_order_quote:
        reasons.append(f"montant {amt} < minimum Bitvavo {market.min_order_quote} : refusé (pas d'arrondi vers le haut)")
        return SizingResult(ZERO, reasons, binding)
    return SizingResult(amt, reasons, binding)
