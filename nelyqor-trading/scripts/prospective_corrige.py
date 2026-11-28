#!/usr/bin/env python3
"""DIAGNOSTIC HORS GEL : rétrospectif sur données nouvelles des candidats figés v1/v2, avec la correction de fin
d'échantillon (`fix_tail`). Les évaluations officielles restent celles de `prospective.py` (v1) et
`prospective_v2.py` (v2), inchangées ; ce script ne modifie ni candidats, ni paramètres, ni critères, et n'écrit
dans aucun journal figé. Il quantifie seulement l'effet du défaut de décote finale relevé en phase 4 sur ces
évaluations (sans lui, une position ouverte à la dernière bougie agrégée incomplète est comptée à −50 %).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import scripts.research_phase3 as R3  # noqa: E402
from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.research.xsection import build_panel, run_xsection, xs_metrics  # noqa: E402
from backend.research.xsection_v2 import run_xs2  # noqa: E402
from backend.research.xsection_v3 import fix_tail, radiations_at_end  # noqa: E402
from scripts import prospective_v2 as P2  # noqa: E402


def evaluate(store: CandleStore) -> dict[str, Any]:
    out: dict[str, Any] = {"nature": "diagnostic hors gel (correction de fin d'échantillon)"}
    v1 = json.loads((ROOT / "reports/prospective/candidats_figes.json").read_text())
    for c in v1["candidats"]:
        if c["type"] != "xs":
            out[c["id"]] = {"statut": "non concerné (moteur par marché)"}
            continue
        data = P2.load_data(store, c.get("quote", "EUR"), c["interval"])
        cfg = R3.make_variant(c["interval"], c["variant"], **c.get("overrides", {}))
        out[c["id"]] = _both(data, int(v1["cutoff_ms"]), c["interval"],
                             lambda d, s, P, cfg=cfg, q=c.get("quote", "EUR"): run_xsection(d, cfg, start=s, regime_market=f"BTC-{q}", panel=P),
                             lambda d, cfg=cfg: build_panel(d, cfg))
    v2p = ROOT / "reports/prospective/v2/candidats_figes_v2.json"
    if v2p.exists():
        v2 = json.loads(v2p.read_text())
        for c in v2["candidats"]:
            data = P2.load_data(store, c.get("quote", "EUR"), c["interval"])

            def run(d: Any, s: int, P: Any, c: dict[str, Any] = c) -> Any:
                cfg, P_, score, expo, opt = P2.build(c, d)
                return run_xs2(d, cfg, opt, start=s, panel=P_, score=score, exposure=expo)

            out[c["id"]] = _both(data, int(v2["cutoff_ms"]), c["interval"], run, lambda d: None)
    return out


def _both(data: dict[str, Any], cutoff: int, interval: str, runner: Any, panel: Any) -> dict[str, Any]:
    if not data:
        return {"statut": "aucune donnée"}
    res = {}
    for name, d in (("tel_que_fige", data), ("fin_corrigee", fix_tail(data))):
        idx = np.asarray(next(iter(d.values())).index)
        start = int(np.searchsorted(idx, cutoff))
        if start >= len(idx) - 1:
            res[name] = {"bougies": 0}
            continue
        r = runner(d, start, panel(d))
        m = xs_metrics(r, R3.ppy(interval))
        res[name] = {"bougies": len(idx) - start, "rendement_net": m["rendement_net"], "trades": m["trades"],
                     "radiations_fin": radiations_at_end(r)}
    return res


def main() -> None:
    store = CandleStore(os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    out = evaluate(store)
    print("PROSPECTIF_CORRIGE_JSON_DEBUT")
    print(json.dumps(out, default=str, ensure_ascii=False))
    print("PROSPECTIF_CORRIGE_JSON_FIN")


if __name__ == "__main__":
    main()
