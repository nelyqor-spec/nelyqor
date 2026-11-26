"""Génération E1 sur données SYNTHÉTIQUES : gel, prédictions complètes, rejeu exact, falsification détectée,
filtre événementiel (abstention seulement, causal)."""
import json
from argparse import Namespace

import numpy as np
import pandas as pd
import pytest

import scripts.prospective_e1 as E1
from backend.events.hygiene import group
from backend.events.model import Event

H = 3_600_000


def series(n, seed, p0):
    rng = np.random.default_rng(seed)
    t0 = 1_790_000_000_000 // H * H
    c = p0 * np.exp(np.cumsum(np.r_[0, rng.normal(0.0004, 0.01, n - 1)]))
    o = np.r_[c[0], c[:-1]]
    hi, lo = np.maximum(o, c) * 1.003, np.minimum(o, c) * 0.997
    idx = pd.Index(t0 + np.arange(n) * H, dtype=np.int64)
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 1e3}, index=idx)


@pytest.fixture
def env(tmp_path, monkeypatch):
    raw = {"BTC-EUR": series(2400, 1, 60000.0), "ETH-EUR": series(2400, 2, 3000.0)}
    monkeypatch.setattr(E1, "FROZEN", tmp_path / "figes.json")
    src = tmp_path / "c.json"
    src.write_text((E1.DIR / "candidats_e1.json").read_text())
    t_end = max(int(d.index.max()) for d in raw.values())
    E1.freeze(src, now_ms=t_end - 8 * 86_400_000)
    return raw, tmp_path


def _run(raw, tmp, monkeypatch, upto=None, events_dir=None):
    data = raw if upto is None else {m: d[d.index <= upto] for m, d in raw.items()}
    monkeypatch.setattr(E1.P3, "load_raw", lambda store: data)
    return E1.run(Namespace(root=str(tmp), journal=str(tmp / "j.jsonl"), events_dir=str(events_dir or tmp / "pas_d_evenements")))


def test_predictions_replay_and_tamper(env, monkeypatch):
    raw, tmp = env
    t_end = max(int(d.index.max()) for d in raw.values())
    o1 = _run(raw, tmp, monkeypatch, upto=t_end - 24 * H)
    assert o1["validite"] == "valide", o1
    assert all("statut" not in r for r in o1["resultats"].values()), o1["resultats"]
    assert o1["resultats"]["e1_cash"]["trades"] == 0
    o2 = _run(raw, tmp, monkeypatch)
    assert o2["validite"] == "valide", o2["rejeu"]
    rows = [json.loads(x) for x in (tmp / "j.jsonl").read_text().splitlines()]
    assert {r["candidat"] for r in rows} == {"e1_ls", "e1_lc", "e1_ls_evt", "e1_lc_evt", "e1_cash"}
    assert all({"positions", "entrees_en_attente", "sorties_en_attente", "equite"} <= set(r["decision"]) for r in rows)
    lines = (tmp / "j.jsonl").read_text().splitlines()
    r0 = json.loads(lines[0])
    r0["decision"]["equite"] = 999.0
    lines[0] = json.dumps(r0)
    (tmp / "j.jsonl").write_text("\n".join(lines) + "\n")
    o3 = _run(raw, tmp, monkeypatch)
    assert o3["validite"].startswith("INVALIDE") and o3["rejeu"]["n_divergences"] >= 1


def _ev(i, pub, received, cats=("piratage",), assets=("btc",)):
    return Event(id=f"e{i}", source="test", source_kind="media", title=f"exchange hacked bitcoin stolen {i}",
                 norm_title="exchange hacked bitcoin stolen", url=f"https://{pub}/a{i}", publisher=pub,
                 published_ms=received, received_ms=received, categories=list(cats), assets=list(assets), cluster="c1")


def test_blocking_requires_corroboration_window_and_asset():
    t = 1_800_000_000_000
    one = group([_ev(1, "a.com", t)])
    assert E1.blocking_clusters(one, "BTC-EUR", t + 1000) == []  # un seul éditeur : non bloquant
    two = group([_ev(1, "a.com", t), _ev(2, "b.com", t + 60_000)])
    assert E1.blocking_clusters(two, "BTC-EUR", t + 30_000) == []  # 2e éditeur pas encore reçu (causal)
    assert E1.blocking_clusters(two, "BTC-EUR", t + 120_000) == ["c1"]
    assert E1.blocking_clusters(two, "ETH-EUR", t + 120_000) == []  # autre actif
    assert E1.blocking_clusters(two, "BTC-EUR", t + 25 * H) == []  # plus de 24 h
    sysg = group([_ev(1, "a.com", t, ("faillite",), ()), _ev(2, "b.com", t, ("faillite",), ())])
    assert E1.blocking_clusters(sysg, "ETH-EUR", t + 1000) == ["c1"]  # systémique sans actif
    etf = group([_ev(1, "a.com", t, ("etf",)), _ev(2, "b.com", t, ("etf",))])
    assert E1.blocking_clusters(etf, "BTC-EUR", t + 1000) == []  # catégorie non bloquante


def test_event_filter_only_abstains(env, monkeypatch):
    """Avec un événement bloquant permanent sur BTC et ETH, les candidats « evt » ne prennent AUCUNE position."""
    raw, tmp = env
    monkeypatch.setattr(E1, "blocking_clusters", lambda groups, m, t: ["c1"])
    from backend.events.journal import append_observations
    ev_dir = tmp / "events"
    append_observations(ev_dir, [_ev(1, "a.com", 1_800_000_000_000)])
    o = _run(raw, tmp, monkeypatch, events_dir=ev_dir)
    assert o["resultats"]["e1_ls_evt"]["trades"] == 0 and o["resultats"]["e1_lc_evt"]["trades"] == 0
    assert o["validite"] == "valide"
