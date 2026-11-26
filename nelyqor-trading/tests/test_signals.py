"""Phase 5 — signaux élémentaires : cas SYNTHÉTIQUES à réponse connue (aucune valeur probante sur le marché)."""
import numpy as np
import pandas as pd
import pytest

from backend.data.resample import resample_ohlc
from backend.research.signals import (
    DAY_MS,
    HOUR_MS,
    SignalConfig,
    aggregate_daily,
    benjamini_hochberg,
    compute_features,
    daily_panel,
    holm,
    portfolio_net,
    ratio_test,
    run_study,
    simulate_top_liquid,
    timing_returns,
)
from backend.research.synthetic import synthetic_universe
from backend.research.xsection_v2 import block_shuffle

T0 = 1_700_006_400_000 - (1_700_006_400_000 % DAY_MS)  # minuit UTC


def hourly(prices, start=T0):
    p = np.asarray(prices, float)
    idx = pd.Index(start + HOUR_MS * np.arange(len(p)), dtype="int64")
    return pd.DataFrame({"open": p, "high": p * 1.001, "low": p * 0.999, "close": p, "volume": 1000.0}, index=idx)


def uni(n_markets=10, days=420, seed=3, drop=0.005):
    d = synthetic_universe(["BTC-EUR"] + [f"S{i}-EUR" for i in range(n_markets - 1)], days * 24, seed=seed)
    rng = np.random.default_rng(seed)
    out = {}
    for m, df in d.items():
        df = df.copy()
        df.index = df.index - (int(df.index[0]) % DAY_MS)
        df["volume"] *= 1000
        if m != "BTC-EUR":
            df = df[rng.random(len(df)) >= drop]
        out[m] = df
    return out


SMALL = SignalConfig(min_liq_eur_day=0.0, state_burn_in_days=60, liq_norm_days=60, n_boot=60, n_random=30,
                     min_age_days=10, top_n=3, min_state_days=50)


# ------------------------------------------------------------------ corrections pour essais multiples
def test_holm_bh_valeurs_connues():
    p = [0.01, 0.04, 0.03, 0.005]
    assert np.allclose(holm(p), [0.03, 0.06, 0.06, 0.02])
    assert np.allclose(benjamini_hochberg(p), [0.02, 0.04, 0.04, 0.02])
    assert np.allclose(holm([0.5, 0.9]), [1.0, 1.0])
    assert np.allclose(benjamini_hochberg([0.5, 0.9]), [0.9, 0.9])


# ------------------------------------------------------------------ agrégation et bougies incomplètes
def test_agregation_stricte_tolerante_et_bougie_incomplete():
    df = hourly(100 + np.arange(24 * 3 + 5, dtype=float))  # 3 jours + 5 h : dernier jour incomplet
    df = df.drop(df.index[30])  # une heure absente le 2e jour
    s = aggregate_daily(df, 0, "stricte")
    ref = resample_ohlc(df, "1D")
    pd.testing.assert_frame_equal(s, ref)
    assert np.isnan(s["close"].iloc[1])  # jour avec trou : NaN (jamais reconstitué)
    t = aggregate_daily(df, 0, "tolerante")
    assert t["close"].iloc[1] == df["close"].iloc[47 - 1]  # dernier prix réel du jour (ligne 30 supprimée)
    panel, meta = daily_panel({"A-EUR": df, "B-EUR": hourly(np.full(24 * 3 + 5, 50.0))}, 0, "tolerante")
    assert len(panel["close"]) == 3 and meta["bougies_incompletes_fin"] == 2  # retirées ET comptées
    # phase 5 h : la première et la dernière bougie sont incomplètes -> retirées explicitement
    panel5, meta5 = daily_panel({"A-EUR": df}, 5, "tolerante")
    assert meta5["bougies_incompletes_debut"] == 1 and len(panel5["close"]) == 3


def test_politiques_de_trous():
    days = 40
    p = 100 * np.exp(np.linspace(0, 0.4, days))
    close = pd.DataFrame({"A-EUR": p.copy()}, index=pd.Index(np.arange(days), dtype="int64"))
    close.iloc[20, 0] = np.nan
    panel = {"close": close, "open": close, "high": close * 1.01, "low": close * 0.99,
             "volume": pd.DataFrame(1e9, index=close.index, columns=close.columns)}
    cfg = SignalConfig(min_liq_eur_day=0, min_age_days=1, min_names=1, horizons=(1, 3))
    fd = compute_features(panel, cfg, "dernier_prix")
    fe = compute_features(panel, cfg, "exclure")
    assert np.isnan(fd["close"].iloc[20, 0])  # aucun prix inventé
    # rendement 1 j le lendemain du trou : depuis le DERNIER PRIX CONNU (jour 19) ou exclu
    assert np.isclose(fd["asset_signals"]["persistance_runs"].iloc[21, 0], 20)  # série non interrompue (20 hausses observées)
    assert np.isnan(fe["asset_signals"]["persistance_runs"].iloc[21, 0])  # rendement à travers le trou exclu
    assert np.isclose(fe["asset_signals"]["persistance_runs"].iloc[22, 0], 1)  # série remise à zéro
    g_d, g_e = fd["fwd_gross"][3], fe["fwd_gross"][3]
    assert np.isclose(g_d.iloc[18, 0], p[21] / p[18] - 1)
    assert np.isnan(g_e.iloc[18, 0]) and np.isclose(g_e.iloc[10, 0], p[13] / p[10] - 1)
    assert np.isnan(g_d.iloc[20, 0])  # pas d'entrée un jour sans prix


