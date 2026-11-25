#!/usr/bin/env python3
"""Laboratoire (rôle LAB : aucune clé, aucune exécution, base SÉPARÉE de la production).

Compare, avec les mêmes données, frais, spread, glissement, minimums et coût d'hébergement :
- les 6 familles classiques (walk-forward purgé + holdout final verrouillé) ;
- un hybride dont les poids viennent UNIQUEMENT des performances d'entraînement de chaque fold ;
- une stratégie à modèle prédictif (logistique réentraînée périodiquement, causale) ;
- les benchmarks : 100 € conservés en euros, achat passif BTC, tendance simple (EMA 50/200).

  python scripts/run_lab.py --source real                 # Parquet Bitvavo réel (scripts/download_candles.py)
  python scripts/run_lab.py --source real --all            # univers complet disponible (limite la survivance)
  python scripts/run_lab.py --source synthetic             # données SYNTHÉTIQUES (aucune valeur probante)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.core.config import ROOT, load_strategy_params  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.models.predict import fit_and_evaluate, walk_forward_brier  # noqa: E402
from backend.research.backtest import BTConfig  # noqa: E402
from backend.research.synthetic import SYNTHETIC_LABEL, synthetic_universe  # noqa: E402
from backend.research.walkforward import Lab  # noqa: E402
from backend.storage.db import Database  # noqa: E402
from backend.strategies import base as S  # noqa: E402

GRIDS = {
    "momentum": {"lookback": [12, 24, 48]},
    "breakout": {"channel": [24, 48, 96]},
    "mean_reversion": {"z_entry": [-2.5, -2.0]},
    "trend_following": {"fast": [12, 21], "mid": [50, 100]},
    "swing": {"pullback_rsi": [35, 40]},
    "rebound": {"min_drop": [0.08, 0.12, 0.2]},
}
MAKERS: dict[str, Any] = {"momentum": S.Momentum, "breakout": S.Breakout, "mean_reversion": S.MeanReversion,
                          "trend_following": S.TrendFollowing, "swing": S.Swing, "rebound": S.Rebound}


class Precomputed(S.Strategy):
    """Signaux calculés UNE fois sur tout l'historique par un modèle causal (testé), relus par horodatage."""

    def __init__(self, name: str, table: dict[str, pd.DataFrame]) -> None:
        super().__init__({})
        self.name = name
        self.table = table

    def signals(self, df: pd.DataFrame) -> S.Signals:
        t = self.table[df.attrs["market"]].reindex(df.index)
        return S.Signals(t["entry"].fillna(False).to_numpy(bool), t["exit"].fillna(False).to_numpy(bool),
                         t["strength"].fillna(0).to_numpy(float), int(t.attrs.get("max_bars", 6)))


