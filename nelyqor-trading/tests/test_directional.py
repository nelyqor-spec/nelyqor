"""Scénarios DÉTERMINISTES du simulateur directionnel LONG/SHORT/CASH (phase 6, simulation seulement).

Toutes les données sont SYNTHÉTIQUES, construites à la main (aucune donnée de marché réelle). Chaque résultat
attendu est calculé à la main dans la docstring du test et comparé avec une tolérance de 1e-9 €.

Convention commune (configuration BASE) : bougies horaires ; bougie « plate » O=C=100, H=101, L=99 ; fenêtres ATR et
vol de 2 bougies => ATR = 2 (TR = max(2, 1, 1)) et vol = 0 (log-rendements nuls) dès la bougie 2 ; poids = plafond
0,5 (vol nulle) × facteur de risque 1 => 50 € sur 100 € ; décision à la clôture de 2, exécution à l'ouverture de 3
au prix 100 => quantité 0,5, frais taker 0,25 % = 0,125 €. Stop = 100 − 2 × ATR = 96 (long) / 104 (short).
Glissement, demi-écart, taux d'emprunt et glissement de liquidation nuls sauf mention contraire.
"""
from __future__ import annotations

import ast
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.portfolio.sizing_math import risk_factor
from backend.research import directional as D
from backend.research.directional import (
    CASH,
    LONG,
    SHORT,
    DirConfig,
    compare,
    owner_risk_factor,
    simulate,
    simulate_frames,
)
from backend.research.directional_rules import scripted_rule, trend_rule
from backend.research.xsection_v4 import align

H = 3_600_000
ROOT = Path(__file__).resolve().parents[1]
FLAT = (100.0, 101.0, 99.0, 100.0)
BASE = DirConfig(slippage_bps=0.0, half_spread=0.0, fee_taker=0.0025, fee_maker=0.0015, atr_window=2, vol_window=2,
                 max_weight=0.5, stop_atr=2.0, tp1_atr=None, tp2_atr=None, trail_atr=None, borrow_rate_annual=0.0,
                 liquidation_slippage=0.0)
TP = replace(BASE, tp1_atr=2.0, tp2_atr=4.0, tp1_fraction=0.5)
RATE_1E4 = 0.876  # taux annuel => 0,876 / 8760 = 1e-4 par heure


def bars(n, over=None, base=FLAT):
    """n bougies plates ; `over` = {indice: (o, h, l, c) ou None (bougie absente)}."""
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


def actions(res):
    return [(t["i"], t["market"], t["action"]) for t in res.journal]


# ------------------------------------------------------------------------------------------------ LONG : sorties
def test_long_stop_exact_and_no_reentry_while_signal_persists():
    """Entrée 0,5 à 100 : trésorerie 100 − 50 − 0,125 = 49,875. Bougie 4 (O 100, H 101, L 95) : stop 96 touché ;
    vente 0,5 × 96 = 48, frais 0,12 => trésorerie 97,755. PnL = 48 − 0,12 − 50,125 = −2,245. La règle dit encore
    LONG mais la sortie protectrice bloque la ré-entrée jusqu'à un changement de signal."""
    res, eng = run({"A": bars(8, {4: (100, 101, 95, 97)})}, {2: {"A": LONG}})
    assert eng.cash == pytest.approx(97.755, abs=1e-9)
    assert res.trades[0]["pnl"] == pytest.approx(-2.245, abs=1e-9)
    assert actions(res) == [(3, "A", "entree_long"), (4, "A", "stop")]
    assert res.equity.iloc[-1] == pytest.approx(97.755, abs=1e-9)


def test_stop_and_tp_same_bar_assumes_stop_first():
    """Bougie 4 : H = 110 (au-delà de l'objectif 104 et 108) ET L = 95 (sous le stop 96) : ordre PRUDENT => seul le
    stop s'exécute à 96 ; trésorerie 97,755 comme dans le scénario de stop pur, aucun TP journalisé."""
    res, eng = run({"A": bars(6, {4: (100, 110, 95, 105)})}, {2: {"A": LONG}}, cfg=TP)
    assert eng.cash == pytest.approx(97.755, abs=1e-9)
    assert [a for _, _, a in actions(res)] == ["entree_long", "stop"]


