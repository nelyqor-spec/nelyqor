#!/usr/bin/env python3
"""Collecte des événements (une requête par source, aucune clé) -> journaux JSONL en ajout seul.

  python scripts/events_collect.py [--data-dir data/events] [--sources a,b] [--time-budget 300]
  python scripts/events_collect.py --replay DOSSIER   # rejoue des réponses enregistrées <source>.txt (hors ligne)

Code de sortie : 0 si au moins une source a répondu, 2 sinon (réseau coupé ou toutes les sources en échec)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.events.collector import FetchResult, collect, http_fetcher  # noqa: E402
from backend.events.sources import SourceSpec, select  # noqa: E402


def replay_fetcher(folder: Path):  # type: ignore[no-untyped-def]
    def fetch(spec: SourceSpec) -> FetchResult:
        p = folder / f"{spec.name}.txt"
        if not p.is_file():
            return FetchResult(None, "", "pas d'enregistrement")
        text = p.read_text(encoding="utf-8", errors="replace")
        return FetchResult(200, text, "", 0, len(text.encode()))
    return fetch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="data/events")
    ap.add_argument("--sources", default="")
    ap.add_argument("--time-budget", type=float, default=300.0)
    ap.add_argument("--pause", type=float, default=0.2, help="pause entre deux sources (s)")
    ap.add_argument("--replay", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    specs = select(a.sources or None)
    fetch = replay_fetcher(Path(a.replay)) if a.replay else http_fetcher()
    res = collect(specs, Path(a.data_dir), fetch, time_budget_s=a.time_budget, pause_s=0 if a.replay else a.pause)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"{res['ok']}/{res['sources']} sources OK, {res['new']} observations nouvelles, {res['duration_s']} s")
        for r in res["rows"]:
            etat = "OK " if r.get("ok") else "ÉCHEC"
            etat = {"ok": "OK   ", "vide": "VIDE ", "echec": "ÉCHEC"}.get(str(r.get("statut")), etat)
            print(f"  {etat} {r['source']:<20} http={r.get('http')} {r.get('duration_ms', 0)} ms "
                  f"items={r.get('n_items', 0)} nouv={r.get('n_new', 0)} groupes={r.get('n_new_clusters', 0)} "
                  f"exact={r.get('n_exact', 0)} quasi={r.get('n_quasi', 0)} rep={r.get('n_repeat', 0)} "
                  f"{r.get('error', '')}")
    return 0 if res["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
