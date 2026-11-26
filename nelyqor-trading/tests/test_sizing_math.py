"""Cas à solution connue calculée à la main pour `backend.portfolio.sizing_math`, plus propriétés générales."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from backend.portfolio.sizing_math import (
    decide_invest,
    historical_cvar,
    kelly_fraction,
    ledoit_wolf_cov,
    min_variance_weights,
    risk_factor,
    risk_score_from_daily_vol,
    select_assets,
    simple_moments_from_log,
    size_positions,
    target_vol_weights,
)

# série connue : deux pertes (-10 %, -5 %) puis 0 %, 1 %, ..., 17 % (18 valeurs) -> n = 20
SERIE = np.array([-0.10, -0.05] + [k / 100 for k in range(18)])


# ------------------------------------------------------------------------------------------------ Kelly
def test_kelly_textbook():
    """f* = mu / s2 = 0,01 / 0,04 = 0,25 ; fraction 1/2 -> 0,125 ; fraction 1 -> 0,25."""
    assert kelly_fraction(0.01, 0.04, fraction=1.0) == pytest.approx(0.25)
    assert kelly_fraction(0.01, 0.04, fraction=0.5) == pytest.approx(0.125)


def test_kelly_bounds_and_degenerate():
    """mu = 0,05, s2 = 0,04, fraction 1 -> 1,25 brut, plafonné à cap = 1 (pas de levier) puis à 0,35.
    mu <= 0 -> fraction brute <= 0 -> plancher (0 par défaut, 0,02 si demandé). s2 = 0 ou NaN -> 0."""
    assert kelly_fraction(0.05, 0.04, 1.0, cap=1.0) == 1.0
    assert kelly_fraction(0.05, 0.04, 1.0, cap=0.35) == 0.35
    assert kelly_fraction(-0.01, 0.04, 0.5) == 0.0
    assert kelly_fraction(0.0, 0.04, 0.5) == 0.0
    assert kelly_fraction(-0.01, 0.04, 0.5, floor=0.02) == 0.02
    assert kelly_fraction(0.01, 0.0, 0.5) == 0.0
    assert kelly_fraction(float("nan"), 0.04, 0.5) == 0.0
    with pytest.raises(ValueError):
        kelly_fraction(0.01, 0.04, 0.5, cap=0.1, floor=0.2)


def test_kelly_time_unit_invariance():
    """Par bougie (1 h) mu = 0,0004, s2 = 0,0016/24 ; par jour : mu x 24 = 0,0096, s2 = 0,0016.
    Ratio identique : 0,0004 x 24 / 0,0016 = 6 -> 1/4 x 6 = 1,5 -> plafonné à 1 dans les deux cas ; avec
    fraction 0,1 : 0,6 dans les deux unités."""
    a = kelly_fraction(0.0004, 0.0016 / 24, 0.1)
    b = kelly_fraction(0.0004 * 24, 0.0016, 0.1)
    assert a == pytest.approx(0.6) and b == pytest.approx(0.6)


def test_log_mean_bias_of_v2_formula():
    """v2 utilise la moyenne des LOG-rendements : f_v2 = 1/4 x mu_log / s2. Avec mu_log = 0 et s2 = 0,04 :
    f_v2 = 0 (l'actif est écarté), alors que le rendement simple a une espérance e^(0,02) - 1 = 0,020201 > 0 et
    Var[R] = (e^0,04 - 1) e^0,04 = 0,042477 -> Kelly plein = 0,4756 (~ 1/2, le terme s2/2 / s2 oublié),
    1/4 Kelly = 0,1189."""
    mu_log, s2 = 0.0, 0.04
    f_v2 = 0.0 if mu_log <= 0 else min(0.35, 0.25 * mu_log / s2)
    m, v = simple_moments_from_log(mu_log, s2)
    assert m == pytest.approx(math.exp(0.02) - 1)
    assert v == pytest.approx((math.exp(0.04) - 1) * math.exp(0.04))
    assert f_v2 == 0.0
    assert kelly_fraction(m, v, 1.0) == pytest.approx(0.020201 / 0.042477, rel=1e-4)
    assert kelly_fraction(m, v, 0.25) == pytest.approx(0.1189, abs=1e-4)


def test_kelly_is_not_a_filter_in_sizing():
    """size_positions dimensionne ce qu'on lui donne : B (mu < 0) n'est pas retiré de la liste, il reçoit le
    plancher 0 avec une raison explicite. A : 1/2 x 0,01/0,04 = 0,125 -> 12,50 EUR sur 100 EUR."""
    r = size_positions(["A", "B"], 100.0, "kelly", mu={"A": 0.01, "B": -0.01}, var={"A": 0.04, "B": 0.04},
                       kelly_frac=0.5)
    assert set(r.amounts_eur) == {"A", "B"}
    assert r.amounts_eur == {"A": pytest.approx(12.5), "B": 0.0}
    assert any("mu <= 0" in x for x in r.reasons["B"])


def test_kelly_sum_above_one_is_scaled_down():
    """Deux actifs à f = 1 (plafond) -> somme 2 > 1 -> réduits à 1/2 chacun : 50 EUR + 50 EUR."""
    r = size_positions(["A", "B"], 100.0, "kelly", mu={"A": 0.1, "B": 0.1}, var={"A": 0.01, "B": 0.01},
                       kelly_frac=1.0)
    assert r.amounts_eur["A"] == pytest.approx(50) and r.amounts_eur["B"] == pytest.approx(50)


# ------------------------------------------------------------------------------------------------ volatilité
def test_target_vol_textbook():
    """Cible 2 %, s = 4 % -> 2/4 = 0,5 ; s = 1 % -> min(1, 2) = 1 (aucun levier).
    Deux actifs (4 %, 1 %) : poches de 1/2 -> (0,25, 0,5). s = 0 ou NaN -> 0."""
    assert target_vol_weights([0.04], 0.02)[0] == pytest.approx(0.5)
    assert target_vol_weights([0.01], 0.02)[0] == pytest.approx(1.0)
    np.testing.assert_allclose(target_vol_weights([0.04, 0.01], 0.02), [0.25, 0.5])
    np.testing.assert_allclose(target_vol_weights([0.0, float("nan")], 0.02), [0.0, 0.0])
    r = size_positions(["A"], 100.0, "volatilite", sigma={"A": 0.04}, target_vol=0.02)
    assert r.amounts_eur["A"] == pytest.approx(50.0)


# ------------------------------------------------------------------------------------------------ CVaR
def test_historical_cvar_known_series():
    """Pertes triées : 0,10 ; 0,05 ; 0 ; -0,01 ; ... n = 20.
    alpha = 0,95 -> k = ceil(20 x 0,05) = 1 -> CVaR = 0,10.
    alpha = 0,90 -> k = ceil(20 x 0,10) = 2 -> CVaR = (0,10 + 0,05)/2 = 0,075.
    alpha = 0,85 -> k = 3 -> (0,10 + 0,05 + 0)/3 = 0,05. Signe : positive = perte."""
    assert historical_cvar(SERIE, 0.95) == pytest.approx(0.10)
    assert historical_cvar(SERIE, 0.90) == pytest.approx(0.075)
    assert historical_cvar(SERIE, 0.85) == pytest.approx(0.05)
    assert historical_cvar(np.append(SERIE, np.nan), 0.90) == pytest.approx(0.075)  # NaN ignoré
    assert math.isnan(historical_cvar([], 0.95))
    assert historical_cvar([0.01, 0.02, 0.03], 0.5) < 0  # aucune perte : valeur négative, signalée


def test_cvar_budget_sizing():
    """Budget 1,5 % du capital, CVaR90 = 7,5 % -> w = 0,015/0,075 = 0,2 -> 20 EUR sur 100 EUR.
    Aucune perte dans la queue -> plafond max_weight = 0,3 -> 30 EUR."""
    r = size_positions(["A"], 100.0, "cvar", returns=SERIE[:, None], cvar_alpha=0.90, cvar_budget=0.015)
    assert r.amounts_eur["A"] == pytest.approx(20.0)
    gains = np.linspace(0.01, 0.2, 20)[:, None]
    r2 = size_positions(["A"], 100.0, "cvar", returns=gains, cvar_alpha=0.90, cvar_budget=0.015, max_weight=0.3)
    assert r2.amounts_eur["A"] == pytest.approx(30.0)
    r3 = size_positions(["A"], 100.0, "cvar", returns=SERIE[:5, None], cvar_budget=0.015)
    assert r3.amounts_eur["A"] == 0.0  # 5 < 20 observations


# ------------------------------------------------------------------------------------------------ variance minimale
def test_min_variance_two_uncorrelated():
    """s1 = 2 %, s2 = 4 %, rho = 0 : w1 = s2^2/(s1^2 + s2^2) = 0,0016/0,0020 = 0,8 ; w2 = 0,2.
    Avec plafond 0,6 : w1 bloqué à 0,6, le reste (0,4) sur l'actif 2."""
    cov = np.diag([0.02**2, 0.04**2])
    np.testing.assert_allclose(min_variance_weights(cov), [0.8, 0.2], atol=1e-8)
    np.testing.assert_allclose(min_variance_weights(cov, max_weight=0.6), [0.6, 0.4], atol=1e-8)
    np.testing.assert_allclose(min_variance_weights(cov, max_weight=0.4), [0.4, 0.4])  # 2 x 0,4 < 1


def test_min_variance_matches_closed_form_when_unconstrained():
    """Sans borne active : w = Sigma^-1 1 / (1' Sigma^-1 1). Sigma symétrique définie positive."""
    s = np.array([0.03, 0.04, 0.05])
    rho = np.array([[1, 0.3, 0.2], [0.3, 1, 0.4], [0.2, 0.4, 1]])
    cov = rho * np.outer(s, s)
    x = np.linalg.solve(cov, np.ones(3))
    assert (x > 0).all()
    np.testing.assert_allclose(min_variance_weights(cov), x / x.sum(), atol=1e-7)


def test_min_variance_long_only_drops_negative():
    """Deux actifs très corrélés (rho = 0,9), s1 = 1 %, s2 = 3 % : solution non contrainte
    w2 = (s1^2 - rho s1 s2)/(s1^2 + s2^2 - 2 rho s1 s2) = (1 - 2,7)/(1 + 9 - 5,4) = -0,37 < 0
    -> long-only : (1, 0)."""
    s1, s2, rho = 0.01, 0.03, 0.9
    cov = np.array([[s1 * s1, rho * s1 * s2], [rho * s1 * s2, s2 * s2]])
    np.testing.assert_allclose(min_variance_weights(cov), [1.0, 0.0], atol=1e-8)


def test_ledoit_wolf_against_sklearn_and_bounds():
    sk = pytest.importorskip("sklearn.covariance")
    x = np.random.default_rng(1).normal(0, 0.03, (60, 5))
    cov, delta = ledoit_wolf_cov(x)
    ref_cov, ref_delta = sk.ledoit_wolf(x)
    assert 0 <= delta <= 1
    assert delta == pytest.approx(ref_delta, rel=1e-9)
    np.testing.assert_allclose(cov, ref_cov, rtol=1e-9)


def test_variance_min_sizing_scale_invariant_in_returns():
    """Multiplier tous les rendements par c = 10 multiplie Sigma par 100 : les poids de variance minimale
    sont inchangés (la contrainte sum w = 1 est homogène de degré 0)."""
    x = np.random.default_rng(2).normal(0, 0.02, (100, 3)) * np.array([1.0, 2.0, 3.0])
    a = size_positions(["A", "B", "C"], 1000.0, "variance_min", returns=x)
    b = size_positions(["A", "B", "C"], 1000.0, "variance_min", returns=10 * x)
    for k in "ABC":
        assert a.weights[k] == pytest.approx(b.weights[k], abs=1e-7)
    assert sum(a.weights.values()) == pytest.approx(1.0)


# ------------------------------------------------------------------------------------------------ règle 45/100
def test_risk_factor_known_values():
    """(1 - (rs - 45)/55)^2 : 45 -> 1 ; 72,5 -> (1 - 0,5)^2 = 0,25 ; 100 -> 0 ; 30 -> 1 ; NaN -> 0."""
    assert risk_factor(45) == 1.0
    assert risk_factor(30) == 1.0
    assert risk_factor(72.5) == pytest.approx(0.25)
    assert risk_factor(100) == 0.0
    assert risk_factor(130) == 0.0
    assert risk_factor(float("nan")) == 0.0


def test_risk_factor_monotone_and_continuous():
    xs = np.linspace(0, 100, 1001)
    f = np.array([risk_factor(x) for x in xs])
    assert (np.diff(f) <= 1e-15).all()
    assert risk_factor(45 + 1e-9) == pytest.approx(1.0)


def test_risk_score_from_daily_vol_matches_existing_scale():
    """1 % -> 0 ; 12 % -> 100 ; 5,95 % -> 45 ; 8,975 % -> 72,5 (bornes identiques à xsection.risk_score_vol)."""
    assert risk_score_from_daily_vol(0.01) == 0.0
    assert risk_score_from_daily_vol(0.12) == pytest.approx(100.0)
    assert risk_score_from_daily_vol(0.0595) == pytest.approx(45.0)
    assert risk_score_from_daily_vol(0.08975) == pytest.approx(72.5)
    assert math.isnan(risk_score_from_daily_vol(float("nan")))


def test_risk_rule_applied_in_sizing_money_stays_in_euros():
    """Poids égal sur 100 EUR, 2 actifs : 50 + 50. Risques 45 et 72,5 -> 50 x 1 = 50 et 50 x 0,25 = 12,50.
    Le reste (37,50 EUR) n'est pas redistribué : il reste en euros. Risque 100 -> 0."""
    r = size_positions(["A", "B"], 100.0, "egal", risk_scores={"A": 45, "B": 72.5})
    assert r.amounts_eur == {"A": pytest.approx(50.0), "B": pytest.approx(12.5)}
    assert r.cash_left_eur == pytest.approx(37.5)
    r2 = size_positions(["A"], 100.0, "egal", risk_scores={"A": 100})
    assert r2.amounts_eur["A"] == 0.0 and r2.cash_left_eur == 100.0


# ------------------------------------------------------------------------------------------------ contraintes
def test_minimum_order_5_eur():
    """14 EUR en poids égal sur 3 actifs : 4,67 EUR < 5 -> 0 partout, 14 EUR restent en euros.
    15 EUR : 5,00 EUR chacun (égal au minimum : accepté)."""
    r = size_positions(["A", "B", "C"], 14.0, "egal")
    assert all(v == 0 for v in r.amounts_eur.values()) and r.cash_left_eur == 14.0
    r2 = size_positions(["A", "B", "C"], 15.0, "egal")
    assert all(v == pytest.approx(5.0) for v in r2.amounts_eur.values())


def test_insufficient_capital_all_in_euros():
    """Trésorerie 4 EUR < 5 -> pas d'investissement (a) et montants nuls (c). Réserve 50 > trésorerie 40 :
    capital investissable 0."""
    d = decide_invest(regime_ok=True, cash_eur=4.0)
    assert not d.invest and any("minimum" in x for x in d.reasons)
    assert size_positions(["A"], 4.0, "egal").amounts_eur["A"] == 0.0
    r = size_positions(["A"], 40.0, "egal", reserve_eur=50.0)
    assert r.amounts_eur["A"] == 0.0 and r.cash_left_eur == 40.0
    empty = size_positions([], 100.0, "egal")
    assert empty.amounts_eur == {} and empty.cash_left_eur == 100.0


def test_reserve_fees_and_max_weight():
    """Trésorerie 100, réserve 10, frais 0,25 % : investissable = 90/1,0025 = 89,7756 ; un actif -> 89,7756,
    coût total 89,7756 x 1,0025 = 90 <= 100 - 10. Poids max 0,3 sur 2 actifs égaux : 30 + 30, 40 restent."""
    r = size_positions(["A"], 100.0, "egal", reserve_eur=10.0, fee_rate=0.0025)
    assert r.amounts_eur["A"] == pytest.approx(90 / 1.0025)
    assert r.amounts_eur["A"] * 1.0025 <= 90.0 + 1e-9
    r2 = size_positions(["A", "B"], 100.0, "egal", max_weight=0.3)
    assert r2.amounts_eur == {"A": pytest.approx(30.0), "B": pytest.approx(30.0)}
    assert r2.cash_left_eur == pytest.approx(40.0)


def test_rounding_down_to_steps():
    """50 EUR à 3,00 EUR, pas de quantité 0,1 : 16,666... -> 16,6 unités -> 49,80 EUR (jamais 16,7 = 50,10).
    Pas en euros 1 EUR sur 33,333... -> 33 EUR."""
    r = size_positions(["A"], 50.0, "egal", prices={"A": 3.0}, qty_steps={"A": 0.1})
    assert r.amounts_eur["A"] == pytest.approx(49.8)
    r2 = size_positions(["A", "B", "C"], 100.0, "egal", quote_step=1.0)
    assert all(v == 33.0 for v in r2.amounts_eur.values())


def test_amounts_linear_in_cash():
    """Invariance d'échelle : loin du minimum et sans pas d'arrondi, montants(10 x trésorerie) = 10 x montants."""
    x = np.random.default_rng(4).normal(0.001, 0.03, (200, 3))
    cases: list[tuple[str, dict[str, Any]]] = [("egal", {}), ("cvar", {"returns": x, "cvar_budget": 0.01}),
                                              ("variance_min", {"returns": x}), ("kelly", {"returns": x})]
    for m, kw in cases:
        a = size_positions(["A", "B", "C"], 1000.0, m, **kw)
        b = size_positions(["A", "B", "C"], 10000.0, m, **kw)
        for k in "ABC":
            assert b.amounts_eur[k] == pytest.approx(10 * a.amounts_eur[k], rel=1e-9, abs=1e-9)


# ------------------------------------------------------------------------------------------------ (a) et (b)
def test_decide_invest_reasons():
    assert decide_invest(regime_ok=True, cash_eur=100.0).invest
    assert not decide_invest(regime_ok=False, cash_eur=100.0).invest
    assert not decide_invest(regime_ok=None, cash_eur=100.0).invest
    d = decide_invest(regime_ok=True, cash_eur=100.0, blockers=["données périmées"])
    assert not d.invest and "blocage : données périmées" in d.reasons
    assert not decide_invest(regime_ok=True, cash_eur=100.0, reserve_eur=96.0).invest  # 4 EUR < 5


def test_select_assets_ranking_and_explicit_filters():
    """Scores C 3 > A 2 = B 2 > D 1 ; E NaN exclu ; égalité A/B tranchée par l'ordre alphabétique.
    top 2 -> (C, A). Filtre explicite « moyenne 30 j > 0 » : C a -0,1 -> exclu ici (et nulle part ailleurs)."""
    sc = {"A": 2.0, "B": 2.0, "C": 3.0, "D": 1.0, "E": float("nan")}
    s = select_assets(sc, top_n=2)
    assert s.selected == ("C", "A")
    assert s.excluded["E"] == "score non fini" and s.excluded["B"] == "hors du top"
    s2 = select_assets(sc, top_n=2, must_be_positive={"A": 0.1, "B": 0.2, "C": -0.1, "D": 0.3})
    assert s2.selected == ("A", "B") and "C" in s2.excluded
    s3 = select_assets(sc, top_n=5, eligible={"A": True, "C": True}, min_score=2.5)
    assert s3.selected == ("C",)


# ------------------------------------------------------------------------------------------------ propriétés
@pytest.mark.parametrize("method", ["egal", "volatilite", "kelly", "cvar", "variance_min"])
def test_properties_random(method):
    """Pour 200 tirages : montants >= 0, sum x (1 + frais) <= trésorerie - réserve, 0 ou >= 5 EUR,
    montant non croissant quand le score de risque d'un actif augmente."""
    rng = np.random.default_rng(10)
    names = ["A", "B", "C", "D"]
    for _ in range(200):
        x = rng.normal(rng.normal(0, 0.002), rng.uniform(0.005, 0.06), (120, 4))
        x[rng.random(x.shape) < 0.02] = np.nan
        cash, reserve, fee = float(rng.uniform(0, 500)), float(rng.uniform(0, 50)), float(rng.choice([0, 0.0025]))
        kw: dict[str, Any] = {"returns": x, "reserve_eur": reserve, "fee_rate": fee, "max_weight": float(rng.uniform(0.1, 1)),
              "cvar_budget": 0.02, "sigma": dict(zip(names, np.nanstd(x, 0), strict=True)), "target_vol": 0.02,
              "prices": {k: float(rng.uniform(0.01, 100)) for k in names},
              "qty_steps": {k: float(rng.choice([1e-8, 0.01, 1.0])) for k in names}}
        rs = {k: float(rng.uniform(0, 100)) for k in names}
        r = size_positions(names, cash, method, risk_scores=rs, **kw)
        vals = list(r.amounts_eur.values())
        assert all(v >= 0 for v in vals)
        assert all(v == 0 or v >= 5.0 for v in vals)
        assert sum(vals) * (1 + fee) <= max(0.0, cash - reserve) + 1e-9
        assert r.cash_left_eur >= -1e-9
        worse = size_positions(names, cash, method, risk_scores={**rs, "A": min(100.0, rs["A"] + 20)}, **kw)
        assert worse.amounts_eur["A"] <= r.amounts_eur["A"] + 1e-12
