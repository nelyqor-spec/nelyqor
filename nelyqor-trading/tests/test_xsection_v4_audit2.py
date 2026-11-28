"""Intégré au dépôt tel quel (phase 5) — Contre-expertise n°2 du moteur v4 corrigé (commit 887c7e5). Scénarios déterministes calculés à la main.
Tests préfixés `test_defaut_` : un échec = défaut avéré. Tests `test_ok_` : validation des corrections."""
import numpy as np
import pandas as pd
import pytest

from backend.research.strategies_v4 import Rotation, RotationParams, prepare
from backend.research.xsection import XSConfig
from backend.research.xsection_v4 import (
    SANS_TRANSACTION,
    DataPolicy,
    Engine,
    ExecConfig,
    aggregate,
    align,
    classify,
    classify_reference,
    simulate,
)

H = 3_600_000


def bars(rows, start=0):
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
    return lambda i, eng: schedule.get(i, {})


def run(data, schedule, cfg, pol=None, **kw):
    grid, al = align(data, H)
    return simulate(grid, al, plan(schedule), cfg, pol, **kw)


LIM = dict(slippage_bps=0, mode="limit", through_bps=0, limit_offset_bps=0, amount_step=1e-8)


# ============================================================== DÉFAUTS
def test_defaut_vente_reliquat_sous_minimum():
    """Achat 9 unités à 10 (90 €, maker 0,135 €) ; trésorerie 9,865. Décision t=1 : équité 99,865, cible 30 %
    -> vente 6,00405 u. t=2 : capacité 116 × 10 × 5 % = 58 € -> 5,8 u vendues ; reliquat 0,20405 u (2,04 €) reste
    en attente et est vendu t=3 : ordre de 2,04 € < minimum 5 €."""
    cfg = ExecConfig(ttl_bars=5, max_volume_share=0.05, **LIM)
    rows = [(10, 10, 10, 10, 1e6), (10, 10, 9, 10, 1e6), (10, 11, 10, 10, 116), (10, 11, 10, 10, 1e6), flat(10, 1)[0]]
    res, eng = run({"A": bars(rows)}, {0: {"A": 0.9}, 1: {"A": 0.3}}, cfg, liquidate_at_end=False)
    sells = [o["notional"] for o in eng.orders_log if o["side"] == "sell"]
    assert sells[0] == pytest.approx(58.0)
    assert all(n >= 5.0 - 1e-9 for n in sells), sells


def test_defaut_plafond_volume_depasse_par_regle_anti_reliquat():
    """1 unité à 10 € ; sortie limite ; capacité 12 × 10 × 5 % = 6 € (≥ 5) ; reliquat 4 € < 5 -> TOUT est vendu
    (10 €) malgré un plafond de 6 €."""
    cfg = ExecConfig(ttl_bars=5, max_volume_share=0.05, **LIM)
    rows = [(10, 10, 10, 10, 1e6), (10, 10, 9, 10, 1e6), (10, 11, 10, 10, 12), flat(10, 1)[0]]
    res, eng = run({"A": bars(rows)}, {0: {"A": 0.1}, 1: {"A": 0.0}}, cfg, liquidate_at_end=False)
    sell = [o for o in eng.orders_log if o["side"] == "sell"][0]
    assert sell["notional"] <= 6.0 + 1e-9, sell["notional"]


def test_defaut_grille_vide_plante():
    """0 marché / grille vide : simulate doit renvoyer une équité vide, pas lever IndexError (eq[-1])."""
    grid, al = align({}, H)
    res, eng = simulate(grid, al, plan({}), ExecConfig())
    assert len(res.equity) == 0


def test_defaut_agregation_avant_sanitize():
    """4 bougies horaires valides sauf une heure au bas = 0 (tick invalide) : la bougie 4 h devrait exister
    (l'heure invalide = absente) ; or bas agrégé = 0 -> toute la bougie 4 h est déclarée absente."""
    rows = [(10, 11, 9, 10, 5), (10, 11, 0.0, 10, 5), (10, 11, 9, 10, 5), (10, 11, 9, 10, 5)]
    g = aggregate(bars(rows), "4h")
    grid, al = align({"A": g, "B": aggregate(bars(flat(5, 4)), "4h")}, 4 * H)
    assert np.isfinite(al["A"]["close"].iloc[0])


def test_defaut_prepare_1h_doublons_plante():
    """Le chemin 1 h de `prepare` ne dédoublonne pas (D9 seulement dans `aggregate`) : reindex lève ValueError."""
    a = bars(flat(10, 4))
    dup = pd.concat([a, a.iloc[[1]]])
    grid, data = prepare({"A": dup, "B": bars(flat(5, 4))}, "1h")
    assert len(grid) == 4


