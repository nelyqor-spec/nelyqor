"""Corrections de la contre-expertise quantitative : décote de fin d'échantillon, bougies absentes, filtre 30 j."""
import numpy as np
import pandas as pd

from backend.data.resample import resample_ohlc
from backend.research.synthetic import synthetic_universe
from backend.research.xsection import XSConfig, build_panel
from backend.research.xsection_v2 import XS2Options, run_xs2
from backend.research.xsection_v3 import (
    EGAL_FILTRE_30J,
    build_panel_v3,
    fix_tail,
    radiations_at_end,
    regime_exposures_v3,
)

CFG = XSConfig(rebalance_bars=6, mom_lookback=12, trend_slow=30, min_age_bars=20, liq_lookback=20, regime_ema=40,
               min_liquidity_quote=0, regime_filter=False, vol_lookback=20)


def uni4h(hours=4003):
    d = synthetic_universe([f"S{i}-EUR" for i in range(10)] + ["BTC-EUR"], hours, seed=2)
    out = {}
    for m, df in d.items():
        df = df.copy()
        df.index = df.index - (int(df.index[0]) % 86_400_000)  # aligné sur minuit UTC
        out[m] = resample_ohlc(df, "4h")  # 4003 h : dernière bougie 4 h incomplète -> NaN
    idx = sorted(set().union(*(set(x.index) for x in out.values())))
    return {m: x.reindex(idx) for m, x in out.items()}


def test_incomplete_last_candle_no_longer_haircuts_open_positions():
    d = uni4h()
    assert all(np.isnan(x["close"].iloc[-1]) for x in d.values())
    r_bad = run_xs2(d, CFG, XS2Options())
    fixed = fix_tail(d)
    r_ok = run_xs2(fixed, CFG, XS2Options())
    assert radiations_at_end(r_bad) > 0  # défaut reproduit : positions « radiées » à −50 %
    assert radiations_at_end(r_ok) == 0
    assert r_ok.equity.iloc[-1] > r_bad.equity.iloc[-1]


def test_true_delisting_still_haircut():
    d = fix_tail(uni4h(4004))
    m = "S3-EUR"
    d[m].iloc[-200:] = np.nan  # disparu depuis ~33 jours : vraie radiation
    d2 = fix_tail(d)
    assert np.isnan(d2[m]["close"].iloc[-1])


def test_missing_hour_does_not_exclude_market_for_two_weeks():
    d = fix_tail(uni4h(4004))
    m = "S1-EUR"
    d[m].iloc[300] = np.nan  # une seule bougie absente
    P1, P3 = build_panel(d, CFG), build_panel_v3(d, CFG)
    after = slice(301, 301 + CFG.vol_lookback)
    assert P1["score"][m].iloc[after].isna().all()  # v1 : exclusion pendant toute la fenêtre
    assert P3["score"][m].iloc[after].notna().mean() > 0.9  # v3 : de nouveau admissible


def test_breadth_ignores_absent_candles_and_filter_equals_capped_kelly():
    d = fix_tail(uni4h(4004))
    P = build_panel_v3(d, CFG)
    ex = regime_exposures_v3(P, CFG)
    assert set(np.unique(ex["proportion_haussiere"])) <= {0.0, 1.0}
    a = run_xs2(d, CFG, XS2Options(**EGAL_FILTRE_30J), panel=P)
    assert np.isfinite(a.equity.iloc[-1]) and isinstance(a.equity, pd.Series)