def test_long_gap_below_stop_executes_at_open_not_stop():
    """Ouverture de la bougie 4 à 90 (< stop 96) : vente À L'OUVERTURE 0,5 × 90 = 45, frais 0,1125 ;
    trésorerie 49,875 + 44,8875 = 94,7625 (et non 97,755 qu'aurait donné une exécution au stop)."""
    res, eng = run({"A": bars(6, {4: (90, 92, 88, 91)})}, {2: {"A": LONG}})
    assert eng.cash == pytest.approx(94.7625, abs=1e-9)
    assert res.journal[-1]["action"] == "stop_gap" and res.journal[-1]["px"] == 90
    assert eng.counts["gaps"] == 1


def test_partial_tp1_then_tp2():
    """TP1 = 104, TP2 = 108. Bougie 4 (H 105) : vente de 50 % (0,25) à 104 = 26, frais maker 0,039 => trésorerie
    75,836. Bougie 5 (H 109) : vente 0,25 à 108 = 27, frais 0,0405 => 102,7955. PnL = (26 − 0,039 − 25,0625) +
    (27 − 0,0405 − 25,0625) = 2,7955."""
    res, eng = run({"A": bars(7, {4: (100, 105, 99.5, 104), 5: (104, 109, 103, 108)})}, {2: {"A": LONG}}, cfg=TP)
    tr = [t for t in res.journal if t["action"].startswith("tp")]
    assert [(t["action"], t["qty"], t["px"]) for t in tr] == [("tp1", 0.25, 104), ("tp2", 0.25, 108)]
    assert eng.cash == pytest.approx(102.7955, abs=1e-9)
    assert res.trades[0]["pnl"] == pytest.approx(2.7955, abs=1e-9)
    assert not eng.pos


def test_breakeven_after_tp1_then_retrace():
    """Après TP1 (trésorerie 75,836), le stop monte au prix d'entrée 100 à la clôture 4 (effectif bougie 5).
    Bougie 5 : L = 99 => vente 0,25 à 100 = 25, frais 0,0625 => 100,7735 ; motif « trailing » (stop déplacé)."""
    res, eng = run({"A": bars(7, {4: (100, 105, 99.5, 104), 5: (103, 103.5, 99, 99.5)})}, {2: {"A": LONG}}, cfg=TP)
    assert eng.cash == pytest.approx(100.7735, abs=1e-9)
    assert res.journal[-1]["action"] == "trailing" and eng.counts["trailing"] == 1


def test_trailing_stop_dynamic_atr():
    """Stop suiveur = plus-haut − 1 × ATR(2), mis à jour à la CLÔTURE (effectif à la bougie suivante).
    Clôture 3 : plus-haut 101, ATR 2 => stop 99. Bougie 4 (O 103, H 110, L 102, C 109) : TR = max(8, 10, 2) = 10,
    ATR = (2 + 10)/2 = 6 => stop 110 − 6 = 104. Bougie 5 (L 103) : vente 0,5 à 104 = 52, frais 0,13 =>
    trésorerie 49,875 + 51,87 = 101,745."""
    cfg = replace(BASE, trail_atr=1.0)
    res, eng = run({"A": bars(7, {4: (103, 110, 102, 109), 5: (108, 108, 103, 104)})}, {2: {"A": LONG}}, cfg=cfg)
    assert eng.cash == pytest.approx(101.745, abs=1e-9)
    assert res.journal[-1]["px"] == pytest.approx(104, abs=1e-12)


