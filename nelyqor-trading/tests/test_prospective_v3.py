"""Prospectif v3 sur données SYNTHÉTIQUES : gel, prédiction = décision complète, rejeu exact, détection d'une
prédiction falsifiée, chaîne de journal."""
import json
from argparse import Namespace

import numpy as np
import pytest

import scripts.prospective_v3 as P3
from backend.research.synthetic import synthetic_universe

H = 3_600_000


@pytest.fixture
def env(tmp_path, monkeypatch):
    mk = ["BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR", "ADA-EUR", "DOT-EUR"]
    raw = synthetic_universe(mk, n=2400, seed=3)
    raw = {m: d.set_axis(np.asarray(d.index, dtype=np.int64) if np.issubdtype(np.asarray(d.index).dtype, np.integer)
                         else (d.index.astype("int64") // 1_000_000)) for m, d in raw.items()}
    monkeypatch.setattr(P3, "FROZEN", tmp_path / "figes.json")
    cands = json.loads((P3.DIR / "candidats_v3.json").read_text())
    src = tmp_path / "c.json"
    src.write_text(json.dumps(cands))
    t_end = max(int(d.index.max()) for d in raw.values())
    P3.freeze(src, now_ms=t_end - 6 * 86_400_000)  # départ ~5 jours avant la fin des données
    return raw, tmp_path


def _run(raw, tmp_path, monkeypatch, upto=None):
    data = raw if upto is None else {m: d[d.index <= upto] for m, d in raw.items()}
    monkeypatch.setattr(P3, "load_raw", lambda store: data)
    return P3.run(Namespace(root=str(tmp_path), journal=str(tmp_path / "j.jsonl")))


def test_predictions_replay_and_tamper(env, monkeypatch, capsys):
    raw, tmp = env
    t_end = max(int(d.index.max()) for d in raw.values())
    o1 = _run(raw, tmp, monkeypatch, upto=t_end - 24 * H)
    assert o1["validite"] == "valide", o1["validite"]
    assert all("statut" not in r for r in o1["resultats"].values()), o1["resultats"]
    rows = [json.loads(x) for x in (tmp / "j.jsonl").read_text().splitlines()]
    assert rows and all(r["type"] == "prediction" for r in rows)
    assert {r["candidat"] for r in rows} == {c for c in o1["resultats"]}
    o2 = _run(raw, tmp, monkeypatch)  # 24 h de plus : le rejeu doit reproduire les décisions enregistrées
    assert o2["validite"] == "valide", o2["rejeu"]
    assert o2["journal"]["chaine_intacte"]
    # falsification d'une décision -> chaîne rompue ET rejeu divergent
    lines = (tmp / "j.jsonl").read_text().splitlines()
    r0 = json.loads(lines[0])
    r0["ordres"] = [{"marche": "BTC-EUR", "sens": "buy", "montant_eur": 1.0, "quantite": None, "type": "market",
                     "prix_limite": None, "duree_validite_bougies": 1, "annulation": ""}]
    lines[0] = json.dumps(r0)
    (tmp / "j.jsonl").write_text("\n".join(lines) + "\n")
    o3 = _run(raw, tmp, monkeypatch)
    assert o3["validite"].startswith("INVALIDE") and o3["rejeu"]["n_divergences"] >= 1


def test_freeze_refuses_second_freeze(env):
    _, tmp = env
    with pytest.raises(SystemExit):
        P3.freeze(tmp / "c.json")
