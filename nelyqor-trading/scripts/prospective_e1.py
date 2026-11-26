#!/usr/bin/env python3
"""Génération expérimentale E1 (phase 6) : directionnel LONG/SHORT/CASH et filtre événementiel, en SIMULATION
seulement (le Short n'est pas accessible par l'API Bitvavo : module non déployable). LAB : aucune clé, aucun ordre.

Règles pré-enregistrées : `reports/prospective/e1/regle_e1.md`. Ce script réutilise le journal chaîné et les
utilitaires du prospectif v3 sans les modifier.

  python scripts/prospective_e1.py freeze --candidates reports/prospective/e1/candidats_e1.json
  python scripts/prospective_e1.py run [--events-dir data/events]
JSON entre PROSPECTIF_E1_JSON_DEBUT / PROSPECTIF_E1_JSON_FIN.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import scripts.prospective_v3 as P3  # noqa: E402
from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402

DIR = ROOT / "reports" / "prospective" / "e1"
FROZEN = DIR / "candidats_figes_e1.json"
CODE_FILES = ["backend/research/directional.py", "backend/research/directional_rules.py", "backend/research/xsection_v4.py",
              "backend/research/strategies_v4.py", "backend/events/hygiene.py", "backend/events/lexicon.py",
              "backend/events/model.py", "backend/events/journal.py", "scripts/prospective_e1.py"]
TAG = "PROSPECTIF_E1"
MARKETS = ["BTC-EUR", "ETH-EUR"]
STEP = 14_400_000  # 4 h
BLOCK_CATS = {"piratage", "faillite", "radiation", "reglementation", "stablecoin"}
SYSTEMIC = {"faillite", "stablecoin"}
WINDOW_MS = 86_400_000
ASSET = {"BTC-EUR": "btc", "ETH-EUR": "eth"}


def code_fingerprint() -> dict[str, str]:
    return {f: P3.sha((ROOT / f).read_bytes()) for f in CODE_FILES}


def freeze(src: Path, now_ms: int | None = None) -> dict[str, Any]:
    if FROZEN.exists():
        sys.exit("Génération E1 déjà figée : un nouveau gel exige une nouvelle génération.")
    now_ms = now_ms or int(time.time() * 1000)
    cands = json.loads(src.read_text())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()  # noqa: S603,S607
    cutoff = P3.next_midnight_ms(now_ms)
    doc = {"fige_le_utc": datetime.fromtimestamp(now_ms / 1000, UTC).isoformat(timespec="seconds"), "commit_parent": commit,
           "cutoff_ms": cutoff, "cutoff_utc": datetime.fromtimestamp(cutoff / 1000, UTC).isoformat(timespec="seconds"),
           "empreinte_code": code_fingerprint(), "empreintes_candidats": {c["id"]: P3.cand_fingerprint(c) for c in cands},
           "regles": "reports/prospective/e1/regle_e1.md (sha256 " + P3.sha((DIR / "regle_e1.md").read_bytes()) + ")",
           "statut": "EXPÉRIMENTAL, NON DÉPLOYABLE (Short indisponible par API)", "candidats": cands}
    FROZEN.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print("gel E1 écrit :", FROZEN, "— départ prospectif", doc["cutoff_utc"])
    return doc


# ------------------------------------------------------------------------------------------- filtre événementiel
def blocking_clusters(groups: dict[str, list[Any]], market: str, as_of_ms: int) -> list[str]:
    """Groupes bloquants pour `market` À L'INSTANT `as_of_ms` (seules les observations déjà reçues comptent)."""
    from backend.events.hygiene import assess
    out = []
    asset = ASSET[market]
    for cid, evs in groups.items():
        a = assess(evs, as_of_ms)
        if a is None or a.n_publishers < 2 or a.recycled or a.rumor or not (0 <= as_of_ms - a.first_received_ms < WINDOW_MS):
            continue
        seen = [e for e in evs if e.received_ms <= as_of_ms]
        cats = {c for e in seen for c in e.categories}
        assets = {x for e in seen for x in e.assets}
        if not cats & BLOCK_CATS:
            continue
        if asset in assets or (not assets and cats & SYSTEMIC):
            out.append(cid)
    return sorted(out)


