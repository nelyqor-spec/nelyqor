"""Scénarios DÉTERMINISTES du moteur v4 : chaque résultat attendu est calculé à la main (voir docstrings) et
comparé au moteur avec une tolérance de 1e-9 €."""
import numpy as np
import pandas as pd
import pytest

from backend.research.xsection_v4 import (
    ACTIF,
    PANNE,
    RADIE,
    SANS_TRANSACTION,
    DataPolicy,
    ExecConfig,
    aggregate,
    align,
    simulate,
)

H = 3_600_000
EXACT = ExecConfig(slippage_bps=0.0, fee_taker=0.0025, fee_maker=0.0015, amount_step=1e-8)


def bars(rows, start=0):
    """rows : liste de (open, high, low, close, volume) ou None (bougie absente)."""
    idx, recs = [], []
    for k, r in enumerate(rows):
        if r is None:
            continue
        idx.append(start + k * H)
        recs.append(dict(zip(["open", "high", "low", "close", "volume"], r, strict=True)))
    return pd.DataFrame(recs, index=pd.Index(idx, dtype=np.int64))


def flat(px, n, vol=1e6):
    return [(px, px, px, px, vol)] * n


def plan(schedule):
    """Stratégie scriptée : {bougie_de_décision: {marché: poids}}."""
    return lambda i, eng: schedule.get(i, {})


def run(data, schedule, cfg=EXACT, pol=None, **kw):
    grid, al = align(data, H)
    return simulate(grid, al, plan(schedule), cfg, pol, **kw)


def test_buy_then_sell_exact_cash():
    """Achat 50 € à 100 (frais 0,25 % = 0,125 €), vente 0,5 à 110 (frais 0,1375 €).
    Trésorerie finale = 100 − 50,125 + 55 − 0,1375 = 104,7375 €."""
    a = bars([(100, 100, 100, 100, 1e6), (100, 110, 100, 110, 1e6), (110, 110, 110, 110, 1e6)])
    res, eng = run({"A-EUR": a}, {0: {"A-EUR": 0.5}, 1: {"A-EUR": 0.0}}, liquidate_at_end=False)
    assert res.equity.iloc[1] == pytest.approx(49.875 + 55, abs=1e-9)  # valorisation à la clôture 110
    assert eng.cash == pytest.approx(104.7375, abs=1e-9)
    assert res.fees_eur == pytest.approx(0.125 + 0.1375, abs=1e-12)
    assert res.trades[0]["pnl"] == pytest.approx(55 - 0.1375 - 50.125, abs=1e-9)


def test_minimum_order_keeps_money_in_eur():
    """Poids 4 % de 100 € = 4 € < 5 € : aucun ordre, 100 € en euros."""
    a = bars(flat(100, 3))
    res, eng = run({"A-EUR": a}, {0: {"A-EUR": 0.04}})
    assert eng.orders_log == [] and res.equity.iloc[-1] == 100.0


def test_missing_candle_without_trade_no_haircut():
    """A n'a pas de bougie en t=3 (aucune transaction), B cote : A est SANS_TRANSACTION, valorisé au dernier prix
    (clôture t=2 = 100). Équité t=3 = trésorerie + q × 100, sans aucune décote."""
    a = bars([(100, 100, 100, 100, 1e6)] * 3 + [None] + [(100, 100, 100, 100, 1e6)] * 2)
    b = bars(flat(10, 6))
    res, eng = run({"A-EUR": a, "B-EUR": b}, {0: {"A-EUR": 0.5}})
    assert res.states[3, 0] == SANS_TRANSACTION
    q = 50 / 100
    cash = 100 - 50 * 1.0025
    assert res.equity.iloc[3] == pytest.approx(cash + q * 100, abs=1e-9)
    assert not any(t["reason"] == "radiation" for t in res.trades)


