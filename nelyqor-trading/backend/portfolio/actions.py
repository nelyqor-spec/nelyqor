"""Comparateur systématique d'actions : acheter au marché, ordre limité, attendre, conserver, vendre totalement,
vendre partiellement, réallouer. Chaque action reçoit une espérance NETTE (frais, spread, glissement inclus),
une incertitude (erreur-type) et une borne basse prudente ; l'action retenue doit battre « rester en euros »
(espérance 0, coût d'opportunité) d'une marge d'incertitude.

Les espérances sont estimées sur les signaux PASSÉS de la même règle (non chevauchants), y compris pour
l'ordre limité : seuls les remplissages réellement possibles (traversée stricte du prix) sont comptés, et
l'espérance est calculée sur les trades REMPLIS — ce qui mesure la sélection adverse (un limité se remplit
surtout quand le prix baisse). Aucune probabilité de succès n'est inventée ni affichée."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ActionEstimate:
    action: str
    esperance: float  # rendement net moyen attendu (fraction), 0 = rester en euros
    erreur_type: float
    n: int
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def borne_basse(self) -> float:
        return self.esperance - 2 * self.erreur_type


def _stats(x: np.ndarray) -> tuple[float, float, int]:
    if len(x) < 2:
        return 0.0, float("inf"), int(len(x))
    return float(np.mean(x)), float(np.std(x, ddof=1) / np.sqrt(len(x))), int(len(x))


def entry_outcomes(df: pd.DataFrame, entry: np.ndarray, horizon: int, fee_taker: float, fee_maker: float,
                   half_spread: float, slip: float, limit_offset: float, ttl: int, through: float = 0.0002,
                   delay: int = 0) -> dict[str, np.ndarray | int]:
    """Issues historiques nettes pour : marché (ouverture t+1+delay), limité (prix = clôture t x (1-offset),
    rempli seulement si un plus bas ultérieur TRAVERSE le prix dans `ttl` bougies), sortie au même instant
    (clôture t+1+delay+horizon) pour rendre les actions comparables."""
    o, lo, c = (df[k].to_numpy(float) for k in ("open", "low", "close"))
    n = len(c)
    mkt, lim = [], []
    signals = fills = 0
    nxt = 0
    exit_cost = fee_taker + half_spread + slip
    for i in np.flatnonzero(entry):
        if i < nxt or i + 1 + delay + horizon >= n:
            continue
        signals += 1
        nxt = int(i) + horizon + 1
        ex = c[i + 1 + delay + horizon] * (1 - exit_cost)
        px_m = o[i + 1 + delay] * (1 + half_spread + slip)
        if np.isfinite(px_m) and np.isfinite(ex):
            mkt.append(ex / (px_m * (1 + fee_taker)) - 1)  # achat : prix x (1 + frais) ; vente : déjà nette
        lim_px = c[i] * (1 - limit_offset)
        for k in range(1, ttl + 1):
            j = i + k + delay
            if j >= n:
                break
            if lo[j] < lim_px * (1 - through):
                fills += 1
                lim.append(ex / (lim_px * (1 + fee_maker)) - 1)
                break
    return {"marche": np.asarray(mkt), "limite": np.asarray(lim), "signaux": signals, "remplis": fills}


def compare_entry(df: pd.DataFrame, entry: np.ndarray, horizon: int, fee_taker: float = 0.0025,
                  fee_maker: float = 0.0015, half_spread: float = 0.0005, slip: float = 0.0005,
                  limit_offset: float = 0.001, ttl: int = 2, wait_bars: int = 1) -> list[ActionEstimate]:
    now = entry_outcomes(df, entry, horizon, fee_taker, fee_maker, half_spread, slip, limit_offset, ttl)
    later = entry_outcomes(df, entry, horizon, fee_taker, fee_maker, half_spread, slip, limit_offset, ttl, delay=wait_bars)
    m, se_m, n_m = _stats(now["marche"])  # type: ignore[arg-type]
    lim = now["limite"]
    p_fill = float(now["remplis"]) / float(now["signaux"]) if now["signaux"] else 0.0  # type: ignore[arg-type]
    ml, se_l, n_l = _stats(lim)  # type: ignore[arg-type]
    w, se_w, n_w = _stats(later["marche"])  # type: ignore[arg-type]
    return [
        ActionEstimate("attendre_en_euros", 0.0, 0.0, 0, {"cout_opportunite": "référence : 0 % (euros)"}),
        ActionEstimate("acheter_marche", m, se_m, n_m),
        # limité : espérance par SIGNAL = p(remplissage) x espérance des trades remplis (non rempli = euros)
        ActionEstimate("acheter_limite", p_fill * ml, se_l * p_fill, n_l,
                       {"taux_remplissage": p_fill, "esperance_si_rempli": ml}),
        ActionEstimate(f"attendre_{wait_bars}_bougie", w, se_w, n_w),
    ]


def compare_exit(gross_hold: np.ndarray, sell_cost: float, partial_fraction: float = 0.5,
                 min_order_ok_partial: bool = True) -> list[ActionEstimate]:
    """`gross_hold` : rendements futurs historiques d'une position dans la même situation (ex. après un
    signal d'invalidation). Conserver = espérance de ces rendements ; vendre = -coût de sortie évité plus tard
    (la vente est inévitable : on compare donc « vendre maintenant » à « vendre plus tard »)."""
    h, se, n = _stats(gross_hold)
    out = [ActionEstimate("conserver", h, se, n), ActionEstimate("vendre_totalement", 0.0, 0.0, 0)]
    if min_order_ok_partial:
        out.append(ActionEstimate("vendre_partiellement", partial_fraction * h, partial_fraction * se, n,
                                  {"fraction_conservee": partial_fraction}))
    return out


def realloc_gain(held_edge: ActionEstimate, cand_edge: ActionEstimate, switch_cost: float, hysteresis: float = 0.002
                 ) -> ActionEstimate:
    """Réallouer seulement si l'avantage du candidat dépasse celui de la position + coûts de changement +
    hystérésis, avec marge d'incertitude (évite la rotation inutile)."""
    diff = cand_edge.esperance - held_edge.esperance - switch_cost - hysteresis
    se = float(np.hypot(cand_edge.erreur_type, held_edge.erreur_type))
    return ActionEstimate("reallouer", diff, se, min(cand_edge.n, held_edge.n),
                          {"cout_changement": switch_cost, "hysteresis": hysteresis})


def best_action(cands: list[ActionEstimate]) -> ActionEstimate:
    """Action à la meilleure borne basse ; « attendre en euros » / « conserver » si rien ne la dépasse."""
    safe = [c for c in cands if np.isfinite(c.borne_basse)]
    return max(safe, key=lambda c: c.borne_basse)


def pick_reallocation(held: dict[str, ActionEstimate], candidate: ActionEstimate, switch_cost: float,
                      hysteresis: float = 0.002) -> tuple[str | None, ActionEstimate | None]:
    """Position à libérer au profit du candidat (la plus faible), seulement si la borne basse du gain est > 0."""
    if not held:
        return None, None
    weakest = min(held, key=lambda k: held[k].esperance)
    g = realloc_gain(held[weakest], candidate, switch_cost, hysteresis)
    return (weakest, g) if g.borne_basse > 0 else (None, g)
