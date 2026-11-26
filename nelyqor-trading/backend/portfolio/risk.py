"""Score de risque expérimental, plafond de position, CVaR, scénarios par bootstrap par blocs, objectif J.

Rappels du cahier des charges :
- le score de risque n'est PAS une probabilité de perte ;
- au-delà de 45/100 le montant est réduit, l'opportunité n'est pas exclue pour ce seul motif ;
- la formule de plafond ne produit jamais de mise minimale forcée ;
- les poids 25/20/15/15/15/10 sont des points de départ à tester.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from ..core.num import ZERO, D

DEFAULT_WEIGHTS = {"volatility": 25, "liquidity": 20, "bearish": 15, "anomaly": 15, "uncertainty": 15, "macro": 10}


@dataclass
class RiskInputs:
    """Chaque composante est normalisée dans [0,1] (1 = risque maximal)."""

    volatility: float
    liquidity: float
    bearish: float
    anomaly: float
    uncertainty: float
    macro: float = 0.5  # inconnu par défaut : neutre-prudent, module macro non branché


def risk_score(x: RiskInputs, weights: dict[str, float] | None = None) -> float:
    w = weights or DEFAULT_WEIGHTS
    tot = sum(w.values())
    s = sum(w[k] * float(np.clip(getattr(x, k), 0, 1)) for k in w) / tot
    return round(100 * s, 2)


def normalize_volatility(daily_vol: float, lo: float = 0.01, hi: float = 0.12) -> float:
    return float(np.clip((daily_vol - lo) / (hi - lo), 0, 1))


def normalize_liquidity(depth_eur_within_50bps: float, order_eur: float) -> float:
    """1 si la profondeur ne couvre pas 10x l'ordre ; 0 si elle couvre 200x."""
    if order_eur <= 0:
        return 0.0
    cover = depth_eur_within_50bps / order_eur
    return float(np.clip(1 - (np.log10(max(cover, 1e-9)) - 1) / (np.log10(200) - 1), 0, 1))


def position_cap(equity_eur: Decimal, risk: float, confidence: float, base_fraction: Decimal = D("0.5"),
                 exponent: int = 2) -> Decimal:
    """plafond = equity * 0.5 * (1 - risk/100)^2 * confidence/100 (formule candidate à tester)."""
    r = D(str(max(0.0, min(100.0, risk)))) / 100
    c = D(str(max(0.0, min(100.0, confidence)))) / 100
    cap = equity_eur * base_fraction * (1 - r) ** exponent * c
    return max(ZERO, cap)


def cvar(losses: np.ndarray, alpha: float = 0.95) -> float:
    """Perte moyenne dans la queue défavorable (1-alpha), k = ceil(n(1-alpha)) pires pertes. Prudence petits
    échantillons : jamais sous la pire perte / 2. (Phase 5 : l'ancien minimum de 3 observations rendait
    l'estimateur MOINS prudent quand n < 60 — défaut relevé par l'audit mathématique ; NaN ignorés.)"""
    losses = np.asarray(losses, dtype=float)
    losses = np.sort(losses[np.isfinite(losses)])[::-1]
    n = len(losses)
    if n == 0:
        return 0.0
    k = max(1, int(np.ceil(round(n * (1 - alpha), 9))))  # round : évite 1000*0.05 -> 50.000000000000004
    k = min(k, n)
    est = float(losses[:k].mean())
    if n < 100:
        est = max(est, float(losses[0]) / 2)
    return est


