#!/usr/bin/env python3
"""Application MÉCANIQUE de la règle pré-enregistrée (reports/prospective/v2/regle_de_selection.md) aux
résultats JSON de la recherche phase 4 : aucun choix manuel.

  python scripts/select_v2_candidates.py --rotation r1.json r2.json --regime reg.json --sizing siz.json \
      --out reports/prospective/v2/candidats_v2.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

H_SCORES = ("H1_force_relative_multi", "H2_ajuste_volatilite", "H3_confirmation_multi_horizon", "H4_momentum_residuel_btc")


def worst_half(m: dict[str, Any]) -> float:
    a, b = m.get("moitie_1"), m.get("moitie_2")
    return min(x for x in (a, b) if x is not None) if a is not None or b is not None else float("-inf")


def hyp_candidate(key: str, m: dict[str, Any]) -> dict[str, Any]:
    interval, rest = key.split("_", 1)
    hold = int(rest.rsplit("_detention", 1)[1].rstrip("j"))
    h = rest.rsplit("_detention", 1)[0]
    bpd = {"1h": 24, "2h": 12, "4h": 6, "8h": 3, "12h": 2, "1d": 1}[interval]
    c: dict[str, Any] = {"id": f"v2_hyp_{interval}_{h}_det{hold}j", "type": "xs2", "quote": "EUR", "interval": interval,
                         "variant": "base", "overrides": {"min_hold_bars": hold * bpd}, "exposure": "btc", "options": {}}
    if h in H_SCORES:
        c["score"] = h
    elif h == "H5_rotation_si_gain_net":
        c["options"] = {"rotation_hurdle": 1.0}
    elif h == "H6_sortie_regime_defavorable":
        c["options"] = {"exit_on_risk_off": True}
    elif h == "H7_reserve_dynamique":
        c["exposure"] = "reserve_dynamique"
    c["recherche"] = {k: m.get(k) for k in ("rendement_net", "moitie_1", "moitie_2", "trades", "max_drawdown", "sharpe")}
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotation", nargs="+", required=True)
    ap.add_argument("--regime", required=True)
    ap.add_argument("--sizing", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cands: list[dict[str, Any]] = []
    hyps: dict[str, Any] = {}
    for f in a.rotation:
        hyps.update(json.loads(Path(f).read_text()).get("hypotheses", {}))
    ok = {k: v for k, v in hyps.items() if v.get("trades", 0) >= 100}
    if ok:
        k = max(ok, key=lambda x: worst_half(ok[x]))
        cands.append(hyp_candidate(k, ok[k]))
    reg = json.loads(Path(a.regime).read_text()).get("4h", {}).get("variantes", {})
    rk = {k: v for k, v in reg.items() if k.startswith(("seul_", "btc_et_"))}
    if rk:
        k = max(rk, key=lambda x: worst_half(rk[x]))
        name = k.split("_", 1)[1] if k.startswith("seul_") else k
        cands.append({"id": f"v2_regime_4h_{k}", "type": "xs2", "quote": "EUR", "interval": "4h", "variant": "base",
                      "exposure": name if k.startswith("seul_") else k, "options": {},
                      "recherche": {kk: rk[k].get(kk) for kk in ("rendement_net", "moitie_1", "moitie_2", "trades", "max_drawdown")}})
    siz = json.loads(Path(a.sizing).read_text())
    base = siz.get("4h_base_egal")
    if base:
        el = {k: v for k, v in siz.items() if k.startswith("4h_base_") and isinstance(v, dict) and "rendement_net" in v
              and v["rendement_net"] >= base["rendement_net"]}
        k = max(el, key=lambda x: el[x]["max_drawdown"])  # drawdown négatif : le plus proche de 0
        cands.append({"id": f"v2_dimensionnement_4h_{k.rsplit('_', 1)[1]}", "type": "xs2", "quote": "EUR", "interval": "4h",
                      "variant": "base", "exposure": "btc", "options": {"sizing": k.rsplit("_", 1)[1]},
                      "recherche": {kk: el[k].get(kk) for kk in ("rendement_net", "max_drawdown", "trades")}})
    cands.append({"id": "v2_temoin_4h_sans_filtre", "type": "xs2", "quote": "EUR", "interval": "4h", "variant": "base",
                  "exposure": "aucun", "options": {}, "recherche": {"note": "témoin de la valeur du filtre BTC"}})
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(cands, indent=2, ensure_ascii=False))
    print(json.dumps(cands, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
