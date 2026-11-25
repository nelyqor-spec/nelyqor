#!/usr/bin/env python3
"""Validation PROSPECTIVE v2 (phase 4) — séparée de la v1 figée le 25/11/2026 (commit 9003293), qui reste
évaluée par `scripts/prospective.py` sans aucune modification de ses candidats, paramètres ou critères.

  python scripts/prospective_v2.py freeze --candidates reports/prospective/v2/candidats_v2.json
  python scripts/prospective_v2.py run

Trois natures de résultats, TOUJOURS séparées dans le journal et le rapport :
1. PRÉDICTION (`type=prediction`) : à chaque exécution, pour la dernière bougie CLÔTURÉE, la cible du
   candidat (marchés classés, exposition) est enregistrée AVANT que son résultat existe, avec l'heure
   d'exécution et l'empreinte de la configuration. Journal chaîné par sha256 (toute modification ou
   suppression d'une ligne casse la chaîne). Une prédiction n'est JAMAIS reconstituée après coup.
2. NOTATION (`type=score`) : une fois l'horizon écoulé, la prédiction est notée sur les bougies réelles
   (rendement brut et net d'un aller-retour taker, comparé au BTC et à la médiane de l'univers).
3. RÉTROSPECTIF SUR DONNÉES NOUVELLES (`type=retrospectif`) : simulation complète du candidat sur toutes les
   bougies >= cutoff, recalculée à chaque exécution. Utile, mais ce n'est PAS une prédiction : un oubli
   d'exécution y est comblé, alors qu'une prédiction manquée reste manquée (taux de couverture publié).

Rattrapage : si une exécution est manquée, la suivante complète les données et la partie rétrospective ;
les créneaux de prédiction manqués sont comptés (`predictions_manquees`), jamais remplis.
GitHub Actions n'exécute ceci que périodiquement : ce n'est ni un serveur de trading, ni du temps réel.
"""
from __future__ import annotations

import argparse
import hashlib
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
import pandas as pd  # noqa: E402

from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402

DIR = ROOT / "reports" / "prospective" / "v2"
FROZEN = DIR / "candidats_figes_v2.json"
CODE_FILES = ["backend/research/xsection.py", "backend/research/xsection_v2.py", "backend/data/resample.py",
              "backend/features/indicators.py", "scripts/research_phase3.py", "scripts/research_phase4.py",
              "scripts/prospective_v2.py"]
DAY = 86_400_000


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def code_fingerprint() -> dict[str, str]:
    return {f: sha((ROOT / f).read_bytes()) for f in CODE_FILES}


def cand_fingerprint(c: dict[str, Any]) -> str:
    return sha(json.dumps(c, sort_keys=True, ensure_ascii=False).encode())


def next_midnight_ms(now_ms: int, margin_ms: int = 3_600_000) -> int:
    d = (now_ms + margin_ms) // DAY + 1
    return int(d * DAY)


def freeze(src: Path, now_ms: int | None = None) -> dict[str, Any]:
    if FROZEN.exists():
        sys.exit("Candidats v2 déjà figés : un nouveau gel exige un nouveau fichier (v3) et un nouveau départ.")
    now_ms = now_ms or int(time.time() * 1000)
    cands = json.loads(src.read_text())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()  # noqa: S603,S607
    cutoff = next_midnight_ms(now_ms)
    doc = {"fige_le_utc": datetime.fromtimestamp(now_ms / 1000, UTC).isoformat(timespec="seconds"),
           "commit_parent": commit, "cutoff_ms": cutoff,
           "cutoff_utc": datetime.fromtimestamp(cutoff / 1000, UTC).isoformat(timespec="seconds"),
           "empreinte_code": code_fingerprint(),
           "empreintes_candidats": {c["id"]: cand_fingerprint(c) for c in cands},
           "regles": "aucune modification ; prédictions enregistrées avant observation ; notation à horizon ; "
                     "le rétrospectif sur données nouvelles est publié séparément",
           "candidats": cands}
    FROZEN.parent.mkdir(parents=True, exist_ok=True)
    FROZEN.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print("gel v2 écrit :", FROZEN, "— départ prospectif", doc["cutoff_utc"])
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


def load_data(store: CandleStore, quote: str, interval: str, base: str = "1h") -> dict[str, pd.DataFrame]:
    import scripts.research_phase3 as R3
    out = {}
    for m in store.markets():
        if not m.endswith("-" + quote):
            continue
        df = store.load_real(m, base)
        if len(df) >= 200:
            out[m] = df
    return R3.derive(out, interval, base) if out else {}


