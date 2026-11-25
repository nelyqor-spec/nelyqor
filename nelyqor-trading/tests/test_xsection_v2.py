"""Moteur transversal v2 : identique à la v1 par défaut ; exposition nulle = capital entièrement en euros."""
from dataclasses import replace

import numpy as np
import pytest

from backend.research.synthetic import synthetic_universe
from backend.research.xsection import XSConfig, build_panel, run_xsection
from backend.research.xsection_v2 import EXEC_MODELS, SIZING, XS2Options, block_shuffle, order_effects, run_xs2


@pytest.fixture(scope="module")
def uni():
    d = synthetic_universe([f"S{i}-EUR" for i in range(12)] + ["BTC-EUR"], 2500, seed=5)
    cfg = XSConfig(rebalance_bars=6, mom_lookback=24, trend_slow=60, min_age_bars=50, liq_lookback=50,
                   regime_ema=100, min_liquidity_quote=0)
    return d, cfg, build_panel(d, cfg)


@pytest.mark.parametrize("ex", ["market", "limit", "hybrid"])
def test_v2_reproduces_v1_exactly(uni, ex):
    d, cfg, P = uni
    a = run_xsection(d, replace(cfg, execution=ex), panel=P)
    b = run_xs2(d, cfg, XS2Options(exec_model=ex), panel=P)
    assert np.allclose(a.equity.to_numpy(), b.equity.to_numpy()) and len(a.trades) == len(b.trades)


def test_zero_exposure_keeps_all_capital_in_eur(uni):
    d, cfg, P = uni
    r = run_xs2(d, cfg, XS2Options(), panel=P, exposure=np.zeros(len(P["close"])))
    assert r.trades == [] and r.equity.iloc[-1] == cfg.initial_eur


def test_all_models_and_sizings_run_and_respect_minimum(uni):
    d, cfg, P = uni
    for ex in EXEC_MODELS:
        run_xs2(d, cfg, XS2Options(exec_model=ex), panel=P)
    for sz in SIZING:
        r = run_xs2(d, cfg, XS2Options(sizing=sz), panel=P)
        assert r.equity.min() > 0


def test_risk_cut_reduces_size(uni):
    d, cfg, P = uni
    low = run_xs2(d, replace(cfg, risk_cut=0.0), XS2Options(), panel=P)
    high = run_xs2(d, cfg, XS2Options(), panel=P)
    assert low.turnover_eur < high.turnover_eur  # seuil de risque abaissé -> montants engagés plus faibles


def test_order_log_and_block_shuffle(uni):
    d, cfg, P = uni
    r = run_xs2(d, cfg, XS2Options(exec_model="limit"), panel=P)
    eff = order_effects(r.stats["ordres"])
    assert 0 <= eff["taux_remplissage_entrees"] <= 1
    x = (np.arange(1000) % 200 < 100).astype(float)
    y = block_shuffle(x, 50, np.random.default_rng(0))
    assert len(y) == len(x) and abs(y.mean() - x.mean()) < 0.15
