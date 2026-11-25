"""Stratégies candidates. Chaque signal à l'indice t n'utilise que les bougies <= t
et ne peut être exécuté qu'à partir de l'ouverture de la bougie t+1 (latence)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..features import indicators as ind
from ..models.regime import Regime, classify_regime


@dataclass
class Signals:
    entry: np.ndarray  # bool : intention d'achat à la clôture de t
    exit: np.ndarray  # bool : invalidation de la thèse à la clôture de t
    strength: np.ndarray  # [0,1] force normalisée (pas une probabilité)
    max_bars: int


class Strategy:
    name = "base"

    def __init__(self, params: dict[str, Any]) -> None:
        self.p = params

    def signals(self, df: pd.DataFrame) -> Signals:
        raise NotImplementedError

    @staticmethod
    def _arr(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return tuple(df[k].to_numpy(dtype=float) for k in ("close", "high", "low", "volume"))  # type: ignore[return-value]


def _clip01(x: np.ndarray) -> np.ndarray:
    return np.clip(np.nan_to_num(x, nan=0.0), 0.0, 1.0)


class Momentum(Strategy):
    name = "momentum"

    def signals(self, df: pd.DataFrame) -> Signals:
        c, h, lo, v = self._arr(df)
        lb, ex = int(self.p["lookback"]), int(self.p["exit_lookback"])
        ret = pd.Series(np.log(c)).diff(lb).to_numpy()
        if "bench_close" in df.columns:
            # rendement RELATIF (cahier des charges) : excès de rendement sur la référence (BTC)
            ret = ret - pd.Series(np.log(df["bench_close"].to_numpy(dtype=float))).diff(lb).to_numpy()
        rv = ind.rel_volume(v, int(self.p["volume_lookback"]))
        reg = classify_regime(df)
        vol = ind.realized_vol(c, lb) * np.sqrt(lb)
        with np.errstate(invalid="ignore", divide="ignore"):
            score = ret / vol
        entry = (score > 1.0) & (rv > float(self.p["min_rel_volume"])) & (reg != Regime.ANOMALY.value) & (
            reg != Regime.BEARISH.value)
        short = pd.Series(np.log(c)).diff(ex).to_numpy()
        exit_ = (short < 0) & (pd.Series(score).shift(1).to_numpy() > score) | (reg == Regime.BEARISH.value)
        return Signals(np.nan_to_num(entry).astype(bool), np.nan_to_num(exit_).astype(bool),
                       _clip01(score / 3), int(self.p["max_bars"]))


class Breakout(Strategy):
    name = "breakout"

    def signals(self, df: pd.DataFrame) -> Signals:
        c, h, lo, v = self._arr(df)
        n = int(self.p["channel"])
        hi = ind.channel_high(h, n)
        low_ch = ind.channel_low(lo, n)
        rv = ind.rel_volume(v, n)
        entry = (c > hi) & (rv > float(self.p["min_rel_volume"]))
        # Échec de cassure : retour SOUS le niveau cassé, figé au moment du signal (correction phase 2 :
        # comparer au canal courant, qui inclut la bougie de cassure, faisait sortir presque aussitôt).
        level = pd.Series(np.where(entry, hi, np.nan)).ffill().to_numpy()
        exit_ = c < level
        with np.errstate(invalid="ignore", divide="ignore"):
            strength = (c - hi) / (hi - low_ch) * 5
        return Signals(np.nan_to_num(entry).astype(bool), np.nan_to_num(exit_).astype(bool), _clip01(strength),
                       int(self.p["max_bars"]))


class MeanReversion(Strategy):
    name = "mean_reversion"

    def signals(self, df: pd.DataFrame) -> Signals:
        c, h, lo, v = self._arr(df)
        z = ind.zscore(c, int(self.p["window"]))
        a = ind.adx(h, lo, c)
        reg = classify_regime(df)
        entry = (z < float(self.p["z_entry"])) & (a < float(self.p["adx_max"])) & (reg == Regime.RANGE.value)
        exit_ = (z > float(self.p["z_exit"])) | (a > float(self.p["adx_max"]) * 1.5) | (reg == Regime.ANOMALY.value)
        return Signals(np.nan_to_num(entry).astype(bool), np.nan_to_num(exit_).astype(bool),
                       _clip01(-np.nan_to_num(z) / 4), int(self.p["max_bars"]))


class TrendFollowing(Strategy):
    name = "trend_following"

    def signals(self, df: pd.DataFrame) -> Signals:
        c, h, lo, v = self._arr(df)
        f, m, s = (ind.ema(c, int(self.p[k])) for k in ("fast", "mid", "slow"))
        slope = pd.Series(m).diff(5).to_numpy() / c
        aligned = (f > m) & (m > s) & (slope > 0)
        prev = np.roll(aligned, 1)
        prev[0] = False
        entry = aligned & ~prev
        exit_ = (f < m) & (c < m)
        return Signals(np.nan_to_num(entry).astype(bool), np.nan_to_num(exit_).astype(bool),
                       _clip01(np.nan_to_num(slope) * 200), int(self.p["max_bars"]))


class Swing(Strategy):
    name = "swing"

    def signals(self, df: pd.DataFrame) -> Signals:
        c, h, lo, v = self._arr(df)
        t = ind.ema(c, int(self.p["trend"]))
        r = ind.rsi(c, 14)
        slope = pd.Series(t).diff(10).to_numpy()
        prev_r = np.roll(r, 1)
        entry = (slope > 0) & (prev_r < float(self.p["pullback_rsi"])) & (r > prev_r) & (c > t * 0.97)
        exit_ = (r > float(self.p["exit_rsi"])) | (c < t * 0.95)
        return Signals(np.nan_to_num(entry).astype(bool), np.nan_to_num(exit_).astype(bool),
                       _clip01((float(self.p["pullback_rsi"]) - np.nan_to_num(prev_r, nan=50)) / 20 + 0.3),
                       int(self.p["max_bars"]))


class Rebound(Strategy):
    """Chute suivie de stabilisation. N'assume pas qu'un actif en baisse remontera."""

    name = "rebound"

    def signals(self, df: pd.DataFrame) -> Signals:
        c, h, lo, v = self._arr(df)
        n, k = int(self.p["drop_lookback"]), int(self.p["stabilize_bars"])
        peak = pd.Series(h).shift(k).rolling(n, min_periods=n).max().to_numpy()
        drop = 1 - c / peak
        recent_low = pd.Series(lo).rolling(k, min_periods=k).min().to_numpy()
        prior_low = pd.Series(lo).shift(k).rolling(n, min_periods=n).min().to_numpy()
        stabilized = (recent_low >= prior_low) & (c > pd.Series(c).shift(k).to_numpy())
        entry = (drop > float(self.p["min_drop"])) & stabilized
        exit_ = c < recent_low * 0.99
        return Signals(np.nan_to_num(entry).astype(bool), np.nan_to_num(exit_).astype(bool),
                       _clip01(np.nan_to_num(drop) * 3), int(self.p["max_bars"]))


class EventTrading(Strategy):
    """Inactif : aucune donnée événementielle point-in-time vérifiée n'est disponible."""

    name = "event"

    def signals(self, df: pd.DataFrame) -> Signals:
        z = np.zeros(len(df), dtype=bool)
        return Signals(z, z.copy(), np.zeros(len(df)), 1)