# ------------------------------------------------------------------------------------------- simulation
def run_candidate(c: dict[str, Any], grid: np.ndarray, data: dict[str, Any], start: int, groups: dict[str, list[Any]] | None,
                  recorded_blocks: dict[int, dict[str, list[str]]]) -> tuple[Any, dict[int, dict[str, Any]], np.ndarray, int]:
    from backend.research.directional import CASH, DirConfig, DirEngine, always_cash, long_only
    from backend.research.directional_rules import trend_rule
    base = always_cash if c["type"] == "cash" else trend_rule(fast=10, slow=40, band_atr=0.5)
    if c["type"] != "cash" and not c.get("short", False):
        base = long_only(base)
    blocks_at: dict[int, dict[str, list[str]]] = {}
    altered = {"n": 0}

    def rule(i: int, view: Any) -> dict[str, str]:
        if i < start:
            return {}
        sig = dict(base(i, view))
        if c.get("evt"):
            ts = int(grid[i])
            bl = recorded_blocks.get(ts)
            if bl is None:
                close_ms = ts + STEP
                bl = {m: blocking_clusters(groups, m, close_ms) for m in MARKETS} if groups is not None else {m: [] for m in MARKETS}
            blocks_at[ts] = bl
            for m in MARKETS:
                if bl.get(m) and sig.get(m, CASH) != CASH:
                    sig[m] = CASH
                    altered["n"] += 1
        return sig

    eng = DirEngine(grid, data, DirConfig())
    eq = np.full(len(grid), np.nan)
    decisions: dict[int, dict[str, Any]] = {}
    for i in range(len(grid)):
        eng.step_bar(i, rule)
        eq[i] = eng.equity()
        if i >= start:
            ts = int(grid[i])
            decisions[ts] = canon(eng, blocks_at.get(ts, {}))
    return eng, decisions, eq, altered["n"]


def _r(x: float | None, nd: int = 8) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def canon(eng: Any, blocks: dict[str, list[str]]) -> dict[str, Any]:
    """Décision complète, forme canonique (comparaison exacte au rejeu)."""
    pos = {m: {"sens": "long" if p.side > 0 else "short", "quantite": _r(p.qty), "stop": _r(p.stop, 6), "tp1": _r(p.tp1, 6),
               "tp2": _r(p.tp2, 6), "tp1_fait": bool(p.tp1_done)} for m, p in sorted(eng.pos.items())}
    ent = [{"marche": pe.market, "sens": "long" if pe.side > 0 else "short", "montant_eur": _r(pe.eur, 4)}
           for pe in sorted(eng.pending_entry, key=lambda x: (x.market, x.side))]
    return {"positions": pos, "entrees_en_attente": ent, "sorties_en_attente": sorted(eng.pending_exit),
            "equite": _r(eng.equity(), 4), "groupes_bloquants": {m: v for m, v in sorted(blocks.items()) if v}}


