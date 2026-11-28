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


def make_target(close: pd.Series, horizon: int, cost: float, open_: pd.Series | None = None) -> pd.Series:
    """1 si le rendement EXÉCUTABLE dépasse le coût aller-retour : achat à l'ouverture t+1 (le signal est
    connu à la clôture t), revente à la clôture t+horizon. NaN en fin de série.
    (Version initiale : clôture t -> clôture t+h, prix non exécutable : léger biais d'anticipation.)"""
    entry = open_.shift(-1) if open_ is not None else close
    fwd = close.shift(-horizon) / entry - 1
    y = (fwd > cost).astype(float)
    y[fwd.isna()] = np.nan
    return y


def brier_skill_ci(y: np.ndarray, p: np.ndarray, p_ref: np.ndarray, block: int = 24, n: int = 1000,
                   seed: int = 0) -> tuple[float, float, float]:
    """Score de compétence de Brier (1 - B/B_ref) et IC 95 % par bootstrap par blocs (dépendance temporelle)."""
    e, e0 = (p - y) ** 2, (p_ref - y) ** 2
    skill = 1 - e.mean() / e0.mean() if e0.mean() > 0 else 0.0
    rng = np.random.default_rng(seed)
    m = len(y)
    b = max(1, min(block, m // 4))
    nb = int(np.ceil(m / b))
    out = np.empty(n)
    for k in range(n):
        idx = np.concatenate([np.arange(s, s + b) for s in rng.integers(0, m - b + 1, nb)])[:m]
        d0 = e0[idx].mean()
        out[k] = 1 - e[idx].mean() / d0 if d0 > 0 else 0.0
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(skill), float(lo), float(hi)


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
    skill: float = 0.0
    skill_ci: tuple[float, float] = (0.0, 0.0)

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
    y = make_target(df["close"].astype(float), horizon, cost, df["open"].astype(float))
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
    # Référence équitable : taux de base de la fenêtre la plus récente connue (calibration), pas l'entraînement.
    base_rate = float(yca.mean())
    ref = np.full(len(yte), base_rate)
    brier_base = float(brier_score_loss(yte, ref))
    skill, lo, hi = brier_skill_ci(yte.to_numpy(), p, ref, block=max(24, horizon))
    digest = hashlib.sha256(json.dumps({"kind": kind, "h": horizon, "seed": seed, "n": n}).encode()).hexdigest()
    # « calibré » exige une compétence STATISTIQUEMENT positive (borne basse de l'IC > 0), pas un écart au hasard
    rep = ModelReport(kind, horizon, len(Xtr), len(Xte), brier, brier_base, lo > 0,
                      calibration_table(yte.to_numpy(), p), digest, skill, (lo, hi))
    return rep, cal


def walk_forward_brier(df: pd.DataFrame, horizon: int, cost: float, kind: str = "logistic", folds: int = 4,
                       seed: int = 7) -> list[dict[str, float]]:
    """Stabilité : compétence de Brier sur des fenêtres successives (fenêtre croissante)."""
    out = []
    n = len(df)
    for k in range(folds):
        end = int(n * (0.55 + 0.45 * (k + 1) / folds))
        try:
            rep, _ = fit_and_evaluate(df.iloc[:end], horizon, cost, kind, seed)
        except ValueError:
            continue
        out.append({"fin_fenetre": int(df.index[end - 1]), "skill": rep.skill, "ic_bas": rep.skill_ci[0],
                    "ic_haut": rep.skill_ci[1], "n_test": rep.n_test})
    return out


def rolling_model_probabilities(df: pd.DataFrame, horizon: int, cost: float, retrain_every: int = 500,
                                min_train: int = 1500, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Probabilités hors échantillon produites comme en production : à l'instant t, le modèle n'a été
    entraîné que sur des étiquettes CONNUES en t (indices <= t - horizon - 1). Renvoie (p, taux_de_base)."""
    X = feature_frame(df).to_numpy(dtype=float)
    y = make_target(df["close"].astype(float), horizon, cost, df["open"].astype(float)).to_numpy()
    n = len(df)
    p = np.full(n, np.nan)
    base = np.full(n, np.nan)
    ok_x = ~np.isnan(X).any(axis=1)
    for t0 in range(min_train, n, retrain_every):
        known = np.arange(0, max(0, t0 - horizon - 1))
        known = known[ok_x[known] & ~np.isnan(y[known])]
        if len(known) < 300 or len(np.unique(y[known])) < 2:
            continue
        cut = int(len(known) * 0.8)
        tr, ca = known[:max(1, cut - horizon)], known[cut:]
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[ca])) < 2:
            continue
        mdl = _model("logistic", seed).fit(X[tr], y[tr])
        raw_ca = mdl.predict_proba(X[ca])[:, 1].reshape(-1, 1)
        platt = LogisticRegression().fit(_logit(raw_ca), y[ca])
        sl = np.arange(t0, min(n, t0 + retrain_every))
        sl = sl[ok_x[sl]]
        if len(sl):
            p[sl] = platt.predict_proba(_logit(mdl.predict_proba(X[sl])[:, 1].reshape(-1, 1)))[:, 1]
            base[sl] = float(y[ca].mean())
    return p, base


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
