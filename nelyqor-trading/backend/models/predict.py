"""Prévisions probabilistes : référence logistique, LightGBM optionnel, calibration et score de Brier.

Une probabilité n'est affichée comme telle que si le modèle est calibré sur un jeu distinct
ET que son Brier hors échantillon bat la référence « taux de base ». Sinon : « estimation non calibrée ».
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..features.indicators import feature_frame


def make_target(close: pd.Series, horizon: int, cost: float) -> pd.Series:
    """1 si le rendement futur à `horizon` bougies dépasse le coût aller-retour. NaN en fin de série."""
    fwd = close.shift(-horizon) / close - 1
    y = (fwd > cost).astype(float)
    y[fwd.isna()] = np.nan
    return y


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum():
            out.append({"bin": float(lo), "n": int(m.sum()), "pred": float(p[m].mean()), "obs": float(y[m].mean())})
    return out


@dataclass
class ModelReport:
    name: str
    horizon: int
    n_train: int
    n_test: int
    brier: float
    brier_base: float
    calibrated: bool
    calibration: list[dict[str, float]] = field(default_factory=list)
    sha256: str = ""

    @property
    def label(self) -> str:
        return "probabilité calibrée" if self.calibrated else "estimation non calibrée"


def _model(kind: str, seed: int) -> Any:
    if kind == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=500))
    if kind == "lightgbm":
        import lightgbm as lgb

        return lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=50,
                                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=seed,
                                  verbose=-1)
    raise ValueError(kind)


def fit_and_evaluate(df: pd.DataFrame, horizon: int, cost: float, kind: str = "logistic", seed: int = 7,
                     purge: int | None = None) -> tuple[ModelReport, Any]:
    """Découpage chronologique : 60 % entraînement | purge | 20 % calibration | purge | 20 % test."""
    X = feature_frame(df)
    y = make_target(df["close"].astype(float), horizon, cost)
    ok = X.notna().all(axis=1) & y.notna()
    X, y = X[ok], y[ok]
    n = len(X)
    gap = purge if purge is not None else horizon
    a, b = int(n * 0.6), int(n * 0.8)
    Xtr, ytr = X.iloc[: a - gap], y.iloc[: a - gap]
    Xca, yca = X.iloc[a: b - gap], y.iloc[a: b - gap]
    Xte, yte = X.iloc[b:], y.iloc[b:]
    if len(Xtr) < 200 or len(Xte) < 50 or ytr.nunique() < 2 or yca.nunique() < 2:
        raise ValueError("données insuffisantes pour entraîner/valider")
    base = _model(kind, seed).fit(Xtr, ytr)
    cal = _calibrate(base, Xca, yca)
    p = cal.predict_proba(Xte)[:, 1]
    brier = float(brier_score_loss(yte, p))
    base_rate = float(ytr.mean())
    brier_base = float(brier_score_loss(yte, np.full(len(yte), base_rate)))
    digest = hashlib.sha256(json.dumps({"kind": kind, "h": horizon, "seed": seed, "n": n}).encode()).hexdigest()
    rep = ModelReport(kind, horizon, len(Xtr), len(Xte), brier, brier_base, brier < brier_base,
                      calibration_table(yte.to_numpy(), p), digest)
    return rep, cal


class _Calibrated:
    """Calibration de Platt sur un jeu distinct de l'entraînement (pas de réajustement du modèle de base)."""

    def __init__(self, base: Any, a: LogisticRegression) -> None:
        self.base, self.a = base, a

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.base.predict_proba(X)[:, 1].reshape(-1, 1)
        return self.a.predict_proba(_logit(raw))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _calibrate(base: Any, X: pd.DataFrame, y: pd.Series) -> _Calibrated:
    raw = base.predict_proba(X)[:, 1].reshape(-1, 1)
    lr = LogisticRegression().fit(_logit(raw), y)
    return _Calibrated(base, lr)


def anomaly_scores(df: pd.DataFrame, seed: int = 7, train_frac: float = 0.6) -> np.ndarray:
    """Isolation Forest entraînée sur le passé uniquement ; score [0,1] (1 = très anormal)."""
    X = feature_frame(df)[["ret_1", "atr_n", "relvol_24", "vol_24"]].replace([np.inf, -np.inf], np.nan)
    ok = X.notna().all(axis=1).to_numpy()
    out = np.zeros(len(df))
    idx = np.flatnonzero(ok)
    if len(idx) < 300:
        return out
    cut = idx[int(len(idx) * train_frac)]
    tr = X.iloc[: cut][ok[:cut]]
    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=seed).fit(tr)
    s = -iso.score_samples(X[ok])
    lo, hi = np.percentile(-iso.score_samples(tr), [50, 99.5])
    out[ok] = np.clip((s - lo) / max(hi - lo, 1e-9), 0, 1)
    out[: cut] = 0.0  # pas de score sur la période d'entraînement (évite l'auto-évaluation)
    return out
