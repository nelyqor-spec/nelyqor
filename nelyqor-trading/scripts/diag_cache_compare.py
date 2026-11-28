"""Diagnostic d'INTÉGRITÉ des données (phase 8) : compare deux caches de bougies 1 h Bitvavo, bougie par bougie.

Contexte : à l'import du journal sur `main` (run 46134632301), le rejeu exact de v3 a divergé (6 décisions) alors
que, sur la branche de travail, il reproduisait tout. Les règles figées n'ont pas changé : seules les DONNÉES
diffèrent (cache de la branche, accumulé incrémentalement depuis le 25/11, contre cache neuf de `main`). Ce script
mesure où : marchés présents d'un seul côté, début / fin d'historique, bougies absentes d'un côté (trous non comblés),
bougies présentes des deux côtés mais différentes (révisions par l'échange).

Lecture seule, aucune recherche, aucune stratégie exécutée. Sortie : JSON (données de marché publiques seulement).
Usage : python scripts/diag_cache_compare.py --a CACHE_BRANCHE --b CACHE_MAIN --out diag.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.data.store import REAL, CandleStore  # noqa: E402

FOCUS = ("BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR", "ADA-EUR")
PROSPECTIVE_FROM = 1_795_737_600_000 - 30 * 86_400_000  # 30 jours avant le départ v3 (27/11/2026 00:00 UTC)


def load(root: Path) -> dict[str, pd.DataFrame]:
    st = CandleStore(root)
    out = {}
    for m in st.markets():
        df = st.read(REAL, "bitvavo", m, "1h")
        if len(df):
            out[m] = df.set_index("ts").sort_index()
    return out


def compare(a: dict[str, pd.DataFrame], b: dict[str, pd.DataFrame]) -> dict[str, Any]:
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    per: dict[str, Any] = {}
    tot = {"marches_communs": 0, "marches_avec_ecart": 0, "bougies_absentes_dans_b": 0, "bougies_absentes_dans_a": 0,
           "bougies_differentes": 0, "ecarts_recents_30j": 0}
    for m in sorted(set(a) & set(b)):
        x, y = a[m], b[m]
        lo, hi = max(x.index.min(), y.index.min()), min(x.index.max(), y.index.max())
        xs, ys = x.loc[lo:hi], y.loc[lo:hi]
        miss_b = sorted(set(xs.index) - set(ys.index))
        miss_a = sorted(set(ys.index) - set(xs.index))
        common = xs.index.intersection(ys.index)
        cols = ["open", "high", "low", "close", "volume"]
        diff_mask = (xs.loc[common, cols].astype(float) - ys.loc[common, cols].astype(float)).abs().max(axis=1) > 0
        diffs = list(common[diff_mask.to_numpy()])
        tot["marches_communs"] += 1
        recent = [t for t in miss_a + miss_b + diffs if t >= PROSPECTIVE_FROM]
        tot["ecarts_recents_30j"] += len(recent)
        if miss_a or miss_b or diffs:
            tot["marches_avec_ecart"] += 1
            tot["bougies_absentes_dans_b"] += len(miss_b)
            tot["bougies_absentes_dans_a"] += len(miss_a)
            tot["bougies_differentes"] += len(diffs)
        if m in FOCUS or recent:
            per[m] = {"debut_a": int(x.index.min()), "debut_b": int(y.index.min()), "fin_a": int(x.index.max()),
                      "fin_b": int(y.index.max()), "absentes_dans_b": len(miss_b), "absentes_dans_a": len(miss_a),
                      "differentes": len(diffs), "ecarts_recents_30j": len(recent),
                      "exemples_recents": [int(t) for t in recent[:5]],
                      "exemple_difference": None if not diffs else {
                          "ts": int(diffs[-1]), "a": xs.loc[diffs[-1], cols].astype(float).to_dict(),
                          "b": ys.loc[diffs[-1], cols].astype(float).to_dict()}}
    return {"marches_a": len(a), "marches_b": len(b), "seulement_dans_a": only_a, "seulement_dans_b": only_b,
            "totaux": tot, "par_marche": per}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="cache A (branche : données des prédictions enregistrées)")
    ap.add_argument("--b", required=True, help="cache B (main : téléchargement neuf)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = compare(load(Path(a.a)), load(Path(a.b)))
    txt = json.dumps(res, ensure_ascii=False, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(txt)
    print("DIAG_JSON_DEBUT")
    print(txt[:60000])
    print("DIAG_JSON_FIN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