# ------------------------------------------------------------------------------------------------ SHORT
def test_short_accounting_borrow_fee_and_partial_cover():
    """Short 0,5 à 100 : collatéral propre M = 50 et produit P = 50 bloqués, frais 0,125 => trésorerie 49,875 ;
    emprunt (1e-4/h) bougie 3 : 0,5 × 100 × 1e-4 = 0,005 => 49,87 ; équité clôture 3 = 49,87 + 100 − 50 = 99,87.
    Bougie 4 : emprunt 0,005 (49,865) ; L = 95 ≤ TP1 96 : rachat 0,25 à 96 = 24, maker 0,036, libère 50 =>
    75,829. Signal CASH à la clôture 4. Bougie 5 : emprunt 0,25 × 96 × 1e-4 = 0,0024 (75,8266) puis rachat 0,25
    à l'ouverture 96 = 24, taker 0,06, libère 50 => 101,7666. Frais d'emprunt cumulés 0,0124."""
    cfg = replace(TP, borrow_rate_annual=RATE_1E4)
    res, eng = run({"A": bars(7, {4: (100, 101, 95, 96), 5: (96, 97, 95, 96)})}, {2: {"A": SHORT}, 4: {"A": CASH}},
                   cfg=cfg)
    assert res.equity.iloc[3] == pytest.approx(99.87, abs=1e-9)
    assert eng.cash == pytest.approx(101.7666, abs=1e-9)
    assert eng.counts["borrow_fees"] == pytest.approx(0.0124, abs=1e-12)
    assert sum(t["pnl"] for t in res.trades) == pytest.approx(1.7666, abs=1e-9)


def test_short_collateral_blocked_equity_formula():
    """Après l'entrée short : trésorerie 49,875, collatéral bloqué M + P = 100, dette 0,5 × 100 = 50 ;
    équité = trésorerie + collatéral + valeur longue − dette = 49,875 + 100 + 0 − 50 = 99,875."""
    _, eng = run({"A": bars(4)}, {2: {"A": SHORT}})
    assert eng.cash == pytest.approx(49.875, abs=1e-12)
    assert eng.collateral() == pytest.approx(100.0, abs=1e-12)
    assert eng.equity() == pytest.approx(99.875, abs=1e-12)


def test_short_stop_gap_executes_at_open():
    """Short, stop 104 ; ouverture de la bougie 4 à 120 (sous le prix de liquidation 100/(0,5 × 1,1) = 181,82) :
    rachat À L'OUVERTURE 0,5 × 120 = 60, frais 0,15 ; trésorerie 49,875 + 100 − 60,15 = 89,725."""
    res, eng = run({"A": bars(6, {4: (120, 125, 118, 122)})}, {2: {"A": SHORT}})
    assert eng.cash == pytest.approx(89.725, abs=1e-9)
    assert res.journal[-1]["action"] == "stop_gap"


def test_short_gap_loss_exceeds_notional_with_liquidation_at_open():
    """Discontinuité : ouverture à 250 ≥ prix de liquidation 181,82 => liquidation À L'OUVERTURE : rachat 0,5 × 250
    = 125, frais 0,3125, pénalité 2 % = 2,5. Trésorerie = 49,875 + 100 − 125 − 0,3125 − 2,5 = 22,0625.
    PnL = 50 − 125 − 0,3125 − 2,5 − 0,125 = −77,9375 : la perte DÉPASSE le notionnel de 50 € (> 100 %)."""
    res, eng = run({"A": bars(6, {4: (250, 260, 240, 255)})}, {2: {"A": SHORT}})
    assert eng.cash == pytest.approx(22.0625, abs=1e-9)
    assert res.trades[0]["pnl"] == pytest.approx(-77.9375, abs=1e-9)
    assert -res.trades[0]["pnl"] > res.trades[0]["notional0"]
    assert eng.counts["liquidations"] == 1 and res.journal[-1]["penalty"] == pytest.approx(2.5)


