#!/usr/bin/env python3
"""Phase 4b — contrôle des biais relevés par la contre-expertise quantitative (LAB, aucune clé, aucun ordre).

Pour chaque intervalle, chaque configuration est rejouée sur TOUTES les phases de rééquilibrage et sous trois
traitements des données :
  stricte_v2          : comme en phases 3–4 (agrégation stricte, décote de fin d'échantillon erronée) ;
  stricte_corrigee    : fin d'échantillon corrigée + rendements/volatilité robustes aux bougies absentes ;
  tolerante_corrigee  : idem avec agrégation tolérante (une bougie agrégée dès qu'une heure existe).
L'estimation principale publiée est la MÉDIANE sur les phases (pas la phase 0). Configurations figées ici,
avant exécution : base, detention3j, H5, kelly, egal_filtre30j (isole le filtre de Kelly), largeur
(BTC + proportion haussière), largeur_kelly, sans_filtre. DSR : nombre d'essais cumulé phases 3–4 inclus.
Données <= 25/11/2026 : RECHERCHE, pas une validation. JSON entre PHASE4B_JSON_DEBUT / PHASE4B_JSON_FIN.
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

import scripts.research_phase3 as R3  # noqa: E402
import scripts.research_phase4 as R4  # noqa: E402
from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.research.metrics import deflated_sharpe  # noqa: E402
from backend.research.xsection import build_panel, xs_metrics  # noqa: E402
from backend.research.xsection_v2 import XS2Options, regime_exposures, run_xs2  # noqa: E402
from backend.research.xsection_v3 import (  # noqa: E402
    EGAL_FILTRE_30J,
    build_panel_v3,
    fix_tail,
    radiations_at_end,
    regime_exposures_v3,
)

PRIOR_TRIALS = 1200  # ordre de grandeur documenté des essais des phases 3 et 4 (106 + 720 + 6 + ~370)
CONFIGS: dict[str, tuple[str, dict[str, Any], str]] = {
    "base": ("base", {}, "btc"),
    "detention3j": ("detention_min_3j", {}, "btc"),
    "H5_detention3j": ("detention_min_3j", {"rotation_hurdle": 1.0}, "btc"),
    "kelly": ("base", {"sizing": "kelly"}, "btc"),
    "egal_filtre30j": ("base", dict(EGAL_FILTRE_30J), "btc"),
    "largeur": ("base", {}, "largeur"),
    "largeur_kelly": ("base", {"sizing": "kelly"}, "largeur"),
    "sans_filtre": ("base", {}, "aucun"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="4h")
    ap.add_argument("--base", default="1h")
    ap.add_argument("--max-phases", type=int, default=6)
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--out", default=str(ROOT / "reports-ci"))
    ap.add_argument("--synthetic-smoke", action="store_true")
    a = ap.parse_args()
    R4.SMOKE = a.synthetic_smoke
    t0 = time.time()
    store = CandleStore(a.root)
    raw = R4.universe(store, a.base)
    res: dict[str, Any] = {}
    trials: list[tuple[str, float]] = []
    for interval in a.intervals.split(","):
        b = R3.bars_per_day(interval)
        datasets = {"stricte_v2": R3.derive(raw, interval, a.base)}
        datasets["stricte_corrigee"] = fix_tail(datasets["stricte_v2"])
        if interval != a.base:
            datasets["tolerante_corrigee"] = fix_tail(R4.tolerant_derive(raw, interval))
        for agg, data in datasets.items():
            cfg0 = R3.make_variant(interval, "base")
            v3 = agg != "stricte_v2"
            panels: dict[str, Any] = {}
            for name, (variant, opts, expo_name) in CONFIGS.items():
                cfg = R3.make_variant(interval, variant)
                key_p = variant
                if key_p not in panels:
                    panels[key_p] = build_panel_v3(data, cfg) if v3 else build_panel(data, cfg)
                P = panels[key_p]
                ex = (regime_exposures_v3 if v3 else regime_exposures)(P, cfg0)
                expo = {"btc": ex["btc"], "largeur": ex["btc"] * ex["proportion_haussiere"],
                        "aucun": np.ones(len(P["close"]))}[expo_name]
                rb = cfg.rebalance_bars
                phases = list(range(rb)) if rb <= a.max_phases else [int(x) for x in np.linspace(0, rb - 1, a.max_phases)]
                rows = []
                for p in phases:
                    r = run_xs2(data, cfg, XS2Options(phase=p, **opts), panel=P, exposure=expo)
                    m = xs_metrics(r, R3.ppy(interval))
                    eq = r.equity.dropna()
                    h = len(eq) // 2
                    rows.append({"phase": p, "net": m["rendement_net"], "sharpe": m["sharpe"], "trades": m["trades"],
                                 "dd": m["max_drawdown"], "m1": float(eq.iloc[h] / eq.iloc[0] - 1),
                                 "m2": float(eq.iloc[-1] / eq.iloc[h] - 1), "radiations_fin": radiations_at_end(r),
                                 "exposition": m["exposition"], "annees": m["annees"]})
                    trials.append((interval, m["sharpe"] / math.sqrt(R3.ppy(interval))))
                net = np.array([x["net"] for x in rows])
                k = f"{interval}|{agg}|{name}"
                res[k] = {"phases": len(rows), "mediane": float(np.median(net)), "min": float(net.min()),
                          "max": float(net.max()), "part_positive": float((net > 0).mean()),
                          "mediane_moitie_1": float(np.median([x["m1"] for x in rows])),
                          "mediane_moitie_2": float(np.median([x["m2"] for x in rows])),
                          "part_deux_moities_positives": float(np.mean([(x["m1"] > 0) and (x["m2"] > 0) for x in rows])),
                          "radiations_fin_moy": float(np.mean([x["radiations_fin"] for x in rows])),
                          "trades_moy": float(np.mean([x["trades"] for x in rows])),
                          "dd_median": float(np.median([x["dd"] for x in rows])),
                          "sharpe_median": float(np.median([x["sharpe"] for x in rows])),
                          "par_phase": [round(x["net"], 4) for x in rows], "annees": rows[0]["annees"], "bars_per_day": b}
                print(f"4b {k:48s} médiane {res[k]['mediane']:+.1%} [{res[k]['min']:+.0%} ; {res[k]['max']:+.0%}] "
                      f"pos {res[k]['part_positive']:.0%} 2moitiés {res[k]['part_deux_moities_positives']:.0%} "
                      f"radiations_fin {res[k]['radiations_fin_moy']:.1f}", flush=True)
    # DSR de la meilleure configuration CORRIGÉE (médiane des phases), essais cumulés
    corr = {k: v for k, v in res.items() if "|stricte_v2|" not in k}
    if corr:
        kb = max(corr, key=lambda x: corr[x]["sharpe_median"])
        iv = kb.split("|")[0]
        same = [s for i, s in trials if i == iv]
        res["meilleure_corrigee"] = {"cle": kb, "sharpe_median": corr[kb]["sharpe_median"], "n_essais": len(trials) + PRIOR_TRIALS,
                                     "dsr": deflated_sharpe(corr[kb]["sharpe_median"] / math.sqrt(R3.ppy(iv)),
                                                            int(corr[kb]["annees"] * R3.ppy(iv)), len(trials) + PRIOR_TRIALS,
                                                            float(np.var(same)) if len(same) > 1 else 0.0)}
    res["_meta"] = {"duree_s": round(time.time() - t0, 1), "essais_partie": len(trials), "essais_anterieurs": PRIOR_TRIALS,
                    "periode": "RECHERCHE (<= 25/11/2026)",
                    "donnees": "SYNTHÉTIQUES (test de code)" if a.synthetic_smoke else "RÉELLES (API publique Bitvavo)"}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, f"phase4b_{a.intervals.replace(',', '-')}.json").write_text(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE4B_JSON_DEBUT")
    print(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE4B_JSON_FIN")


if __name__ == "__main__":
    main()