def test_collection_outage_freezes_and_defers():
    """t=1 : aucune bougie pour AUCUN marché = panne. L'achat décidé en t=0 n'est pas exécuté en t=1 mais en t=2
    (ouverture 105) : q = 50/105 ; frais 0,125 € ; équité figée en t=1."""
    a = bars([(100, 100, 100, 100, 1e6), None, (105, 105, 105, 105, 1e6), (105, 105, 105, 105, 1e6)])
    b = bars([(10, 10, 10, 10, 1e6), None, (10, 10, 10, 10, 1e6), (10, 10, 10, 10, 1e6)])
    res, eng = run({"A-EUR": a, "B-EUR": b}, {0: {"A-EUR": 0.5}}, liquidate_at_end=False)
    assert res.states[1, 0] == PANNE and res.outage_bars == 1
    assert res.equity.iloc[1] == 100.0
    assert eng.orders_log[0]["i"] == 2 and eng.orders_log[0]["px"] == 105
    assert eng.qty[0] == pytest.approx(np.floor(50 / 105 / 1e-8 + 1e-9) * 1e-8, abs=1e-15)


def test_delisting_detected_causally_and_haircut_parameter():
    """Écart de radiation = 3 bougies. A disparaît à partir de t=3 alors que B cote : t=3,4 SANS_TRANSACTION,
    t=5 RADIE → vente au dernier prix (100) × (1 − 0,10) = 90, frais taker 0,25 %. q = 0,5 :
    trésorerie = 100 − 50,125 + 0,5 × 90 × (1 − 0,0025) = 94,76375."""
    a = bars(flat(100, 3))
    b = bars(flat(10, 7))
    pol = DataPolicy(gap_days=3 / 24, delist_haircut=0.10)
    res, eng = run({"A-EUR": a, "B-EUR": b}, {0: {"A-EUR": 0.5}}, pol=pol, liquidate_at_end=False)
    assert list(res.states[3:6, 0]) == [SANS_TRANSACTION, SANS_TRANSACTION, RADIE]
    assert eng.cash == pytest.approx(100 - 50.125 + 0.5 * 90 * 0.9975, abs=1e-9)


def test_end_of_sample_never_a_delisting():
    """Les deux marchés s'arrêtent ensemble à la fin : aucune radiation ; liquidation finale au dernier prix,
    frais taker, sans décote : 100 − 50,125 + 50 × 0,9975 = 99,75."""
    a = bars(flat(100, 4))
    b = bars(flat(10, 4))
    res, eng = run({"A-EUR": a, "B-EUR": b}, {0: {"A-EUR": 0.5}})
    assert not any(t["reason"] == "radiation" for t in res.trades)
    assert res.equity.iloc[-1] == pytest.approx(100 - 50.125 + 50 * 0.9975, abs=1e-9)


def test_incomplete_last_aggregated_candle_is_dropped():
    """10 h de données (0 → 9), agrégation 4 h : intervalles [0,4[ et [4,8[ complets ; [8,12[ incomplet → retiré.
    Une heure sans bougie (h=5) n'empêche pas la bougie [4,8[ d'exister (aucune transaction ≠ trou)."""
    rows = [(1 + k, 2 + k, 0.5 + k, 1.5 + k, 10) for k in range(10)]
    rows[5] = None
    df = bars(rows)
    g = aggregate(df, "4h")
    assert list(g.index) == [0, 4 * H]
    assert g.loc[4 * H, "n_sources"] == 3 and g.loc[4 * H, "volume"] == 30
    assert g.loc[0, "open"] == 1 and g.loc[0, "close"] == 4.5 and g.loc[0, "high"] == 5 and g.loc[0, "low"] == 0.5


