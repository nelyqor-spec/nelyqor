"""Walk-forward chronologique avec purge, holdout final verrouillé, tests de robustesse et journal d'expériences."""
from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from ..core.clock import new_id
from ..models.regime import classify_regime
from ..storage.db import Database
from .backtest import BTConfig, BTResult, buy_and_hold, run_backtest
from .metrics import Metrics, compute_metrics, deflated_sharpe, evaluate_promotion


def code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,  # noqa: S603,S607
                              timeout=5).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def slice_data(data: dict[str, pd.DataFrame], a: int, b: int) -> dict[str, pd.DataFrame]:
    return {m: df.iloc[a:b] for m, df in data.items()}


@dataclass
class Split:
    train: tuple[int, int]
    test: tuple[int, int]


def walk_forward_splits(n: int, n_folds: int, holdout_frac: float, purge: int, min_train: int) -> tuple[list[Split], tuple[int, int]]:
    """Fenêtre d'entraînement croissante, tests successifs non chevauchants ; holdout = fin de série, intouchée."""
    hold_start = int(n * (1 - holdout_frac))
    usable = hold_start - purge
    test_len = (usable - min_train) // n_folds
    if test_len <= 0:
        raise ValueError("historique insuffisant pour le walk-forward demandé")
    splits = []
    for k in range(n_folds):
        te_a = min_train + k * test_len
        splits.append(Split((0, te_a - purge), (te_a, te_a + test_len)))
    return splits, (hold_start, n)


