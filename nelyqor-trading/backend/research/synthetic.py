"""Données SYNTHÉTIQUES pour tests et démonstration. Elles ne prouvent RIEN sur les marchés réels.

Modèle : régimes markoviens (haussier/baissier/latéral/volatil) + sauts, volume corrélé à |rendement|.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SYNTHETIC_LABEL = "SYNTHETIQUE (fixture, aucune valeur probante)"


def synthetic_candles(n: int = 6000, seed: int = 0, start_ms: int = 1_700_000_000_000, step_ms: int = 3_600_000,
                      p0: float = 50000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    mus = np.array([0.0004, -0.0004, 0.0, 0.0])
    sig = np.array([0.008, 0.010, 0.005, 0.02])
    P = np.array([[0.995, 0.002, 0.002, 0.001], [0.002, 0.995, 0.002, 0.001],
                  [0.002, 0.002, 0.995, 0.001], [0.01, 0.01, 0.01, 0.97]])
    s = 2
    r = np.empty(n)
    for i in range(n):
        s = rng.choice(4, p=P[s])
        r[i] = rng.normal(mus[s], sig[s]) + (rng.normal(0, 0.05) if rng.random() < 0.002 else 0.0)
    close = p0 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[p0], close[:-1]])
    wig = np.abs(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + wig)
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol = (50 + 4000 * np.abs(r)) * rng.lognormal(0, 0.3, n)
    idx = pd.Index(start_ms + step_ms * np.arange(n), name="ts")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def synthetic_universe(markets: list[str], n: int = 6000, seed: int = 0) -> dict[str, pd.DataFrame]:
    return {m: synthetic_candles(n, seed + k, p0=[50000, 3000, 150, 0.5, 20][k % 5]) for k, m in enumerate(markets)}
