"""Régimes de marché par règles robustes (première version, avant tout modèle appris)."""
from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd

from ..features import indicators as ind


class Regime(Enum):
    BULLISH = "haussier"
    BEARISH = "baissier"
    RANGE = "lateral"
    VOLATILE = "volatil"
    ANOMALY = "anomalie"
    UNKNOWN = "inconnu"


def classify_regime(df: pd.DataFrame, fast: int = 50, slow: int = 200, vol_n: int = 48) -> np.ndarray:
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    ef, es = ind.ema(c, fast), ind.ema(c, slow)
    a = ind.adx(h, lo, c)
    vol = ind.realized_vol(c, vol_n)
    vol_ref = pd.Series(vol).shift(1).rolling(500, min_periods=100).median().to_numpy()
    r1 = np.abs(ind.log_returns(c))
    r_ref = pd.Series(r1).shift(1).rolling(200, min_periods=50).std().to_numpy()
    rv = ind.rel_volume(v, 48)
    out = np.full(len(c), Regime.UNKNOWN.value, dtype=object)
    with np.errstate(invalid="ignore"):
        anomaly = (r1 > 8 * r_ref) | (rv > 15)
        volatile = vol > 2.0 * vol_ref
        bull = (ef > es) & (a > 20) & (c > ef)
        bear = (ef < es) & (a > 20) & (c < ef)
        rng = a <= 20
    out[rng] = Regime.RANGE.value
    out[bull] = Regime.BULLISH.value
    out[bear] = Regime.BEARISH.value
    out[volatile] = Regime.VOLATILE.value
    out[anomaly] = Regime.ANOMALY.value
    out[np.isnan(es) | np.isnan(a)] = Regime.UNKNOWN.value
    return out
