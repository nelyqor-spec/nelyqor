"""Phase 8 — bilan financier de la simulation et apprentissage en ombre (gains ET pertes, validation sur inédit)."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal as Dec
from pathlib import Path

import pytest

from backend.learning import apprentissage as A
from backend.learning.bilan import Trade, bilan, max_drawdown, round_trips
from backend.research.registry import Registry
from backend.storage.db import Database

DAY = 86_400_000
T0 = 1_770_000_000_000


def fill(db, i, ts, market, side, qty, px, fee, strategy="momentum", mode="simulation", ref=None):
    db.exec("INSERT INTO intents(id,ts,mode,market,side,order_type,price,strategy,status,client_order_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)", (f"i{i}", ts, mode, market, side, "limit" if ref else "market",
                                             str(ref) if ref else None, strategy, "filled", f"c{i}"))
    db.exec("INSERT INTO fills(id,intent_id,ts,market,side,amount,price,fee,fee_currency,fee_eur,taker,mode) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f"f{i}", f"i{i}", ts, market, side, str(qty), str(px), str(fee),
                                                 "EUR", str(fee), 1, mode))


def test_round_trips_fifo_with_fees_and_partial_exits(tmp_path):
    db = Database(tmp_path / "e.db")
    fill(db, 1, T0, "BTC-EUR", "buy", 1.0, 100, 0.25)
    fill(db, 2, T0 + 1, "BTC-EUR", "buy", 1.0, 110, 0.25)
    fill(db, 3, T0 + 2, "BTC-EUR", "sell", 1.5, 120, 0.45)
    tr = round_trips(db)
    assert [round(t.qty, 6) for t in tr] == [1.0, 0.5]
    a, b = tr
    assert a.cost_eur == pytest.approx(100.25) and a.proceeds_eur == pytest.approx(120 - 0.30)
    assert a.net_pnl_eur == pytest.approx(19.45) and a.gross_pnl_eur == pytest.approx(20) and a.win
    assert b.cost_eur == pytest.approx(55 + 0.125) and b.net_pnl_eur == pytest.approx(60 - 0.15 - 55.125)
    assert round_trips(db, "real") == []  # jamais de mélange simulation / réel


def test_bilan_metrics_from_real_journal_only(tmp_path):
    db = Database(tmp_path / "e.db")
    assert bilan(db, Dec(100), Dec("7.19"), T0)["rendement"]["statut"] == "non mesurable"
    for k, eq in enumerate([100, 104, 98, 102]):
        db.exec("INSERT INTO equity_snapshots VALUES(?,?,?,?,?)", (T0 + k * DAY, "simulation", str(eq), "0", "0"))
    db.exec("INSERT INTO candles(market,interval,ts,close) VALUES('BTC-EUR','1h',?,'50000')", (T0,))
    db.exec("INSERT INTO candles(market,interval,ts,close) VALUES('BTC-EUR','1h',?,'55000')", (T0 + 3 * DAY,))
    fill(db, 1, T0, "ETH-EUR", "buy", 1, 50, 0.5, ref=49.9)
    fill(db, 2, T0 + DAY, "ETH-EUR", "sell", 1, 55, 0.5, ref=55.1)
    r = bilan(db, Dec(100), Dec("7.19"), T0 + 30 * DAY)
    rd = r["rendement"]
    assert rd["net_apres_frais"] == pytest.approx(0.02) and rd["brut_avant_frais"] == pytest.approx(0.03)
    assert rd["baisse_maximale"] == pytest.approx(98 / 104 - 1)
    assert rd["cout_hebergement_eur"] == pytest.approx(7.19 * 30 / 30.4375, rel=1e-3)
    assert rd["net_apres_hebergement"] < rd["net_apres_frais"]       # l'hébergement n'est jamais oublié
    assert r["references_passives"]["achat_passif_btc"] == pytest.approx(0.1)
    assert r["frais_cumules_eur"] == pytest.approx(1.0) and r["nombre_fills"] == 2 and r["allers_retours_fermes"] == 1
    assert r["couts_execution"]["cout_execution_eur"] == pytest.approx(0.1 + 0.1)
    assert r["fiabilite_predictions"]["statut"] == "non mesurable"   # aucune donnée inventée
    assert r["qualite_decisions"]["momentum"]["taux_reussite"] == 1.0
    assert "FICTIF" in r["avertissement"]


def T(strategy, entry, exit_, pnl, cost=100.0):
    return Trade("BTC-EUR", strategy, entry, exit_, 1.0, cost, cost + pnl, 0.5, pnl + 0.5, pnl)


def test_learning_rewards_winners_penalises_losers_with_shrinkage():
    trades = ([T("gagnante", T0 + i, T0 + i + 1, 3.0) for i in range(40)]
              + [T("perdante", T0 + i, T0 + i + 1, -3.0) for i in range(40)]
              + [T("rare", T0, T0 + 1, 5.0)])
    p = A.learn(trades, T0 + DAY)
    assert p.multipliers["gagnante"] > 1.4 and p.multipliers["perdante"] < 0.6   # apprend dans les DEUX sens
    assert 1.0 < p.multipliers["rare"] < 1.25                                     # 1 seul gain : effet limité
    assert all(A.M_MIN <= m <= A.M_MAX for m in p.multipliers.values())
    # une transaction fermée APRÈS la date d'apprentissage n'est pas utilisée
    assert A.learn(trades + [T("futur", T0, T0 + 2 * DAY, 9.0)], T0 + DAY).multipliers.get("futur") is None


def test_shadow_evaluated_only_on_unseen_trades_and_promotion_needs_evidence():
    past = [T("gagnante", T0 + i, T0 + i + 1, 3.0) for i in range(30)] + \
           [T("perdante", T0 + i, T0 + i + 1, -3.0) for i in range(30)]
    pol = A.learn(past, T0 + DAY)
    ev0 = A.evaluate_shadow(pol, past, 100.0)
    assert ev0["allers_retours_inedits"] == 0 and not ev0["promotion"]["promu"]     # le passé ne compte pas
    future = []
    for d in range(1, 16):  # 15 jours inédits : les tendances persistent
        future += [T("gagnante", T0 + d * DAY + 10, T0 + d * DAY + 20, 2.0),
                   T("perdante", T0 + d * DAY + 10, T0 + d * DAY + 20, -2.0)]
    ev = A.evaluate_shadow(pol, past + future, 100.0)
    assert ev["allers_retours_inedits"] == 30 and ev["promotion"]["promu"]
    assert ev["resultat_ombre_eur"] > ev["resultat_reel_eur"]
    # tendances inversées sur l'inédit : la proposition n'est PAS promue
    flipped = [T(t.strategy, t.entry_ts, t.exit_ts, -t.net_pnl_eur) for t in future]
    assert not A.evaluate_shadow(pol, past + flipped, 100.0)["promotion"]["promu"]


def test_registry_valid_only_with_evidence_and_never_active(tmp_path):
    reg = Registry(Database(tmp_path / "lab.db"), lambda: T0, role="lab")
    pol = A.Policy(T0, {"s": 1.2}, 10, 10.0, 0.02)
    bad = {"promotion": {"promu": False}}
    assert A.register_and_judge(reg, pol, bad) == "simulation"
    good = {"promotion": {"promu": True}}
    assert A.register_and_judge(reg, pol, good) == "valide"
    with pytest.raises(PermissionError):
        reg.set_status("apprentissage_dimensionnement", pol.sha256[:16], "actif")  # le laboratoire n'active jamais


def test_script_reads_engine_db_readonly_and_policies_are_append_only(tmp_path):
    import subprocess
    import sys
    eng = tmp_path / "engine.db"
    db = Database(eng)
    fill(db, 1, T0, "BTC-EUR", "buy", 1, 100, 0.25)
    fill(db, 2, T0 + 1, "BTC-EUR", "sell", 1, 103, 0.25)
    db.exec("INSERT INTO equity_snapshots VALUES(?,?,?,?,?)", (T0, "simulation", "100", "100", "0"))
    db.close()
    before = hashlib.sha256(eng.read_bytes()).hexdigest()
    props = tmp_path / "lab" / "apprentissage.json"
    cmd = [sys.executable, "scripts/bilan_simulation.py", "--engine-db", str(eng), "--lab-db", str(tmp_path / "lab.db"),
           "--propositions", str(props), "--out", str(tmp_path / "bilan.json")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=120)
    assert r.returncode == 0, r.stderr
    assert hashlib.sha256(eng.read_bytes()).hexdigest() == before      # base du moteur jamais modifiée
    rep = json.loads((tmp_path / "bilan.json").read_text())
    assert rep["bilan"]["allers_retours_fermes"] == 1
    assert len(json.loads(props.read_text())) == 1
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=120)
    assert r.returncode == 0 and len(json.loads(props.read_text())) == 1  # pas de nouvelle proposition avant 7 j
    data = json.loads(props.read_text())
    data[0]["multipliers"] = {"momentum": 1.5}                              # proposition altérée après coup
    props.write_text(json.dumps(data))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], timeout=120)
    assert r.returncode != 0 and "altérée" in (r.stderr + r.stdout)


def test_max_drawdown_basic():
    assert max_drawdown([100, 120, 90, 130]) == pytest.approx(90 / 120 - 1)
    assert max_drawdown([100]) is None


async def test_engine_publishes_bilan_hourly_and_never_when_disk_critical(tmp_path):
    from backend.core import disk
    from backend.core.clock import FakeClock
    from tests.test_phase5_separation import make
    clock = FakeClock(T0)
    eng, db = make(tmp_path, clock)
    await eng.step()
    b = db.kv_get("bilan_simulation")
    assert b and b["bilan"]["mode"] == "simulation" and b["bilan"]["fictif"] is True
    assert len(db.kv_get("apprentissage_propositions")) == 1          # 1re proposition figée, en ombre
    first = b["genere_ms"]
    clock.advance(60_000)
    eng.snapshot(force=True)
    assert db.kv_get("bilan_simulation")["genere_ms"] == first        # pas plus d'une fois par heure
    eng.disk_level = disk.CRITIQUE
    clock.advance(3_600_001)
    eng.update_bilan()
    assert db.kv_get("bilan_simulation")["genere_ms"] == first        # disque critique : suspendu
    eng.disk_level = disk.OK
    eng.update_bilan()
    assert db.kv_get("bilan_simulation")["genere_ms"] > first
    # une proposition altérée n'est jamais réutilisée : le bilan l'ignore (journalisé), la simulation continue
    props = db.kv_get("apprentissage_propositions")
    props[0]["multipliers"] = {"x": 1.5}
    db.kv_set("apprentissage_propositions", props)
    clock.advance(3_600_001)
    eng.update_bilan()
    assert db.kv_get("apprentissage_propositions")[0]["multipliers"] == {"x": 1.5}  # rien n'est réécrit


def test_api_bilan_requires_session_and_reports_fictitious(tmp_path):
    src = open("backend/api/app.py").read()
    i = src.index('@app.get("/api/bilan")')
    assert "Depends(require_session)" in src[i:i + 200] and '"capital": "FICTIF"' in src[i:i + 900]
