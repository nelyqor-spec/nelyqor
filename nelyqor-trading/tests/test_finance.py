"""Finance : absence de fuite temporelle, backtests reproductibles, coûts inclus, hypothèses intrabougie
conservatrices, walk-forward purgé, critères de promotion figés, risque/dimensionnement, calibration."""
from __future__ import annotations

from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest

from backend.core.config import load_strategy_params
from backend.features.indicators import feature_frame
from backend.market_data.models import MarketInfo
from backend.portfolio.risk import (
    RiskInputs,
    block_bootstrap,
    choose_weight,
    cvar,
    fractional_kelly,
    objective_j,
    position_cap,
    risk_score,
)
from backend.portfolio.sizing import size_buy
from backend.research.backtest import BTConfig, run_backtest
from backend.research.metrics import Metrics, deflated_sharpe, evaluate_promotion
from backend.research.registry import Registry
from backend.research.synthetic import synthetic_candles, synthetic_universe
from backend.research.walkforward import walk_forward_splits
from backend.strategies.base import Signals, Strategy, build_strategies
from tests.conftest import BTC_RAW

PARAMS = load_strategy_params()


@pytest.fixture(scope="module")
def df():
    return synthetic_candles(1500, seed=11)


def test_features_are_causal(df):
    full = feature_frame(df)
    cut = feature_frame(df.iloc[:1000])
    pd.testing.assert_frame_equal(full.iloc[:1000], cut, check_exact=False, rtol=1e-9)


def test_strategy_signals_are_causal(df):
    for s in build_strategies(PARAMS):
        a, b = s.signals(df), s.signals(df.iloc[:1000])
        assert np.array_equal(a.entry[:1000], b.entry), s.name
        assert np.array_equal(a.exit[:1000], b.exit), s.name


def test_future_perturbation_does_not_change_past_signals(df):
    d2 = df.copy()
    d2.iloc[1200:, :] *= 3.0  # choc futur
    for s in build_strategies(PARAMS):
        assert np.array_equal(s.signals(df).entry[:1200], s.signals(d2).entry[:1200]), s.name


def test_backtest_reproducible():
    data = synthetic_universe(["A-EUR", "B-EUR"], 2000, seed=5)
    s = build_strategies(PARAMS)[0]
    r1, r2 = run_backtest(data, s), run_backtest(data, s)
    assert r1.equity.equals(r2.equity) and len(r1.trades) == len(r2.trades)


class AlwaysIn(Strategy):
    name = "always"

    def signals(self, df):
        n = len(df)
        e = np.zeros(n, bool)
        e[20] = True
        x = np.zeros(n, bool)
        x[30] = True
        return Signals(e, x, np.ones(n), 1000)


def _flat(n=50, px=100.0):
    idx = pd.Index(1_700_000_000_000 + 3_600_000 * np.arange(n))
    return pd.DataFrame({"open": px, "high": px * 1.001, "low": px * 0.999, "close": px, "volume": 1.0}, index=idx)


def test_costs_included_flat_market_loses_fees():
    r = run_backtest({"X-EUR": _flat()}, AlwaysIn({}), BTConfig(atr_stop_mult=100, cooldown_bars=1000))
    assert len(r.trades) == 1
    t = r.trades[0]
    assert t.entry_ts == int(_flat().index[21])  # exécution à l'ouverture SUIVANTE
    assert t.entry_px > 100 and t.exit_px < 100  # glissement des deux côtés
    assert r.equity.iloc[-1] < 100 and r.fees_eur > 0


def test_doubled_fees_cost_more():
    data = {"X-EUR": _flat()}
    a = run_backtest(data, AlwaysIn({}), BTConfig(atr_stop_mult=100, cooldown_bars=1000))
    b = run_backtest(data, AlwaysIn({}), BTConfig(atr_stop_mult=100, cooldown_bars=1000, fee_multiplier=2))
    assert b.equity.iloc[-1] < a.equity.iloc[-1]


def test_intrabar_ambiguity_assumes_stop_first():
    df = _flat()
    df.iloc[23, df.columns.get_loc("high")] = 200.0  # objectif touché
    df.iloc[23, df.columns.get_loc("low")] = 50.0  # ET stop touché dans la même bougie
    r = run_backtest({"X-EUR": df}, AlwaysIn({}), BTConfig(take_profit_partial_r=1, cooldown_bars=1000))
    assert r.trades[0].reason == "stop" and r.trades[0].exit_ts == int(df.index[23])
    assert r.trades[0].proceeds_eur < r.trades[0].cost_eur


def test_min_order_never_forced():
    r = run_backtest({"X-EUR": _flat()}, AlwaysIn({}), BTConfig(initial_eur=4.0))
    assert r.trades == []


def test_walk_forward_splits_purged_and_holdout_untouched():
    splits, (ha, hb) = walk_forward_splits(5000, 4, 0.2, purge=100, min_train=1500)
    for sp in splits:
        assert sp.train[1] + 100 <= sp.test[0]
        assert sp.test[1] <= ha - 100 + 1
    for a, b in zip(splits, splits[1:]):
        assert a.test[1] <= b.test[0]
    assert (ha, hb) == (4000, 5000)


def _m(**kw):
    base = dict(net_return=0.1, max_drawdown=-0.1, profit_factor=1.5, n_trades=150, win_rate=0.5, sharpe=1, sortino=1,
                periods_per_year=8766, turnover_eur=0, fees_eur=0, exposure=0.5, mean_trade_ret=0.01,
                ci95_mean_trade=(0.001, 0.02), ci95_net_return=(0.0, 0.2))
    base.update(kw)
    return Metrics(**base)


