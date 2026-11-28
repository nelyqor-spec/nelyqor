"""Dimensionnement en trois étapes STRICTEMENT séparées (module pur : numpy + stdlib, aucun état, aucun réseau).

(a) `decide_invest`  : investir ou non (régime, trésorerie, blocages explicites) -> booléen + raisons ;
(b) `select_assets`  : quels actifs (classement par score, éligibilité, filtres EXPLICITES) -> liste classée ;
(c) `size_positions` : combien d'euros par actif retenu -> montants.

Le dimensionnement (c) ne décide JAMAIS d'investir ni ne sélectionne : un actif qui lui est passé est
dimensionné selon la méthode choisie, et un montant nul n'est qu'une conséquence de la formule ou des
contraintes (minimum d'ordre, score de risque, plancher Kelly), toujours accompagnée d'une raison.

Conventions d'unités (à respecter par l'appelant, rien n'est deviné) :
- les rendements sont des rendements SIMPLES (arithmétiques) par période, R = P_t / P_{t-1} - 1 ;
  `simple_moments_from_log` convertit des moments de log-rendements sous hypothèse log-normale ;
- toutes les grandeurs d'une même méthode sont dans la MÊME unité de temps (par bougie, ou par jour :
  `per_period_to_daily` fait la conversion sous hypothèse i.i.d. : moyenne x k, variance x k, écart-type x sqrt(k)) ;
- le ratio de Kelly mu / sigma^2 est invariant à l'unité de temps ; la volatilité cible et la CVaR ne le sont pas.

Contraintes communes appliquées par `size_positions`, dans cet ordre :
1. capital investissable = max(0, trésorerie - réserve) / (1 + frais) ;
2. poids bruts de la méthode (fractions du capital investissable), bornés à [0, poids_max], somme <= 1 (pas de levier) ;
3. RÈGLE UTILISATEUR : poids x (1 - (rs - 45) / 55)^2 si le score de risque rs dépasse 45/100 (même forme que
   `xsection.run_xsection` / `xsection_v2.size`) ; l'argent libéré reste en euros (aucune redistribution) ;
4. montant = poids x capital investissable, arrondi VERS LE BAS au pas de quantité (avec le prix) et au pas en euros ;
5. montant < minimum d'ordre (5 EUR Bitvavo) -> 0 : l'argent reste en euros ; jamais d'arrondi vers le haut.
Garanties : montants >= 0, somme des montants x (1 + frais) <= trésorerie - réserve <= trésorerie.

Le score de risque n'est pas une probabilité ; ce module ne produit ni probabilité ni score de confiance.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

import numpy as np

BITVAVO_MIN_ORDER_EUR = 5.0
RISK_CUT = 45.0
METHODS = ("egal", "volatilite", "kelly", "cvar", "variance_min")


# --------------------------------------------------------------------------------------------------------------
# Conversions d'unités
# --------------------------------------------------------------------------------------------------------------
def per_period_to_daily(mean: float, var: float, periods_per_day: float) -> tuple[float, float]:
    """Moments par période -> par jour sous hypothèse i.i.d. : (mean x k, var x k)."""
    if not periods_per_day > 0:
        raise ValueError("periods_per_day doit être > 0")
    return mean * periods_per_day, var * periods_per_day


def simple_moments_from_log(mu_log: float, var_log: float) -> tuple[float, float]:
    """Moments EXACTS du rendement simple R = exp(X) - 1 quand X ~ N(mu_log, var_log) (log-normale) :
    E[R] = exp(mu + s2/2) - 1 ; Var[R] = (exp(s2) - 1) exp(2 mu + s2).
    Au premier ordre E[R] ~ mu_log + var_log / 2 : prendre la moyenne des LOG-rendements pour Kelly sous-estime
    la fraction pleine de 1/2 (f = mu_log/s2 au lieu de (mu_log + s2/2)/s2)."""
    if var_log < 0:
        raise ValueError("variance négative")
    m = math.exp(mu_log + var_log / 2) - 1
    v = (math.exp(var_log) - 1) * math.exp(2 * mu_log + var_log)
    return m, v


def risk_score_from_daily_vol(daily_vol: float, lo: float = 0.01, hi: float = 0.12) -> float:
    """Score 0-100 identique à `xsection.risk_score_vol` (vol journalière 1 % -> 0, 12 % -> 100).
    Pas une probabilité. Entrée non finie -> NaN (traité comme « risque inconnu » par `risk_factor`)."""
    if not math.isfinite(daily_vol):
        return float("nan")
    return float(np.clip((daily_vol - lo) / (hi - lo), 0.0, 1.0) * 100)


def risk_factor(risk_score: float, cut: float = RISK_CUT) -> float:
    """Facteur multiplicatif de la règle utilisateur : 1 si rs <= cut, (1 - (rs - cut)/(100 - cut))^2 au-delà,
    0 à 100 (rs borné à [0, 100]). rs = 45 -> 1 ; 72,5 -> 0,25 ; 100 -> 0.
    ÉCART VOLONTAIRE avec l'existant : un score NaN donnait `NaN > 45 == False`, donc AUCUNE réduction ;
    ici un score inconnu donne 0 (on ne dimensionne pas un risque qu'on ne sait pas mesurer)."""
    if not math.isfinite(risk_score):
        return 0.0
    rs = min(100.0, max(0.0, risk_score))
    if rs <= cut:
        return 1.0
    return max(0.0, 1.0 - (rs - cut) / (100.0 - cut)) ** 2


# --------------------------------------------------------------------------------------------------------------
# (a) Décision d'investir
# --------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class InvestDecision:
    invest: bool
    reasons: tuple[str, ...]


def decide_invest(*, regime_ok: bool | None, cash_eur: float, reserve_eur: float = 0.0,
                  min_order_eur: float = BITVAVO_MIN_ORDER_EUR, fee_rate: float = 0.0,
                  blockers: Sequence[str] = ()) -> InvestDecision:
    """Investir (True) ou rester 100 % en euros (False). `regime_ok` vient de l'extérieur (filtre de régime
    déterministe) ; None = régime inconnu -> on n'investit pas. `blockers` : motifs explicites de blocage
    (ex. « données périmées »). Aucune probabilité n'est calculée ici."""
    reasons: list[str] = []
    if regime_ok is None:
        reasons.append("régime inconnu : pas de nouvelle entrée")
    elif not regime_ok:
        reasons.append("régime défavorable : pas de nouvelle entrée")
    reasons += [f"blocage : {b}" for b in blockers]
    investable = _investable(cash_eur, reserve_eur, fee_rate)
    if investable < min_order_eur:
        reasons.append(f"capital investissable {investable:.2f} EUR < minimum d'ordre {min_order_eur:.2f} EUR")
    invest = not reasons
    if invest:
        reasons.append("conditions d'entrée réunies")
    return InvestDecision(invest, tuple(reasons))


# --------------------------------------------------------------------------------------------------------------
# (b) Sélection
# --------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Selection:
    selected: tuple[str, ...]
    excluded: dict[str, str]


def select_assets(scores: Mapping[str, float], *, top_n: int, min_score: float = 0.0,
                  eligible: Mapping[str, bool] | None = None,
                  must_be_positive: Mapping[str, float] | None = None) -> Selection:
    """Classement décroissant des scores (égalités : ordre alphabétique, déterministe), puis les `top_n` premiers.
    Exclusions explicites : score non fini, score <= min_score, non éligible, et — si fourni — critère
    `must_be_positive` <= 0 ou non fini. C'est ICI (et non dans le dimensionnement) qu'un filtre du type
    « moyenne passée > 0 » doit vivre s'il est voulu (c'est ce que faisait implicitement le « Kelly » de v2)."""
    if top_n < 0:
        raise ValueError("top_n doit être >= 0")
    excluded: dict[str, str] = {}
    ok: list[tuple[float, str]] = []
    for a, s in scores.items():
        if not math.isfinite(s):
            excluded[a] = "score non fini"
        elif eligible is not None and not eligible.get(a, False):
            excluded[a] = "non éligible"
        elif s <= min_score:
            excluded[a] = f"score {s:.4g} <= seuil {min_score:.4g}"
        elif must_be_positive is not None and not (math.isfinite(must_be_positive.get(a, float("nan")))
                                                   and must_be_positive[a] > 0):
            excluded[a] = "critère explicite <= 0 ou inconnu"
        else:
            ok.append((s, a))
    ok.sort(key=lambda t: (-t[0], t[1]))
    for _, a in ok[top_n:]:
        excluded[a] = "hors du top"
    return Selection(tuple(a for _, a in ok[:top_n]), excluded)


# --------------------------------------------------------------------------------------------------------------
# Formules de poids (fractions du capital investissable, sans contrainte commune)
# --------------------------------------------------------------------------------------------------------------
def equal_weights(n: int) -> np.ndarray:
    """w_i = 1/n."""
    return np.full(n, 1.0 / n) if n > 0 else np.zeros(0)


def target_vol_weights(sigma: Sequence[float], target_vol: float) -> np.ndarray:
    """Volatilité cible PAR POCHE, sans levier : w_i = (1/n) x min(1, target_vol / sigma_i).
    sigma_i et target_vol dans la même unité de temps. Ce n'est PAS une cible de volatilité du portefeuille
    (les corrélations sont ignorées) : chaque poche égale voit sa volatilité plafonnée à target_vol.
    sigma_i <= 0 ou non fini -> 0 (une volatilité estimée nulle signale des données plates, pas un actif sûr)."""
    s = np.asarray(sigma, dtype=float)
    n = len(s)
    if n == 0:
        return np.zeros(0)
    if not target_vol > 0:
        raise ValueError("target_vol doit être > 0")
    out = np.zeros(n)
    good = np.isfinite(s) & (s > 0)
    out[good] = np.minimum(1.0, target_vol / s[good]) / n
    return out


def kelly_fraction(mu: float, var: float, fraction: float = 0.25, cap: float = 1.0, floor: float = 0.0) -> float:
    """Kelly fractionné pour UN actif : f = clip(fraction x mu / var, floor, cap).
    mu, var : moyenne et variance du rendement SIMPLE excédentaire par période (même unité ; le ratio est
    invariant à l'unité). mu/var est l'argmax du développement d'ordre 2 de E[log(1 + f R)], exact en temps
    continu pour un mouvement brownien géométrique (mu = dérive arithmétique). Aucun rôle de filtre :
    si mu <= 0 la formule donne une fraction <= 0, ramenée au `floor` (0 par défaut, documenté) ; décider de
    ne pas investir appartient à `decide_invest` / `select_assets`. var <= 0 ou non fini -> 0 (indéfini)."""
    if not (0 <= floor <= cap):
        raise ValueError("il faut 0 <= floor <= cap")
    if not fraction >= 0:
        raise ValueError("fraction doit être >= 0")
    if not (math.isfinite(mu) and math.isfinite(var)) or var <= 0:
        return 0.0
    return float(min(cap, max(floor, fraction * mu / var)))


def historical_cvar(returns: Sequence[float] | np.ndarray, alpha: float = 0.95) -> float:
    """CVaR historique (expected shortfall) des PERTES, positive = perte : moyenne des k plus grandes pertes
    L = -R, k = ceil(n (1 - alpha)). Les valeurs non finies sont ignorées. Échantillon vide -> NaN.
    Peut être <= 0 si la queue ne contient aucune perte (le dimensionnement le traite explicitement)."""
    if not 0 < alpha < 1:
        raise ValueError("alpha doit être dans ]0, 1[")
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n == 0:
        return float("nan")
    k = max(1, math.ceil(round(n * (1 - alpha), 9)))
    losses = np.sort(-r)[::-1]
    return float(losses[:k].mean())


def cvar_weights(cvars: Sequence[float], budget: float, max_weight: float = 1.0) -> np.ndarray:
    """Budget de perte : w_i = budget / CVaR_i (la CVaR de la position vaut alors budget x capital), borné à
    max_weight. CVaR_i <= 0 (aucune perte dans la queue historique) -> max_weight (la contrainte ne lie pas,
    le plafond s'applique). CVaR non finie -> 0."""
    c = np.asarray(cvars, dtype=float)
    if not budget > 0:
        raise ValueError("budget doit être > 0")
    out = np.zeros(len(c))
    fin = np.isfinite(c)
    pos = fin & (c > 0)
    out[pos] = np.minimum(max_weight, budget / c[pos])
    out[fin & (c <= 0)] = max_weight
    return out


def ledoit_wolf_cov(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Covariance rétrécie de Ledoit-Wolf (2004, « A well-conditioned estimator ») vers m I, m = tr(S)/p :
    Sigma = delta m I + (1 - delta) S, delta = min(b2_bar, d2) / d2, avec S = X'X/n (X centré),
    d2 = ||S - mI||^2/p, b2_bar = (1/n^2) sum_k ||x_k x_k' - S||^2 / p (norme de Frobenius).
    `returns` : matrice T x p sans valeur manquante. Renvoie (Sigma, delta)."""
    x = np.asarray(returns, dtype=float)
    if x.ndim != 2 or x.shape[0] < 2:
        raise ValueError("il faut une matrice T x p avec T >= 2")
    if not np.isfinite(x).all():
        raise ValueError("valeurs non finies : nettoyer les lignes avant")
    n, p = x.shape
    x = x - x.mean(axis=0)
    s = x.T @ x / n
    m = float(np.trace(s)) / p
    d2 = float(((s - m * np.eye(p)) ** 2).sum()) / p
    if d2 <= 0:
        return s.copy(), 0.0
    b2_bar = float(sum(((np.outer(r, r) - s) ** 2).sum() for r in x)) / (n * n) / p
    delta = min(b2_bar, d2) / d2
    return delta * m * np.eye(p) + (1 - delta) * s, delta


def _project_capped_simplex(v: np.ndarray, cap: float) -> np.ndarray:
    """Projection euclidienne sur {w : sum w = 1, 0 <= w <= cap} (bisection sur le multiplicateur)."""
    lo, hi = float(v.min()) - cap, float(v.max())
    for _ in range(200):
        tau = (lo + hi) / 2
        if np.clip(v - tau, 0.0, cap).sum() > 1:
            lo = tau
        else:
            hi = tau
    return np.clip(v - (lo + hi) / 2, 0.0, cap)


def min_variance_weights(cov: np.ndarray, max_weight: float = 1.0, tol: float = 1e-14,
                         max_iter: int = 50_000) -> np.ndarray:
    """Variance minimale long-only : argmin w' Sigma w s.c. sum w = 1, 0 <= w <= max_weight (gradient projeté
    accéléré ; problème convexe, donc optimum global à la tolérance près). Sans borne active, la solution est
    Sigma^-1 1 / (1' Sigma^-1 1) ; à deux actifs non corrélés : w1 = s2^2 / (s1^2 + s2^2).
    Si p x max_weight <= 1, tous les poids valent max_weight (le reste demeure en euros)."""
    c = np.asarray(cov, dtype=float)
    p = c.shape[0]
    if p == 0:
        return np.zeros(0)
    if c.shape != (p, p) or not np.isfinite(c).all():
        raise ValueError("covariance p x p finie attendue")
    if p * max_weight <= 1 + 1e-12:
        return np.full(p, max_weight)
    lmax = float(np.linalg.eigvalsh((c + c.T) / 2).max())
    if lmax <= 0:
        return _project_capped_simplex(np.full(p, 1.0 / p), max_weight)
    step = 1.0 / (2 * lmax)
    w = _project_capped_simplex(np.full(p, 1.0 / p), max_weight)
    y, t = w.copy(), 1.0
    for _ in range(max_iter):
        w_new = _project_capped_simplex(y - step * 2 * c @ y, max_weight)
        t_new = (1 + math.sqrt(1 + 4 * t * t)) / 2
        y = w_new + (t - 1) / t_new * (w_new - w)
        if float(np.abs(w_new - w).max()) < tol:
            w = w_new
            break
        w, t = w_new, t_new
    return w


# --------------------------------------------------------------------------------------------------------------
# (c) Dimensionnement en euros
# --------------------------------------------------------------------------------------------------------------
@dataclass
class AllocationResult:
    amounts_eur: dict[str, float]
    weights: dict[str, float]  # poids après plafonds et règle de risque, avant arrondi et minimum
    invested_eur: float
    cash_left_eur: float
    reasons: dict[str, list[str]] = field(default_factory=dict)


def _investable(cash_eur: float, reserve_eur: float, fee_rate: float) -> float:
    if not (math.isfinite(cash_eur) and math.isfinite(reserve_eur)) or fee_rate < 0:
        raise ValueError("trésorerie/réserve finies et frais >= 0 attendus")
    return max(0.0, cash_eur - max(0.0, reserve_eur)) / (1 + fee_rate)


def _floor_to(x: float, step: float) -> float:
    if step <= 0:
        return x
    d = (Decimal(repr(x)) / Decimal(repr(step))).to_integral_value(rounding=ROUND_FLOOR)
    return float(d * Decimal(repr(step)))


def _column(returns: np.ndarray, j: int) -> np.ndarray:
    r = returns[:, j]
    return r[np.isfinite(r)]


def size_positions(assets: Sequence[str], cash_eur: float, method: str = "egal", *,
                   reserve_eur: float = 0.0, max_weight: float = 1.0,
                   min_order_eur: float = BITVAVO_MIN_ORDER_EUR, fee_rate: float = 0.0,
                   risk_scores: Mapping[str, float] | None = None, risk_cut: float = RISK_CUT,
                   sigma: Mapping[str, float] | None = None, target_vol: float | None = None,
                   mu: Mapping[str, float] | None = None, var: Mapping[str, float] | None = None,
                   kelly_frac: float = 0.25, kelly_floor: float = 0.0,
                   returns: np.ndarray | None = None, cvar_alpha: float = 0.95, cvar_budget: float | None = None,
                   min_obs: int = 20,
                   prices: Mapping[str, float] | None = None, qty_steps: Mapping[str, float] | None = None,
                   quote_step: float = 0.0) -> AllocationResult:
    """Montants en euros pour des actifs DÉJÀ sélectionnés (aucune décision ni sélection ici).

    Méthodes (entrées attendues) :
    - "egal"         : rien ;
    - "volatilite"   : `sigma` (par actif) et `target_vol`, même unité ;
    - "kelly"        : `mu` et `var` (rendements simples, même unité) OU `returns` ; si la somme des fractions
                       dépasse 1, elles sont réduites proportionnellement (Kelly par actif = approximation
                       diagonale : les corrélations sont ignorées) ;
    - "cvar"         : `returns` (T x n, colonnes dans l'ordre de `assets`) et `cvar_budget` ;
    - "variance_min" : `returns` ; covariance de Ledoit-Wolf sur les lignes complètes, long-only.
    `risk_scores` absent -> aucune réduction ; présent -> la règle des 45/100 s'applique (inconnu -> 0).
    """
    if method not in METHODS:
        raise ValueError(f"méthode inconnue : {method}")
    if not 0 <= max_weight <= 1:
        raise ValueError("max_weight doit être dans [0, 1] (pas de levier)")
    if len(set(assets)) != len(assets):
        raise ValueError("actifs en double")
    n = len(assets)
    investable = _investable(cash_eur, reserve_eur, fee_rate)
    reasons: dict[str, list[str]] = {a: [] for a in assets}
    rets = None if returns is None else np.asarray(returns, dtype=float)
    if rets is not None and (rets.ndim != 2 or rets.shape[1] != n):
        raise ValueError("returns doit être une matrice T x len(assets)")

    if n == 0:
        w = np.zeros(0)
    elif method == "egal":
        w = equal_weights(n)
    elif method == "volatilite":
        if sigma is None or target_vol is None:
            raise ValueError("volatilite : sigma et target_vol requis")
        w = target_vol_weights([sigma.get(a, float("nan")) for a in assets], target_vol)
        for a, wi in zip(assets, w, strict=True):
            if wi == 0:
                reasons[a].append("volatilité nulle ou inconnue : poids 0")
    elif method == "kelly":
        w = np.zeros(n)
        for j, a in enumerate(assets):
            if mu is not None and var is not None:
                m, v = mu.get(a, float("nan")), var.get(a, float("nan"))
            elif rets is not None and len(_column(rets, j)) >= min_obs:
                col = _column(rets, j)
                m, v = float(col.mean()), float(col.var(ddof=1))
            else:
                m = v = float("nan")
            w[j] = kelly_fraction(m, v, kelly_frac, cap=max_weight, floor=min(kelly_floor, max_weight))
            if not (math.isfinite(m) and math.isfinite(v)) or v <= 0:
                reasons[a].append("moments indisponibles ou variance nulle : Kelly indéfini, poids 0")
            elif m <= 0:
                reasons[a].append(f"Kelly : mu <= 0, montant au plancher ({w[j]:.4g})")
    elif method == "cvar":
        if rets is None or cvar_budget is None:
            raise ValueError("cvar : returns et cvar_budget requis")
        cv = []
        for j, a in enumerate(assets):
            col = _column(rets, j)
            if len(col) < min_obs:
                cv.append(float("nan"))
                reasons[a].append(f"historique {len(col)} < {min_obs} observations : poids 0")
            else:
                cv.append(historical_cvar(col, cvar_alpha))
                if cv[-1] <= 0:
                    reasons[a].append("aucune perte dans la queue historique : plafond appliqué")
        w = cvar_weights(cv, cvar_budget, max_weight)
    else:  # variance_min
        if rets is None:
            raise ValueError("variance_min : returns requis")
        rows = rets[np.isfinite(rets).all(axis=1)]
        sd = rows.std(axis=0) if len(rows) else np.zeros(n)
        live = [j for j in range(n) if len(rows) >= min_obs and sd[j] > 0]
        w = np.zeros(n)
        for j in set(range(n)) - set(live):
            reasons[assets[j]].append("historique insuffisant ou variance nulle : poids 0")
        if live:
            cov, _delta = ledoit_wolf_cov(rows[:, live])
            w[live] = min_variance_weights(cov, max_weight)

    w = np.clip(np.nan_to_num(w, nan=0.0), 0.0, max_weight)
    if w.sum() > 1:
        w = w / w.sum()
    if risk_scores is not None:
        for j, a in enumerate(assets):
            rs = float(risk_scores.get(a, float("nan")))
            f = risk_factor(rs, risk_cut)
            if f < 1:
                reasons[a].append(f"score de risque {rs:.4g}/100 > {risk_cut:.0f} : poids x {f:.4g}"
                                  if math.isfinite(rs) else "score de risque inconnu : poids 0")
            w[j] *= f

    amounts: dict[str, float] = {}
    for j, a in enumerate(assets):
        amt = float(w[j]) * investable
        px = prices.get(a) if prices else None
        qs = qty_steps.get(a) if qty_steps else None
        if px is not None and qs is not None and px > 0 and qs > 0:
            amt = _floor_to(amt / px, qs) * px  # quantité arrondie vers le bas au pas de quantité
        if quote_step > 0:
            amt = _floor_to(amt, quote_step)  # montant arrondi vers le bas au pas en euros
        amt = min(amt, float(w[j]) * investable)  # l'arrondi ne peut jamais augmenter le montant
        if 0 < amt < min_order_eur or (amt == 0 and w[j] > 0):
            reasons[a].append(f"montant {amt:.2f} EUR < minimum d'ordre {min_order_eur:.2f} EUR : reste en euros")
            amt = 0.0
        amounts[a] = max(0.0, amt)
    invested = sum(amounts.values())
    return AllocationResult(amounts, {a: float(w[j]) for j, a in enumerate(assets)}, invested,
                            cash_eur - invested * (1 + fee_rate), reasons)