def test_defaut_compteur_limite_remplis_sans_execution():
    """Achats limites A 96 % (exécuté en premier, plus petit delta) puis B 97 % ; après A, trésorerie = 100 − 96 × 1,0015 = 3,856 € -> B non exécuté (q = 0) mais compté
    « rempli » et l'ordre disparaît."""
    cfg = ExecConfig(ttl_bars=3, **LIM)
    rows = [(10, 10, 10, 10, 1e6), (10, 10, 9, 10, 1e6), flat(10, 1)[0]]
    res, eng = run({"A": bars(rows), "B": bars(rows)}, {0: {"A": 0.96, "B": 0.97}}, cfg, liquidate_at_end=False)
    assert len(eng.orders_log) == 1
    assert res.stats["limite_remplis"] == 1, res.stats


def _fake_rotation(p, rv=0.05, expo=1.0, n=10, markets=("A",)):
    rot = object.__new__(Rotation)
    rot.cfg, rot.p = XSConfig(), p
    rot.P = {"rv": pd.DataFrame({m: [rv] * n for m in markets})}
    rot.expo = np.full(n, expo)
    rot.bpd = 24.0
    rot.journal = []
    return rot


def _engine(markets, n=10):
    grid, al = align({m: bars(flat(10, n)) for m in markets}, H)
    eng = Engine(grid, al, ExecConfig())
    return eng


def test_defaut_s4_liquide_en_regime_defavorable_sans_exit_on_risk_off():
    """exit_on_risk_off=False : en exposition 0 une position conservée ne doit pas être vendue. Or si son
    risque > 45, S4 calcule size(e=0) = 0 -> cible 0 = sortie totale (et, pour 0 < e < 1, la position risquée
    est ramenée à la taille du régime alors que les autres ne le sont pas)."""
    rot = _fake_rotation(RotationParams(rebalance_bars=1, exit_on_risk_off=False), expo=0.0)
    rot.select = lambda i, eng: ["A"]
    rot.risk = lambda i, m: 80.0
    eng = _engine(["A"])
    eng.i = 2
    eng.last_px[0], eng.qty[0], eng.cash, eng.entry_i[0] = 10.0, 3.0, 70.0, 0
    tg = rot(2, eng)
    assert tg.get("A", None) != 0.0, tg


def test_defaut_rotation_sort_une_position_sur_une_bougie_manquante():
    """A détenue, meilleur score, absente UNE bougie (SANS_TRANSACTION) au rééquilibrage : `select` exige ACTIF,
    A sort du classement donc de la zone de conservation -> cible 0 (vente jamais annulée ensuite)."""
    a = bars(flat(10, 3) + [None] + flat(10, 3))
    b = bars(flat(5, 7))
    grid, al = align({"A": a, "B": b}, H)
    eng = Engine(grid, al, ExecConfig())
    assert eng.state[3, 0] == SANS_TRANSACTION
    n = len(grid)
    close = pd.DataFrame({"A": al["A"]["close"].to_numpy(), "B": al["B"]["close"].to_numpy()})
    rot = object.__new__(Rotation)
    rot.cfg = XSConfig(min_age_bars=0, min_liquidity_quote=0.0, max_universe=0)
    rot.p = RotationParams(rebalance_bars=1, top_n=1, buffer=1)
    one = pd.DataFrame({"A": [1.0] * n, "B": [1.0] * n})
    rot.P = {"close": close, "age": one * 1000, "liq": one * 1e9, "score": pd.DataFrame({"A": [2.0] * n, "B": [1.0] * n}),
             "trend": one, "rv": one * 0.001}
    rot.expo, rot.bpd, rot.journal = np.ones(n), 24.0, []
    eng.last_px[:] = [10.0, 5.0]
    eng.qty[0], eng.cash, eng.entry_i[0] = 3.0, 70.0, 0
    tg = rot(3, eng)
    assert tg.get("A", 1.0) > 0, tg


