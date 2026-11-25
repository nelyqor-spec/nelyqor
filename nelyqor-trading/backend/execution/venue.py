"""Comparateur dynamique de routes d'exécution EUR / USDC.

Coût d'un ALLER-RETOUR (achat + vente) d'un montant S, exprimé en fraction de S :
- route EUR : 2 x (frais + demi-spread + impact de profondeur) sur la paire X-EUR ;
- route USDC « par transaction » : 2 x (coût X-USDC) + 2 x (coût de conversion USDC-EUR) ;
- route USDC « capital conservé en USDC » : 2 x (coût X-USDC) + conversions amorties sur N allers-retours
  + prime de risque de change (écart-type USDC/EUR sur la durée de détention, pas un coût certain).
Les frais par défaut sont des HYPOTHÈSES (cahier des charges, 25/11/2026 : EUR 0,15 %/0,25 %, USDC 0,05 %) ;
en réel ils doivent venir de GET /account/fees. Un USDC n'est PAS une réserve en euros garantie."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class FeeSchedule:
    eur_maker: float = 0.0015
    eur_taker: float = 0.0025
    usdc_maker: float = 0.0005
    usdc_taker: float = 0.0005
    conv_taker: float = 0.0025  # hypothèse prudente pour USDC-EUR (catégorie de frais à vérifier)
    trades_per_conversion: int = 20  # route « capital en USDC » : conversions amorties


def side_cost(row: dict[str, Any], size_quote: float, fee: float) -> float | None:
    """Frais + demi-spread + impact approximatif si la taille dépasse la profondeur à 0,1 %/0,5 %/1 %."""
    if not row or not row.get("bid") or not row.get("ask"):
        return None
    hs = (row["ask"] - row["bid"]) / (row["ask"] + row["bid"])
    impact = 0.0
    d1, d5, d10 = (min(row.get(f"depth_bid_{p}", 0) or 0, row.get(f"depth_ask_{p}", 0) or 0) for p in (0.001, 0.005, 0.01))
    if d1 or d5 or d10:
        if size_quote > d10:
            return None  # profondeur insuffisante : route inexécutable à cette taille
        impact = 0.0 if size_quote <= d1 else (0.0025 if size_quote <= d5 else 0.0075)
    return fee + hs + impact


def route_costs(eur_row: dict[str, Any] | None, usdc_row: dict[str, Any] | None, conv_row: dict[str, Any] | None,
                size_eur: float, f: FeeSchedule, fx_daily_vol: float = 0.0005, hold_days: float = 3.0,
                maker: bool = False) -> dict[str, float | None]:
    fe, fu = (f.eur_maker, f.usdc_maker) if maker else (f.eur_taker, f.usdc_taker)
    ce = side_cost(eur_row or {}, size_eur, fe) if eur_row else None
    cu = side_cost(usdc_row or {}, size_eur, fu) if usdc_row else None
    cc = side_cost(conv_row or {}, size_eur, f.conv_taker) if conv_row else None
    out: dict[str, float | None] = {"eur": 2 * ce if ce is not None else None}
    if cu is not None and cc is not None:
        out["usdc_par_transaction"] = 2 * cu + 2 * cc
        out["usdc_capital_conserve"] = 2 * cu + 2 * cc / f.trades_per_conversion
        out["risque_change_1_ecart_type"] = fx_daily_vol * np.sqrt(hold_days)
    return out


def choose_route(costs: dict[str, float | None], fx_risk_aversion: float = 1.0) -> tuple[str, float]:
    """Route la moins chère ; la route « capital en USDC » est pénalisée de son risque de change."""
    cands: dict[str, float] = {}
    for k in ("eur", "usdc_par_transaction", "usdc_capital_conserve"):
        v = costs.get(k)
        if v is not None:
            cands[k] = float(v)
    if "usdc_capital_conserve" in cands:
        cands["usdc_capital_conserve"] += fx_risk_aversion * float(costs.get("risque_change_1_ecart_type") or 0)
    if not cands:
        return "aucune", float("nan")
    k = min(cands, key=lambda x: cands[x])
    return k, cands[k]


def compare_routes(rows: list[dict[str, Any]], f: FeeSchedule, fx: dict[str, float] | None,
                   sizes: tuple[float, ...] = (20, 100, 1000)) -> dict[str, Any]:
    by = {r["market"]: r for r in rows}
    conv = by.get("USDC-EUR")
    fx_vol = (fx or {}).get("vol_journaliere", 0.0005)
    bases = sorted({r["base"] for r in rows if r.get("base")})
    detail, summary = [], {}
    for size in sizes:
        wins = {"eur": 0, "usdc_par_transaction": 0, "usdc_capital_conserve": 0}
        savings = []
        for b in bases:
            e, u = by.get(f"{b}-EUR"), by.get(f"{b}-USDC")
            if not e or not u or "depth_bid_0.001" not in e or "depth_bid_0.001" not in u:
                continue
            c = route_costs(e, u, conv, size, f, fx_vol)
            best, val = choose_route(c)
            if best in wins:
                wins[best] += 1
            ce = c.get("eur")
            if ce is not None and best != "eur":
                savings.append((ce - val) * 1e4)
            if size == sizes[1]:
                detail.append({"actif": b, **{k: (round(v * 1e4, 1) if v is not None else None) for k, v in c.items()},
                               "meilleure_route": best})
        summary[f"taille_{size}_eur"] = {"marches_compares": sum(wins.values()), "victoires": wins,
                                         "economie_mediane_bps_si_usdc": float(np.median(savings)) if savings else 0.0}
    return {"hypotheses_frais": f.__dict__, "resume": summary, "detail_100_eur_bps": detail[:80],
            "conversion_usdc_eur": {k: conv.get(k) for k in ("bid", "ask", "spread_bps")} if conv else None}