def test_promotion_criteria_strict():
    ok = evaluate_promotion(_m(), _m(), {"haussier": 0.1, "baissier": 0.01}, False, 0)
    assert ok["promu"]
    for bad in (dict(profit_factor=1.19), dict(n_trades=99), dict(net_return=-0.01), dict(ci95_mean_trade=(-0.001, 0.02))):
        assert not evaluate_promotion(_m(**bad), _m(), {"h": 0.1}, False, 0)["promu"], bad
    assert not evaluate_promotion(_m(), _m(net_return=-0.01), {"h": 0.1}, False, 0)["promu"]
    assert not evaluate_promotion(_m(), _m(), {"h": 0.1}, True, 0)["promu"]


def test_registry_refuses_promotion_without_evidence(db, clock):
    reg = Registry(db, clock.now_ms)
    reg.register("momentum", "v1", {"a": 1})
    reg.set_status("momentum", "v1", "simulation")
    with pytest.raises(PermissionError):
        reg.set_status("momentum", "v1", "valide", {"promotion": {"promu": False}})
    reg.set_status("momentum", "v1", "valide", {"promotion": {"promu": True}})
    lab = Registry(db, clock.now_ms, role="lab")
    with pytest.raises(PermissionError):
        lab.set_status("momentum", "v1", "actif")
    reg.set_status("momentum", "v1", "actif")
    assert reg.rollback("momentum") is None  # aucune autre version validée -> pause recommandée


def test_registry_detects_tampering(db, clock):
    reg = Registry(db, clock.now_ms)
    reg.register("x", "v1", {"a": 1})
    db.exec("UPDATE model_registry SET params='{\"a\": 2}'")
    with pytest.raises(RuntimeError):
        reg.set_status("x", "v1", "simulation")


def test_dsr_penalizes_many_trials():
    one = deflated_sharpe(0.05, 2000, 1, 0.0)
    many = deflated_sharpe(0.05, 2000, 500, 0.001)
    assert many < one


def test_risk_score_and_cap():
    lo = risk_score(RiskInputs(0.1, 0.1, 0, 0, 0.1, 0.2))
    hi = risk_score(RiskInputs(0.9, 0.8, 1, 0.5, 0.9, 0.8))
    assert 0 <= lo < 45 < hi <= 100
    caps = [position_cap(D(100), r, 80) for r in (0, 30, 45, 60, 90)]
    assert all(a > b for a, b in zip(caps, caps[1:]))
    assert position_cap(D(100), 0, 100) == D(50)


def test_sizing_refuses_below_minimum_and_never_rounds_up(limits):
    m = MarketInfo.from_bitvavo(BTC_RAW)
    r = size_buy(equity=D(100), available_quote=D(100), current_exposure=D(0), position_value=D(0), risk=95,
                 confidence=50, limits=limits, market=m, fee_rate=D("0.0025"), depth_quote=D(10_000),
                 stress_loss_frac=D("0.2"), max_stress_loss_eur=D(5))
    assert r.amount_quote == 0 and any("minimum" in x for x in r.reasons)
    r2 = size_buy(equity=D(100), available_quote=D(100), current_exposure=D(0), position_value=D(0), risk=50,
                  confidence=90, limits=limits, market=m, fee_rate=D("0.0025"), depth_quote=D(10_000),
                  stress_loss_frac=D("0.2"), max_stress_loss_eur=D(5))
    assert D(5) <= r2.amount_quote <= position_cap(D(100), 50, 90)
    assert any("45" in x for x in r2.reasons)


def test_cvar_prudent_small_samples():
    losses = np.array([0.01, 0.02, 0.5])
    assert cvar(losses) >= 0.25
    big = np.linspace(-0.05, 0.05, 1000)
    assert abs(cvar(big) - big[-50:].mean()) < 1e-9


def test_objective_handles_ruin_and_hysteresis():
    assert objective_j(np.array([0.1, -1.0]), 1, 0, 0) == float("-inf")
    rng = np.random.default_rng(0)
    scen = rng.normal(0.0, 0.05, (500, 1))  # espérance nulle
    w, j, j0 = choose_weight(scen, np.array([0.0]), [np.array([0.5]), np.array([1.0])], 0.0025, 1.0, 0.1, 0.5, 0.004)
    assert np.allclose(w, [0.0])  # rester en euros si aucun avantage net


def test_block_bootstrap_shapes():
    r = np.random.default_rng(1).normal(0, 0.01, (1000, 3))
    s = block_bootstrap(r, 24, 200, 12, seed=1)
    assert s.shape == (200, 3)
    with pytest.raises(ValueError):
        block_bootstrap(r[:10], 24, 10, 12)


def test_kelly_bounded():
    assert fractional_kelly(-0.01, 0.01) == 0
    assert fractional_kelly(1.0, 0.01) == 0.35




def test_delisting_forces_exit_with_haircut_and_no_invented_prices():
    df = _flat(60)
    df.iloc[28:, :] = np.nan  # le marché disparaît à la bougie 28 (radiation), position ouverte depuis 21
    r = run_backtest({"X-EUR": df}, AlwaysIn({}), BTConfig(atr_stop_mult=100, cooldown_bars=1000))
    t = r.trades[0]
    assert t.reason == "radiation" and t.exit_px <= 100 * 0.5 + 1e-9
    assert not np.isnan(r.equity).any()


def test_data_gap_no_execution_inside_gap():
    df = _flat(60)
    df.iloc[25:28, :] = np.nan  # trou temporaire pendant la position
    r = run_backtest({"X-EUR": df}, AlwaysIn({}), BTConfig(atr_stop_mult=100, cooldown_bars=1000))
    assert len(r.trades) == 1 and r.trades[0].reason == "invalidation"
    assert not np.isnan(r.equity).any()