def load_real(store: CandleStore, markets: list[str], interval: str) -> dict[str, pd.DataFrame]:
    frames = {m: store.load_real(m, interval) for m in markets}
    frames = {m: f for m, f in frames.items() if len(f)}
    if not frames:
        return {}
    idx = sorted(set().union(*(set(f.index) for f in frames.values())))
    out = {}
    for m, f in frames.items():
        g = f.reindex(idx)  # NaN avant le listing / après la radiation : AUCUN prix inventé
        g.attrs["provenance"] = "real/bitvavo"
        out[m] = g
    return out


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Agrégation CAUSALE : une bougie 4 h n'est produite qu'à partir de bougies 1 h toutes antérieures ou égales
    à sa clôture ; une bougie agrégée incomplète (trou) est laissée NaN plutôt que reconstituée."""
    idx = pd.to_datetime(df.index, unit="ms", utc=True)
    d = df.set_axis(idx)
    agg = d.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = d["close"].resample(rule, label="left", closed="left").count()
    expected = pd.Timedelta(rule) / pd.Timedelta(np.median(np.diff(df.index.to_numpy())), unit="ms")
    agg[counts < expected] = np.nan
    agg.index = (agg.index.astype("int64") // 10**6).astype("int64")
    agg.attrs = dict(df.attrs)
    return agg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["real", "synthetic"], default="real")
    ap.add_argument("--markets", default="BTC-EUR,ETH-EUR,SOL-EUR")
    ap.add_argument("--all", action="store_true", help="tous les marchés présents dans le magasin réel")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--bars", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--lab-db", default=os.environ.get("NELYQOR_LAB_DB", str(ROOT / "data" / "lab" / "lab.db")))
    ap.add_argument("--out", default=os.environ.get("NELYQOR_LAB_OUT", str(ROOT / "reports")))
    ap.add_argument("--hosting", type=float, default=float(os.environ.get("NELYQOR_HOSTING_EUR", "7.19")))
    ap.add_argument("--min-bars", type=int, default=3000)
    ap.add_argument("--resample", default="", help="agrégation causale des bougies (ex. 4h) à partir de --interval")
    a = ap.parse_args()
    db = Database(a.lab_db)
    store = CandleStore(a.root)
    if a.source == "real":
        markets = store.markets() if a.all else a.markets.split(",")
        data = load_real(store, markets, a.interval)
        if not data or len(next(iter(data.values()))) < a.min_bars:
            sys.exit(f"Données réelles insuffisantes (< {a.min_bars} bougies). Lancer scripts/download_candles.py.")
        n = len(next(iter(data.values())))
        tag = "bitvavo"
        if a.resample:
            data = {m: resample_ohlc(df, a.resample) for m, df in data.items()}
            n = len(next(iter(data.values())))
            tag = f"bitvavo_{a.resample}"
        source = (f"RÉEL — API publique Bitvavo, {a.interval}{' agrégé ' + a.resample if a.resample else ''}, "
                  f"{len(data)} marchés, {n} bougies")
    else:
        data = synthetic_universe(a.markets.split(","), a.bars, seed=a.seed)
        source = SYNTHETIC_LABEL
        tag = "synthetique"
    for m, df in data.items():
        df.attrs["market"] = m
        if "BTC-EUR" in data and m != "BTC-EUR":
            df["bench_close"] = data["BTC-EUR"]["close"]  # momentum relatif à BTC (cahier des charges)
    params = load_strategy_params()
    sim = params["simulation"]
    cfg = BTConfig(fee_taker=float(sim["fee_taker"]), slippage_bps=float(sim["slippage_bps"]),
                   latency_bars=int(sim["latency_bars"]))
    lab = Lab(db, source, a.seed, hosting_eur_month=a.hosting)
    t0 = time.time()
    results: list[dict[str, Any]] = []

    def summarize(out: dict[str, Any], kind: str) -> dict[str, Any]:
        h = out["holdout"]
        row = {"strategy": out["strategy"], "type": kind, "wf_oos_net": out["walk_forward_oos"]["net_return"],
               "wf_oos_trades": out["walk_forward_oos"]["n_trades"], "holdout_net": h["net_return"],
               "holdout_economique": out["holdout_economique"],
               "holdout_doubled_fees": out["holdout_frais_doubles"]["net_return"],
               "holdout_wide_spread": out["holdout_spread_elargi"]["net_return"], "pf": h["profit_factor"],
               "trades": h["n_trades"], "maxdd": h["max_drawdown"], "sharpe": h["sharpe"],
               "ci95_trade": h["ci95_mean_trade"], "dsr": out["deflated_sharpe"],
               "stabilite_parametres": out["stabilite_parametres"], "regimes": out["rendement_par_regime"],
               "holdout_consultations_anterieures": out["consultations_holdout_anterieures"],
               "fuite_temporelle": out["fuite_temporelle_detectee"],
               "promu": out["promotion"]["promu"], "non_satisfaits": out["promotion"]["non_satisfaits"],
               "buy_hold": out["benchmark_buy_hold"], "fenetre_holdout": out["fenetre_holdout"],
               "diagnostic_sans_couts": out["diagnostic_sans_couts"]["net_return"],
               "frais_holdout_eur": out["frais_holdout_eur"], "duree_moyenne_bougies": out["duree_moyenne_bougies"]}
        print(f"{row['strategy']:22s} holdout {row['holdout_net']:+.2%} (éco {row['holdout_economique']:+.2%}, "
              f"sans coûts {row['diagnostic_sans_couts']:+.2%}) PF {row['pf']:.2f} trades {row['trades']:4d} "
              f"promu={row['promu']}", flush=True)
        return row

    fam_out = {}
    for name, maker in MAKERS.items():
        fam_out[name] = lab.evaluate(data, maker, params[name], GRIDS[name], cfg)
        results.append(summarize(fam_out[name], "classique"))
    # Hybride : poids = max(0, Sharpe d'ENTRAÎNEMENT) de chaque famille, fold par fold (aucune info du test)
    n_folds = len(next(iter(lab.fold_choices.values())))
    per_fold = []
    for k in range(n_folds):
        members = {nm: lab.fold_choices[nm][k][0] for nm in MAKERS}
        weights = {nm: max(0.0, lab.fold_choices[nm][k][1]) for nm in MAKERS}
        per_fold.append({"members": members, "weights": weights, "threshold": 0.15})
    hold = {"members": {nm: lab.final_choice[nm][0] for nm in MAKERS},
            "weights": {nm: max(0.0, lab.final_choice[nm][1]) for nm in MAKERS}, "threshold": 0.15}

    def make_hybrid(p: dict[str, Any]) -> S.Hybrid:
        mem = [MAKERS[nm](pp) for nm, pp in p.get("members", {nm: params[nm] for nm in MAKERS}).items()]
        return S.Hybrid({"threshold": p.get("threshold", 0.15)}, mem, p.get("weights", {}))

    results.append(summarize(lab.evaluate(data, make_hybrid, {}, {}, cfg, per_fold_params=per_fold,
                                          holdout_params=hold), "hybride"))
    # Modèle prédictif comme stratégie (réentraînement périodique causal), signaux précalculés une fois
    table = {}
    mp = {"horizon": 6, "cost": float(sim["fee_taker"]) * 2 + 0.002, "retrain_every": 500, "min_train": 1500}
    for m, df in data.items():
        sig = S.ModelStrategy(mp).signals(df)
        t = pd.DataFrame({"entry": sig.entry, "exit": sig.exit, "strength": sig.strength}, index=df.index)
        t.attrs["max_bars"] = sig.max_bars
        table[m] = t
    results.append(summarize(lab.evaluate(data, lambda p: Precomputed("modele_logistique", table), {}, {}, cfg),
                             "modele_predictif"))
    # Benchmark simple de tendance (paramètres fixes, aucune optimisation)
    def make_trend_bm(p: dict[str, Any]) -> S.Strategy:
        st = S.TrendFollowing({"fast": 50, "mid": 100, "slow": 200, "max_bars": 10_000})
        st.name = "benchmark_tendance_ema"  # nom distinct : registre du holdout séparé de trend_following
        return st

    trend_bm = lab.evaluate(data, make_trend_bm, {}, {}, cfg)
    results.append(summarize(trend_bm, "benchmark"))
    # Modèles prédictifs : compétence de Brier, IC, stabilité par fenêtre
    ref_mk = "BTC-EUR" if "BTC-EUR" in data else next(iter(data))
    ref_df = data[ref_mk].dropna(subset=["close"])
    models = []
    for kind in ("logistic", "lightgbm"):
        for hz in (1, 6, 24):
            try:
                rep, _ = fit_and_evaluate(ref_df, hz, cost=0.005, kind=kind, seed=a.seed)
                stab = walk_forward_brier(ref_df, hz, 0.005, kind, folds=3) if kind == "logistic" else []
                models.append({"modele": kind, "marche": ref_mk, "horizon_bougies": hz, "brier": rep.brier,
                               "brier_reference": rep.brier_base, "skill": rep.skill, "skill_ic95": rep.skill_ci,
                               "statut": rep.label, "n_test": rep.n_test, "stabilite": stab})
            except Exception as e:  # noqa: BLE001
                models.append({"modele": kind, "horizon_bougies": hz, "erreur": str(e)})
    bh = results[0]["buy_hold"]
    economic_hosting = a.hosting
    report = {"date": datetime.now(UTC).isoformat(timespec="seconds"), "source": source,
              "donnees_synthetiques": a.source == "synthetic", "marches": sorted(data),
              "n_essais_total": lab.n_trials, "duree_s": round(time.time() - t0, 1), "resultats": results,
              "modeles": models, "buy_hold_btc": bh.get("BTC-EUR", float(np.nanmean(list(bh.values())))),
              "buy_hold_par_marche": bh, "benchmark_euros": 0.0, "hebergement_eur_mois": economic_hosting,
              "conclusion": ("Aucune stratégie promue : rester en simulation." if not any(r["promu"] for r in results)
                             else "Au moins une stratégie satisfait les critères ; validation indépendante "
                                  "(paper trading sur flux réels) requise avant tout usage réel.")}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"lab_report_{tag}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print("RAPPORT_JSON_DEBUT")
    print(json.dumps(report, ensure_ascii=False, default=str))
    print("RAPPORT_JSON_FIN")
    print(json.dumps({"conclusion": report["conclusion"], "essais": lab.n_trials, "duree_s": report["duree_s"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