def build(c: dict[str, Any], data: dict[str, pd.DataFrame]) -> tuple[Any, ...]:
    import scripts.research_phase3 as R3
    import scripts.research_phase4 as R4
    from backend.research.xsection import build_panel
    from backend.research.xsection_v2 import XS2Options, btc_regime, regime_exposures

    interval = c["interval"]
    cfg = R3.make_variant(interval, c.get("variant", "base"), **c.get("overrides", {}))
    P = build_panel(data, cfg)
    score = None
    if c.get("score", "base") != "base":
        score = R4.custom_scores(P, cfg, interval)[c["score"]]
    expo_name = c.get("exposure", "btc")
    if expo_name == "btc":
        expo = btc_regime(P, cfg, f"BTC-{c.get('quote', 'EUR')}")
    elif expo_name == "aucun":
        expo = np.ones(len(P["close"]))
    else:
        expo = regime_exposures(P, cfg, f"BTC-{c.get('quote', 'EUR')}")[expo_name]
    opt = XS2Options(**c.get("options", {}), record_orders=False)
    return cfg, P, score, expo, opt


def predict(c: dict[str, Any], cfg: Any, P: dict[str, pd.DataFrame], score: Any, expo: np.ndarray) -> dict[str, Any]:
    from backend.research.xsection import eligible
    cnt = P["close"].notna().sum(axis=1).to_numpy()
    i = int(np.flatnonzero(cnt >= 0.5 * cnt.max())[-1])  # dernière bougie COMPLÈTE (la bougie agrégée en cours est NaN)
    S = score if score is not None else P["score"]
    ok = eligible(P, cfg, i)
    sc = S.iloc[i].where(ok).dropna().sort_values(ascending=False).head(cfg.top_n)
    return {"bougie_ts": int(P["close"].index[i]), "exposition": float(expo[i]),
            "cibles": [m for m, v in sc.items() if v > cfg.min_score] if expo[i] > 0 else [],
            "scores": {m: float(v) for m, v in sc.items()}}


def score_prediction(pred: dict[str, Any], P: dict[str, pd.DataFrame], horizon_ms: int, quote: str) -> dict[str, Any] | None:
    C = P["close"]
    t0 = pred["bougie_ts"]
    t1 = t0 + horizon_ms
    idx = np.asarray(C.index)
    if t1 > idx[-1] or t0 not in set(idx):
        return None
    i0, i1 = int(np.searchsorted(idx, t0)), int(np.searchsorted(idx, t1))
    if i1 >= len(idx) or idx[i1] != t1:
        return None
    rets = (C.iloc[i1] / C.iloc[i0] - 1).replace([np.inf, -np.inf], np.nan)
    tg = [m for m in pred["cibles"] if np.isfinite(rets.get(m, np.nan))]
    gross = float(rets[tg].mean()) * pred["exposition"] if tg else 0.0
    cost = 2 * 0.0025 * pred["exposition"] if tg else 0.0
    btc = rets.get(f"BTC-{quote}")
    return {"rendement_brut": gross, "rendement_net_aller_retour_taker": gross - cost,
            "btc": float(btc) if btc is not None and np.isfinite(btc) else None,
            "mediane_univers": float(rets.median()) if rets.notna().any() else None, "n_cibles": len(tg)}


