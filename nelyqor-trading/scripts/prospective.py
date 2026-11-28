#!/usr/bin/env python3
"""Simulation PROSPECTIVE indépendante des candidats figés (aucune clé, aucun ordre).

  python scripts/prospective.py freeze --candidates reports/prospective/candidats.json   # gel (une seule fois)
  python scripts/prospective.py run                                                     # évaluation périodique

Règles :
- Les candidats et l'EMPREINTE du code (sha256) sont figés AVANT les données évaluées ; le gel est commité dans
  git (horodatage + empreinte vérifiables). Toute modification ultérieure du code concerné rend l'évaluation
  « invalide » (signalé, jamais masqué).
- Seules les bougies dont l'horodatage est >= CUTOFF (26/11/2026 00:00 UTC) comptent dans les résultats ; les
  bougies antérieures ne servent qu'à « chauffer » les indicateurs (aucune position avant le CUTOFF).
- À chaque exécution, les INTENTIONS courantes (marchés visés, signaux) sont ajoutées au journal avec l'heure
  d'exécution, AVANT que leur résultat soit connu. Le journal est conservé dans le cache et les artefacts de
  GitHub Actions (pas un serveur permanent : évaluations périodiques, pas d'exécution continue).
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
from backend.data.resample import resample_ohlc  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402

CUTOFF_MS = 1_795_651_200_000  # 2026-11-26T00:00:00Z
CODE_FILES = ["backend/research/xsection.py", "backend/research/backtest.py", "backend/strategies/base.py",
              "backend/data/resample.py", "backend/features/indicators.py", "backend/models/regime.py",
              "scripts/research_phase3.py", "scripts/prospective.py"]
FROZEN = ROOT / "reports" / "prospective" / "candidats_figes.json"


def code_fingerprint() -> dict[str, str]:
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in CODE_FILES}


def freeze(src: Path) -> None:
    if FROZEN.exists():
        sys.exit("Candidats déjà figés : un nouveau gel exige un nouveau fichier et une nouvelle date de départ.")
    cands = json.loads(src.read_text())
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()  # noqa: S603,S607
    doc = {"fige_le_utc": datetime.now(UTC).isoformat(timespec="seconds"), "commit_parent": commit,
           "cutoff_ms": CUTOFF_MS, "cutoff_utc": "2026-11-26T00:00:00Z", "empreinte_code": code_fingerprint(),
           "regles": "aucune modification des candidats ; évaluation uniquement sur bougies >= cutoff",
           "candidats": cands}
    FROZEN.parent.mkdir(parents=True, exist_ok=True)
    FROZEN.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print("gel écrit :", FROZEN, "— à commiter AVANT le", doc["cutoff_utc"])


def load(store: CandleStore, markets: list[str], base: str, interval: str) -> dict[str, pd.DataFrame]:
    rule = {"1h": None, "2h": "2h", "4h": "4h", "8h": "8h", "12h": "12h", "1d": "1D"}[interval]
    out = {}
    for m in markets:
        df = store.load_real(m, base)
        if len(df) < 50:
            continue
        out[m] = resample_ohlc(df, rule) if rule else df
    idx = sorted(set().union(*(set(d.index) for d in out.values()))) if out else []
    return {m: d.reindex(idx) for m, d in out.items()}


def eval_candidate(c: dict[str, Any], store: CandleStore, base: str) -> dict[str, Any]:
    interval = c["interval"]
    if c["type"] == "xs":
        from backend.research.xsection import build_panel, run_xsection, xs_metrics
        from scripts.research_phase3 import make_variant  # type: ignore[import-not-found]
        markets = [m for m in store.markets() if m.endswith("-" + c.get("quote", "EUR"))]
        data = load(store, markets, base, interval)
        cfg = make_variant(interval, c["variant"], **c.get("overrides", {}))
        idx = next(iter(data.values())).index
        start = int(np.searchsorted(np.asarray(idx), CUTOFF_MS))
        if start >= len(idx) - 1:
            return {"statut": "aucune bougie prospective encore disponible", "bougies": 0}
        P = build_panel(data, cfg)
        r = run_xsection(data, cfg, start=start, regime_market=f"BTC-{c.get('quote', 'EUR')}", panel=P)
        ppy = 365.25 * 86_400_000 / float(np.median(np.diff(np.asarray(idx, dtype=np.int64))))
        m = xs_metrics(r, ppy)
        # intention courante (dernière bougie clôturée) : marchés visés, enregistrés AVANT leur résultat
        i = len(idx) - 1
        sc = P["score"].iloc[i].dropna().sort_values(ascending=False).head(cfg.top_n)
        intent = {"bougie_ts": int(idx[i]), "cibles": {k: float(v) for k, v in sc.items()}}
        btc = data.get(f"BTC-{c.get('quote', 'EUR')}")
        bh = float(btc["close"].iloc[-1] / btc["open"].iloc[start] - 1) if btc is not None else None
        return {"statut": "ok", "bougies": len(idx) - start, "rendement_net": m["rendement_net"], "trades": m["trades"],
                "max_drawdown": m["max_drawdown"], "frais_eur": m["frais_eur"], "achat_passif_btc": bh,
                "intention": intent}
    if c["type"] == "strat":
        from backend.core.config import load_strategy_params
        from backend.research.backtest import BTConfig, run_backtest
        from backend.strategies import base as S

        data = load(store, c["markets"], base, interval)
        idx = next(iter(data.values())).index
        start = int(np.searchsorted(np.asarray(idx), CUTOFF_MS))
        if start >= len(idx) - 1:
            return {"statut": "aucune bougie prospective encore disponible", "bougies": 0}
        fam = {"trend_following": S.TrendFollowing, "breakout": S.Breakout, "swing": S.Swing}[c["family"]]
        p = {**load_strategy_params()[c["family"]], **c.get("params", {})}
        strat = S.Filtered({**c.get("filters", {}), "name": c["id"]}, fam(p))
        cfg = BTConfig(**c.get("bt", {}))
        r = run_backtest(data, strat, cfg, trade_start=start)
        eq = r.equity.iloc[start:]
        sig_now = {m: bool(strat.signals(df).entry[-1]) for m, df in data.items()}
        return {"statut": "ok", "bougies": len(idx) - start, "rendement_net": float(eq.iloc[-1] / eq.iloc[0] - 1),
                "trades": len(r.trades), "intention": {"bougie_ts": int(idx[-1]), "signaux_entree": sig_now}}
    return {"statut": f"type inconnu {c['type']}"}


def run(a: argparse.Namespace) -> None:
    if not FROZEN.exists():
        sys.exit("Aucun candidat figé.")
    doc = json.loads(FROZEN.read_text())
    fp = code_fingerprint()
    changed = [f for f, h in doc["empreinte_code"].items() if fp.get(f) != h]
    store = CandleStore(a.root)
    journal = Path(a.journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    run_ts = int(time.time() * 1000)
    results = {}
    for c in doc["candidats"]:
        try:
            res = eval_candidate(c, store, a.base)
        except Exception as e:  # noqa: BLE001
            res = {"statut": f"erreur {type(e).__name__}: {str(e)[:200]}"}
        results[c["id"]] = res
        with journal.open("a") as f:
            f.write(json.dumps({"execution_ts": run_ts, "candidat": c["id"], "resultat": res,
                                "code_modifie_depuis_gel": changed}, default=str, ensure_ascii=False) + "\n")
    out = {"execution_utc": datetime.now(UTC).isoformat(timespec="seconds"), "gel": doc["fige_le_utc"],
           "cutoff": doc["cutoff_utc"], "validite": "INVALIDE (code modifié : " + ", ".join(changed) + ")" if changed else "valide",
           "entrees_journal": sum(1 for _ in journal.open()), "resultats": results,
           "avertissement": "Évaluation périodique sur données publiques ; pas une exécution continue en temps réel."}
    print("PROSPECTIF_JSON_DEBUT")
    print(json.dumps(out, default=str, ensure_ascii=False))
    print("PROSPECTIF_JSON_FIN")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--candidates", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    r.add_argument("--base", default="1h")
    r.add_argument("--journal", default=str(ROOT / "data" / "prospective" / "journal.jsonl"))
    a = ap.parse_args()
    if a.cmd == "freeze":
        freeze(Path(a.candidates))
    else:
        run(a)


if __name__ == "__main__":
    main()
