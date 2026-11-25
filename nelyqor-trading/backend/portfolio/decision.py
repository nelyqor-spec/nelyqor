"""QUANT : transforme signaux + contexte en intentions. Ne signe rien, ne contourne pas GUARDIAN.

Pour chaque marché éligible : attendre est l'option par défaut. Un achat n'est proposé que si
l'avantage net estimé (après frais, spread, probabilité de non-exécution) reste positif avec marge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from ..core.config import HardLimits
from ..core.num import ZERO, D
from ..features import indicators as ind
from ..market_data.models import MarketInfo
from ..market_data.universe import MarketState, spread_bps
from ..models.regime import Regime, classify_regime
from ..strategies.base import Strategy
from .risk import RiskInputs, breakeven_move, normalize_liquidity, normalize_volatility, risk_score
from .sizing import size_buy


@dataclass
class Proposal:
    market: str
    action: str  # acheter | vendre | reduire | attendre | conserver
    strategy: str
    amount_quote: Decimal = ZERO
    amount_base: Decimal = ZERO
    order_type: str = "market"
    limit_price: Decimal | None = None
    post_only: bool = False
    risk: float = 0.0
    confidence: float = 0.0
    edge: float = 0.0
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HeldPosition:
    market: str
    qty: Decimal
    avg_cost: Decimal
    entry_ts: int
    strategy: str
    stop: Decimal
    bars_held: int = 0


def expected_edge(df: pd.DataFrame, signals_entry: np.ndarray, horizon: int) -> tuple[float, float, int]:
    """Rendement moyen historique (in-sample, sur le PASSÉ disponible) après les signaux d'entrée de cette
    stratégie sur ce marché, et son écart-type. Sert d'estimation prudente, pas de preuve."""
    c = df["close"].to_numpy(dtype=float)
    idx = np.flatnonzero(signals_entry[:-horizon - 1]) if len(c) > horizon + 1 else np.array([], dtype=int)
    if len(idx) < 5:
        return 0.0, 1.0, int(len(idx))
    fwd = c[idx + 1 + horizon] / c[idx + 1] - 1  # entrée à la bougie suivante
    return float(np.mean(fwd)), float(np.std(fwd, ddof=1)), int(len(idx))


