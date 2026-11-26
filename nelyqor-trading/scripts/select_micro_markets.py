#!/usr/bin/env python3
"""Marchés RÉELLEMENT sélectionnés par les candidats figés (phase 5, priorité 6) — pour concentrer la collecte de
microstructure (carnets + transactions publiques) sur eux, au lieu d'extrapoler six grands marchés à des
centaines de petits. LAB : aucune clé, aucun ordre.

Sources, dans l'ordre : (1) marchés des prédictions et positions du journal prospectif v3 ; (2) marchés traités par
les candidats v3 « rotation » rejoués sur les `--days` derniers jours (une phase par candidat) ; (3) BTC-EUR
(références). Sortie : liste séparée par des virgules (stdout, dernière ligne) et `--out` (JSON détaillé).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import scripts.prospective_v3 as P3  # noqa: E402
from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--journal", default=str(ROOT / "data" / "prospective" / "journal_v3.jsonl"))
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "data" / "micro" / "selection.json"))
    a = ap.parse_args()
    from backend.research.strategies_v4 import prepare
    from backend.research.xsection_v4 import RULE_MS, DataPolicy, classify
    cnt: Counter[str] = Counter()
    src: dict[str, list[str]] = {"journal": [], "rejeu": []}
    jp = Path(a.journal)
    if jp.exists():
        for line in jp.read_text().splitlines():
            r = json.loads(line) if line.strip() else {}
            for o in r.get("ordres", []):
                cnt[o["marche"]] += 3
                src["journal"].append(o["marche"])
            for m in (r.get("etat") or {}).get("positions", {}):
                cnt[m] += 3
    doc = json.loads(P3.FROZEN.read_text()) if P3.FROZEN.exists() else {"candidats": []}
    raw = P3.load_raw(CandleStore(a.root))
    grid, data = prepare(raw, "4h")
    if len(grid):
        C = np.column_stack([data[m]["close"].to_numpy(float) for m in sorted(data)])
        states = classify(C, RULE_MS["4h"], DataPolicy())
        start = max(0, len(grid) - int(a.days * 6))
        for c in doc["candidats"]:
            if c["type"] != "rotation":
                continue
            ph, strat, ec = P3.make_accounts(c, grid, data, start)[0]
            eng, _, _ = P3.run_account(grid, data, strat, ec, start, states)
            for o in eng.orders_log:
                cnt[o["market"]] += 1
                src["rejeu"].append(o["market"])
    cnt["BTC-EUR"] += 1000  # référence de tous les critères
    sel = [m for m, _ in cnt.most_common(a.max)]
    out = {"marches": sel, "poids": dict(cnt.most_common(a.max)), "jours_rejoues": a.days,
           "n_ordres_rejeu": len(src["rejeu"]), "n_ordres_journal": len(src["journal"])}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False))
    print(",".join(sel))


if __name__ == "__main__":
    main()