def test_short_extreme_gap_creates_deficit_negative_equity():
    """Poids 100 %, ouverture à 400 : le rachat (≈ 0,9975 × 400 ≈ 399 €) dépasse collatéral + produit (≈ 199,5 €)
    et la trésorerie : trésorerie et équité NÉGATIVES, déficit constaté ; plus aucune entrée ensuite."""
    cfg = replace(BASE, max_weight=1.0)
    res, eng = run({"A": bars(8, {4: (400, 410, 390, 400)})}, {2: {"A": SHORT}, 5: {"A": LONG}}, cfg=cfg)
    assert eng.cash < 0 and eng.equity() < 0
    assert eng.counts["deficits"] == 1
    assert [a for _, _, a in actions(res)] == ["entree_short", "liquidation"]


def test_liquidation_intrabar_when_stop_beyond_liquidation():
    """Stop à 100 + 50 × 2 = 200, au-delà du prix de liquidation Lq = 100/(0,5 × 1,1) = 181,8181… ; bougie 4 H = 190 :
    liquidation au niveau Lq × (1 + 0,5 %) (glissement de liquidation), frais 0,25 % et pénalité 2 % :
    trésorerie = 49,875 + 100 − 0,5 × px × 1,0225."""
    cfg = replace(BASE, stop_atr=50.0, liquidation_slippage=0.005)
    _, eng = run({"A": bars(6, {4: (100, 190, 99, 150)})}, {2: {"A": SHORT}}, cfg=cfg)
    px = 100 / (0.5 * 1.1) * 1.005
    assert eng.cash == pytest.approx(149.875 - 0.5 * px * 1.0225, abs=1e-9)
    assert eng.counts["liquidations"] == 1


def test_short_stop_before_liquidation_same_bar():
    """Même bougie (H = 190) mais stop 104 < Lq : le stop est atteint d'abord sur le trajet défavorable ;
    rachat 0,5 × 104 = 52, frais 0,13 => trésorerie 97,745 ; aucune liquidation."""
    _, eng = run({"A": bars(6, {4: (100, 190, 99, 150)})}, {2: {"A": SHORT}})
    assert eng.cash == pytest.approx(97.745, abs=1e-9)
    assert eng.counts["liquidations"] == 0


def test_health_alert_counted():
    """Ratio de santé = (M + P)/(q·x) = 100/(0,5 x). Clôture 160 => 1,25 (≥ 1,20, pas d'alerte) ; clôture 170 =>
    1,176 < 1,20 => une alerte ; clôture 6 à 100 => 2,0 ; pas de liquidation (seuil 1,10 => x ≥ 181,82)."""
    cfg = replace(BASE, stop_atr=100.0)
    _, eng = run({"A": bars(7, {4: (100, 160, 99, 160), 5: (160, 170, 159, 170)})}, {2: {"A": SHORT}}, cfg=cfg)
    assert eng.counts["alertes_sante"] == 1
    assert eng.counts["liquidations"] == 0


def test_recall_forces_buyback_at_open():
    """Empruntable jusqu'à la bougie 3, rappel à la bougie 4 : rachat forcé à l'ouverture 100 = 50, frais 0,125 ;
    trésorerie 49,875 + 100 − 50,125 = 99,75 ; motif « rappel »."""
    ok = np.array([True, True, True, True, False, False])
    res, eng = run({"A": bars(6)}, {2: {"A": SHORT}}, borrowable={"A": ok})
    assert eng.cash == pytest.approx(99.75, abs=1e-9)
    assert res.journal[-1]["action"] == "rappel" and eng.counts["rappels"] == 1


def test_not_borrowable_means_no_short():
    """Marché absent de la liste empruntable : aucun short, 100 € restent en euros."""
    res, eng = run({"A": bars(6)}, {2: {"A": SHORT}}, borrowable={})
    assert not res.journal and eng.cash == 100.0
    assert eng.counts["short_refuse_emprunt"] > 0