def run(a: argparse.Namespace) -> dict[str, Any]:
    import scripts.research_phase3 as R3
    from backend.research.xsection import xs_metrics
    from backend.research.xsection_v2 import run_xs2
    if not FROZEN.exists():
        sys.exit("Aucun candidat v2 figé.")
    doc = json.loads(FROZEN.read_text())
    changed = [f for f, h in doc["empreinte_code"].items() if code_fingerprint().get(f) != h]
    cand_changed = [c["id"] for c in doc["candidats"] if doc["empreintes_candidats"].get(c["id"]) != cand_fingerprint(c)]
    store = CandleStore(a.root)
    J = Journal(Path(a.journal))
    chain_ok = J.verify()
    run_ts = int(time.time() * 1000)
    cutoff = int(doc["cutoff_ms"])
    out: dict[str, Any] = {"execution_utc": datetime.now(UTC).isoformat(timespec="seconds"), "cutoff": doc["cutoff_utc"],
                           "validite": "valide" if not (changed or cand_changed or not chain_ok) else
                           f"INVALIDE (code : {changed} ; candidats : {cand_changed} ; chaîne : {chain_ok})",
                           "resultats": {}, "avertissement": "Évaluation périodique (GitHub Actions) ; pas une exécution continue."}
    cache: dict[tuple[str, str], dict[str, pd.DataFrame]] = {}
    for c in doc["candidats"]:
        key = (c.get("quote", "EUR"), c["interval"])
        if key not in cache:
            cache[key] = load_data(store, *key)
        data = cache[key]
        res: dict[str, Any] = {}
        try:
            if not data:
                raise ValueError("aucune donnée")
            cfg, P, score, expo, opt = build(c, data)
            idx = np.asarray(P["close"].index)
            step = int(np.median(np.diff(idx)))
            # 1) prédiction pour la dernière bougie clôturée (jamais pour une bougie passée non prédite)
            pred = predict(c, cfg, P, score, expo)
            done = {r["prediction"]["bougie_ts"] for r in J.rows if r["type"] == "prediction" and r["candidat"] == c["id"]}
            if pred["bougie_ts"] >= cutoff - step and pred["bougie_ts"] not in done:
                J.append({"type": "prediction", "candidat": c["id"], "execution_ts": run_ts, "prediction": pred,
                          "empreinte_candidat": cand_fingerprint(c)})
            # 2) notation des prédictions arrivées à horizon
            horizon = cfg.rebalance_bars * step
            scored = {r["prediction_hash"] for r in J.rows if r["type"] == "score" and r["candidat"] == c["id"]}
            for r in [r for r in J.rows if r["type"] == "prediction" and r["candidat"] == c["id"]]:
                if r["hash"] in scored:
                    continue
                s = score_prediction(r["prediction"], P, horizon, key[0])
                if s is not None:
                    J.append({"type": "score", "candidat": c["id"], "execution_ts": run_ts, "prediction_hash": r["hash"],
                              "bougie_ts": r["prediction"]["bougie_ts"], "score": s})
            preds = [r for r in J.rows if r["type"] == "prediction" and r["candidat"] == c["id"]]
            scores = [r["score"] for r in J.rows if r["type"] == "score" and r["candidat"] == c["id"]]
            # créneaux attendus : une prédiction par période de rééquilibrage depuis le départ prospectif
            slots = {(int(t) - cutoff) // horizon for t in idx if t >= cutoff}
            covered = {(int(r["prediction"]["bougie_ts"]) - cutoff) // horizon for r in preds}
            res["predictions"] = {"n": len(preds), "notees": len(scores),
                                  "creneaux_attendus": len(slots), "predictions_manquees": len(slots - covered),
                                  "rendement_net_cumule_notees": float(np.prod([1 + s["rendement_net_aller_retour_taker"] for s in scores]) - 1) if scores else None,
                                  "btc_cumule_notees": float(np.prod([1 + (s["btc"] or 0) for s in scores]) - 1) if scores else None}
            # 3) rétrospectif sur données nouvelles (>= cutoff), positions nulles avant
            start = int(np.searchsorted(idx, cutoff))
            if start < len(idx) - 1:
                rr = run_xs2(data, cfg, opt, start=start, panel=P, score=score, exposure=expo)
                m = xs_metrics(rr, R3.ppy(c["interval"]))
                res["retrospectif"] = {"bougies": len(idx) - start, "rendement_net": m["rendement_net"], "trades": m["trades"],
                                       "max_drawdown": m["max_drawdown"], "frais_eur": m["frais_eur"]}
            else:
                res["retrospectif"] = {"bougies": 0, "statut": "aucune bougie après le départ prospectif"}
        except Exception as e:  # noqa: BLE001
            res = {"statut": f"erreur {type(e).__name__}: {str(e)[:200]}"}
        out["resultats"][c["id"]] = res
    out["journal"] = {"lignes": len(J.rows), "chaine_intacte": J.verify(), "tete": J.rows[-1]["hash"] if J.rows else None}
    print("PROSPECTIF_V2_JSON_DEBUT")
    print(json.dumps(out, default=str, ensure_ascii=False))
    print("PROSPECTIF_V2_JSON_FIN")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--candidates", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    r.add_argument("--journal", default=str(ROOT / "data" / "prospective" / "journal_v2.jsonl"))
    a = ap.parse_args()
    if a.cmd == "freeze":
        freeze(Path(a.candidates))
    else:
        run(a)


if __name__ == "__main__":
    main()