def test_limit_requires_trade_through():
    """Limite d'achat à 100 × (1 − 5 pb) = 99,95 ; seuil de traversée 99,95 × (1 − 2 pb) = 99,930009.
    Bas suivant 99,94 → non rempli (toucher ne suffit pas) ; bas 99,90 → rempli à 99,95, frais maker 0,15 %."""
    cfg = ExecConfig(slippage_bps=0, mode="limit", ttl_bars=1, amount_step=1e-8)
    for low, filled in ((99.94, False), (99.90, True)):
        a = bars([(100, 100, 100, 100, 1e6), (100, 100.5, low, 100, 1e6), (100, 100, 100, 100, 1e6)])
        res, eng = run({"A-EUR": a}, {0: {"A-EUR": 0.5}}, cfg=cfg, liquidate_at_end=False)
        assert bool(eng.qty[0] > 0) is filled
        if filled:
            q = np.floor(50 / 99.95 / 1e-8 + 1e-9) * 1e-8
            assert eng.cash == pytest.approx(100 - q * 99.95 * 1.0015, abs=1e-9)
        else:
            assert res.stats["limite_manques"] == 1


def test_quantity_rounded_down_and_no_dust():
    """Pas de quantité 0,001, prix 3 : 10 € → q = 3,333 (arrondi bas), notionnel 9,999 €.
    Vente de 60 % → reliquat 1,3332 × 3 ≈ 4 € < 5 € : tout est vendu."""
    cfg = ExecConfig(slippage_bps=0, amount_step=0.001)
    a = bars(flat(3, 4))
    res, eng = run({"A-EUR": a}, {0: {"A-EUR": 0.10}}, cfg=cfg, liquidate_at_end=False)
    assert eng.qty[0] == pytest.approx(3.333, abs=1e-12)
    res2, eng2 = run({"A-EUR": a}, {0: {"A-EUR": 0.10}, 1: {"A-EUR": 0.04}}, cfg=cfg, liquidate_at_end=False)
    assert eng2.qty[0] == 0.0


def test_no_lookahead_truncation_invariance():
    """La stratégie n'utilise que les clôtures ≤ t : tronquer les données après k ne change pas l'équité avant k."""
    rng = np.random.default_rng(1)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    rows = [(p, p * 1.01, p * 0.99, p, 1e6) for p in px]

    def strat(i, eng):
        c = eng.C[: i + 1, 0]
        return {"A-EUR": 0.8 if c[-1] > np.nanmean(c[-20:]) else 0.0}

    full_grid, full = align({"A-EUR": bars(rows)}, H)
    cut_grid, cut = align({"A-EUR": bars(rows[:120])}, H)
    r1, _ = simulate(full_grid, full, strat, EXACT, liquidate_at_end=False)
    r2, _ = simulate(cut_grid, cut, strat, EXACT, liquidate_at_end=False)
    assert np.allclose(r1.equity.iloc[:119].to_numpy(), r2.equity.iloc[:119].to_numpy(), atol=1e-12)


def test_relisting_after_absence_returns_active():
    pol = DataPolicy(gap_days=2 / 24)
    a = bars(flat(100, 2) + [None, None, None] + flat(100, 2))
    b = bars(flat(10, 7))
    grid, al = align({"A-EUR": a, "B-EUR": b}, H)
    res, _ = simulate(grid, al, plan({}), EXACT, pol)
    assert res.states[4, 0] == RADIE and res.states[5, 0] == ACTIF


def test_accounting_identity_every_bar():
    rng = np.random.default_rng(3)
    data = {f"M{k}-EUR": bars([(p, p * 1.02, p * 0.98, p, 1e5) for p in 10 * np.exp(np.cumsum(rng.normal(0, 0.02, 100)))])
            for k in range(4)}

    def strat(i, eng):
        if i % 10:
            return {}
        k = (i // 10) % 4
        return {m: (0.3 if n == k else 0.0) for n, m in enumerate(eng.markets)}

    grid, al = align(data, H)
    res, eng = simulate(grid, al, strat, ExecConfig(), liquidate_at_end=True)
    assert eng.cash >= 0 and (eng.qty == 0).all()
    assert res.equity.iloc[-1] == pytest.approx(eng.cash, abs=1e-9)
    assert res.fees_eur == pytest.approx(sum(o["fee"] for o in res.orders), abs=1e-9)