def test_borrow_lost_between_decision_and_execution_cancels_entry():
    """Empruntable à la décision (bougie 2), plus à l'exécution (bougie 3) : entrée annulée, 100 € conservés."""
    ok = np.array([True, True, True, False, True, True])
    res, eng = run({"A": bars(4)}, {2: {"A": SHORT}}, borrowable={"A": ok})
    assert not res.journal and eng.counts["entrees_annulees"] == 1


def test_variable_borrow_rate_and_stress():
    """Taux par bougie : 0,876 (1e-4/h) à la bougie 3, 1,752 (2e-4/h) à la bougie 4 : frais 0,5 × 100 × (1e-4 +
    2e-4) = 0,015. Stress × 3 : 0,045."""
    rate = np.array([0, 0, 0, 0.876, 1.752], dtype=float)
    _, eng = run({"A": bars(5)}, {2: {"A": SHORT}}, borrow_rate={"A": rate})
    assert eng.counts["borrow_fees"] == pytest.approx(0.015, abs=1e-12)
    _, eng3 = run({"A": bars(5)}, {2: {"A": SHORT}}, cfg=replace(BASE, borrow_stress=3.0), borrow_rate={"A": rate})
    assert eng3.counts["borrow_fees"] == pytest.approx(0.045, abs=1e-12)


def test_default_borrow_rate_is_bitvavo_hourly():
    """Défaut : 0,0023 %/h. Short de 50 € tenu une bougie : 50 × 0,000023 = 0,00115 €."""
    cfg = replace(BASE, borrow_rate_annual=DirConfig().borrow_rate_annual)
    _, eng = run({"A": bars(4)}, {2: {"A": SHORT}}, cfg=cfg)
    assert eng.counts["borrow_fees"] == pytest.approx(0.00115, abs=1e-12)
    d = DirConfig()
    assert (d.liquidation_penalty, d.health_alert, d.fee_taker, d.liquidation_slippage) == (0.02, 1.2, 0.0025, 0.005)
    assert 1 + d.maintenance_margin == pytest.approx(1.10)


# ------------------------------------------------------------------------------------------------ levier, taille
def test_no_leverage_multi_market_and_config_refused():
    """A LONG, B SHORT, C LONG, plafond 50 % chacun : à la décision 50 + 50 = 100 € = équité, C n'a plus de place
    (< 5 €) ; à l'exécution B est réduit pour que Σ|notionnels| ≤ équité APRÈS frais et trésorerie ≥ 0.
    Levier et collatéral < 100 % refusés par la configuration."""
    data = {m: bars(6) for m in ("A", "B", "C")}
    res, eng = run(data, {2: {"A": LONG, "B": SHORT, "C": LONG}})
    assert {t["market"] for t in res.journal} == {"A", "B"}
    assert res.stats["levier_max_entree"] <= 1.0 + 1e-12
    assert eng.gross() <= eng.equity() + 1e-9 and eng.cash >= 0
    assert eng.collateral() >= eng.pos["B"].qty * 100 * 2 - 1e-9  # M + P ≥ 2 × notionnel (ratio 1)
    with pytest.raises(ValueError):
        DirConfig(max_gross=1.5).validate()
    with pytest.raises(ValueError):
        DirConfig(collateral_ratio=0.5).validate()
    assert D.LEVERAGE == 1


def test_owner_risk_factor_matches_rule():
    """(1 − (r − 45)/55)² : r = 45 -> 1 ; 72,5 -> 0,25 ; 100 -> 0 ; NaN -> 0 ; identique à sizing_math."""
    assert owner_risk_factor(45) == 1.0 and owner_risk_factor(72.5) == pytest.approx(0.25)
    assert owner_risk_factor(100) == 0.0 and owner_risk_factor(float("nan")) == 0.0
    for r in (0, 30, 45, 50, 60, 72.5, 90, 100, float("nan")):
        assert owner_risk_factor(r) == pytest.approx(risk_factor(r))


