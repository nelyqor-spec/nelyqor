"""Corrections méthodologiques (phase 4, contre-expertise quantitative) — SANS modifier les moteurs figés.

`xsection.py` (gel v1) et `xsection_v2.py` (gel v2) ne doivent plus changer. Les défauts relevés sont corrigés
EN AMONT, sur les données et le panneau passés au moteur `run_xs2` inchangé :

1. Décote de radiation erronée en fin d'échantillon : la dernière bougie agrégée est presque toujours
   incomplète (donc NaN, agrégation stricte) ; le moteur croyait alors TOUS les marchés radiés et vendait les
   positions ouvertes à −50 %. `fix_tail` retire les lignes finales incomplètes et, pour un marché simplement
   sans transaction récente (dernière bougie < `gap_days`), reporte son dernier prix sur la dernière ligne
   (volume nul) : seule une vraie disparition (absence prolongée) reste traitée comme une radiation.
2. Agrégation stricte = filtre de liquidité caché : une seule heure sans transaction rendait la volatilité NaN
   pendant 14 jours (fenêtre exigeant toutes ses observations), d'où exclusion et vente forcée. `build_panel_v3`
   calcule les rendements depuis le dernier prix connu et exige seulement un quart de la fenêtre.
3. `proportion_haussiere` comptait une bougie absente comme « baissière » ; `correlation_basse` choisissait ses
   marchés sur l'échantillon COMPLET (information future). `regime_exposures_v3` corrige les deux.
4. Kelly fractionné = filtre de momentum absolu 30 j : `EGAL_FILTRE_30J` (Kelly avec fraction quasi infinie,
   donc plafonné au poids égal) isole l'effet du filtre de l'effet de taille.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .xsection import XSConfig, build_panel
from .xsection_v2 import XS2Options, btc_regime

EGAL_FILTRE_30J = {"sizing": "kelly", "kelly_fraction": 1e9}  # = poids égal, entrée seulement si moyenne 30 j > 0


def fix_tail(data: dict[str, pd.DataFrame], gap_days: float = 7.0, min_share: float = 0.5) -> dict[str, pd.DataFrame]:
    if not data:
        return data
    idx = next(iter(data.values())).index
    step = float(np.median(np.diff(np.asarray(idx, dtype=np.int64)))) if len(idx) > 1 else 3_600_000.0
    cnt = pd.DataFrame({m: d["close"].notna() for m, d in data.items()}).sum(axis=1).to_numpy()
    keep = int(np.flatnonzero(cnt >= min_share * cnt.max())[-1]) + 1 if cnt.max() > 0 else len(idx)
    gap = max(1, int(gap_days * 86_400_000 / step))
    out = {}
    for m, d in data.items():
        d = d.iloc[:keep].copy()
        lv = d["close"].last_valid_index()
        if lv is not None and pd.isna(d["close"].iloc[-1]):
            pos = d.index.get_loc(lv)
            if len(d) - 1 - int(pos) <= gap:  # pas de transaction récente : ce n'est pas une radiation
                px = float(d["close"].loc[lv])
                d.iloc[-1, d.columns.get_indexer(["open", "high", "low", "close"])] = px
                d.iloc[-1, d.columns.get_loc("volume")] = 0.0
        out[m] = d
    return out


def build_panel_v3(data: dict[str, pd.DataFrame], cfg: XSConfig, min_frac: float = 0.25) -> dict[str, pd.DataFrame]:
    """Panneau v1 + volatilité/rendements robustes aux bougies absentes (rendement depuis le dernier prix
    connu ; fenêtres exigeant `min_frac` des observations au lieu de toutes). Causal."""
    P = build_panel(data, cfg)
    close = P["close"]
    logc = np.log(close.ffill())
    ret1 = logc.diff().where(close.notna())
    mp = max(3, int(cfg.vol_lookback * min_frac))
    rv = ret1.rolling(cfg.vol_lookback, min_periods=mp).std()
    downside = ret1.clip(upper=0).rolling(cfg.vol_lookback, min_periods=mp).std()
    qvol = P["qvol"]
    rel_vol = qvol / qvol.shift(1).rolling(cfg.vol_lookback, min_periods=mp).mean()
    mom = logc.diff(cfg.mom_lookback).where(close.notna())
    mom_rel = mom.sub(mom.median(axis=1), axis=0)

    def z(df: pd.DataFrame) -> pd.DataFrame:
        df = df.replace([np.inf, -np.inf], np.nan)
        mu, sd = df.mean(axis=1), df.std(axis=1).replace(0, np.nan)
        zz = df.sub(mu, axis=0).div(sd, axis=0).clip(-3, 3)
        return zz.where(~(sd.isna().to_numpy()[:, None] & df.notna().to_numpy()), 0.0)

    w = cfg.weights
    score = (w.get("mom", 0) * z(mom_rel) + w.get("trend", 0) * z(P["trend"]) + w.get("volume", 0) * z(np.log(rel_vol))
             + w.get("vol", 0) * z(rv) + w.get("downside", 0) * z(downside))
    P.update({"rv": rv, "mom": mom, "score": score})
    return P


def regime_exposures_v3(P: dict[str, pd.DataFrame], cfg: XSConfig, btc: str = "BTC-EUR") -> dict[str, np.ndarray]:
    close = P["close"]
    lb = cfg.regime_ema
    ok = (P["age"] >= cfg.min_age_bars) & (P["liq"] >= cfg.min_liquidity_quote) & close.notna()
    ema = close.ffill().ewm(span=cfg.trend_slow, adjust=False, min_periods=cfg.trend_slow).mean()
    breadth = (close > ema).where(ok).mean(axis=1)
    r1 = np.log(close.ffill()).diff().where(ok)
    mkt = np.exp(r1.mean(axis=1).fillna(0).cumsum())
    win = max(10, cfg.vol_lookback)
    # corrélation moyenne implicite sur les 30 marchés les plus présents sur la fenêtre PASSÉE seulement
    present = ok.astype(float).rolling(win * 3, min_periods=win).sum()
    rank = present.rank(axis=1, ascending=False, method="first")
    x = r1.where(rank <= 30)
    var_idx = x.mean(axis=1).rolling(win, min_periods=win // 2).var()
    var_ind = x.rolling(win, min_periods=win // 2).var().mean(axis=1)
    k = x.notna().sum(axis=1).clip(lower=2)
    corr = ((var_idx * k * k / var_ind - k) / (k * (k - 1))).clip(-1, 1)
    return {
        "btc": btc_regime(P, cfg, btc) if btc in close else np.ones(len(close)),
        "tendance_marche": (mkt > mkt.ewm(span=lb, adjust=False, min_periods=lb).mean()).to_numpy(float),
        "proportion_haussiere": (breadth > 0.5).to_numpy(float),
        "correlation_basse": (~(corr > corr.rolling(lb * 3, min_periods=lb).quantile(0.7))).to_numpy(float),
    }


def radiations_at_end(r: object) -> int:
    """Nombre de ventes « radiation » sur la dernière bougie (symptôme du défaut n° 1)."""
    eq = getattr(r, "equity")  # noqa: B009
    n_last = len(eq) - 1
    return sum(1 for t in getattr(r, "trades") if t.get("reason") == "radiation" and t["exit_i"] >= n_last - 1)  # noqa: B009


__all__ = ["EGAL_FILTRE_30J", "XS2Options", "build_panel_v3", "fix_tail", "radiations_at_end", "regime_exposures_v3"]