def block_bootstrap(returns: np.ndarray, horizon: int, n_scen: int, block: int, seed: int = 0) -> np.ndarray:
    """Scénarios de rendements cumulés (log) sur `horizon` pas, par blocs contigus (préserve l'autocorrélation).
    `returns` : matrice T x A de log-rendements (A actifs → dépendance croisée préservée)."""
    r = np.atleast_2d(returns.T).T if returns.ndim == 1 else returns
    r = r[~np.isnan(r).any(axis=1)]
    T, A = r.shape
    if T < block * 2:
        raise ValueError("historique insuffisant pour le bootstrap")
    rng = np.random.default_rng(seed)
    out = np.zeros((n_scen, A))
    nb = int(np.ceil(horizon / block))
    for s in range(n_scen):
        starts = rng.integers(0, T - block + 1, nb)  # borne haute exclue : +1 pour pouvoir tirer le dernier bloc
        path = np.concatenate([r[i: i + block] for i in starts])[:horizon]
        out[s] = path.sum(axis=0)
    return out


def stress_scenarios(n_assets: int, crash: float = -0.35, corr_crash: float = -0.25) -> np.ndarray:
    """Scénarios de krach ajoutés au bootstrap (log-rendements)."""
    return np.array([np.full(n_assets, np.log1p(crash)), np.full(n_assets, np.log1p(corr_crash)),
                     np.full(n_assets, np.log1p(-0.10))])


def portfolio_returns(weights: np.ndarray, scen_log: np.ndarray, cost_frac: float) -> np.ndarray:
    """Rendements simples du portefeuille par scénario, après coût de transaction marginal."""
    simple = np.expm1(scen_log)
    return simple @ weights - cost_frac


def objective_j(rp: np.ndarray, lam: float, gamma: float, uncertainty: float) -> float:
    """J = moyenne(log(1+r_p)) - lambda * CVaR95(pertes) - gamma * incertitude.
    Un scénario de richesse non positive => -inf (risque de ruine traité explicitement)."""
    if np.any(1 + rp <= 0):
        return float("-inf")
    return float(np.mean(np.log1p(rp)) - lam * cvar(-rp) - gamma * uncertainty)


def choose_weight(scen_log: np.ndarray, current_w: np.ndarray, candidates: list[np.ndarray], fee: float,
                  lam: float, gamma: float, uncertainty: float, hysteresis: float = 0.0) -> tuple[np.ndarray, float, float]:
    """Choisit parmi des allocations candidates (dont « ne rien faire » et « tout en euros »).
    Une réallocation n'est retenue que si son gain dépasse celui du statu quo + hystérésis."""
    def J(w: np.ndarray) -> float:
        turnover = float(np.abs(w - current_w).sum())
        return objective_j(portfolio_returns(w, scen_log, turnover * fee), lam, gamma, uncertainty)

    j_hold = J(current_w)
    best_w, best_j = current_w, j_hold
    for w in candidates:
        j = J(w)
        if j > best_j + (hysteresis if not np.allclose(w, current_w) else 0):
            best_w, best_j = w, j
    return best_w, best_j, j_hold


def fractional_kelly(mean: float, var: float, fraction: float = 0.25, cap: float = 0.35) -> float:
    """Kelly fractionné borné (candidat, pas une obligation). 0 si l'avantage est négatif."""
    if var <= 0 or mean <= 0:
        return 0.0
    return float(min(cap, fraction * mean / var))


def net_round_trip(entry: Decimal, exit_: Decimal, fee_in: Decimal, fee_out: Decimal) -> Decimal:
    """R_net = (sortie/entrée) * (1 - frais_entrée) * (1 - frais_sortie) - 1"""
    return (exit_ / entry) * (1 - fee_in) * (1 - fee_out) - 1


def breakeven_move(fee_in: Decimal, fee_out: Decimal, spread_frac: Decimal = ZERO, fill_prob: Decimal = D(1)
                   ) -> Decimal:
    """Hausse minimale pour un R_net nul, ajustée de la probabilité d'exécution (ordres non remplis = coût
    d'opportunité). Le spread n'est ajouté que s'il n'est pas déjà dans les prix utilisés."""
    be = 1 / ((1 - fee_in) * (1 - fee_out)) - 1 + spread_frac
    return be / fill_prob if fill_prob > 0 else D("Infinity")
