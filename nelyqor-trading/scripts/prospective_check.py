#!/usr/bin/env python3
"""Contrôle des évaluations prospectives (CI) : fait ÉCHOUER le job — donc déclenche l'alerte — si une évaluation
est invalide, si un candidat est en erreur, si une chaîne de journal est rompue, si un journal d'archive a été
réécrit (ajout seul exigé) ou si le journal a disparu alors que le départ prospectif est passé.

  python scripts/prospective_check.py --v1 v1.log --v2 v2.log [--archive DIR --journal-dir data/prospective]
Écrit un résumé lisible (GITHUB_STEP_SUMMARY si défini) et `resultats.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

CUTOFF_MS = 1_795_651_200_000  # départ prospectif commun v1/v2 (26/11/2026 00:00 UTC)


def extract(path: str, tag: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    cur: list[str] | None = None
    for line in p.read_text(errors="replace").splitlines():
        if f"{tag}_JSON_DEBUT" in line:
            cur = []
        elif f"{tag}_JSON_FIN" in line and cur is not None:
            try:
                return json.loads(" ".join(cur))  # type: ignore[no-any-return]
            except ValueError:
                return None
        elif cur is not None:
            cur.append(line)
    return None


def problems(v1: dict[str, Any] | None, v2: dict[str, Any] | None) -> list[str]:
    out = []
    for name, doc in (("v1", v1), ("v2", v2)):
        if doc is None:
            out.append(f"{name} : aucune sortie d'évaluation lisible")
            continue
        if not str(doc.get("validite", "")).startswith("valide"):
            out.append(f"{name} : validité = {doc.get('validite')}")
        for cid, res in (doc.get("resultats") or {}).items():
            st = str(res.get("statut", "ok"))
            if st.startswith("erreur"):
                out.append(f"{name}/{cid} : {st}")
    if v2 is not None and not (v2.get("journal") or {}).get("chaine_intacte", False):
        out.append("v2 : chaîne du journal rompue")
    return out


def append_only(old: Path, new: Path) -> str | None:
    if not old.exists():
        return None
    if not new.exists():
        return f"{new.name} absent alors que l'archive en contient une version"
    a, b = old.read_bytes(), new.read_bytes()
    return None if b.startswith(a) else f"{new.name} : l'archive n'est pas un préfixe du nouveau journal (réécriture)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", required=True)
    ap.add_argument("--v2", required=True)
    ap.add_argument("--v3", default="")
    ap.add_argument("--last-ok", type=int, default=-1, help="horodatage (s) de la dernière réussite connue avant ce run ; 0 = aucun")
    ap.add_argument("--cutoff-ms", type=int, default=CUTOFF_MS)
    ap.add_argument("--archive", default="")
    ap.add_argument("--journal-dir", default="data/prospective")
    ap.add_argument("--out", default="resultats.json")
    a = ap.parse_args()
    v1, v2 = extract(a.v1, "PROSPECTIF"), extract(a.v2, "PROSPECTIF_V2")
    pb = problems(v1, v2)
    v3 = extract(a.v3, "PROSPECTIF_V3") if a.v3 and Path(a.v3).exists() else None
    if v3 is not None:
        pb += [p.replace("v1", "v3", 1) for p in problems(v3, None) if p.startswith("v1")]
        if not (v3.get("journal") or {}).get("chaine_intacte", False):
            pb.append("v3 : chaîne du journal rompue")
    jd = Path(a.journal_dir)
    now = int(time.time() * 1000)
    if a.last_ok == 0 and now > a.cutoff_ms + 86_400_000:
        pb.append("aucun état antérieur (ni archive, ni cache) plus de 24 h après le départ prospectif : journal perdu "
                  "ou non importé — relancer avec seed_run_id")
    for f in ("journal.jsonl", "journal_v2.jsonl", "journal_v3.jsonl"):
        if a.archive:
            why = append_only(Path(a.archive) / f, jd / f)
            if why:
                pb.append(why)
    summary = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "problemes": pb,
               "v1": v1, "v2": v2, "v3": v3}
    Path(a.out).write_text(json.dumps(summary, ensure_ascii=False, default=str, indent=1))
    lines = ["## Évaluation prospective", "", f"- v1 : {v1.get('validite') if v1 else 'absente'}",
             f"- v2 : {v2.get('validite') if v2 else 'absente'} ; journal {((v2 or {}).get('journal') or {})}", ""]
    lines += [f"- ❌ {p}" for p in pb] or ["- ✅ aucun problème détecté"]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(text + "\n")
    sys.exit(1 if pb else 0)


if __name__ == "__main__":
    main()
