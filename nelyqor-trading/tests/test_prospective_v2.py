"""Prospectif v2 : prédictions horodatées avant observation, journal chaîné, notation à horizon,
rétrospectif séparé, aucune reconstitution de prédiction manquée ; la v1 figée n'est pas touchée."""
import argparse
import json
import os

os.environ.setdefault("NELYQOR_ROLE", "lab")

import pytest

from backend.data.store import REAL, CandleStore
from backend.research.synthetic import synthetic_universe
from scripts import prospective_v2 as P2

CANDS = [{"id": "t_xs4h", "type": "xs2", "quote": "EUR", "interval": "4h", "variant": "base",
          "overrides": {"min_liquidity_quote": 0, "min_age_bars": 20}, "exposure": "aucun"}]


def write_store(root, upto_after_cutoff: int):
    d = synthetic_universe([f"S{i}-EUR" for i in range(8)] + ["BTC-EUR"], 1600, seed=9)
    k = next(iter(d))
    shift = 1_900_000_000_000 - int(d[k].index[len(d[k]) - 200])  # 200 h après le « départ »
    st = CandleStore(root)
    for m, df in d.items():
        df = df.copy()
        df.index = df.index + shift
        df = df.iloc[: len(df) - 200 + upto_after_cutoff]
        st.write(df.rename_axis("ts").reset_index(), REAL, "bitvavo", m, "1h", {"test": "synthetique"})
    return 1_900_000_000_000


@pytest.fixture()
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(P2, "FROZEN", tmp_path / "f.json")
    src = tmp_path / "c.json"
    src.write_text(json.dumps(CANDS))
    return tmp_path, src


def test_freeze_sets_future_start_and_refuses_refreeze(frozen):
    tmp, src = frozen
    doc = P2.freeze(src, now_ms=1_899_990_000_000)
    assert doc["cutoff_ms"] > 1_899_990_000_000 and doc["cutoff_ms"] % P2.DAY == 0
    with pytest.raises(SystemExit):
        P2.freeze(src)


def test_predictions_chain_scoring_and_no_backfill(frozen):
    tmp, src = frozen
    P2.freeze(src, now_ms=1_899_990_000_000)
    doc = json.loads(P2.FROZEN.read_text())
    doc["cutoff_ms"] = write_store(tmp / "m", 24)  # départ prospectif aligné sur les données de test
    P2.FROZEN.write_text(json.dumps(doc))
    a = argparse.Namespace(root=str(tmp / "m"), journal=str(tmp / "j.jsonl"))
    out1 = P2.run(a)
    J = P2.Journal(tmp / "j.jsonl")
    assert J.verify() and sum(r["type"] == "prediction" for r in J.rows) == 1
    # exécution manquée pendant 3 jours puis rattrapage : UNE prédiction (la courante), pas de reconstitution
    write_store(tmp / "m", 24 * 4 + 1)
    out2 = P2.run(a)
    J = P2.Journal(tmp / "j.jsonl")
    preds = [r for r in J.rows if r["type"] == "prediction"]
    assert len(preds) == 2 and preds[1]["execution_ts"] >= preds[0]["execution_ts"]
    assert any(r["type"] == "score" for r in J.rows)  # la 1re prédiction est notée à horizon (1 jour)
    res = out2["resultats"]["t_xs4h"]
    assert res["predictions"]["predictions_manquees"] >= 2  # créneaux manqués comptés, jamais remplis
    assert res["retrospectif"]["bougies"] > 0  # le rétrospectif, lui, couvre toute la période
    assert out1["validite"] == "valide" and out2["journal"]["chaine_intacte"]
    # falsification d'une ligne -> chaîne cassée -> évaluation INVALIDE
    lines = (tmp / "j.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    rec["prediction"]["cibles"] = ["S1-EUR"]
    lines[0] = json.dumps(rec)
    (tmp / "j.jsonl").write_text("\n".join(lines) + "\n")
    assert "INVALIDE" in P2.run(a)["validite"]


def test_v1_freeze_untouched():
    from scripts import prospective as P1
    doc = json.loads(P1.FROZEN.read_text())
    assert doc["fige_le_utc"] == "2026-11-25T20:07:04+00:00" and len(doc["candidats"]) == 5
    assert P1.code_fingerprint() == doc["empreinte_code"]