def test_vol_target_sizing():
    """Clôtures 100, 100·e^a, 100 avec a = 0,04/√24 : log-rendements (a, −a), écart-type a => vol journalière
    a·√24 = 4 % ; score (4 − 1)/11 × 100 = 27,3 < 45 (facteur 1) ; poids = min(0,5 ; 1 %/4 %) = 0,25 => 25 € ;
    quantité 0,25 à 100."""
    a = 0.04 / math.sqrt(24)
    c1 = 100 * math.exp(a)
    df = bars(5, {1: (100, c1 + 1, 99, c1), 2: (c1, c1 + 1, 99, 100)})
    res, _ = run({"A": df}, {2: {"A": LONG}}, cfg=replace(BASE, target_vol_daily=0.01))
    assert res.journal[0]["qty"] == pytest.approx(0.25, abs=2e-8)  # arrondi vers le bas au pas 1e-8


def test_no_martingale_size_never_increases_after_loss():
    """Trade 1 : score de risque 72,5 => facteur 0,25 => 0,5 × 0,25 × 100 = 12,5 € ; perte au stop 96.
    Puis score 0 : la taille brute (vol cible : log-rendements ln(100/97) et 0 => vol 7,46 %/j => poids 0,268)
    serait ≈ 26,7 € mais elle est plafonnée au notionnel du trade perdant : 12,5 €. Sans la protection, la taille
    aurait AUGMENTÉ après la perte (contrôle)."""
    rs = np.array([72.5] * 3 + [0.0] * 7)
    data = {"A": bars(10, {4: (100, 101, 95, 97), 5: (100, 101, 99, 100)})}
    sched = {2: {"A": LONG}, 5: {"A": CASH}, 6: {"A": LONG}}
    res, _ = run(data, sched, risk_score={"A": rs})
    entries = [t for t in res.journal if t["action"] == "entree_long"]
    assert res.trades[0]["pnl"] < 0
    assert [e["notional"] for e in entries] == [pytest.approx(12.5), pytest.approx(12.5)]
    res2, _ = run(data, sched, cfg=replace(BASE, anti_martingale=False), risk_score={"A": rs})
    assert [t for t in res2.journal if t["action"] == "entree_long"][1]["notional"] > 20


# ------------------------------------------------------------------------------------------------ frictions
def test_minimum_order_and_partial_below_minimum():
    """Poids 4 % => 4 € < 5 € : aucun ordre. Poids 8 % => 8 € : TP1 de 50 % laisserait 4,16 € < 5 € => la position
    ENTIÈRE est vendue à 104 (jamais de reliquat sous le minimum)."""
    res, eng = run({"A": bars(5)}, {2: {"A": LONG}}, cfg=replace(BASE, max_weight=0.04))
    assert not res.journal and eng.cash == 100.0
    res2, eng2 = run({"A": bars(6, {4: (100, 105, 99.5, 104)})}, {2: {"A": LONG}}, cfg=replace(TP, max_weight=0.08))
    assert res2.journal[-1]["action"] == "tp1" and res2.journal[-1]["qty"] == pytest.approx(0.08)
    assert not eng2.pos


def test_quantity_rounded_down():
    """Prix 3, pas de quantité 0,01 : 50/3 = 16,666… => 16,66 ; notionnel 49,98 ; trésorerie 100 − 49,98 × 1,0025
    = 49,89505."""
    res, eng = run({"A": bars(5, base=(3.0, 3.03, 2.97, 3.0))}, {2: {"A": LONG}}, cfg=replace(BASE, amount_step=0.01))
    assert res.journal[0]["qty"] == pytest.approx(16.66, abs=1e-12)
    assert eng.cash == pytest.approx(49.89505, abs=1e-9)


