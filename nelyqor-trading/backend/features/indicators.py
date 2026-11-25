"""Indicateurs techniques causaux : la valeur à l'indice t n'utilise que les données <= t.

Toutes les fonctions opèrent sur des tableaux numpy float (analyse statistique, pas comptabilité).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

Arr = np.ndarray


def ema(x: Arr, span: int) -> Arr:
    return pd.Series(x).ewm(span=span, adjust=False, min_periods=span).mean().to_numpy()


def sma(x: Arr, n: int) -> Arr:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def rolling_std(x: Arr, n: int) -> Arr:
    return pd.Series(x).rolling(n, min_periods=n).std(ddof=1).to_numpy()


def rsi(close: Arr, n: int = 14) -> Arr:
    d = np.diff(close, prepend=np.nan)
    up = pd.Series(np.where(d > 0, d, 0.0))
    dn = pd.Series(np.where(d < 0, -d, 0.0))
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[ad == 0] = 100.0
    out.iloc[:n] = np.nan
    return out.to_numpy()


def macd(close: Arr, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Arr, Arr, Arr]:
    m = ema(close, fast) - ema(close, slow)
    s = pd.Series(m).ewm(span=signal, adjust=False).mean().to_numpy()
    return m, s, m - s


def true_range(high: Arr, low: Arr, close: Arr) -> Arr:
    pc = np.roll(close, 1)
    pc[0] = np.nan
    tr = np.nanmax(np.vstack([high - low, np.abs(high - pc), np.abs(low - pc)]), axis=0)
    tr[0] = high[0] - low[0]
    return tr


def atr(high: Arr, low: Arr, close: Arr, n: int = 14) -> Arr:
    return pd.Series(true_range(high, low, close)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def adx(high: Arr, low: Arr, close: Arr, n: int = 14) -> Arr:
    up = np.diff(high, prepend=np.nan)
    dn = -np.diff(low, prepend=np.nan)
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.Series(true_range(high, low, close)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * pd.Series(pdm).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    ndi = 100 * pd.Series(ndm).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def bollinger(close: Arr, n: int = 20, k: float = 2.0) -> tuple[Arr, Arr, Arr]:
    m = sma(close, n)
    s = rolling_std(close, n)
    return m - k * s, m, m + k * s


def rolling_vwap(close: Arr, volume: Arr, n: int) -> Arr:
    pv = pd.Series(close * volume).rolling(n, min_periods=n).sum()
    v = pd.Series(volume).rolling(n, min_periods=n).sum().replace(0, np.nan)
    return (pv / v).to_numpy()


def log_returns(close: Arr) -> Arr:
    r = np.diff(np.log(close), prepend=np.nan)
    return r


def realized_vol(close: Arr, n: int) -> Arr:
    return pd.Series(log_returns(close)).rolling(n, min_periods=n).std(ddof=1).to_numpy()


def rel_volume(volume: Arr, n: int) -> Arr:
    """Volume courant / moyenne des n bougies PRÉCÉDENTES (exclut la bougie courante)."""
    prev = pd.Series(volume).shift(1).rolling(n, min_periods=n).mean().replace(0, np.nan)
    return (pd.Series(volume) / prev).to_numpy()


def channel_high(high: Arr, n: int) -> Arr:
    """Plus haut des n bougies antérieures (exclut la bougie courante : pas de fuite)."""
    return pd.Series(high).shift(1).rolling(n, min_periods=n).max().to_numpy()


def channel_low(low: Arr, n: int) -> Arr:
    return pd.Series(low).shift(1).rolling(n, min_periods=n).min().to_numpy()


def zscore(close: Arr, n: int) -> Arr:
    m = sma(close, n)
    s = rolling_std(close, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (close - m) / s


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Matrice de caractéristiques tabulaires (toutes causales)."""
    c, h, lo, v = (df[k].to_numpy(dtype=float) for k in ("close", "high", "low", "volume"))
    f = pd.DataFrame(index=df.index)
    for n in (1, 3, 6, 12, 24, 72):
        f[f"ret_{n}"] = pd.Series(np.log(c)).diff(n).to_numpy()
    for span in (9, 21, 50, 200):
        f[f"ema_gap_{span}"] = c / ema(c, span) - 1
    f["rsi_14"] = rsi(c, 14)
    _, _, hist = macd(c)
    f["macd_hist_n"] = hist / c
    f["atr_n"] = atr(h, lo, c) / c
    f["adx_14"] = adx(h, lo, c)
    lb, mb, ub = bollinger(c)
    with np.errstate(invalid="ignore", divide="ignore"):
        f["bb_pos"] = (c - lb) / (ub - lb)
    f["vol_24"] = realized_vol(c, 24)
    f["relvol_24"] = rel_volume(v, 24)
    f["vwap_gap_24"] = c / rolling_vwap(c, v, 24) - 1
    f["z_48"] = zscore(c, 48)
    return f
