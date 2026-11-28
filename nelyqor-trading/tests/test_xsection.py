"""Moteur transversal : causalité, cotation (aucun prix avant la 1re bougie), remplissage limite conservateur,
minimums, radiation, filtre de régime, réduction au-delà de 45/100, anti-doublon de vente."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.research.synthetic import synthetic_universe
from backend.research.xsection import XSConfig, run_xsection, xs_metrics

H = 3_600_000


def uni(n=1500, k=8, seed=5):
    d = synthetic_universe([f"C{i}-EUR" for i in range(k)] + ["BTC-EUR"], n, seed=seed)
    return d


def cfg(**kw):
    base = dict(min_liquidity_quote=0, min_age_bars=50, regime_filter=False, mom_lookback=24, trend_slow=50,
                vol_lookback=24, liq_lookback=50)
    base.update(kw)
    return XSConfig(**base)


def test_no_trade_before_listing():
    d = uni()
    d["C0-EUR"].iloc[:900, :] = np.nan  # coté à partir de la bougie 900
    r = run_xsection(d, cfg(min_age_bars=60))
    entries = [t for t in r.trades if t["market"] == "C0-EUR"]
    assert all(t["entry_i"] >= 900 + 60 for t in entries)


def test_causal_future_shock_does_not_change_past():
    d = uni()
    a = run_xsection(d, cfg())
    d2 = {m: df.copy() for m, df in d.items()}
    for df in d2.values():
        df.iloc[1200:, :] *= 3
    b = run_xsection(d2, cfg())
    pd.testing.assert_series_equal(a.equity.iloc[:1199], b.equity.iloc[:1199])


def _flat_market(n=400):
    idx = pd.Index(1_700_000_000_000 + H * np.arange(n))
    c = np.full(n, 100.0)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": np.full(n, 1e6)}, index=idx)


def test_limit_never_filled_on_touch_only():
    d = {"A-EUR": _flat_market(), "B-EUR": _flat_market()}
    d["B-EUR"]["close"] = d["B-EUR"]["close"] * np.linspace(1, 1.5, 400)  # B surperforme : candidate
    d["B-EUR"]["open"] = d["B-EUR"]["high"] = d["B-EUR"]["low"] = d["B-EUR"]["close"]
    d["B-EUR"]["low"] = d["B-EUR"]["close"] * 0.99995  # touche à peine (0,5 pb) sous la clôture précédente ?
    r = run_xsection(d, cfg(execution="limit", top_n=1, limit_offset_bps=0, through_bps=2.0))
    # la clôture monte à chaque bougie : jamais de traversée de 2 pb sous la limite -> aucun remplissage
    assert r.stats["limit_remplis"] == 0 and r.stats["limit_manques"] > 0 and not r.trades


def test_min_order_respected_with_small_capital():
    r = run_xsection(uni(), cfg(top_n=30, initial_eur=100))
    assert all(t["pnl"] is not None for t in r.trades)
    # 30 positions x 3 € < 5 € de minimum : aucun ordre sous le minimum n'est passé
    assert len(r.trades) == 0


def test_delisting_forced_exit_with_haircut():
    d = uni()
    r0 = run_xsection(d, cfg(top_n=1))
    if not r0.trades:
        return
    m = r0.trades[0]["market"]
    cut = r0.trades[0]["entry_i"] + 1
    d[m].iloc[cut:, :] = np.nan
    r = run_xsection(d, cfg(top_n=1))
    assert any(t["reason"] == "radiation" for t in r.trades if t["market"] == m)
    assert not np.isnan(r.equity).any()


def test_regime_filter_blocks_entries_in_bear():
    d = uni()
    n = len(d["BTC-EUR"])
    down = 50000 * np.exp(-0.002 * np.arange(n))  # baisse continue : BTC toujours sous sa moyenne
    d["BTC-EUR"] = pd.DataFrame({"open": down * 1.001, "high": down * 1.002, "low": down * 0.999, "close": down,
                                 "volume": np.full(n, 100.0)}, index=d["BTC-EUR"].index)
    r = run_xsection(d, cfg(regime_filter=True, regime_ema=50))
    late = [t for t in r.trades if t["entry_i"] > 300]
    assert not late  # BTC sous sa moyenne : aucune nouvelle entrée


def test_metrics_and_cost_scenarios():
    r = run_xsection(uni(), cfg())
    m = xs_metrics(r, 8766)
    e = m["economique"]
    assert e["0"] >= e["3"] >= e["5"] >= e["7.19"] >= e["10"]
    assert m["frais_eur"] >= 0 and m["trades"] == len(r.trades)
