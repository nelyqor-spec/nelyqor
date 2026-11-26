"""Audit indépendant du simulateur directionnel (commit 4997f75), intégré au dépôt (phase 6). A4 et A5 sont RÉÉCRITS
(justification mathématique dans leurs docstrings) ; les autres tests sont ceux de l'auditeur.
Données synthétiques uniquement."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from backend.research import directional as D
from backend.research.directional import LONG, SHORT, CASH, DirConfig, simulate_frames
from backend.research.directional_rules import scripted_rule
from backend.research.xsection_v4 import align

H = 3_600_000
FLAT = (100.0, 101.0, 99.0, 100.0)
BASE = DirConfig(slippage_bps=0.0, half_spread=0.0, fee_taker=0.0025, fee_maker=0.0015, atr_window=2, vol_window=2,
                 max_weight=0.5, stop_atr=2.0, tp1_atr=None, tp2_atr=None, trail_atr=None, borrow_rate_annual=0.0,
                 liquidation_slippage=0.0)
TP = replace(BASE, tp1_atr=2.0, tp2_atr=4.0, tp1_fraction=0.5)


def bars(n, over=None, base=FLAT):
    over = over or {}
    idx, recs = [], []
    for k in range(n):
        r = over.get(k, base)
        if r is None:
            continue
        idx.append(k * H)
        recs.append(dict(zip(["open", "high", "low", "close"], r, strict=True), volume=1e6))
    return pd.DataFrame(recs, index=pd.Index(idx, dtype=np.int64))


def run(data, schedule, cfg=BASE, **kw):
    return simulate_frames(data, scripted_rule(schedule), cfg, close_at_end=False, **kw)


def test_A1_recall_does_not_bypass_gap_liquidation():
    """Short 0,5 @100 (M=P=50), liquidation L = 100/(0,5·1,1) = 181,82. Bougie 4 : ouverture 200 (> L) ET rappel.
    La position est déjà sous le seuil de santé (100/(0,5·200) = 100 % < 110 %) : elle doit être LIQUIDÉE
    (pénalité 2 % de 100 = 2 €), pas rachetée en « rappel » sans pénalité. Code : rappel traité avant la
    discontinuité => pnl −50,375 au lieu de −52,375 (2 € optimistes)."""
    d = {"A": bars(6, {4: (200.0, 200.0, 199.0, 200.0)})}
    bor = {"A": np.array([True, True, True, True, False, False])}
    res, eng = run(d, {2: {"A": SHORT}}, borrowable=bor)
    assert res.stats["liquidations"] == 1, [(j["i"], j["action"], j["pnl"]) for j in res.journal]


def test_A2_stale_pending_exit_after_missing_bar():
    """Long ouvert en 3. Clôture 4 : CASH => sortie prévue en 5 ; bougie 5 absente (sortie reportée) ; clôture 5 :
    la règle redemande LONG. La décision la plus récente doit prévaloir : aucune sortie. Code : `pending_exit` n'est
    jamais purgé quand le signal revient au sens de la position => sortie « signal » en 6 (aller-retour inutile,
    ≈ 0,25 € de frais, et ré-entrée retardée par l'ATR NaN)."""
    d = {"A": bars(10, {5: None})}
    res, eng = run(d, {2: {"A": LONG}, 4: {"A": CASH}, 5: {"A": LONG}})
    assert not [j for j in res.journal if j["action"] == "signal"], [(j["i"], j["action"]) for j in res.journal]


def test_A3_tp1_not_marked_done_when_partial_exit_refused():
    """Long 0,2 @100 (20 €), TP1 = 104 avec fraction 0,2 => q = 0,04, 4,16 € < 5 € (sortie refusée, reliquat
    16,64 € ≥ 5). Le code met tp1_done=True et compte un TP1 AVANT de savoir si la sortie a eu lieu => le seuil de
    rentabilité (stop 100) est armé sans gain réalisé et la position entière sort à 100 en bougie 5
    (stop initial 96 non touché)."""
    cfg = replace(TP, tp1_fraction=0.2, max_weight=0.2)
    d = {"A": bars(8, {4: (100.0, 104.5, 99.5, 102.0), 5: (101.0, 101.0, 99.9, 100.5)})}
    res, eng = run(d, {2: {"A": LONG}}, cfg)
    assert res.stats["tp1"] == 0
    assert "A" in eng.pos, [(j["i"], j["action"], j["px"]) for j in res.journal]


def test_A4_effective_leverage_bounded_by_liquidation_and_reduced():
    """REMPLACE l'ancien A4 (« levier ≤ 1 à chaque clôture »), mathématiquement impossible sans rééquilibrage continu.

    Short de notionnel n = w·E0, collatéral M = n, produit P = n, quantité q = n/x0. À la clôture au prix x :
      E(x) = E0 − q(x − x0) − frais,   L(x) = q·x / E(x),
    strictement croissant en x. La décision est prise À la clôture et ne s'exécute qu'à l'ouverture suivante : on ne
    peut donc pas empêcher L > 1 à la clôture même, seulement le MESURER puis le RÉDUIRE ensuite. La liquidation
    (santé (M+P)/(q·x) < 1,1) borne en revanche q·x < 2n/1,1, d'où
      L ≤ L_liq(w) = (2w/1,1) / (1 − (2/1,1 − 1)·w) = 1,818w / (1 − 0,818w)   (frais négligés, borne majorée de 1 %).
    Invariants testés sur 200 trajectoires aléatoires haussières : (1) le levier mesuré à chaque clôture ne dépasse
    jamais L_liq(w) ; (2) avec un seuil de 1,2, toute clôture au-delà du seuil est suivie d'une réduction à
    l'ouverture suivante (ou d'une sortie/liquidation)."""
    rng = np.random.default_rng(7)
    w = BASE.max_weight
    bound = (2 * w / 1.1) / (1 - (2 / 1.1 - 1) * w) * 1.01
    cfg = replace(BASE, stop_atr=1000.0, max_effective_gross=1.2)
    for _ in range(200):
        n = 14
        steps = np.r_[np.zeros(4), rng.uniform(0.0, 0.12, n - 4)]
        c = 100.0 * np.exp(np.cumsum(steps))
        rows = {k: (c[k - 1] if k else c[0], c[k] * 1.001, min(c[k - 1] if k else c[0], c[k]) * 0.999, c[k]) for k in range(n)}
        grid, al = align({"A": bars(n, rows)}, H)
        eng = D.DirEngine(grid, al, cfg)
        rule = scripted_rule({2: {"A": SHORT}})
        lev = []
        for i in range(len(grid)):
            eng.step_bar(i, rule)
            e = eng.equity()
            lev.append(eng.gross() / e if eng.pos and e > 0 else 0.0)
        assert max(lev) <= bound, (max(lev), bound)
        acts = {(j["i"], j["action"]) for j in eng.journal}
        for i, x in enumerate(lev[:-1]):
            if x > 1.2 + 1e-9:
                assert any(t == i + 1 for t, a in acts if a in ("reduction_levier", "liquidation", "stop", "rappel")) \
                    or not eng.pos, (i, x, sorted(acts))


def test_A5_view_exposes_no_path_to_future_data():
    """REMPLACE l'ancien A5 (il lisait `view._e`, attribut supprimé par la correction : il échouait par
    AttributeError, ce qui ne prouvait rien). Preuve positive : parcours de TOUT le graphe d'objets atteignable depuis
    la vue (gc.get_referents, profondeur 4) : aucun DirEngine, aucun tableau de plus de i+1 lignes ; et la décision
    de la règle est identique avec les données tronquées à i."""
    import gc
    d = {"A": bars(8, {4: (100.0, 130.0, 99.0, 130.0), 6: (90.0, 91.0, 89.0, 90.0)})}
    grid, al = align(d, H)

    def reachable(root, depth=4):
        seen, frontier, out = {id(root)}, [root], []
        for _ in range(depth):
            nxt = []
            for o in frontier:
                for r in gc.get_referents(o):
                    if id(r) in seen or isinstance(r, type):
                        continue
                    seen.add(id(r)); out.append(r); nxt.append(r)
            frontier = nxt
        return out

    def spy(i, view):
        for o in reachable(view):
            assert not isinstance(o, D.DirEngine), "moteur atteignable depuis la vue"
            if isinstance(o, np.ndarray) and o.ndim >= 1 and o.shape[0] > i + 1 and o.ndim == 2:
                raise AssertionError(f"tableau de {o.shape[0]} lignes atteignable à i={i}")
        return {}
    D.simulate(grid, al, spy, BASE)
    full, _ = D.simulate(grid, al, scripted_rule({2: {"A": LONG}}), BASE)
    cut = {"A": d["A"].iloc[:6]}
    g2, al2 = align(cut, H)
    part, _ = D.simulate(g2, al2, scripted_rule({2: {"A": LONG}}), BASE, close_at_end=False)
    assert np.allclose(full.equity.to_numpy()[:5], part.equity.to_numpy()[:5])


def test_A6_atr_ignores_gap_after_missing_bar():
    """TR après une bougie absente : |H − C_{t−1}| vaut NaN et np.fmax l'IGNORE => TR = H − L. Bougies :
    t0 (100,101,99,100), t1 absente, t2 (150,151,149,150), t3 (150,151,149,150). ATR(2) en t3 = (2+2)/2 = 2
    alors que l'écart réel depuis la dernière clôture connue (100) est ≈ 51 : ATR sous-estimé (stops/tailles
    trop serrés/gros juste après une coupure)."""
    Hh = np.array([[101.0], [np.nan], [151.0], [151.0]])
    Ll = np.array([[99.0], [np.nan], [149.0], [149.0]])
    Cc = np.array([[100.0], [np.nan], [150.0], [150.0]])
    atr = D.causal_atr(Hh, Ll, Cc, 2)
    assert not (np.isfinite(atr[3, 0]) and atr[3, 0] < 10), atr[:, 0]


def test_A7_incoherent_ohlc_treated_as_present():
    """Bougie incohérente (haut 99 < ouverture 100) acceptée comme présente : les tests stop/TP intra-bougie
    deviennent incohérents (ex. long avec TP déclenché alors que H < O)."""
    d = {"A": bars(4, {2: (100.0, 99.0, 98.0, 98.5)})}
    grid, al = align(d, H)
    eng = D.DirEngine(grid, al, BASE)
    assert not eng.present[2, 0]