class Hybrid(Strategy):
    """Ensemble pondéré des stratégies retenues. Poids = fiabilité démontrée (fournis par le registre)."""

    name = "hybrid"

    def __init__(self, params: dict[str, Any], members: list[Strategy], weights: dict[str, float]) -> None:
        super().__init__(params)
        self.members = members
        self.weights = weights

    def signals(self, df: pd.DataFrame) -> Signals:
        n = len(df)
        score = np.zeros(n)
        exit_votes = np.zeros(n)
        total = sum(max(0.0, self.weights.get(m.name, 0.0)) for m in self.members)
        max_bars = 1
        if total <= 0:  # aucun membre n'a démontré de fiabilité : l'hybride n'entre jamais
            z = np.zeros(n, dtype=bool)
            return Signals(z, z.copy(), np.zeros(n), 1)
        for m in self.members:
            w = max(0.0, self.weights.get(m.name, 0.0)) / total
            if w <= 0:
                continue
            s = m.signals(df)
            max_bars = max(max_bars, s.max_bars)
            score += w * s.entry * np.maximum(s.strength, 0.25)
            exit_votes += w * s.exit
        entry = score >= float(self.params_threshold)
        return Signals(entry, exit_votes >= 0.5, _clip01(score), max_bars)

    @property
    def params_threshold(self) -> float:
        return float(self.p.get("threshold", 0.15))


