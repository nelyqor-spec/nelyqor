#!/usr/bin/env python3
"""Prospectif v3 (phase 5) — candidats figés évalués sur le moteur v4 VALIDÉ (LAB : aucune clé, aucun ordre).

Règles : `reports/prospective/v3/regle_v3.md` (pré-enregistrées). Résumé :
- chaque candidat est évalué sur TOUTES ses phases de rééquilibrage, chaque phase = un compte simulé de 100 €
  démarrant au départ prospectif (aucune décision avant) ; score du candidat = MOYENNE des comptes ;
- PRÉDICTION = décision complète pour la dernière bougie 4 h clôturée : ordres émis à l'ouverture suivante (marché,
  sens, montant, quantité, type, prix limite, validité, conditions d'annulation) + état du compte ;
- REJEU : à chaque évaluation, le moteur rejoue tout depuis le départ et doit reproduire EXACTEMENT les décisions
  déjà enregistrées ; sinon l'évaluation est INVALIDE ;
- journal JSONL chaîné sha256 (`data/prospective/journal_v3.jsonl`), empreintes du code et des candidats.

  python scripts/prospective_v3.py freeze --candidates reports/prospective/v3/candidats_v3.json
  python scripts/prospective_v3.py run
JSON entre PROSPECTIF_V3_JSON_DEBUT / PROSPECTIF_V3_JSON_FIN.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402

DIR = ROOT / "reports" / "prospective" / "v3"
FROZEN = DIR / "candidats_figes_v3.json"
CODE_FILES = ["backend/research/xsection_v4.py", "backend/research/strategies_v4.py", "backend/research/xsection_v3.py",
              "backend/research/xsection.py", "scripts/research_phase3.py", "scripts/prospective_v3.py"]
DAY = 86_400_000
TAG = "PROSPECTIF_V3"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def code_fingerprint() -> dict[str, str]:
    return {f: sha((ROOT / f).read_bytes()) for f in CODE_FILES}


def cand_fingerprint(c: dict[str, Any]) -> str:
    return sha(json.dumps(c, sort_keys=True, ensure_ascii=False).encode())


def next_midnight_ms(now_ms: int, margin_ms: int = 3_600_000) -> int:
    return int(((now_ms + margin_ms) // DAY + 1) * DAY)


def freeze(src: Path, now_ms: int | None = None) -> dict[str, Any]:
    if FROZEN.exists():
        sys.exit("Candidats v3 déjà figés : un nouveau gel exige une nouvelle génération et un nouveau départ.")
    now_ms = now_ms or int(time.time() * 1000)
    cands = json.loads(src.read_text())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()  # noqa: S603,S607
    cutoff = next_midnight_ms(now_ms)
    doc = {"fige_le_utc": datetime.fromtimestamp(now_ms / 1000, UTC).isoformat(timespec="seconds"),
           "commit_parent": commit, "cutoff_ms": cutoff,
           "cutoff_utc": datetime.fromtimestamp(cutoff / 1000, UTC).isoformat(timespec="seconds"),
           "empreinte_code": code_fingerprint(),
           "empreintes_candidats": {c["id"]: cand_fingerprint(c) for c in cands},
           "regles": "reports/prospective/v3/regle_v3.md (sha256 " + sha((DIR / "regle_v3.md").read_bytes()) + ")",
           "candidats": cands}
    FROZEN.parent.mkdir(parents=True, exist_ok=True)
    FROZEN.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print("gel v3 écrit :", FROZEN, "— départ prospectif", doc["cutoff_utc"])
    return doc


class Journal:
    """Journal JSONL chaîné : chaque ligne contient le sha256 de la précédente."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []

    def verify(self) -> bool:
        prev = "0" * 64
        for r in self.rows:
            body = {k: v for k, v in r.items() if k != "hash"}
            if r.get("prev") != prev or r.get("hash") != sha(json.dumps(body, sort_keys=True, default=str).encode()):
                return False
            prev = r["hash"]
        return True

    def append(self, rec: dict[str, Any]) -> dict[str, Any]:
        rec = {**rec, "prev": self.rows[-1]["hash"] if self.rows else "0" * 64}
        rec["hash"] = sha(json.dumps(rec, sort_keys=True, default=str).encode())
        self.rows.append(rec)
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
        return rec


def load_raw(store: CandleStore, quote: str = "EUR", base: str = "1h") -> dict[str, pd.DataFrame]:
    out = {}
    for m in store.markets():
        if m.endswith("-" + quote):
            df = store.load_real(m, base)
            if len(df):
                out[m] = df
    return out


