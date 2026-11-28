#!/usr/bin/env python3
"""Phase 5 — réévaluation sur le moteur v4 VALIDÉ (LAB : aucune clé, aucun ordre). Données <= 25/11/2026 =
RECHERCHE (déjà consultées) : rien ici n'est une validation.

Configurations PRÉ-ENREGISTRÉES (écrites avant exécution, toutes comptées) : rotation × régime {btc, largeur,
aucun} × détention {0, 3 j} × dimensionnement {égal, volatilité} (volatilité : régime largeur seulement),
TOUTES les phases de rééquilibrage (≤ 6 échantillonnées en 1 h), intervalles 1 h / 4 h / 8 h / 1 j ; sensibilité
de la décote de radiation {0 ; 0,10 ; 0,50} et de l'exécution limite sur la configuration de base 4 h.
Références avec les mêmes coûts : euros (0), achat passif BTC, BTC filtré (tendance), exposition constante au
BTC égale à l'exposition moyenne de la stratégie « largeur », équipondéré des 5 marchés les plus liquides
(point-in-time, mensuel, minimum 5 €). Estimation principale = MÉDIANE DES PHASES ; DSR avec le nombre total
d'essais (phases 3–5). JSON entre PHASE5_JSON_DEBUT / PHASE5_JSON_FIN.
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
from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.research.metrics import block_bootstrap_ci, deflated_sharpe  # noqa: E402
from backend.research.strategies_v4 import (  # noqa: E402
    Rotation,
    RotationParams,
    prepare,
    ref_btc_trend,
    ref_buy_hold,
    ref_constant,
    ref_equal_weight,
)
from backend.research.xsection_v4 import DataPolicy, ExecConfig, classify, simulate  # noqa: E402

PRIOR_TRIALS = 1500  # phases 3, 4 et 4b (ordre de grandeur documenté)
TRIALS: list[tuple[str, float]] = []


def metrics(eq: Any, ppy: float, count: bool, interval: str) -> dict[str, Any]:
    e = eq.dropna()
    r = e.pct_change().dropna()
    h = len(e) // 2
    sh = float(r.mean() / r.std() * math.sqrt(ppy)) if r.std() > 0 else 0.0
    if count:
        TRIALS.append((interval, sh / math.sqrt(ppy)))
    yrs = len(e) / ppy
    out = {"net": float(e.iloc[-1] / e.iloc[0] - 1), "m1": float(e.iloc[h] / e.iloc[0] - 1), "m2": float(e.iloc[-1] / e.iloc[h] - 1),
           "dd": float((e / e.cummax() - 1).min()), "sharpe": sh, "annees": yrs}
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    net = np.array([x["net"] for x in rows])
    return {"phases": len(rows), "mediane": float(np.median(net)), "min": float(net.min()), "max": float(net.max()),
            "part_positive": float((net > 0).mean()), "mediane_m1": float(np.median([x["m1"] for x in rows])),
            "mediane_m2": float(np.median([x["m2"] for x in rows])),
            "deux_moities_positives": float(np.mean([x["m1"] > 0 and x["m2"] > 0 for x in rows])),
            "dd_median": float(np.median([x["dd"] for x in rows])), "sharpe_median": float(np.median([x["sharpe"] for x in rows])),
            "trades_moy": float(np.mean([x.get("trades", 0) for x in rows])), "exposition_moy": float(np.mean([x.get("expo", 0) for x in rows])),
            "radiations_moy": float(np.mean([x.get("radiations", 0) for x in rows])), "par_phase": [round(x["net"], 4) for x in rows],
            "annees": rows[0]["annees"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="4h")
    ap.add_argument("--base", default="1h")
    ap.add_argument("--max-phases", type=int, default=6)
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--out", default=str(ROOT / "reports-ci"))
    ap.add_argument("--synthetic-smoke", action="store_true")
    a = ap.parse_args()
    R3.SMOKE = a.synthetic_smoke
    t0 = time.time()
    raw = R3.load_universe(CandleStore(a.root), a.base, ["EUR"])
    res: dict[str, Any] = {}
    pol = DataPolicy()
    for interval in a.intervals.split(","):
        grid, data = prepare(raw, interval)
        ppy = R3.ppy(interval)
        cfg = R3.scaled_cfg(interval)
        b = R3.bars_per_day(interval)
        C = np.column_stack([data[m]["close"].to_numpy(float) for m in sorted(data)])
        states = classify(C, int(np.median(np.diff(grid))), pol)
        res[f"{interval}|donnees"] = {"marches": len(data), "bougies": len(grid), "pannes": int(states[1].sum()),
                                      "etats": {k: int((states[0] == v).sum()) for k, v in (("non_cote", 0), ("actif", 1), ("sans_transaction", 2), ("radie", 3))}}
        rb = cfg.rebalance_bars
        phases = list(range(rb)) if rb <= a.max_phases else [int(x) for x in np.linspace(0, rb - 1, a.max_phases)]
        configs: list[tuple[str, dict[str, Any], ExecConfig, DataPolicy]] = []
        for regime in ("btc", "largeur", "aucun"):
            for hold in (0, 3):
                configs.append((f"rotation_{regime}_det{hold}j", {"regime": regime, "min_hold_bars": int(hold * b)}, ExecConfig(), pol))
        configs.append(("rotation_largeur_det0j_volatilite", {"regime": "largeur", "sizing": "volatilite"}, ExecConfig(), pol))
        if interval == "4h":
            for hc in (0.0, 0.5):
                configs.append((f"rotation_btc_det0j_decote{hc}", {"regime": "btc"}, ExecConfig(), DataPolicy(delist_haircut=hc)))
            configs.append(("rotation_btc_det0j_limite", {"regime": "btc"}, ExecConfig(mode="limit", ttl_bars=1), pol))
            configs.append(("rotation_largeur_det0j_limite", {"regime": "largeur"}, ExecConfig(mode="limit", ttl_bars=1), pol))
        strat0 = None
        for name, kw, ec, pp in configs:
            rows = []
            for ph in phases:
                strat = Rotation(data, cfg, RotationParams(rebalance_bars=rb, phase=ph, **kw))
                strat0 = strat0 or strat
                st = states if pp is pol else None
                r, eng = simulate(grid, data, strat, ec, pp, states=st)
                m = metrics(r.equity, ppy, True, interval)
                m.update({"trades": len(r.trades), "radiations": sum(t["reason"] == "radiation" for t in r.trades),
                          "expo": float(r.stats["exposition_reelle_moy"])})
                rows.append(m)
            res[f"{interval}|{name}"] = summarize(rows)
            s = res[f"{interval}|{name}"]
            print(f"p5 {interval} {name:36s} médiane {s['mediane']:+.1%} [{s['min']:+.0%};{s['max']:+.0%}] pos {s['part_positive']:.0%} "
                  f"m2 {s['mediane_m2']:+.0%} expo {s['exposition_moy']:.0%} tr {s['trades_moy']:.0f}", flush=True)
        # références
        assert strat0 is not None
        P = strat0.P
        # exposition RÉELLEMENT investie (1 − trésorerie/équité), moyenne sur les phases
        expo_largeur = res[f"{interval}|rotation_largeur_det0j"]["exposition_moy"] if f"{interval}|rotation_largeur_det0j" in res else 0.3
        refs = {"euros": None, "btc_achat_passif": ref_buy_hold(None, "BTC-EUR"), "btc_filtre_tendance": ref_btc_trend(P, cfg, rebalance_bars=rb),
                "btc_exposition_constante_egale_largeur": ref_constant("BTC-EUR", max(0.0, min(0.999, expo_largeur))),
                "equipondere_5_liquides_mensuel": ref_equal_weight(P, cfg, 5, int(30 * b))}
        for nm, s in refs.items():
            if s is None:
                res[f"{interval}|ref_{nm}"] = {"net": 0.0}
                continue
            r, _ = simulate(grid, data, s, ExecConfig(), pol, states=states)
            m = metrics(r.equity, ppy, False, interval)
            rets = r.equity.dropna().pct_change().dropna().to_numpy()
            m["ic95_net"] = block_bootstrap_ci(rets, lambda x: float(np.prod(1 + x) - 1), block=max(10, int(b * 30))) if len(rets) > 100 else None
            m["trades"] = len(r.trades)
            res[f"{interval}|ref_{nm}"] = m
            print(f"p5 {interval} ref {nm:36s} net {m['net']:+.1%} m1 {m['m1']:+.0%} m2 {m['m2']:+.0%} dd {m['dd']:.0%}", flush=True)
    # DSR : meilleure configuration (médiane de Sharpe sur les phases), essais cumulés
    cand = {k: v for k, v in res.items() if "|rotation_" in k}
    if cand:
        kb = max(cand, key=lambda x: cand[x]["sharpe_median"])
        iv = kb.split("|")[0]
        same = [s for i, s in TRIALS if i == iv]
        n = len(TRIALS) + PRIOR_TRIALS
        res["meilleure"] = {"cle": kb, "sharpe_median": cand[kb]["sharpe_median"], "n_essais": n,
                            "dsr": deflated_sharpe(cand[kb]["sharpe_median"] / math.sqrt(R3.ppy(iv)), int(cand[kb]["annees"] * R3.ppy(iv)),
                                                   n, float(np.var(same)) if len(same) > 1 else 0.0)}
    res["_meta"] = {"duree_s": round(time.time() - t0, 1), "essais_partie": len(TRIALS), "essais_anterieurs": PRIOR_TRIALS,
                    "moteur": "v4", "periode": "RECHERCHE (<= 25/11/2026)",
                    "donnees": "SYNTHÉTIQUES (test de code)" if a.synthetic_smoke else "RÉELLES (API publique Bitvavo)"}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, f"phase5_{a.intervals.replace(',', '-')}.json").write_text(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE5_JSON_DEBUT")
    print(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE5_JSON_FIN")


if __name__ == "__main__":
    main()
