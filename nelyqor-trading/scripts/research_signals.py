#!/usr/bin/env python3
"""Phase 5 (priorités 3 et 4) — pouvoir prédictif des signaux élémentaires APRÈS COÛTS (LAB, aucune clé, aucun
ordre). Méthode : `backend/research/signals.py`.

Données réelles : bougies 1 h `CandleStore` (comme `scripts/research_phase4.py`), période de RECHERCHE
≤ 25/11/2026 (`scripts.research_phase3.load_universe`). Toutes les heures de rééquilibrage (phases) sont
utilisées et poolées ; quatre traitements des données : agrégation stricte/tolérante × trous dernier_prix/exclure.
Le traitement PRINCIPAL (pré-enregistré) est stricte|dernier_prix ; les autres sont des contrôles de sensibilité,
mais TOUS leurs tests sont comptés dans la correction globale (Holm, BH).

  python scripts/research_signals.py                       # données réelles (GitHub Actions)
  python scripts/research_signals.py --synthetic-smoke     # données SYNTHÉTIQUES : test du code uniquement

JSON imprimé entre SIGNALS_JSON_DEBUT / SIGNALS_JSON_FIN et écrit dans --out.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import scripts.research_phase4 as R4  # noqa: E402
from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.research.signals import AGGREGATIONS, GAP_POLICIES, SignalConfig, run_study  # noqa: E402
from backend.research.synthetic import SYNTHETIC_LABEL, synthetic_universe  # noqa: E402


def smoke_universe(n_markets: int = 30, days: int = 700, seed: int = 11) -> dict[str, pd.DataFrame]:
    """Univers SYNTHÉTIQUE (aucune valeur probante) : bougies alignées sur minuit, liquidités variées, heures
    manquantes, une cotation tardive et une radiation — pour exercer tous les chemins du code."""
    rng = np.random.default_rng(seed)
    names = ["BTC-EUR"] + [f"S{i}-EUR" for i in range(n_markets - 1)]
    d = synthetic_universe(names, days * 24, seed=seed)
    out = {}
    for k, (m, df) in enumerate(d.items()):
        df = df.copy()
        df.index = df.index - (int(df.index[0]) % 86_400_000)
        if m != "BTC-EUR":
            df["volume"] *= 10 ** rng.uniform(1.0, 4.0)
            drop = rng.random(len(df)) < rng.uniform(0.0, 0.01)  # heures sans transaction
            df = df[~drop]
        if k == 3:
            df = df.iloc[len(df) // 3:]  # cotation tardive
        if k == 4:
            df = df.iloc[: 2 * len(df) // 3]  # radiation
        out[m] = df
    return out


def clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [clean(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="1h")
    ap.add_argument("--phases", type=int, default=24, help="nombre de phases (heures) réparties sur 0–23 h")
    ap.add_argument("--aggregations", default=",".join(AGGREGATIONS))
    ap.add_argument("--gaps", default=",".join(GAP_POLICIES))
    ap.add_argument("--horizons", default="1,3,7,14")
    ap.add_argument("--fee", type=float, default=0.0025, help="commission par côté (taker)")
    ap.add_argument("--spread-floor-bps", type=float, default=5.0)
    ap.add_argument("--half-spread-fixe", type=float, default=None, help="demi-spread constant (sinon Corwin-Schultz)")
    ap.add_argument("--min-liq-eur-jour", type=float, default=50_000.0)
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--n-random", type=int, default=500)
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--n-prior", type=int, default=1200)
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--out", default=str(ROOT / "reports-ci"))
    ap.add_argument("--synthetic-smoke", action="store_true")
    a = ap.parse_args()
    if a.base != "1h":
        raise SystemExit("seules les bougies de base 1 h sont prises en charge")
    t0 = time.time()
    if a.synthetic_smoke:
        raw = smoke_universe()
    else:
        R4.SMOKE = False
        raw = R4.universe(CandleStore(a.root), a.base)
    cfg = SignalConfig(fee_taker=a.fee, spread_floor_bps=a.spread_floor_bps, half_spread_fixed=a.half_spread_fixe,
                       horizons=tuple(int(x) for x in a.horizons.split(",")), min_liq_eur_day=a.min_liq_eur_jour,
                       n_boot=a.n_boot, n_random=a.n_random, top_n=a.top_n, n_prior_trials=a.n_prior)
    phases = sorted({int(x) for x in np.linspace(0, 23, max(1, min(24, a.phases))).round()})
    res = run_study(raw, cfg, phases, a.aggregations.split(","), a.gaps.split(","),
                    log=lambda s: print(s, flush=True))
    res["_meta"] = {"duree_s": round(time.time() - t0, 1), "marches_charges": len(raw),
                    "periode": "RECHERCHE (données <= 25/11/2026, déjà consultées) : aucune validation indépendante",
                    "donnees": f"SYNTHÉTIQUES (test de code) — {SYNTHETIC_LABEL}" if a.synthetic_smoke
                    else "RÉELLES (API publique Bitvavo)", "traitement_principal": "stricte|dernier_prix"}
    res = clean(res)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    name = "signals_smoke.json" if a.synthetic_smoke else "signals.json"
    Path(a.out, name).write_text(json.dumps(res, ensure_ascii=False))
    c = res["comptage"]
    print(f"tests comptés : {c['n_tests_pvaleurs_global']} (principal {c['n_tests_pvaleurs_traitement_principal']}) ; "
          f"bruts p<5 % : {c['significatifs_bruts_5pc']} ; Holm : {c['significatifs_holm_global_5pc']} ; "
          f"BH : {c['significatifs_bh_global_5pc']} ; durée {res['_meta']['duree_s']} s", flush=True)
    print("SIGNALS_JSON_DEBUT")
    print(json.dumps(res, ensure_ascii=False))
    print("SIGNALS_JSON_FIN")


if __name__ == "__main__":
    main()
