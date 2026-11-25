"""Agrégation CAUSALE des bougies (1 h -> 2 h/4 h/8 h/12 h/1 j ; 15 min -> 30 min).
Une bougie agrégée n'est produite qu'à partir de bougies toutes antérieures ou égales à sa clôture ; une bougie
agrégée INCOMPLÈTE (trou dans les données) est laissée NaN, jamais reconstituée."""
from __future__ import annotations

import numpy as np
import pandas as pd


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if len(df) < 2:
        return df.copy()
    idx = pd.to_datetime(df.index, unit="ms", utc=True)
    d = df[["open", "high", "low", "close", "volume"]].set_axis(idx)
    agg = d.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = d["close"].resample(rule, label="left", closed="left").count()
    step = float(np.median(np.diff(df.index.to_numpy())))
    expected = pd.Timedelta(rule) / pd.Timedelta(step, unit="ms")
    agg[counts < expected] = np.nan
    # unité explicite : pandas 3 stocke en microsecondes (diviser par 10**6 donnait des SECONDES — défaut phase 2)
    agg.index = pd.Index(agg.index.as_unit("ms").astype("int64"), dtype="int64")
    agg.attrs = dict(df.attrs)
    return agg
