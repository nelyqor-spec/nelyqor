"""Scanner d'univers : tous les marchés accessibles sont scannés, seuls les éligibles sont achetables."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..core.config import HardLimits
from ..core.num import D
from .models import MarketInfo


@dataclass
class MarketState:
    info: MarketInfo
    last_price: Decimal | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    quote_volume_24h: Decimal = D(0)
    ts_market_ms: int = 0
    ts_received_ms: int = 0
    anomaly_score: Decimal = D(0)


@dataclass
class Eligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)


def spread_bps(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (ask - bid) / ((ask + bid) / 2) * 10000


def check_eligibility(st: MarketState, limits: HardLimits, now_ms: int, quote: str = "EUR") -> Eligibility:
    r: list[str] = []
    if not st.info.is_trading:
        r.append(f"statut {st.info.status}")
    if st.info.quote != quote:
        r.append(f"devise de cotation {st.info.quote}")
    if now_ms - st.ts_received_ms > limits.max_stale_ms:
        r.append("donnée périmée")
    if st.ts_market_ms and st.ts_market_ms - st.ts_received_ms > 5000:
        r.append("horodatage marché incohérent (futur)")
    sp = spread_bps(st.best_bid, st.best_ask)
    if sp is None:
        r.append("carnet indisponible")
    elif sp < 0:
        r.append("carnet croisé")
    elif sp > limits.max_spread_bps:
        r.append(f"spread {sp:.0f} bps")
    if st.quote_volume_24h < limits.min_quote_volume_24h_eur:
        r.append("liquidité insuffisante")
    if st.anomaly_score >= limits.anomaly_refuse_threshold:
        r.append("anomalie / suspicion de manipulation")
    return Eligibility(not r, r)


def diff_markets(old: dict[str, MarketInfo], new: dict[str, MarketInfo]) -> dict[str, list[str]]:
    """Détecte listings, radiations et changements de statut."""
    return {
        "listed": sorted(set(new) - set(old)),
        "delisted": sorted(set(old) - set(new)),
        "status_changed": sorted(m for m in set(old) & set(new) if old[m].status != new[m].status),
    }