def _grid(base: dict[str, Any], grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    out = []
    for vals in itertools.product(*(grid[k] for k in keys)):
        p = dict(base)
        p.update(dict(zip(keys, vals, strict=True)))
        out.append(p)
    return out or [dict(base)]


def _score(m: Metrics) -> float:
    # Sélection sur l'entraînement : rendement pénalisé par le drawdown ; peu de trades => score nul.
    if m.n_trades < 5:
        return -1e9
    return m.net_return + 0.5 * m.max_drawdown


def periods_per_year(index: pd.Index) -> float:
    if len(index) < 2:
        return 0.0
    step_ms = float(np.median(np.diff(np.asarray(index, dtype=np.int64))))
    return 365.25 * 24 * 3600 * 1000 / step_ms


class Lab:
    """Laboratoire : aucune clé, aucun accès à l'exécution ; journalise TOUTES les expériences."""

    def __init__(self, db: Database, data_source: str, seed: int = 7) -> None:
        self.db = db
        self.data_source = data_source
        self.seed = seed
        self.n_trials = 0
        self.trial_sharpes: list[float] = []

    def log(self, kind: str, strategy: str, params: dict[str, Any], window: str, metrics: dict[str, Any],
            passed: bool | None, notes: str = "") -> str:
        eid = new_id()
        self.db.exec(
            "INSERT INTO experiments(id,ts,kind,strategy,params,seed,code_version,data_window,data_source,metrics,passed,notes)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, int(pd.Timestamp.now(tz="UTC").timestamp() * 1000), kind, strategy, json.dumps(params, default=str),
             self.seed, code_version(), window, self.data_source, json.dumps(metrics, default=str),
             None if passed is None else int(passed), notes))
        return eid

    def evaluate(self, data: dict[str, pd.DataFrame], make: Any, base_params: dict[str, Any],
                 grid: dict[str, list[Any]], cfg: BTConfig, n_folds: int = 4, holdout_frac: float = 0.2,
                 purge: int = 200, min_train: int = 1500, use_holdout: bool = True) -> dict[str, Any]:
        n = len(next(iter(data.values())))
        idx = next(iter(data.values())).index
        ppy = periods_per_year(idx)
        splits, (ha, hb) = walk_forward_splits(n, n_folds, holdout_frac, purge, min_train)
        oos_eq: list[pd.Series] = []
        oos_trades = []
        chosen = []
        name = make(base_params).name
        for k, sp in enumerate(splits):
            cands = _grid(base_params, grid)
            best, best_s = cands[0], -1e18
            for p in cands:
                r = run_backtest(slice_data(data, *sp.train), make(p), cfg)
                m = compute_metrics(r, ppy)
                self.n_trials += 1
                self.trial_sharpes.append(m.sharpe / np.sqrt(ppy) if ppy else 0.0)
                self.log("train", name, p, f"{sp.train}", m.as_dict(), None, f"fold {k}")
                s = _score(m)
                if s > best_s:
                    best, best_s = p, s
            # le test commence `purge` barres plus tôt pour chauffer les indicateurs, mais seules les barres
            # du test sont comptées (les signaux de chauffe ne génèrent pas de trades comptabilisés)
            warm_a = max(0, sp.test[0] - purge)
            r = _trim(run_backtest(slice_data(data, warm_a, sp.test[1]), make(best), cfg, sp.test[0] - warm_a),
                      sp.test[0] - warm_a)
            m = compute_metrics(r, ppy)
            self.log("oos", name, best, f"{sp.test}", m.as_dict(), None, f"fold {k}")
            chosen.append(best)
            oos_eq.append(r.equity / r.equity.iloc[0])
            oos_trades += r.trades
        chained = _chain(oos_eq) * cfg.initial_eur
        wf = BTResult(name, chained, oos_trades, 0.0, 0.0, 0.0, cfg)
        wf_m = compute_metrics(wf, ppy)
        out: dict[str, Any] = {"strategy": name, "walk_forward_oos": wf_m.as_dict(), "params_par_fold": chosen,
                               "ppy": ppy, "n_bars": n}
        # stabilité des paramètres : part des folds où le même jeu a été retenu
        sigs = [hashlib.sha1(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest() for p in chosen]  # noqa: S324
        out["stabilite_parametres"] = max(sigs.count(s) for s in sigs) / len(sigs)
        if use_holdout:
            final = chosen[-1]
            warm_a = max(0, ha - purge)
            hd = slice_data(data, warm_a, hb)
            ts0 = ha - warm_a
            r = _trim(run_backtest(hd, make(final), cfg, ts0), ts0)
            hm = compute_metrics(r, ppy)
            r2 = _trim(run_backtest(hd, make(final), replace(cfg, fee_multiplier=2.0), ts0), ts0)
            hm2 = compute_metrics(r2, ppy)
            r3 = _trim(run_backtest(hd, make(final), replace(cfg, spread_extra_bps=30), ts0), ts0)
            hm3 = compute_metrics(r3, ppy)
            regimes = _regime_returns(hd, r, ha - warm_a)
            sr_obs = hm.sharpe / np.sqrt(ppy) if ppy else 0.0
            dsr = deflated_sharpe(sr_obs, len(r.equity), max(1, self.n_trials), float(np.var(self.trial_sharpes)))
            bh = {m: float(buy_and_hold(df.iloc[ha - warm_a:]).iloc[-1] / 100 - 1) for m, df in hd.items()}
            promo = evaluate_promotion(hm, hm2, regimes, leakage_detected=False, safety_violations=0)
            out.update({"holdout": hm.as_dict(), "holdout_frais_doubles": hm2.as_dict(),
                        "holdout_spread_elargi": hm3.as_dict(), "rendement_par_regime": regimes,
                        "deflated_sharpe": dsr, "n_essais": self.n_trials, "benchmark_buy_hold": bh,
                        "benchmark_euros": 0.0, "promotion": promo})
            self.log("holdout", name, final, f"({ha},{hb})", out["holdout"], promo["promu"],
                     "; ".join(promo["non_satisfaits"]))
        return out


def _trim(r: BTResult, start: int) -> BTResult:
    """Ne conserve que la partie test (aucun trade possible pendant la chauffe : equity = capital initial)."""
    eq = r.equity.iloc[start:]
    trades = r.trades
    return BTResult(r.strategy, eq, trades, sum(t.fees_eur for t in trades),
                    sum(t.cost_eur + t.proceeds_eur for t in trades), r.exposure, r.config)


def _chain(parts: list[pd.Series]) -> pd.Series:
    out = []
    level = 1.0
    for p in parts:
        out.append(p * level)
        level = float(out[-1].iloc[-1])
    return pd.concat(out)


def _regime_returns(data: dict[str, pd.DataFrame], r: BTResult, start: int) -> dict[str, float]:
    ref = next(iter(data.values()))
    reg = classify_regime(ref)[start:]
    eq = r.equity.to_numpy()
    rets = np.diff(eq) / eq[:-1]
    out = {}
    for name in set(reg[1:]):
        mask = reg[1:] == name
        if mask.sum() > 20:
            out[str(name)] = float(np.prod(1 + rets[mask]) - 1)
    return out