# ------------------------------------------------------------------ absence d'anticipation
@pytest.mark.parametrize("gap", ["dernier_prix", "exclure"])
def test_troncature_valeurs_passees_inchangees(gap):
    raw = uni(days=320)
    cut_day = 250
    cut = int(raw["BTC-EUR"].index[0]) + cut_day * DAY_MS
    trunc = {m: d[d.index < cut] for m, d in raw.items()}
    for agg in ("stricte", "tolerante"):
        Pf, _ = daily_panel(raw, 0, agg)
        Pt, _ = daily_panel(trunc, 0, agg)
        Ff, Ft = compute_features(Pf, SMALL, gap), compute_features(Pt, SMALL, gap)
        keys = Pt["close"].index
        assert len(keys) == cut_day
        for name, st in Ft["market_states"].items():
            pd.testing.assert_series_equal(st, Ff["market_states"][name].reindex(keys), check_names=False)
        for name, st in Ft["asset_states"].items():
            pd.testing.assert_frame_equal(st, Ff["asset_states"][name].reindex(keys))
            pd.testing.assert_frame_equal(Ft["asset_signals"][name], Ff["asset_signals"][name].reindex(keys))
        pd.testing.assert_frame_equal(Ft["ok"], Ff["ok"].reindex(keys))
        pd.testing.assert_frame_equal(Ft["hs"], Ff["hs"].reindex(keys))


# ------------------------------------------------------------------ signal parfait / aléatoire
def _arrays(v, state):
    IH, IL = (state == 1), (state == 0)
    return v * IH, IH.astype(float), v * IL, IL.astype(float)


def test_signal_parfait_significatif():
    rng = np.random.default_rng(0)
    n, P = 600, 3
    v = rng.normal(0, 0.02, (n, P))
    state = (v > 0).astype(float)  # « prédit » parfaitement le signe du rendement futur
    r = ratio_test(*_arrays(v, state), np.arange(19000, 19000 + n), 10, SignalConfig(n_boot=300), {})
    assert r["estimation"] > 0.02 and r["ic95"][0] > 0 and r["p"] < 1e-6
    assert r["par_phase"]["part_meme_signe"] == 1.0


def test_signaux_aleatoires_non_significatifs_apres_correction():
    rng = np.random.default_rng(1)
    n, P = 500, 2
    v = rng.normal(0, 0.02, (n, P))
    ps = []
    for _ in range(40):
        state = np.repeat(rng.integers(0, 2, n)[:, None], P, axis=1).astype(float)
        ps.append(ratio_test(*_arrays(v, state), np.arange(n), 10, SignalConfig(n_boot=200), {})["p"])
    ps = np.array(ps)
    assert holm(ps).min() > 0.05 and benjamini_hochberg(ps).min() > 0.05


# ------------------------------------------------------------------ portefeuilles et minutage
def test_couts_portefeuille_et_minutage():
    n = 50
    W = np.ones((n, 1))
    R = np.zeros((n, 1))
    C = np.full((n, 1), 0.003)
    net = portfolio_net(W, R, C)
    assert np.isclose(net[0], -0.003) and np.allclose(net[1:], 0)  # coût d'entrée uniquement
    b = np.zeros((n, 1))
    e = (np.arange(n) % 2).astype(float)[:, None]
    out = timing_returns(e, b, np.zeros_like(b), np.full_like(b, 0.003))
    assert np.allclose(out[1:], -0.003)  # chaque changement d'exposition coûte un côté


def test_minutage_oracle_vs_constant_vs_aleatoire():
    rng = np.random.default_rng(2)
    n = 800
    b = rng.normal(0.0005, 0.02, (n, 1))
    z, c = np.zeros_like(b), np.full_like(b, 0.003)
    oracle = (pd.Series(b[:, 0]).rolling(5, center=True, min_periods=1).mean() > 0).to_numpy(float)[:, None]
    s = timing_returns(oracle, b, z, c).mean()
    k = timing_returns(np.full_like(b, oracle.mean()), b, z, c).mean()
    rnd = [timing_returns(oracle[block_shuffle(np.arange(n), 20, rng).astype(int)], b, z, c).mean() for _ in range(50)]
    assert s > k and s > max(rnd)


def test_top_liquides_ordre_minimal():
    n, M = 100, 30
    cf = np.ones((n, M))
    obs = np.ones((n, M), bool)
    liq = np.tile(np.arange(M, dtype=float), (n, 1))
    hs = np.full((n, M), 0.001)
    eq = simulate_top_liquid(cf, obs, liq, obs, hs, SignalConfig(top_n=25))  # 4 € < 5 € : aucun ordre possible
    assert np.allclose(eq, 100.0)
    eq = simulate_top_liquid(cf, obs, liq, obs, hs, SignalConfig(top_n=10))
    assert eq[0] < 100.0 and np.isclose(eq[0], 100 * (1 - 0.0025) / 1.001, rtol=1e-6)


# ------------------------------------------------------------------ étude complète (fumée)
def test_etude_complete_synthetique():
    res = run_study(uni(days=380), SMALL, [0, 12], log=lambda s: None)
    c = res["comptage"]
    assert c["n_tests_pvaleurs_global"] == len(res["tests"]) == sum(c["par_famille"].values())
    assert set(res["traitements"]) == {"stricte|dernier_prix", "stricte|exclure", "tolerante|dernier_prix",
                                       "tolerante|exclure"}
    for t in res["tests"]:
        assert t["p_holm_global"] >= t["p"] - 1e-12 and t["p_bh_global"] <= t["p_holm_global"] + 1e-12
    for d in res["traitements"]["stricte|dernier_prix"]["decomposition"].values():
        assert np.isclose(d["effet_total_vs_passif"], d["effet_exposition"] + d["effet_minutage"])
    assert res["dsr"]["n_essais"] == res["dsr"]["n_strategies_derivees"] + SMALL.n_prior_trials
    assert "marche" in res["question_largeur"]
