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
from .backtest import BTConfig, BTResult, run_backtest
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


def leakage_check(strategy: Any, df: pd.DataFrame, cut_frac: float = 0.7) -> bool:
    """Vrai si une FUITE est détectée : les signaux passés changent quand on ajoute/perturbe le futur."""
    n = len(df)
    cut = int(n * cut_frac)
    if cut < 300:
        return False
    a = strategy.signals(df.iloc[:cut])
    shocked = df.copy()
    shocked.iloc[cut:, :] = shocked.iloc[cut:, :] * 1.7
    b = strategy.signals(shocked)
    return not (np.array_equal(a.entry, b.entry[:cut]) and np.array_equal(a.exit, b.exit[:cut]))


def data_fingerprint(data: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for m in sorted(data):
        df = data[m]
        h.update(f"{m}:{df.attrs.get('provenance', '?')}:{len(df)}:{df.index[0]}:{df.index[-1]}".encode())
    return h.hexdigest()


class Lab:
    """Laboratoire : aucune clé, aucun accès à l'exécution, BASE SÉPARÉE de la production ; journalise TOUTES
    les expériences (essais infructueux compris) et chaque consultation du holdout."""

    def __init__(self, db: Database, data_source: str, seed: int = 7, hosting_eur_month: float = 0.0) -> None:
        self.db = db
        self.data_source = data_source
        self.seed = seed
        self.n_trials = 0
        self.trial_sharpes: list[float] = []
        self.hosting_eur_month = hosting_eur_month
        self.fold_choices: dict[str, list[tuple[dict[str, Any], float]]] = {}
        self.final_choice: dict[str, tuple[dict[str, Any], float]] = {}

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

    def holdout_previous_views(self, strategy: str, a_ts: int, b_ts: int) -> int:
        """Nombre de consultations antérieures d'un holdout CHEVAUCHANT cette fenêtre pour cette stratégie."""
        n = 0
        for r in self.db.q("SELECT window FROM holdout_views WHERE strategy=?", (strategy,)):
            x, y = (int(v) for v in r["window"].split(","))
            if x <= b_ts and a_ts <= y:
                n += 1
        return n

    def record_holdout_view(self, strategy: str, a_ts: int, b_ts: int, fingerprint: str, params: dict[str, Any]) -> None:
        self.db.exec("INSERT INTO holdout_views(id,ts,data_fingerprint,window,strategy,params_hash) VALUES(?,?,?,?,?,?)",
                     (new_id(), int(pd.Timestamp.now(tz="UTC").timestamp() * 1000), fingerprint, f"{a_ts},{b_ts}",
                      strategy, hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()))

    def _economic(self, m: Metrics, n_bars: int, ppy: float, initial: float) -> float:
        months = n_bars / ppy * 12 if ppy else 0.0
        return m.net_return - self.hosting_eur_month * months / initial

    def evaluate(self, data: dict[str, pd.DataFrame], make: Any, base_params: dict[str, Any],
                 grid: dict[str, list[Any]], cfg: BTConfig, n_folds: int = 4, holdout_frac: float = 0.2,
                 purge: int = 300, min_train: int = 1500, use_holdout: bool = True,
                 per_fold_params: list[dict[str, Any]] | None = None,
                 holdout_params: dict[str, Any] | None = None) -> dict[str, Any]:
        first = next(iter(data.values()))
        n = len(first)
        idx = first.index
        ppy = periods_per_year(idx)
        splits, (ha, hb) = walk_forward_splits(n, n_folds, holdout_frac, purge, min_train)
        oos_eq: list[pd.Series] = []
        oos_trades = []
        chosen: list[dict[str, Any]] = []
        name = make(base_params).name
        self.fold_choices[name] = []

        def select(a: int, b: int, tag: str) -> tuple[dict[str, Any], float]:
            best, best_s, best_sh = None, -1e18, 0.0
            for p in _grid(base_params, grid):
                r = run_backtest(slice_data(data, a, b), make(p), cfg)
                m = compute_metrics(r, ppy)
                self.n_trials += 1
                self.trial_sharpes.append(m.sharpe / np.sqrt(ppy) if ppy else 0.0)
                self.log("train", name, p, f"({a},{b})", m.as_dict(), None, tag)
                sc = _score(m)
                if best is None or sc > best_s:
                    best, best_s, best_sh = p, sc, m.sharpe
            assert best is not None
            return best, best_sh

        for k, sp in enumerate(splits):
            if per_fold_params is not None:
                best, tr_sh = per_fold_params[k], 0.0
            else:
                best, tr_sh = select(*sp.train, f"fold {k}")
            self.fold_choices[name].append((best, tr_sh))
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
                               "ppy": ppy, "n_bars": n,
                               "walk_forward_economique": self._economic(wf_m, len(chained), ppy, cfg.initial_eur)}
        sigs = [hashlib.sha1(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest() for p in chosen]  # noqa: S324
        out["stabilite_parametres"] = max(sigs.count(s) for s in sigs) / len(sigs)
        if use_holdout:
            # paramètres re-sélectionnés sur TOUT le pré-holdout (purge incluse) — jamais sur le holdout
            if holdout_params is not None:
                final, fin_sh = holdout_params, 0.0
            elif per_fold_params is None:
                final, fin_sh = select(0, ha - purge, "pre-holdout")
            else:
                final, fin_sh = chosen[-1], 0.0
            self.final_choice[name] = (final, fin_sh)
            a_ts, b_ts = int(idx[ha]), int(idx[hb - 1])
            prev_views = self.holdout_previous_views(name, a_ts, b_ts)
            fp = data_fingerprint(data)
            self.record_holdout_view(name, a_ts, b_ts, fp, final)
            warm_a = max(0, ha - purge)
            hd = slice_data(data, warm_a, hb)
            ts0 = ha - warm_a
            r = _trim(run_backtest(hd, make(final), cfg, ts0), ts0)
            hm = compute_metrics(r, ppy)
            r2 = _trim(run_backtest(hd, make(final), replace(cfg, fee_multiplier=2.0), ts0), ts0)
            hm2 = compute_metrics(r2, ppy)
            r3 = _trim(run_backtest(hd, make(final), replace(cfg, spread_extra_bps=30), ts0), ts0)
            hm3 = compute_metrics(r3, ppy)
            regimes = _regime_returns(hd, r, ts0)
            sr_obs = hm.sharpe / np.sqrt(ppy) if ppy else 0.0
            dsr = deflated_sharpe(sr_obs, len(r.equity), max(1, self.n_trials), float(np.var(self.trial_sharpes)))
            bh = {m: _bh_return(df.iloc[ts0:]) for m, df in hd.items()}
            leak = any(leakage_check(make(final), df) for df in list(data.values())[:3])
            promo = evaluate_promotion(hm, hm2, regimes, leakage_detected=leak, safety_violations=0)
            promo["criteres"]["holdout_vierge"] = prev_views == 0
            if prev_views:
                promo["non_satisfaits"].append("holdout_vierge")
                promo["promu"] = False
            out.update({"holdout": hm.as_dict(), "holdout_frais_doubles": hm2.as_dict(),
                        "holdout_spread_elargi": hm3.as_dict(), "rendement_par_regime": regimes,
                        "holdout_economique": self._economic(hm, len(r.equity), ppy, cfg.initial_eur),
                        "deflated_sharpe": dsr, "n_essais": self.n_trials, "benchmark_buy_hold": bh,
                        "benchmark_euros": 0.0, "promotion": promo, "consultations_holdout_anterieures": prev_views,
                        "fuite_temporelle_detectee": leak, "fenetre_holdout": [a_ts, b_ts], "empreinte_donnees": fp})
            self.log("holdout", name, final, f"({ha},{hb})", out["holdout"], promo["promu"],
                     "; ".join(promo["non_satisfaits"]))
        return out


def _bh_return(df: pd.DataFrame, fee: float = 0.0025, slippage_bps: float = 10) -> float:
    """Achat passif au premier prix disponible (marché listé plus tard : à partir de son listing)."""
    o = df["open"].dropna()
    c = df["close"].dropna()
    if o.empty or c.empty:
        return float("nan")
    entry = float(o.iloc[0]) * (1 + slippage_bps / 1e4)
    exit_ = float(c.iloc[-1]) * (1 - slippage_bps / 1e4)
    return (exit_ / entry) * (1 - fee) ** 2 - 1


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
