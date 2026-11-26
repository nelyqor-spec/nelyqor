"""Règles de décision directionnelles SIMPLES, PARAMÉTRÉES et CAUSALES (module expérimental, simulation seulement).

Une règle reçoit `(i, vue)` où la vue ne contient que les bougies 0..i, et renvoie {marché: LONG | SHORT | CASH}.
Un marché absent du résultat vaut CASH (abstention par défaut). Aucune probabilité, aucun score de confiance.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from .directional import CASH, LONG, SHORT, MarketView, Rule

__all__ = ["CASH", "LONG", "SHORT", "scripted_rule", "trend_rule"]


def trend_rule(fast: int = 10, slow: int = 40, band_atr: float = 0.5) -> Rule:
    """Moyennes mobiles simples des clôtures : LONG si MM_rapide > MM_lente + band_atr × ATR ET clôture > MM_lente ;
    SHORT si symétrique ; sinon CASH. Historique insuffisant ou bougie absente dans la fenêtre => CASH."""
    if not 0 < fast < slow:
        raise ValueError("il faut 0 < fast < slow")

    def rule(i: int, view: MarketView) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in view.markets:
            c = view.closes(m)
            atr = view.atr(m)
            if len(c) < slow or not np.isfinite(c[-slow:]).all() or not (np.isfinite(atr) and atr > 0):
                continue
            mf, ms = float(c[-fast:].mean()), float(c[-slow:].mean())
            if mf > ms + band_atr * atr and c[-1] > ms:
                out[m] = LONG
            elif mf < ms - band_atr * atr and c[-1] < ms:
                out[m] = SHORT
        return out

    return rule


def scripted_rule(schedule: Mapping[int, Mapping[str, str]]) -> Rule:
    """Règle scriptée pour les scénarios : le signal donné à la bougie b PERSISTE jusqu'au prochain changement.
    Causale par construction (ne lit que les entrées b ≤ i)."""
    keys = sorted(schedule)

    def rule(i: int, view: MarketView) -> dict[str, str]:
        cur: dict[str, str] = {}
        for b in keys:
            if b > i:
                break
            cur.update(schedule[b])
        return cur

    return rule