class ModelStrategy(Strategy):
    """Stratégie issue d'un modèle prédictif, réentraîné périodiquement sur le PASSÉ uniquement
    (fenêtre croissante, purge = horizon). Entrée si la probabilité calibrée dépasse le taux de base récent
    d'une marge ; sortie à l'horizon. Les sorties de ce modèle ne sont JAMAIS présentées comme probabilités
    validées tant que le laboratoire ne l'a pas promu."""

    name = "modele_logistique"

    def signals(self, df: pd.DataFrame) -> Signals:
        from ..models.predict import rolling_model_probabilities

        h = int(self.p.get("horizon", 6))
        p, base = rolling_model_probabilities(df, horizon=h, cost=float(self.p.get("cost", 0.005)),
                                              retrain_every=int(self.p.get("retrain_every", 500)),
                                              min_train=int(self.p.get("min_train", 1500)))
        margin = float(self.p.get("margin", 0.05))
        entry = np.nan_to_num(p - base, nan=-1.0) > margin
        z = np.zeros(len(df), dtype=bool)
        return Signals(entry, z, _clip01(np.nan_to_num(p - base) * 5), h)


class Filtered(Strategy):
    """Enveloppe de réduction des transactions inutiles autour d'une famille :
    - `trend_ema` : entrée seulement au-dessus de la moyenne longue du marché (tendance générale) ;
    - `vol_pct` : entrée seulement si la volatilité réalisée est sous son quantile glissant (marché « calme ») ;
    - `liq_pct` : entrée seulement si le volume en devise dépasse son quantile glissant (liquidité) ;
    - `mtf_mult` : confirmation sur un horizon plus long (pente d'une moyenne `mtf_mult` fois plus lente > 0) ;
    - `min_move_cost` : le mouvement récent doit dépasser k x coût aller-retour (seuil après frais).
    Tous les filtres sont CAUSAUX (fenêtres sur le passé uniquement)."""

    def __init__(self, params: dict[str, Any], inner: Strategy) -> None:
        super().__init__(params)
        self.inner = inner
        self.name = params.get("name", f"{inner.name}_filtre")

    def signals(self, df: pd.DataFrame) -> Signals:
        s = self.inner.signals(df)
        c, h, lo, v = self._arr(df)
        ok = np.ones(len(df), dtype=bool)
        p = self.p
        if p.get("trend_ema"):
            ok &= np.nan_to_num(c > ind.ema(c, int(p["trend_ema"])), nan=0).astype(bool)
        if p.get("vol_pct"):
            rv = pd.Series(ind.realized_vol(c, 48))
            q = rv.shift(1).rolling(1000, min_periods=200).quantile(float(p["vol_pct"]))
            ok &= np.nan_to_num((rv < q).to_numpy(), nan=0).astype(bool)
        if p.get("liq_pct"):
            qv = pd.Series(v * c)
            q = qv.shift(1).rolling(1000, min_periods=200).quantile(float(p["liq_pct"]))
            ok &= np.nan_to_num((qv.rolling(24, min_periods=6).mean() > q).to_numpy(), nan=0).astype(bool)
        if p.get("mtf_mult"):
            slow = ind.ema(c, int(50 * float(p["mtf_mult"])))
            ok &= np.nan_to_num(pd.Series(slow).diff(int(p["mtf_mult"])).to_numpy() > 0, nan=0).astype(bool)
        if p.get("min_move_cost"):
            move = pd.Series(np.log(c)).diff(int(p.get("move_lookback", 12))).abs().to_numpy()
            ok &= np.nan_to_num(move > float(p["min_move_cost"]) * float(p.get("rt_cost", 0.006)), nan=0).astype(bool)
        return Signals(s.entry & ok, s.exit, s.strength, s.max_bars)


def build_strategies(cfg: dict[str, Any]) -> list[Strategy]:
    out: list[Strategy] = [Momentum(cfg["momentum"]), Breakout(cfg["breakout"]), MeanReversion(cfg["mean_reversion"]),
                           TrendFollowing(cfg["trend_following"]), Swing(cfg["swing"]), Rebound(cfg["rebound"])]
    if cfg.get("event", {}).get("enabled"):
        out.append(EventTrading(cfg["event"]))
    return out
