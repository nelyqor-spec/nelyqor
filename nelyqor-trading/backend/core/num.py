"""Arithmétique monétaire en Decimal. Aucun float pour comptabiliser des fonds."""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, Decimal, getcontext

getcontext().prec = 34

ZERO = Decimal("0")
ONE = Decimal("1")


def D(x: object) -> Decimal:
    """Convertit en Decimal sans passer par la représentation binaire d'un float."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(float(x)))  # float() : np.float64 a un repr "np.float64(...)"
    return Decimal(str(x))


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Arrondi vers le bas à un multiple de step (jamais vers le haut : ne pas dépasser un plafond)."""
    if step <= 0:
        raise ValueError("step doit être > 0")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("step doit être > 0")
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def ceil_significant(value: Decimal, digits: int) -> Decimal:
    if value == 0:
        return value
    exp = value.adjusted() - digits + 1
    return value.quantize(Decimal(1).scaleb(exp), rounding=ROUND_UP)


def floor_decimals(value: Decimal, decimals: int) -> Decimal:
    q = Decimal(1).scaleb(-decimals)
    return value.quantize(q, rounding=ROUND_DOWN)


def floor_significant(value: Decimal, digits: int) -> Decimal:
    """Arrondi vers le bas à `digits` chiffres significatifs (ancien pricePrecision Bitvavo)."""
    if value == 0:
        return value
    exp = value.adjusted() - digits + 1
    q = Decimal(1).scaleb(exp)
    return value.quantize(q, rounding=ROUND_DOWN)


def round_eur(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def fmt(value: Decimal) -> str:
    """Représentation canonique sans exposant pour les API."""
    s = format(value.normalize(), "f")
    return s if s != "-0" else "0"
