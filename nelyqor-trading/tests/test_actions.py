"""Comparateur d'actions : remplissage limité par traversée stricte, sélection adverse mesurée, rester en euros
sans avantage, réallocation seulement au-delà des coûts + hystérésis + incertitude."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.portfolio.actions import ActionEstimate, best_action, compare_entry, compare_exit, realloc_gain

H = 3_600_000


def frame(close, low=None):
    c = np.asarray(close, float)
    o = np.r_[c[0], c[:-1]]
    lo = np.minimum(o, c) * 0.9999 if low is None else np.asarray(low, float)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0001, "low": lo, "close": c, "volume": 1.0},
                        index=pd.Index(1_700_000_000_000 + H * np.arange(len(c))))


def test_limit_requires_trade_through_and_captures_adverse_selection():
    # motif répété : signal, puis soit hausse directe (limité jamais rempli), soit creux puis rebond (rempli)
    blocks = []
    for k in range(60):
        if k % 2 == 0:
            blocks += [100, 101, 102, 103, 104, 105]  # hausse : seul le marché en profite
        else:
            blocks += [100, 99.0, 98.0, 97.5, 97.0, 96.5]  # baisse : le limité se remplit... dans une baisse
    df = frame(blocks)
    entry = np.zeros(len(df), bool)
    entry[::6] = True
    acts = {a.action: a for a in compare_entry(df, entry, horizon=4, limit_offset=0.001, ttl=2)}
    lim, mkt = acts["acheter_limite"], acts["acheter_marche"]
    assert 0.3 < lim.details["taux_remplissage"] < 0.7  # rempli seulement sur les baisses
    assert lim.details["esperance_si_rempli"] < 0 < mkt.esperance  # sélection adverse visible


def test_no_edge_means_stay_in_euros():
    rng = np.random.default_rng(0)
    df = frame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 3000))))
    entry = rng.random(3000) < 0.05
    assert best_action(compare_entry(df, entry, horizon=6)).action == "attendre_en_euros"


def test_exit_and_partial():
    acts = compare_exit(np.full(50, -0.01), sell_cost=0.003)
    assert best_action(acts).action == "vendre_totalement"
    acts2 = compare_exit(np.random.default_rng(1).normal(0.02, 0.01, 200), sell_cost=0.003)
    assert best_action(acts2).action == "conserver"


def test_realloc_needs_margin():
    held = ActionEstimate("h", 0.004, 0.001, 100)
    cand = ActionEstimate("c", 0.010, 0.001, 100)
    assert realloc_gain(held, cand, switch_cost=0.005).borne_basse < 0  # gain absorbé par les coûts
    assert realloc_gain(held, ActionEstimate("c", 0.03, 0.001, 100), switch_cost=0.005).borne_basse > 0


def test_pick_reallocation_weakest_only_when_worth_it():
    from backend.portfolio.actions import pick_reallocation
    held = {"A": ActionEstimate("A", 0.002, 0.001, 50), "B": ActionEstimate("B", 0.010, 0.001, 50)}
    v, g = pick_reallocation(held, ActionEstimate("C", 0.03, 0.002, 50), switch_cost=0.01)
    assert v == "A" and g.borne_basse > 0
    v2, _ = pick_reallocation(held, ActionEstimate("C", 0.012, 0.002, 50), switch_cost=0.01)
    assert v2 is None
