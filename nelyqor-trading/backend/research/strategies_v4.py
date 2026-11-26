"""Stratégies et références pour le moteur v4 (phase 5). Décision d'investir, sélection des actifs et montant sont
trois étapes SÉPARÉES et journalisées :

  decide(i)  -> exposition cible e ∈ [0, 1] (régime : btc | largeur | aucun)  — aucune probabilité inventée
  select(i)  -> classement des marchés admissibles (score causal du panneau v3), avec hystérésis (top N + tampon)
  size(i)    -> poids par marché : (1 − réserve) × e / N, plafonné, réduit au-delà d'un risque de 45/100
               (règle utilisateur), option volatilité cible ; le minimum d'ordre est appliqué par le moteur.

Toutes les caractéristiques à t n'utilisent que les bougies ≤ t (panneau `build_panel_v3`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .xsection import XSConfig, eligible, risk_score_vol
from .xsection_v3 import build_panel_v3, regime_exposures_v3
from .xsection_v4 import ACTIF, RULE_MS, Engine, aggregate, align


def prepare(raw_1h: dict[str, pd.DataFrame], interval: str, asof_ms: int | None = None) -> tuple[np.ndarray, dict[str, pd.DataFrame]]:
    """Agrégation v4 de tous les marchés avec la MÊME date d'arrêt (dernière heure écoulée sur l'ensemble)."""
    if not raw_1h:
        return np.array([], dtype=np.int64), {}
    asof = asof_ms if asof_ms is not None else max(int(d.index.max()) for d in raw_1h.values() if len(d)) + 3_600_000
    agg = {m: aggregate(d, interval, asof) if interval != "1h" else d[d.index + 3_600_000 <= asof] for m, d in raw_1h.items()}
    agg = {m: d for m, d in agg.items() if len(d)}
    return align(agg, RULE_MS[interval])


@dataclass
class RotationParams:
    top_n: int = 3
    buffer: int = 2
    rebalance_bars: int = 6
    phase: int = 0
    min_hold_bars: int = 0
    reserve: float = 0.10
    max_weight: float = 0.35
    risk_cut: float = 45.0  # règle utilisateur : réduction au-delà de 45/100
    regime: str = "btc"  # btc | largeur | aucun
    exit_on_risk_off: bool = False
    sizing: str = "egal"  # egal | volatilite
    target_daily_vol: float = 0.04


class Rotation:
    def __init__(self, data: dict[str, pd.DataFrame], cfg: XSConfig, p: RotationParams) -> None:
        self.cfg, self.p = cfg, p
        self.P = build_panel_v3(data, cfg)
        ex = regime_exposures_v3(self.P, cfg)
        n = len(self.P["close"])
        self.expo = {"btc": ex["btc"], "largeur": ex["btc"] * ex["proportion_haussiere"], "aucun": np.ones(n)}[p.regime]
        self.bpd = 86_400_000 / float(np.median(np.diff(np.asarray(self.P["close"].index, dtype=np.int64))))
        self.journal: list[dict[str, Any]] = []

    # 1) décision d'investir
    def decide(self, i: int) -> float:
        return float(np.nan_to_num(self.expo[i]))

    # 2) sélection
    def select(self, i: int, eng: Engine) -> list[str]:
        ok = eligible(self.P, self.cfg, i) & pd.Series(eng.state[i] == ACTIF, index=self.P["close"].columns)
        sc = self.P["score"].iloc[i].where(ok).dropna().sort_values(ascending=False)
        return [m for m, v in sc.items() if v > self.cfg.min_score]

    # 3) montant
    def risk(self, i: int, m: str) -> float:
        rv = float(self.P["rv"][m].iloc[i])
        return risk_score_vol(rv, self.bpd) if np.isfinite(rv) else 100.0

    def size(self, i: int, m: str, e: float) -> float:
        p = self.p
        w = min(p.max_weight, (1 - p.reserve) * e / p.top_n)
        rv = float(self.P["rv"][m].iloc[i])
        daily = rv * np.sqrt(self.bpd) if np.isfinite(rv) else np.nan
        if p.sizing == "volatilite" and daily > 0:
            w *= float(np.clip(p.target_daily_vol / daily, 0.0, 1.0))
        rs = self.risk(i, m)
        if rs > p.risk_cut:
            w *= max(0.0, 1 - (rs - p.risk_cut) / (100 - p.risk_cut)) ** 2
        return float(w)

    def __call__(self, i: int, eng: Engine) -> dict[str, float]:
        p = self.p
        if (i - p.phase) % p.rebalance_bars != 0 or i < p.phase:
            return {}
        e = self.decide(i)
        ranked = self.select(i, eng)
        keep_zone = set(ranked[: p.top_n + p.buffer])
        targets: dict[str, float] = {}
        kept: list[str] = []
        for m in eng.held():
            held_bars = i - int(eng.entry_i[eng.j[m]])
            if held_bars >= p.min_hold_bars and (m not in keep_zone or (p.exit_on_risk_off and e <= 0)):
                targets[m] = 0.0
                continue
            kept.append(m)
            # S4 : réduction SEULEMENT (jamais de renfort) d'une position conservée dont le risque dépasse 45/100
            if held_bars >= p.min_hold_bars and self.risk(i, m) > p.risk_cut:
                w_t = self.size(i, m, max(e, 0.0))
                if eng.weight(m) > w_t + 0.02:
                    targets[m] = w_t
        if e > 0:
            # S1 : nouvelles entrées limitées aux places libres (top_n − positions conservées) et au budget restant
            budget = (1 - p.reserve) * e - sum(targets.get(m, eng.weight(m)) for m in kept)
            slots = p.top_n - len(kept)
            for m in ranked[: p.top_n]:
                if slots <= 0 or budget <= 0:
                    break
                if m not in eng.held():
                    w = min(self.size(i, m, e), budget)
                    targets[m] = w
                    budget -= w
                    slots -= 1
        self.journal.append({"i": i, "exposition": e, "classement": ranked[: p.top_n + p.buffer], "cibles": targets})
        return targets


# --------------------------------------------------------------------------------------------- références
def ref_buy_hold(eng: Engine, market: str) -> Any:
    done = {"x": False}

    def s(i: int, e: Engine) -> dict[str, float]:
        jj = e.j[market]
        if e.qty[jj] <= 0 and e.state[i, jj] == ACTIF and not done["x"]:  # S2 : « fait » = position réellement détenue
            return {market: 0.999}
        if e.qty[jj] > 0:
            done["x"] = True
        return {}
    return s


def ref_btc_trend(P: dict[str, pd.DataFrame], cfg: XSConfig, market: str = "BTC-EUR", rebalance_bars: int = 1) -> Any:
    ema = P["close"][market].ewm(span=cfg.regime_ema, adjust=False, min_periods=cfg.regime_ema).mean()
    cl = P["close"][market].to_numpy(float)
    on = (P["close"][market] > ema).to_numpy()

    def s(i: int, e: Engine) -> dict[str, float]:
        if i % rebalance_bars or not np.isfinite(cl[i]):  # S3 : prix absent -> aucune décision (pas une sortie)
            return {}
        return {market: 0.999 if on[i] else 0.0}
    return s


def ref_constant(market: str, weight: float) -> Any:
    def s(i: int, e: Engine) -> dict[str, float]:
        return {market: weight} if i == 0 or (i % 24 == 0 and abs(e.weight(market) - weight) > 0.05) else {}
    return s


def ref_equal_weight(P: dict[str, pd.DataFrame], cfg: XSConfig, n: int = 5, every_bars: int = 720) -> Any:
    """N marchés les plus liquides À LA DATE (point-in-time), équipondérés, rééquilibrés tous les `every_bars`."""
    def s(i: int, e: Engine) -> dict[str, float]:
        if i % every_bars:
            return {}
        ok = eligible(P, cfg, i)
        top = P["liq"].iloc[i].where(ok).dropna().nlargest(n).index
        tg = {m: 0.0 for m in e.held() if m not in top}
        tg.update({m: 0.98 / max(1, len(top)) for m in top})
        return tg
    return s
