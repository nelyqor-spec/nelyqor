#!/usr/bin/env python3
"""Bilan financier de la simulation permanente et apprentissage en ombre (phase 8).

Lecture SEULE de la base du moteur : elle est copiée (API de sauvegarde SQLite) avant lecture, jamais ouverte en
écriture. Le registre des propositions d'apprentissage vit dans la base SÉPARÉE du laboratoire (rôle lab : il ne
peut jamais activer une proposition). Aucune clé, aucun ordre, aucune donnée historique de recherche.

  python scripts/bilan_simulation.py --engine-db /engine/engine.db --lab-db /lab/lab.db \\
      --propositions /lab/apprentissage.json --out /lab/reports/bilan_simulation.json

Chaque exécution :
1. calcule le bilan (backend/learning/bilan.py) ;
2. évalue chaque proposition d'apprentissage déjà figée, UNIQUEMENT sur les transactions entrées après elle, et
   l'inscrit au registre (« valide » seulement si le critère pré-enregistré est satisfait) ;
3. fige une nouvelle proposition si la dernière a plus de `--intervalle-jours` (7) : chaque proposition dispose
   ainsi de données qu'elle n'a jamais vues.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

os.environ.setdefault("NELYQOR_ROLE", "lab")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.learning import apprentissage as A  # noqa: E402
from backend.learning.bilan import bilan, round_trips  # noqa: E402
from backend.research.registry import Registry  # noqa: E402
from backend.storage.db import Database  # noqa: E402

DAY = 86_400_000


def copy_readonly(src: Path) -> Path:
    dst = Path(tempfile.mkdtemp(prefix="nelyqor-bilan-")) / "engine.db"
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()
    return dst


def run(engine_db: Path, lab_db: Path, policies_path: Path, initial: Decimal, hosting: Decimal, now_ms: int,
        interval_days: float) -> dict[str, Any]:
    db = Database(copy_readonly(engine_db))
    reg = Registry(Database(lab_db), lambda: now_ms, role="lab")
    trades = round_trips(db, "simulation")
    stored = json.loads(policies_path.read_text()) if policies_path.exists() else []
    try:
        policies, judged = A.cycle(trades, stored, reg, float(initial), now_ms, interval_days)
    except ValueError as e:
        raise SystemExit(f"REFUS : {e} : {policies_path}") from e
    if len(policies) != len(stored):
        policies_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = policies_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(policies, indent=2))
        tmp.replace(policies_path)  # ajout seul : les propositions déjà figées ne sont jamais réécrites
    return {"genere_ms": now_ms, "bilan": bilan(db, initial, hosting, now_ms),
            "apprentissage": {"propositions": judged,
                              "note": "jamais appliqué au compte simulé ; activation = décision du propriétaire"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-db", required=True)
    ap.add_argument("--lab-db", required=True)
    ap.add_argument("--propositions", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--capital", default=os.environ.get("NELYQOR_INITIAL_CAPITAL_EUR", "100"))
    ap.add_argument("--hebergement", default=os.environ.get("NELYQOR_HOSTING_EUR", "7.19"))
    ap.add_argument("--intervalle-jours", type=float, default=7.0)
    a = ap.parse_args()
    if os.environ.get("NELYQOR_REAL_TRADING_ALLOWED", "false").lower() == "true":
        print("REFUS : le bilan ne tourne pas avec le réel permis (rôle laboratoire)", file=sys.stderr)
        return 2
    rep = run(Path(a.engine_db), Path(a.lab_db), Path(a.propositions), Decimal(a.capital), Decimal(a.hebergement),
              int(time.time() * 1000), a.intervalle_jours)
    txt = json.dumps(rep, ensure_ascii=False, indent=2, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