def test_slippage_and_spread_are_adverse():
    """Glissement 10 pb + demi-écart 0,1 % : achat à 100 × 1,002 = 100,2 ; short à 100 × 0,998 = 99,8.
    Pas 0,001 : 50/100,2 = 0,49900… => 0,499 ; 50/99,8 = 0,50100… => 0,501."""
    cfg = replace(BASE, slippage_bps=10.0, half_spread=0.001, amount_step=0.001)
    r1, _ = run({"A": bars(4)}, {2: {"A": LONG}}, cfg=cfg)
    r2, _ = run({"A": bars(4)}, {2: {"A": SHORT}}, cfg=cfg)
    assert (r1.journal[0]["px"], r1.journal[0]["qty"]) == (pytest.approx(100.2), pytest.approx(0.499))
    assert (r2.journal[0]["px"], r2.journal[0]["qty"]) == (pytest.approx(99.8), pytest.approx(0.501))


def test_missing_bar_defers_exit_then_gap_at_next_open():
    """Bougie 4 absente : aucune exécution ; bougie 5 ouvre à 90 (< stop 96) => vente à 90 : trésorerie 94,7625.
    Une entrée dont la bougie d'exécution est absente est annulée."""
    res, eng = run({"A": bars(7, {4: None, 5: (90, 92, 88, 91)})}, {2: {"A": LONG}})
    assert eng.cash == pytest.approx(94.7625, abs=1e-9) and res.journal[-1]["i"] == 5
    res2, eng2 = run({"A": bars(5, {3: None})}, {2: {"A": LONG}})
    assert not res2.journal and eng2.counts["entrees_annulees"] == 1


def test_reversal_long_to_short_same_open():
    """LONG décidé en 2, SHORT décidé en 3 : à l'ouverture 4 la sortie (0,5 à 100, frais 0,125 => 99,75) précède
    l'entrée short dimensionnée à la clôture 3 sur l'équité 99,875 : 0,5 × 99,875 = 49,9375 € (frais 0,12484375) ;
    équité = 99,75 − 0,12484375 = 99,62515625."""
    res, eng = run({"A": bars(6)}, {2: {"A": LONG}, 3: {"A": SHORT}})
    assert [a for _, _, a in actions(res)][:3] == ["entree_long", "signal", "entree_short"]
    assert res.equity.iloc[4] == pytest.approx(99.62515625, abs=1e-9)


def test_invalid_signal_refused():
    with pytest.raises(ValueError):
        simulate_frames({"A": bars(4)}, lambda i, v: {"A": "ACHAT_FORT"}, BASE)


def test_cash_by_default_without_history():
    """ATR et vol non définis avant la bougie 2 : un LONG décidé en 0 ou 1 est une ABSTENTION."""
    res, _ = run({"A": bars(3)}, {0: {"A": LONG}})
    assert not res.journal


# ------------------------------------------------------------------------------------------------ biais, comptabilité
def _synthetic(n, seed_shift=0.0):
    """Série SYNTHÉTIQUE déterministe (sinusoïde + tendance + saut), aucune donnée de marché."""
    t = np.arange(n, dtype=float)
    c = 100 + 8 * np.sin(t / 9 + seed_shift) + 0.05 * t - 15 * (t > 0.6 * n)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.7, "low": np.minimum(o, c) - 0.7, "close": c,
                         "volume": 1e6}, index=pd.Index(np.arange(n, dtype=np.int64) * H))


def test_truncation_no_future_information():
    """Résultats IDENTIQUES avant la date de coupure : 120 bougies contre les 80 premières (même règle de tendance,
    short autorisé, frais d'emprunt) : équité [0, 80) et journal des bougies < 80 égaux à l'identique."""
    cfg = DirConfig(atr_window=5, vol_window=10)
    rule = trend_rule(5, 15, 0.2)
    full = {"A": _synthetic(120), "B": _synthetic(120, 2.0)}
    cut = {m: d.iloc[:80] for m, d in full.items()}
    rf, _ = simulate_frames(full, rule, cfg, close_at_end=False)
    rc, _ = simulate_frames(cut, rule, cfg, close_at_end=False)
    assert len(rf.journal) > len([t for t in rf.journal if t["i"] < 80]) > 2
    np.testing.assert_array_equal(rf.equity.to_numpy()[:80], rc.equity.to_numpy())
    assert [t for t in rf.journal if t["i"] < 80] == rc.journal


