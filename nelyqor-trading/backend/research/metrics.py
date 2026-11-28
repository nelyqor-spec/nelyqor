"""Métriques de performance avec hypothèses explicites, intervalles d'incertitude et Deflated Sharpe Ratio."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .backtest import BTResult


@dataclass
class Metrics:
    net_return: float
    max_drawdown: float
    profit_factor: float
    n_trades: int
    win_rate: float
    sharpe: float
    sortino: float
    periods_per_year: float
    turnover_eur: float
    fees_eur: float
    exposure: float
    mean_trade_ret: float
    ci95_mean_trade: tuple[float, float]
    ci95_net_return: tuple[float, float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def max_drawdown(eq: pd.Series) -> float:
    peak = eq.cummax()
    return float((eq / peak - 1).min())


def sharpe_sortino(eq: pd.Series, ppy: float) -> tuple[float, float]:
    r = eq.pct_change().dropna()
    if len(r) < 2 or r.std() == 0:
        return 0.0, 0.0
    sh = float(r.mean() / r.std() * math.sqrt(ppy))
    dn = r[r < 0]
    so = float(r.mean() / dn.std() * math.sqrt(ppy)) if len(dn) > 1 and dn.std() > 0 else 0.0
    return sh, so


def block_bootstrap_ci(x: np.ndarray, stat: Any, block: int = 5, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    if len(x) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    b = max(1, min(block, len(x) // 2))
    out = np.empty(n)
    nb = int(np.ceil(len(x) / b))
    for k in range(n):
        starts = rng.integers(0, len(x) - b + 1, nb)
        out[k] = stat(np.concatenate([x[s: s + b] for s in starts])[: len(x)])
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def compute_metrics(res: BTResult, ppy: float) -> Metrics:
    eq = res.equity
    rets = np.array([t.ret for t in res.trades])
    gains = sum(t.proceeds_eur - t.cost_eur for t in res.trades if t.proceeds_eur > t.cost_eur)
    losses = -sum(t.proceeds_eur - t.cost_eur for t in res.trades if t.proceeds_eur <= t.cost_eur)
    pf = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)
    sh, so = sharpe_sortino(eq, ppy)
    initial = res.config.initial_eur
    # IC du rendement net : bootstrap par blocs des rendements par période, recomposés
    pr = eq.pct_change().dropna().to_numpy()
    ci_ret = block_bootstrap_ci(pr, lambda s: float(np.prod(1 + s) - 1), block=48) if len(pr) > 100 else (
        float("nan"), float("nan"))
    return Metrics(
        net_return=float(eq.iloc[-1] / initial - 1), max_drawdown=max_drawdown(eq), profit_factor=float(pf),
        n_trades=len(res.trades), win_rate=float((rets > 0).mean()) if len(rets) else 0.0, sharpe=sh, sortino=so,
        periods_per_year=ppy, turnover_eur=res.turnover_eur, fees_eur=res.fees_eur, exposure=res.exposure,
        mean_trade_ret=float(rets.mean()) if len(rets) else 0.0,
        ci95_mean_trade=block_bootstrap_ci(rets, np.mean) if len(rets) >= 3 else (float("nan"), float("nan")),
        ci95_net_return=ci_ret,
    )


def deflated_sharpe(sr: float, n_obs: int, n_trials: int, sr_var_trials: float, skew: float = 0.0,
                    kurt: float = 3.0) -> float:
    """DSR (Bailey & López de Prado 2014). sr : Sharpe NON annualisé par observation."""
    if n_trials < 1 or n_obs < 3:
        return float("nan")
    g = 0.5772156649
    if n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(max(sr_var_trials, 0.0)) * ((1 - g) * stats.norm.ppf(1 - 1 / n_trials)
                                                    + g * stats.norm.ppf(1 - 1 / (n_trials * math.e)))
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return float(stats.norm.cdf((sr - sr0) * math.sqrt(n_obs - 1) / denom))


@dataclass
class PromotionCriteria:
    min_profit_factor: float = 1.2
    min_trades: int = 100
    require_positive_oos: bool = True
    require_ci_lower_positive: bool = True
    require_doubled_fees_positive: bool = True
    min_regimes_positive_frac: float = 0.5


def evaluate_promotion(oos: Metrics, oos_doubled_fees: Metrics, regime_returns: dict[str, float],
                       leakage_detected: bool, safety_violations: int, c: PromotionCriteria | None = None
                       ) -> dict[str, Any]:
    """Seuils FIGÉS. Le laboratoire ne peut pas les modifier : ils sont définis ici, dans le code de production."""
    c = c or PromotionCriteria()
    checks = {
        "rendement_hors_echantillon_positif": oos.net_return > 0,
        f"profit_factor_>_{c.min_profit_factor}": oos.profit_factor > c.min_profit_factor,
        f"au_moins_{c.min_trades}_transactions": oos.n_trades >= c.min_trades,
        "borne_basse_IC95_rendement_moyen_>_0": bool(oos.ci95_mean_trade[0] > 0) if not math.isnan(
            oos.ci95_mean_trade[0]) else False,
        "frais_doubles_rendement_positif": oos_doubled_fees.net_return > 0,
        "stabilite_regimes": (sum(v > 0 for v in regime_returns.values()) / max(1, len(regime_returns)))
        >= c.min_regimes_positive_frac,
        "aucune_fuite_temporelle": not leakage_detected,
        "aucune_violation_securite": safety_violations == 0,
    }
    return {"promu": all(checks.values()), "criteres": checks,
            "non_satisfaits": [k for k, v in checks.items() if not v]}