class DecisionEngine:
    def __init__(self, strategies: list[Strategy], limits: HardLimits, fee_taker: Decimal, fee_maker: Decimal,
                 risk_weights: dict[str, float] | None = None, uncertainty_k: float = 1.0) -> None:
        self.strategies = strategies
        self.limits = limits
        self.fee_taker, self.fee_maker = fee_taker, fee_maker
        self.risk_weights = risk_weights
        self.k = uncertainty_k

    def evaluate_entry(self, m: MarketInfo, st: MarketState, df: pd.DataFrame, equity: Decimal,
                       available: Decimal, exposure: Decimal, held: HeldPosition | None,
                       depth_quote: Decimal) -> list[Proposal]:
        out: list[Proposal] = []
        if len(df) < 250:
            return [Proposal(m.market, "attendre", "-", reasons=["historique insuffisant (<250 bougies)"])]
        c = df["close"].to_numpy(dtype=float)
        reg = classify_regime(df)[-1]
        daily_vol = float(np.nanstd(np.diff(np.log(c[-24 * 14:]))) * np.sqrt(24))
        for s in self.strategies:
            sig = s.signals(df)
            if not sig.entry[-1]:
                continue
            mean, sd, n = expected_edge(df, sig.entry, horizon=min(24, sig.max_bars))
            spread = spread_bps(st.best_bid, st.best_ask)
            spread_frac = (spread or D(100)) / D(10000)
            be = float(breakeven_move(self.fee_taker, self.fee_taker, spread_frac / 2))
            se = sd / np.sqrt(max(n, 1))
            edge_low = mean - be - self.k * se
            uncertainty = float(np.clip(se / max(abs(mean), 1e-4), 0, 1)) if n >= 5 else 1.0
            ri = RiskInputs(volatility=normalize_volatility(daily_vol),
                            liquidity=normalize_liquidity(float(depth_quote), float(available) * 0.3 or 1.0),
                            bearish=1.0 if reg == Regime.BEARISH.value else (0.5 if reg == Regime.VOLATILE.value else 0.0),
                            anomaly=float(st.anomaly_score), uncertainty=uncertainty)
            rs = risk_score(ri, self.risk_weights)
            conf = float(100 * np.clip(sig.strength[-1], 0, 1) * (1 - uncertainty * 0.5))
            p = Proposal(m.market, "attendre", s.name, risk=rs, confidence=conf, edge=edge_low,
                         details={"regime": str(reg), "mean_fwd": mean, "sd_fwd": sd, "n_hist": n,
                                  "breakeven": be, "spread_bps": float(spread) if spread is not None else None,
                                  "label": "estimation non calibrée"})
            if edge_low <= 0:
                p.reasons.append(f"avantage net estimé {edge_low:.4f} <= 0 après frais et incertitude")
                out.append(p)
                continue
            if held is not None:
                losing = st.last_price is not None and st.last_price < held.avg_cost
                if losing:
                    p.reasons.append("renforcement d'une position perdante : refusé sans thèse indépendante validée")
                    out.append(p)
                    continue
            atr_v = float(ind.atr(df["high"].to_numpy(float), df["low"].to_numpy(float), c)[-1])
            stress = D(str(min(0.5, 3 * atr_v / c[-1] + 0.1)))
            sz = size_buy(equity=equity, available_quote=available, current_exposure=exposure,
                          position_value=(held.qty * (st.last_price or ZERO)) if held else ZERO, risk=rs,
                          confidence=conf, limits=self.limits, market=m, fee_rate=self.fee_taker,
                          depth_quote=depth_quote, stress_loss_frac=stress, max_stress_loss_eur=equity * D("0.05"))
            p.reasons += sz.reasons
            p.details["contrainte_liante"] = sz.binding
            if sz.amount_quote <= 0:
                out.append(p)
                continue
            p.action, p.amount_quote = "acheter", sz.amount_quote
            p.details["stop"] = float(c[-1] - 2.5 * atr_v)
            # Spread large relativement aux frais : ordre limite postOnly au meilleur bid (maker), sinon marché.
            if spread is not None and spread > D(15) and m.supports("limit") and st.best_bid:
                p.order_type, p.limit_price, p.post_only = "limit", st.best_bid, True
            out.append(p)
        if not out:
            out.append(Proposal(m.market, "attendre", "-", reasons=["aucun signal d'entrée"]))
        return out

    def evaluate_exit(self, m: MarketInfo, st: MarketState, df: pd.DataFrame, pos: HeldPosition,
                      min_hold_bars: int = 3) -> Proposal:
        strat = next((s for s in self.strategies if s.name == pos.strategy), None)
        price = st.best_bid or st.last_price
        if price is None:
            return Proposal(m.market, "conserver", pos.strategy, reasons=["prix indisponible : pas de décision"])
        value = pos.qty * price
        if value < m.min_order_quote or pos.qty < m.min_order_base:
            return Proposal(m.market, "conserver", pos.strategy, reasons=["poussière sous les minimums : invendable"])
        if price <= pos.stop:
            return Proposal(m.market, "vendre", pos.strategy, amount_base=m.round_amount(pos.qty),
                            reasons=[f"stop atteint ({pos.stop})"])
        if strat is not None and pos.bars_held >= min_hold_bars:
            sig = strat.signals(df)
            if sig.exit[-1]:
                return Proposal(m.market, "vendre", pos.strategy, amount_base=m.round_amount(pos.qty),
                                reasons=["invalidation de la thèse"])
            if pos.bars_held >= sig.max_bars:
                return Proposal(m.market, "vendre", pos.strategy, amount_base=m.round_amount(pos.qty),
                                reasons=["durée maximale de la stratégie"])
        return Proposal(m.market, "conserver", pos.strategy, reasons=["thèse intacte"])