def run(a: argparse.Namespace) -> dict[str, Any]:
    from backend.research.strategies_v4 import prepare
    if not FROZEN.exists():
        sys.exit("Aucune génération E1 figée.")
    doc = json.loads(FROZEN.read_text())
    changed = [f for f, h in doc["empreinte_code"].items() if code_fingerprint().get(f) != h]
    cand_changed = [c["id"] for c in doc["candidats"] if doc["empreintes_candidats"].get(c["id"]) != P3.cand_fingerprint(c)]
    J = P3.Journal(Path(a.journal))
    chain_ok = J.verify()
    cutoff = int(doc["cutoff_ms"])
    raw = {m: d for m, d in P3.load_raw(CandleStore(a.root)).items() if m in MARKETS}
    grid, data = prepare(raw, "4h") if raw else (np.array([], dtype=np.int64), {})
    groups = None
    ev_dir = Path(a.events_dir)
    if ev_dir.exists():
        from backend.events.hygiene import group
        from backend.events.journal import load_observations
        groups = group(load_observations(ev_dir, since_ms=cutoff - 2 * WINDOW_MS))
    out: dict[str, Any] = {"execution_utc": datetime.now(UTC).isoformat(timespec="seconds"), "cutoff": doc["cutoff_utc"],
                           "statut": doc["statut"], "evenements_disponibles": groups is not None,
                           "groupes_evenementiels": len(groups or {}), "resultats": {}}
    bad: list[str] = []
    run_ts = int(time.time() * 1000)
    for c in doc["candidats"]:
        try:
            if not len(grid) or set(data) != set(MARKETS):
                raise ValueError(f"données incomplètes : {sorted(data)}")
            start = int(np.searchsorted(grid, cutoff))
            rows = [r for r in J.rows if r["type"] == "prediction" and r["candidat"] == c["id"]]
            recorded = {int(r["bougie_ts"]): r["decision"].get("groupes_bloquants", {}) for r in rows}
            recorded = {t: {m: recorded[t].get(m, []) for m in MARKETS} for t in recorded}
            eng, decisions, eq, altered = run_candidate(c, grid, data, start, groups, recorded)
            for r in rows:
                t = int(r["bougie_ts"])
                if t in decisions and decisions[t] != r["decision"]:
                    bad.append(f"{c['id']}@{t}")
                elif t not in decisions and int(grid[0]) <= t <= int(grid[-1]):
                    bad.append(f"{c['id']}@{t} (bougie absente au rejeu)")
            last = int(grid[-1])
            if last >= cutoff and last not in recorded:
                J.append({"type": "prediction", "candidat": c["id"], "bougie_ts": last, "execution_ts": run_ts,
                          "decision": decisions[last], "empreinte_candidat": P3.cand_fingerprint(c)})
            seg = eq[start:] if start < len(eq) else np.array([])
            out["resultats"][c["id"]] = {
                "bougies_prospectives": int(len(seg)), "equite": float(seg[-1]) if len(seg) else 100.0,
                "rendement": float(seg[-1] / 100 - 1) if len(seg) else 0.0,
                "dd": float((seg / np.maximum.accumulate(seg) - 1).min()) if len(seg) else 0.0,
                "trades": len(eng.trades), "decisions_modifiees_par_evenements": altered,
                "levier_effectif_max": float(max(eng.effective_gross, default=0.0)),
                "predictions": len(rows) + (1 if last >= cutoff and last not in recorded else 0)}
        except Exception as e:  # noqa: BLE001
            out["resultats"][c["id"]] = {"statut": f"erreur {type(e).__name__}: {str(e)[:200]}"}
    if len(grid) and "BTC-EUR" in data:
        cl = data["BTC-EUR"]["close"]
        cl = cl[cl.index >= cutoff].dropna()
        out["btc_achat_passif_depuis_depart"] = float(cl.iloc[-1] / cl.iloc[0] * (1 - 0.0025) - 1) if len(cl) > 1 else None
    out["rejeu"] = {"divergences": bad[:50], "n_divergences": len(bad)}
    ok = not (changed or cand_changed or not chain_ok or bad)
    out["validite"] = "valide" if ok else (f"INVALIDE (code : {changed} ; candidats : {cand_changed} ; chaîne : {chain_ok} ; "
                                           f"rejeu : {len(bad)} divergence(s))")
    out["journal"] = {"lignes": len(J.rows), "chaine_intacte": J.verify(), "tete": J.rows[-1]["hash"] if J.rows else None}
    print(f"{TAG}_JSON_DEBUT")
    print(json.dumps(out, default=str, ensure_ascii=False))
    print(f"{TAG}_JSON_FIN")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--candidates", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    r.add_argument("--journal", default=str(ROOT / "data" / "prospective" / "journal_e1.jsonl"))
    r.add_argument("--events-dir", default=str(ROOT / "data" / "events"))
    a = ap.parse_args()
    if a.cmd == "freeze":
        freeze(Path(a.candidates))
    else:
        run(a)


if __name__ == "__main__":
    main()