# ============================================================== VALIDATIONS DES CORRECTIONS
def test_ok_classify_egal_reference_pannes_partielles_et_radiations_massives():
    rng = np.random.default_rng(11)
    for trial in range(40):
        n, k = 250, int(rng.integers(1, 25))
        C = rng.normal(100, 1, (n, k))
        C[rng.random((n, k)) < rng.uniform(0, 0.4)] = np.nan
        C[: rng.integers(0, 80), rng.integers(0, k)] = np.nan
        cut = rng.integers(20, 200)
        C[cut:, rng.random(k) < 0.6] = np.nan  # radiation massive
        rows = rng.integers(0, n, 5)
        C[rows, : max(1, k * 9 // 10)] = np.nan  # pannes partielles
        C[rng.integers(0, n, 3)] = np.nan
        C[rng.random((n, k)) < 0.01] = 0.0
        pol = DataPolicy(gap_days=int(rng.integers(1, 15)) / 24, outage_share=float(rng.uniform(0.05, 0.5)),
                         min_others_share=float(rng.uniform(0.2, 0.8)))
        a, oa = classify(C, H, pol)
        b, ob = classify_reference(C, H, pol)
        assert (oa == ob).all() and (a == b).all(), trial


def test_ok_d5_achat_marche_annule_sans_transaction():
    a = bars(flat(10, 1) + [None] + flat(10, 2))
    res, eng = run({"A": a, "B": bars(flat(5, 4))}, {0: {"A": 0.5}}, ExecConfig(slippage_bps=0), liquidate_at_end=False)
    assert eng.orders_log == [] and eng.cash == 100.0 and res.stats["achats_annules_sans_transaction"] == 1


def test_ok_ttl_2_rempli_a_la_2e_bougie_et_ttl_1_manque():
    for ttl, filled in ((2, True), (1, False)):
        rows = [(10, 10, 10, 10, 1e6), (10, 10, 10, 10, 1e6), (10, 10, 9, 10, 1e6), flat(10, 1)[0]]
        res, eng = run({"A": bars(rows)}, {0: {"A": 0.5}}, ExecConfig(ttl_bars=ttl, **LIM), liquidate_at_end=False)
        assert bool(eng.qty[0] > 0) is filled
        if filled:
            assert eng.orders_log[0]["i"] == 2 and eng.cash == pytest.approx(100 - 50 * 1.0015, abs=1e-9)


def test_ok_hybride_repli_marche_frais_taker():
    """Limite 99,95 non traversée t=1 (bas 100) -> repli au marché ouverture t=2 = 101 : q = ⌊50/101⌋,
    trésorerie = 100 − q × 101 × 1,0025."""
    cfg = ExecConfig(slippage_bps=0, mode="hybrid", ttl_bars=1, amount_step=1e-8)
    rows = [(100, 100, 100, 100, 1e6), (100, 100, 100, 100, 1e6), (101, 101, 101, 101, 1e6), flat(101, 1)[0]]
    res, eng = run({"A": bars(rows)}, {0: {"A": 0.5}}, cfg, liquidate_at_end=False)
    q = np.floor(50 / 101 / 1e-8 + 1e-9) * 1e-8
    assert eng.orders_log[0]["i"] == 2 and not eng.orders_log[0]["maker"]
    assert eng.cash == pytest.approx(100 - q * 101 * 1.0025, abs=1e-9)


def test_ok_vente_limite_jamais_abandonnee_mode_limit():
    cfg = ExecConfig(ttl_bars=1, **LIM)
    rows = [(10, 10, 10, 10, 1e6), (10, 10, 9, 10, 1e6), (10, 10, 10, 10, 1e6), (12, 12, 12, 12, 1e6), flat(12, 1)[0]]
    res, eng = run({"A": bars(rows)}, {0: {"A": 0.5}, 1: {"A": 0.0}}, cfg, liquidate_at_end=False)
    assert eng.qty[0] == 0 and eng.orders_log[-1]["i"] == 3 and eng.orders_log[-1]["px"] == 12
    assert res.stats["repli_marche"] == 1


def test_ok_pas_de_double_execution_cibles_repetees():
    res, eng = run({"A": bars(flat(10, 6))}, {i: {"A": 0.5} for i in range(6)}, ExecConfig(slippage_bps=0), liquidate_at_end=False)
    assert len(eng.orders_log) == 1


def test_ok_achat_en_attente_annule_par_cible_zero():
    rows = [(10, 10, 10, 10, 1e6), (10, 10, 10, 10, 1e6), (10, 10, 9, 10, 1e6), flat(10, 1)[0]]
    res, eng = run({"A": bars(rows)}, {0: {"A": 0.5}, 1: {"A": 0.0}}, ExecConfig(ttl_bars=3, **LIM), liquidate_at_end=False)
    assert eng.orders_log == [] and eng.next_orders() == []


def test_ok_s1_budget_et_places():
    p = RotationParams(rebalance_bars=1, top_n=3, reserve=0.1, max_weight=0.35)
    rot = _fake_rotation(p, rv=0.0005, markets=("A", "B", "C", "D"))
    rot.select = lambda i, eng: ["A", "B", "C", "D"]
    eng = _engine(["A", "B", "C", "D"])
    eng.last_px[:] = 10.0
    eng.qty[:2], eng.cash, eng.entry_i[:2] = 4.5, 10.0, 0  # A, B à 45 % chacune
    tg = rot(2, eng)
    assert tg == {}
    eng.qty[:2], eng.cash = 3.0, 40.0  # 30 % chacune -> budget 0,30, une place
    tg = rot(2, eng)
    assert tg == {"C": pytest.approx(0.30)}


def test_ok_s4_reduction_seulement():
    rot = _fake_rotation(RotationParams(rebalance_bars=1), expo=1.0)
    rot.select = lambda i, eng: ["A"]
    rot.risk = lambda i, m: 60.0  # facteur (1 − 15/55)² ≈ 0,529 ; taille 0,30 × 0,529 = 0,159
    eng = _engine(["A"])
    eng.last_px[0], eng.qty[0], eng.cash, eng.entry_i[0] = 10.0, 1.0, 90.0, 0  # poids 10 % < cible
    assert "A" not in rot(2, eng)
    eng.qty[0], eng.cash = 5.0, 50.0  # poids 50 % -> réduite
    assert rot(2, eng)["A"] == pytest.approx(0.30 * (1 - 15 / 55) ** 2)


def test_ok_d6_derniere_bougie_et_next_orders():
    res, eng = run({"A": bars(flat(10, 3))}, {2: {"A": 0.5}}, ExecConfig(ttl_bars=2, **LIM), liquidate_at_end=False)
    nx = eng.next_orders()
    assert nx[0]["type"] == "limit" and nx[0]["prix_limite"] == 10.0 and nx[0]["duree_validite_bougies"] == 2
    assert nx[0]["decide_a"] == 2 * H


def test_ok_sanitize_negatif_inf_volume_nan():
    a = bars([(10, 10, 10, 10, 1e6), (-1, 10, 10, 10, np.nan), (10, np.inf, 10, 10, 1e6), (10, 10, 10, 10, np.nan)])
    grid, al = align({"A": a, "B": bars(flat(5, 4))}, H)
    assert al["A"]["close"].isna().tolist() == [False, True, True, False] and al["A"]["volume"].iloc[3] == 0.0


def _random_universe(seed, n=300, k=6):
    rng = np.random.default_rng(seed)
    data = {}
    for j in range(k):
        px = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        rows = [(p, p * 1.02, p * 0.98, p, float(rng.uniform(1, 2000))) for p in px]
        for t in np.flatnonzero(rng.random(n) < 0.1):
            rows[t] = None
        if j == 0:
            rows[150:] = [None] * (n - 150)  # radiation
        if j == 1:
            rows[:60] = [None] * 60  # cotation tardive
        data[f"M{j}"] = bars(rows)
    for t in (100, 101):
        for m in data:
            data[m] = data[m].drop(index=t * H, errors="ignore")
    return data


def _strat(i, eng):
    if i % 7:
        return {}
    k = (i // 7) % len(eng.markets)
    return {m: (0.45 if n in (k, (k + 1) % len(eng.markets)) else 0.0) for n, m in enumerate(eng.markets)}


@pytest.mark.parametrize("mode", ["market", "limit", "hybrid"])
def test_ok_conservation_tresorerie_tous_modes(mode):
    data = _random_universe(5)
    grid, al = align(data, H)
    cfg = ExecConfig(mode=mode, ttl_bars=3, max_volume_share=0.05)
    res, eng = simulate(grid, al, _strat, cfg, DataPolicy(gap_days=10 / 24), liquidate_at_end=True)
    flow = 100.0
    for o in res.orders:
        flow += (o["notional"] - o["fee"]) if o["side"] == "sell" else -(o["notional"] + o["fee"])
    assert eng.cash == pytest.approx(flow, abs=1e-9) and (eng.qty == 0).all()
    assert res.fees_eur == pytest.approx(sum(o["fee"] for o in res.orders), abs=1e-12)
    assert any(t["reason"] == "radiation" for t in res.trades) or eng.counts is not None
    buys = [o for o in res.orders if o["side"] == "buy"]
    assert all(o["notional"] >= 5 - 1e-9 for o in buys)


@pytest.mark.parametrize("mode", ["market", "limit", "hybrid"])
def test_ok_invariance_troncature_avec_trous_et_pannes(mode):
    data = _random_universe(9)
    cfg = ExecConfig(mode=mode, ttl_bars=3)
    pol = DataPolicy(gap_days=10 / 24)
    g1, a1 = align(data, H)
    r1, _ = simulate(g1, a1, _strat, cfg, pol, liquidate_at_end=False)
    cut = 200 * H
    g2, a2 = align({m: d[d.index < cut] for m, d in data.items()}, H)
    r2, _ = simulate(g2, a2, _strat, cfg, pol, liquidate_at_end=False)
    m = len(g2)
    assert np.allclose(r1.equity.iloc[:m].to_numpy(), r2.equity.to_numpy(), atol=1e-12)
