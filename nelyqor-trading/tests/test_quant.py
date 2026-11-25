"""Moteur quantitatif : détection de signaux implantés (et absence de faux signal dans le bruit),
causalité des modèles glissants, objectif log-croissance/CVaR, Kelly, corrélation, risque > 45."""
from __future__ import annotations

from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest

from backend.market_data.models import MarketInfo
from backend.models.predict import fit_and_evaluate, rolling_model_probabilities
from backend.portfolio.decision import choose_fraction, correlation_haircut, forward_outcomes
from backend.portfolio.risk import position_cap
from backend.portfolio.sizing import size_buy
from backend.research.backtest import BTConfig, run_backtest
from backend.research.metrics import compute_metrics
from backend.strategies.base import Breakout, Momentum
from tests.conftest import BTC_RAW


def ar_data(n: int, phi: float, seed: int, sig: float = 0.006) -> pd.DataFrame:
    """Rendements AR(1) : phi = 0 -> bruit pur (aucun signal exploitable) ; phi > 0 -> momentum implanté."""
    rng = np.random.default_rng(seed)
    e = rng.normal(0, sig, n)
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = phi * r[i - 1] + e[i]
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[100, c[:-1]]
    idx = pd.Index(1_700_000_000_000 + 3_600_000 * np.arange(n))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0005, "low": np.minimum(o, c) * 0.9995,
                         "close": c, "volume": rng.lognormal(0, 0.3, n)}, index=idx)


@pytest.fixture(scope="module")
def noise():
    return ar_data(6000, 0.0, 1)


@pytest.fixture(scope="module")
def planted():
    return ar_data(6000, 0.3, 1)


def test_model_finds_planted_signal_and_not_noise(noise, planted):
    rn, _ = fit_and_evaluate(noise, 1, 0.0)
    rp, _ = fit_and_evaluate(planted, 1, 0.0)
    assert not rn.calibrated and rn.label == "estimation non calibrée"
    assert rp.calibrated and rp.skill_ci[0] > 0 and rp.label == "probabilité calibrée"


def test_strategy_profits_only_where_edge_exists(noise, planted):
    m = Momentum({"lookback": 3, "volume_lookback": 48, "min_rel_volume": 0, "exit_lookback": 1, "max_bars": 6})
    cfg = BTConfig(fee_taker=0.0005, slippage_bps=1, atr_stop_mult=5)
    mn = compute_metrics(run_backtest({"X-EUR": noise}, m, cfg), 8766)
    mp = compute_metrics(run_backtest({"X-EUR": planted}, m, cfg), 8766)
    assert mn.net_return < 0 < mp.net_return  # dans le bruit, les coûts font perdre ; le signal implanté paie
    assert mp.profit_factor > 1.2 > mn.profit_factor


def test_rolling_model_is_causal(planted):
    p1, _ = rolling_model_probabilities(planted.iloc[:4000], 1, 0.0, retrain_every=500, min_train=2000)
    shocked = planted.copy()
    shocked.iloc[3500:, :] *= 5  # choc futur
    p2, _ = rolling_model_probabilities(shocked, 1, 0.0, retrain_every=500, min_train=2000)
    # probabilités antérieures à 3500 identiques (sauf la bougie dont l'entrée/horizon touche le choc)
    np.testing.assert_allclose(p1[:3490], p2[:3490], equal_nan=True)


def test_forward_outcomes_non_overlapping():
    df = ar_data(200, 0.0, 3)
    e = np.zeros(200, bool)
    e[[10, 11, 12, 40]] = True
    r = forward_outcomes(df, e, horizon=10)
    assert len(r) == 2  # 11 et 12 chevauchent le signal 10


def test_choose_fraction_stays_in_euros_without_edge():
    rng = np.random.default_rng(0)
    assert choose_fraction(rng.normal(0.0, 0.03, 200), 0.3)["f"] == 0
    assert choose_fraction(rng.normal(-0.01, 0.03, 200), 0.3)["f"] == 0
    assert choose_fraction(rng.normal(0.02, 0.03, 10), 0.3)["f"] == 0  # trop peu d'observations


def test_choose_fraction_bounded_and_shrinks_with_uncertainty():
    out = np.random.default_rng(1).normal(0.01, 0.02, 300)
    a = choose_fraction(out, 0.3, uncertainty=0.0)
    b = choose_fraction(out, 0.3, uncertainty=1.0)
    assert 0 < a["f"] <= 0.3 and b["f"] <= a["f"]
    assert a["f"] <= a["f_kelly"] + 1e-12 and a["f"] <= a["f_J"] + 1e-12


def test_ruin_scenarios_excluded_from_log_objective():
    out = np.r_[np.full(100, 0.02), [-1.2]]  # un scénario de perte > 100 % à plein régime
    ch = choose_fraction(out, 1.0)
    assert ch["f"] < 1.0  # f = 1 rendrait la richesse négative : jamais retenu


def test_correlation_haircut():
    idx = pd.RangeIndex(600)
    a = pd.Series(np.random.default_rng(0).normal(0, 1, 600), index=idx)
    f_same, c_same = correlation_haircut(a, {"B": a * 1.01})
    f_ind, _ = correlation_haircut(a, {"B": pd.Series(np.random.default_rng(9).normal(0, 1, 600), index=idx)})
    assert c_same > 0.99 and f_same == 0.5 and f_ind == 1.0


def test_size_decreases_with_risk_especially_above_45(limits):
    m = MarketInfo.from_bitvavo(BTC_RAW)
    eq = D(1_000)

    def amt(risk):
        return size_buy(equity=eq, available_quote=eq, current_exposure=D(0), position_value=D(0), risk=risk,
                        confidence=80, limits=limits, market=m, fee_rate=D("0.0025"), depth_quote=D(10**9),
                        stress_loss_frac=D("0.1"), max_stress_loss_eur=eq).amount_quote

    vals = [amt(r) for r in (10, 30, 45, 46, 60, 80)]  # 324 > 196 > 121 > 117 > 64 > 16 €
    assert all(x > y for x, y in zip(vals, vals[1:]))
    assert position_cap(eq, 60, 80) / position_cap(eq, 45, 80) < D("0.6")  # forte réduction au-delà de 45
    assert amt(85) > 0  # très réduit, pas exclu pour ce seul motif (tant qu'au-dessus du minimum Bitvavo)
    assert amt(95) == 0  # sous le minimum de 5 € : refusé (jamais d'arrondi vers le haut)


def test_breakout_exit_not_immediate():
    n = 120
    c = np.r_[np.full(100, 100.0), np.full(20, 110.0)]  # cassure à 100 puis maintien au-dessus
    idx = pd.Index(1_700_000_000_000 + 3_600_000 * np.arange(n))
    df = pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                       "volume": np.r_[np.ones(100), np.full(20, 5.0)]}, index=idx)
    s = Breakout({"channel": 48, "min_rel_volume": 1.3, "max_bars": 96}).signals(df)
    assert s.entry[100]
    assert not s.exit[101:].any()  # tant que le prix reste au-dessus du niveau cassé
