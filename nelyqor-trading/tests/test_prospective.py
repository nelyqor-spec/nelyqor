"""Évaluation prospective : seules les bougies >= CUTOFF comptent ; aucune position avant (données synthétiques)."""
import json
import os

os.environ.setdefault("NELYQOR_ROLE", "lab")

from backend.data.store import REAL, CandleStore
from backend.research.synthetic import synthetic_universe
from scripts import prospective as P


def _store(tmp_path, after: int) -> CandleStore:
    d = synthetic_universe([f"S{i}-EUR" for i in range(8)] + ["BTC-EUR", "ETH-EUR"], 2000, seed=3)
    k = next(iter(d))
    shift = P.CUTOFF_MS - int(d[k].index[len(d[k]) - after] if after else d[k].index[-1] + 3_600_000)
    st = CandleStore(tmp_path)
    for m, df in d.items():
        df = df.copy()
        df.index = df.index + shift
        st.write(df.rename_axis("ts").reset_index(), REAL, "bitvavo", m, "1h", {"test": "synthetique"})
    return st


def test_candidates_file_evaluates_only_after_cutoff(tmp_path):
    st = _store(tmp_path, after=400)
    cands = json.loads((P.ROOT / "reports" / "prospective" / "candidats.json").read_text())
    for c in cands:
        if c["type"] == "strat":
            c["markets"] = ["BTC-EUR", "ETH-EUR"]
        r = P.eval_candidate(c, st, "1h")
        assert r["statut"] == "ok", (c["id"], r)
        per_bar = {"4h": 4, "8h": 8}[c["interval"]]
        assert r["bougies"] <= 400 // per_bar + 1


def test_no_prospective_candle_yet(tmp_path):
    st = _store(tmp_path, after=0)
    c = json.loads((P.ROOT / "reports" / "prospective" / "candidats.json").read_text())[0]
    assert P.eval_candidate(c, st, "1h")["bougies"] == 0