def test_rule_sees_only_past():
    seen = []

    def spy(i, view):
        seen.append((i, len(view.closes("A")), len(view.highs("A"))))
        return {}

    simulate_frames({"A": bars(6)}, spy, BASE)
    assert all(n == i + 1 and h == i + 1 for i, n, h in seen)


def test_accounting_identity_and_journal_reconcile():
    """Règle de tendance sur deux marchés synthétiques, frais d'emprunt réels par défaut, glissement : l'invariant
    est contrôlé par le moteur à chaque bougie (exception sinon) ; en fin, tout est clôturé et
    Σ PnL des trades (frais et emprunt compris) = équité finale − 100 ; équité = trésorerie."""
    data = {"A": _synthetic(200), "B": _synthetic(200, 1.0)}
    cfg = DirConfig(atr_window=5, vol_window=10, trail_atr=2.0)
    res, eng = simulate_frames(data, trend_rule(5, 15, 0.2), cfg)
    assert res.stats["n_trades"] >= 3 and any(t["side"] == SHORT for t in res.trades)
    assert sum(t["pnl"] for t in res.trades) == pytest.approx(res.stats["equity_finale"] - 100, abs=1e-9)
    assert eng.equity() == pytest.approx(eng.cash, abs=1e-12) and not eng.pos
    assert res.stats["levier_max_entree"] <= 1.0 + 1e-12


def test_compare_isolates_short_contribution():
    """Prix qui baisse de 100 à 90 après la bougie 3 ; règle : SHORT en 2, CASH en 5. Avec short : rachat 0,5 à
    l'ouverture 6 (90) = 45, frais 0,1125 => 100 − 0,125 + 50 − 45,1125 = 104,7625 ; sans short (LONG/CASH) : rien,
    100 ; CASH : 100 ; apport du short = 4,7625."""
    fall = (90.0, 91.0, 89.0, 90.0)
    df = bars(8, {4: (100, 101, 89, 90), 5: fall, 6: fall, 7: fall})
    grid, al = align({"A": df}, H)
    out = compare(grid, al, scripted_rule({2: {"A": SHORT}, 5: {"A": CASH}}), BASE)
    assert out["long_short"]["equity_finale"] == pytest.approx(104.7625, abs=1e-9)
    assert out["long_cash"]["equity_finale"] == 100.0 and out["cash"]["equity_finale"] == 100.0
    assert out["apport_short"] == pytest.approx(4.7625, abs=1e-9)
    res, _ = simulate(grid, al, scripted_rule({2: {"A": SHORT}}), replace(BASE, allow_short=False))
    assert not res.journal


# ------------------------------------------------------------------------------------------------ isolement
FORBIDDEN_TARGETS = [ROOT / "backend" / "runner.py", ROOT / "backend" / "execution", ROOT / "backend" / "guardian"]


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def test_flags_and_real_engine_never_imports_directional():
    """Drapeaux explicites (expérimental, non déployable, short absent de l'API Bitvavo, levier 1) ; aucun fichier
    du moteur réel (runner, execution, guardian) ni aucun autre module hors `research/directional*` n'importe le
    simulateur directionnel ; l'import du moteur réel ne le charge pas (sous-processus)."""
    assert D.EXPERIMENTAL is True and D.DEPLOYABLE is False and D.API_AVAILABLE is False and D.LEVERAGE == 1
    files = [p for t in FORBIDDEN_TARGETS for p in ([t] if t.is_file() else t.rglob("*.py"))]
    files += [p for p in (ROOT / "backend").rglob("*.py") if not p.name.startswith("directional")]
    assert len(files) > 5
    for f in files:
        bad = {n for n in _imports(f) if "directional" in n}
        assert not bad, f"{f} importe {bad}"
    code = ("import sys, backend.runner, backend.execution, backend.guardian; "
            "print(any('directional' in m for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env={"NELYQOR_ROLE": "lab", "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