def canon(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Forme canonique d'une décision (comparaison exacte au rejeu, arrondis fixés)."""
    out = []
    for o in orders:
        out.append({"marche": o["marche"], "sens": o["sens"], "montant_eur": round(float(o["montant_eur"]), 4),
                    "quantite": None if o["quantite"] is None else round(float(o["quantite"]), 8), "type": o["type"],
                    "prix_limite": None if o["prix_limite"] is None else float(f"{o['prix_limite']:.10g}"),
                    "duree_validite_bougies": int(o["duree_validite_bougies"]), "annulation": o["annulation"]})
    return sorted(out, key=lambda x: (x["sens"] != "sell", x["marche"]))


def phase_gate(base: Any, rb: int, ph: int) -> Any:
    return lambda i, e: base(i, e) if (i - ph) % rb == 0 else {}


def make_accounts(c: dict[str, Any], grid: np.ndarray, data: dict[str, pd.DataFrame], start: int) -> list[tuple[int, Any, Any]]:
    """Renvoie [(phase, stratégie, ExecConfig)] — un compte par phase de rééquilibrage."""
    import scripts.research_phase3 as R3
    from backend.research.strategies_v4 import Rotation, RotationParams, ref_btc_trend, ref_equal_weight
    from backend.research.xsection_v3 import build_panel_v3
    from backend.research.xsection_v4 import ExecConfig
    cfg = R3.scaled_cfg(c["interval"])
    rb = cfg.rebalance_bars
    ec = ExecConfig(**c.get("execution", {}))
    kind = c["type"]
    if kind == "rotation":
        base = Rotation(data, cfg, RotationParams(rebalance_bars=rb, **c.get("params", {})))
        out = []
        for ph in range(rb):
            r = Rotation.__new__(Rotation)
            r.__dict__.update(base.__dict__)
            r.p, r.journal = replace(base.p, phase=ph), []
            out.append((ph, r, ec))
        return out
    P = build_panel_v3(data, cfg)
    if kind == "ref_btc_filtre":
        return [(ph, phase_gate(ref_btc_trend(P, cfg, rebalance_bars=1), rb, ph), ec) for ph in range(rb)]
    if kind == "ref_constante":
        w, m = float(c["poids"]), c.get("marche", "BTC-EUR")

        def const(i: int, e: Any) -> dict[str, float]:
            return {m: w} if abs(e.weight(m) - w) > 0.05 else {}
        return [(ph, phase_gate(const, rb, ph), ec) for ph in range(rb)]
    if kind == "ref_equipondere":
        every = int(c.get("jours", 30) * R3.bars_per_day(c["interval"]))
        base_eq = ref_equal_weight(P, cfg, int(c.get("n", 5)), 1)
        return [(ph, (lambda b, p: (lambda i, e: b(i, e) if (i - start - p * every // rb) % every == 0 else {}))(base_eq, ph), ec)
                for ph in range(rb)]
    raise ValueError(f"type de candidat inconnu : {kind}")


def run_account(grid: np.ndarray, data: dict[str, pd.DataFrame], strat: Any, ec: Any, start: int,
                states: Any) -> tuple[Any, dict[int, list[dict[str, Any]]], np.ndarray]:
    from backend.research.xsection_v4 import DataPolicy, Engine
    eng = Engine(grid, data, ec, DataPolicy(), None, states)
    decisions: dict[int, list[dict[str, Any]]] = {}
    eq = np.full(len(grid), np.nan)
    for i in range(len(grid)):
        eng.step_bar(i, strat, start)
        eq[i] = eng.equity()
        if i >= start:
            decisions[int(grid[i])] = canon([o for o in eng.next_orders() if o["decide_a"] == int(grid[i])])
    return eng, decisions, eq


def run(a: argparse.Namespace) -> dict[str, Any]:
    from backend.research.strategies_v4 import prepare
    from backend.research.xsection_v4 import RULE_MS, DataPolicy, classify
    if not FROZEN.exists():
        sys.exit("Aucun candidat v3 figé.")
    doc = json.loads(FROZEN.read_text())
    changed = [f for f, h in doc["empreinte_code"].items() if code_fingerprint().get(f) != h]
    cand_changed = [c["id"] for c in doc["candidats"] if doc["empreintes_candidats"].get(c["id"]) != cand_fingerprint(c)]
    J = Journal(Path(a.journal))
    chain_ok = J.verify()
    run_ts = int(time.time() * 1000)
    cutoff = int(doc["cutoff_ms"])
    raw = load_raw(CandleStore(a.root))
    out: dict[str, Any] = {"execution_utc": datetime.now(UTC).isoformat(timespec="seconds"), "cutoff": doc["cutoff_utc"],
                           "resultats": {}, "avertissement": "Évaluation périodique (GitHub Actions) ; pas une exécution continue ; "
                           "aucune performance n'est une validation avant les échéances de 6 et 12 mois."}
    replay_bad: list[str] = []
    prepared: dict[str, Any] = {}
    for c in doc["candidats"]:
        res: dict[str, Any] = {}
        try:
            iv = c["interval"]
            if iv not in prepared:
                grid, data = prepare(raw, iv)
                C = np.column_stack([data[m]["close"].to_numpy(float) for m in sorted(data)]) if data else np.empty((0, 0))
                prepared[iv] = (grid, data, classify(C, RULE_MS[iv], DataPolicy()))
            grid, data, states = prepared[iv]
            if not len(grid):
                raise ValueError("aucune donnée")
            start = int(np.searchsorted(grid, cutoff))
            step = RULE_MS[iv]
            accounts = []
            for ph, strat, ec in make_accounts(c, grid, data, start):
                eng, decisions, eq = run_account(grid, data, strat, ec, start, states)
                acc = f"{c['id']}#{ph}"
                # 1) REJEU : chaque décision enregistrée doit être reproduite exactement
                for r in J.rows:
                    if r["type"] != "prediction" or r["compte"] != acc:
                        continue
                    t = int(r["bougie_ts"])
                    if t in decisions and decisions[t] != r["ordres"]:
                        replay_bad.append(f"{acc}@{t}")
                    elif t not in decisions and t >= int(grid[0]) and t <= int(grid[-1]):
                        replay_bad.append(f"{acc}@{t} (bougie absente au rejeu)")
                # 2) PRÉDICTION pour la dernière bougie clôturée (jamais rétroactive)
                last = int(grid[-1])
                done = {int(r["bougie_ts"]) for r in J.rows if r["type"] == "prediction" and r["compte"] == acc}
                if last >= cutoff and last not in done:
                    J.append({"type": "prediction", "compte": acc, "candidat": c["id"], "phase": ph, "bougie_ts": last,
                              "execution_ts": run_ts, "ordres": decisions.get(last, []),
                              "etat": {"tresorerie": round(eng.cash, 6), "equite": round(eng.equity(), 6),
                                       "positions": {m: round(float(eng.qty[eng.j[m]]), 10) for m in eng.held()}},
                              "empreinte_candidat": cand_fingerprint(c)})
                seg = eq[start:] if start < len(eq) else np.array([])
                accounts.append({"phase": ph, "equite": float(seg[-1]) if len(seg) else ec.initial_eur,
                                 "rendement": float(seg[-1] / ec.initial_eur - 1) if len(seg) else 0.0,
                                 "dd": float((seg / np.maximum.accumulate(seg) - 1).min()) if len(seg) else 0.0,
                                 "trades": len(eng.trades), "ordres": len(eng.orders_log)})
            rets = [x["rendement"] for x in accounts]
            res = {"comptes": len(accounts), "bougies_prospectives": max(0, len(grid) - start),
                   "rendement_moyen": float(np.mean(rets)), "rendement_min": float(np.min(rets)), "rendement_max": float(np.max(rets)),
                   "dd_median": float(np.median([x["dd"] for x in accounts])), "trades_cumules": int(sum(x["trades"] for x in accounts)),
                   "predictions": sum(1 for r in J.rows if r["type"] == "prediction" and r["candidat"] == c["id"]),
                   "creneaux_attendus_par_compte": max(0, len(grid) - start), "pas_ms": step}
        except Exception as e:  # noqa: BLE001
            res = {"statut": f"erreur {type(e).__name__}: {str(e)[:200]}"}
        out["resultats"][c["id"]] = res
    # référence de marché (critère figé) : achat passif BTC depuis le départ
    g4 = prepared.get("4h")
    if g4 is not None and "BTC-EUR" in g4[1]:
        cl = g4[1]["BTC-EUR"]["close"]
        cl = cl[cl.index >= cutoff].dropna()
        out["btc_achat_passif_depuis_depart"] = float(cl.iloc[-1] / cl.iloc[0] - 1) if len(cl) > 1 else None
    out["rejeu"] = {"divergences": replay_bad[:50], "n_divergences": len(replay_bad)}
    ok = not (changed or cand_changed or not chain_ok or replay_bad)
    out["validite"] = "valide" if ok else (f"INVALIDE (code : {changed} ; candidats : {cand_changed} ; chaîne : {chain_ok} ; "
                                           f"rejeu : {len(replay_bad)} divergence(s))")
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
    r.add_argument("--journal", default=str(ROOT / "data" / "prospective" / "journal_v3.jsonl"))
    a = ap.parse_args()
    if a.cmd == "freeze":
        freeze(Path(a.candidates))
    else:
        run(a)


if __name__ == "__main__":
    main()
