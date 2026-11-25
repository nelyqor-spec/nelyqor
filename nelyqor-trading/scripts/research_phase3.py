#!/usr/bin/env python3
"""Recherche de rentabilité — phase 3 (rôle LAB : aucune clé, aucune exécution, base séparée).

Toutes les données jusqu'au 25/11/2026 sont une période de RECHERCHE (déjà consultée) : aucun résultat ici
n'est une validation indépendante. Les variantes sont PRÉ-ENREGISTRÉES ci-dessous (avant exécution) et toutes
comptées dans le nombre d'essais (Deflated Sharpe Ratio).

  python scripts/research_phase3.py --part xs        # classement transversal, intervalles, univers, walk-forward
  python scripts/research_phase3.py --part exec      # marché / limite postOnly / hybride
  python scripts/research_phase3.py --part strat --intervals 4h,8h,12h,1d
  python scripts/research_phase3.py --part models    # logistique, LightGBM, XGBoost, bayésien, quantiles, régimes
  python scripts/research_phase3.py --part venue     # EUR vs USDC (instantanés de carnets réels)
Résultats : JSON imprimé entre PHASE3_JSON_DEBUT / PHASE3_JSON_FIN et écrit dans --out.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.core.config import ROOT  # noqa: E402
from backend.data.resample import resample_ohlc  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.research.backtest import BTConfig  # noqa: E402
from backend.research.metrics import block_bootstrap_ci, deflated_sharpe  # noqa: E402
from backend.research.walkforward import Lab  # noqa: E402
from backend.research.xsection import XSConfig, build_panel, run_xsection, xs_metrics  # noqa: E402
from backend.storage.db import Database  # noqa: E402
from backend.strategies import base as S  # noqa: E402

CUTOFF_MS = 1_795_651_200_000  # 2026-11-26T00:00:00Z : au-delà = données PROSPECTIVES (jamais utilisées ici)
RULES = {"1h": "1h", "2h": "2h", "4h": "4h", "8h": "8h", "12h": "12h", "1d": "1D", "30m": "30min"}
HOSTING = (0, 3, 5, 7.19, 10)


def bars_per_day(interval: str) -> float:
    return 86_400_000 / pd.Timedelta(RULES.get(interval, interval)).total_seconds() / 1000


SMOKE = False  # --synthetic-smoke : données SYNTHÉTIQUES pour tester le code uniquement (jamais publiées)


def load_universe(store: CandleStore, base: str, quotes: list[str], min_rows: int = 200) -> dict[str, pd.DataFrame]:
    if SMOKE:
        from backend.research.synthetic import synthetic_universe
        d = synthetic_universe([f"S{i}-{q}" for q in quotes for i in range(24)] + [f"BTC-{q}" for q in quotes], 4000, seed=1)
        return {m: v for m, v in d.items() if len(v) >= min_rows}
    out = {}
    for m in store.markets():
        if m.split("-")[1] not in quotes:
            continue
        df = store.load_real(m, base)
        df = df[df.index < CUTOFF_MS]
        if len(df) >= min_rows:
            out[m] = df
    return out


def align(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    idx = sorted(set().union(*(set(d.index) for d in data.values())))
    return {m: d.reindex(idx) for m, d in data.items()}  # NaN avant cotation / après radiation


def derive(data: dict[str, pd.DataFrame], interval: str, base: str) -> dict[str, pd.DataFrame]:
    if interval == base:
        return align(data)
    return align({m: resample_ohlc(d, RULES[interval]) for m, d in data.items()})


def ppy(interval: str) -> float:
    return 365.25 * bars_per_day(interval)


def scaled_cfg(interval: str, **kw: Any) -> XSConfig:
    """Paramètres exprimés en TEMPS (jours), convertis en bougies : comparables entre unités de temps."""
    b = bars_per_day(interval)
    base = dict(mom_lookback=max(3, int(7 * b)), trend_fast=max(3, int(5 * b)), trend_slow=max(10, int(30 * b)),
                vol_lookback=max(5, int(14 * b)), liq_lookback=max(5, int(30 * b)), min_age_bars=max(5, int(30 * b)),
                rebalance_bars=max(1, int(round(b))), regime_ema=max(20, int(50 * b)),
                min_liquidity_quote=50_000 / b, limit_ttl_bars=max(1, int(round(b / 6))))
    base.update(kw)
    return XSConfig(**base)


# Variantes PRÉ-ENREGISTRÉES (figées avant toute exécution de la phase 3)
XS_VARIANTS: dict[str, dict[str, Any]] = {
    "base": {},
    "sans_filtre_regime": {"regime_filter": False},
    "moins_de_rotation": {"top_n": 2, "buffer": 4},
    "seuil_apres_frais": {"cost_hurdle_mult": 2.0},
    "stop_suiveur_3atr": {"trail_atr": 3.0},
    "momentum_seul": {"weights": {"mom": 1.0}},
    "tendance_seule": {"weights": {"trend": 1.0}},
    "detention_min_3j": {"min_hold_bars_days": 3},
}


def make_variant(interval: str, name: str, **extra: Any) -> XSConfig:
    v = dict(XS_VARIANTS[name])
    if "min_hold_bars_days" in v:
        v["min_hold_bars"] = int(v.pop("min_hold_bars_days") * bars_per_day(interval))
    v.update(extra)
    return scaled_cfg(interval, **v)


def per_year(eq: pd.Series) -> dict[str, float]:
    s = eq.dropna()
    s.index = pd.to_datetime(s.index, unit="ms", utc=True)
    return {str(y): float(g.iloc[-1] / g.iloc[0] - 1) for y, g in s.groupby(s.index.year) if len(g) > 2}


def part_xs(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    res: dict[str, Any] = {"intervalles": {}, "univers": {}, "walk_forward": {}}
    trial_sr: list[float] = []
    for quote in a.quotes.split(","):
        raw = load_universe(store, a.base, [quote])
        if not raw:
            continue
        reg_mk = f"BTC-{quote}"
        for interval in a.intervals.split(","):
            data = derive(raw, interval, a.base)
            P = None
            rows = {}
            for name in XS_VARIANTS:
                cfg = make_variant(interval, name)
                if P is None or name in ("momentum_seul", "tendance_seule") or P.get("_w") != cfg.weights:
                    P = build_panel(data, cfg)
                    P["_w"] = cfg.weights  # type: ignore[assignment]
                r = run_xsection(data, cfg, regime_market=reg_mk, panel=P)
                m = xs_metrics(r, ppy(interval))
                m["par_annee"] = per_year(r.equity)
                rows[name] = m
                trial_sr.append(m["sharpe"] / math.sqrt(ppy(interval)))
                print(f"{quote} {interval:4s} {name:22s} net {m['rendement_net']:+.1%} PF {m['profit_factor']:.2f} "
                      f"trades {m['trades']} DD {m['max_drawdown']:.1%} frais {m['frais_eur']:.1f}", flush=True)
                # walk-forward « sélection par le passé » : segments successifs, variante choisie sur l'avant
                rows[name]["_eq"] = r.equity
            res["intervalles"][f"{quote}_{interval}"] = {k: {kk: vv for kk, vv in v.items() if kk != "_eq"}
                                                         for k, v in rows.items()}
            res["walk_forward"][f"{quote}_{interval}"] = walk_forward_select({k: v["_eq"] for k, v in rows.items()},
                                                                             ppy(interval))
            res["n_marches"] = res.get("n_marches", {})
            res["n_marches"][quote] = len(raw)
        # balayage de la taille d'univers (point-in-time) à 4 h, variante de base
        data4 = derive(raw, "4h", a.base)
        for size in (5, 20, 50, 100, 0):
            cfg = make_variant("4h", "base", max_universe=size)
            r = run_xsection(data4, cfg, regime_market=reg_mk)
            m = xs_metrics(r, ppy("4h"))
            trial_sr.append(m["sharpe"] / math.sqrt(ppy("4h")))
            res["univers"][f"{quote}_{size or 'tous'}"] = {k: m[k] for k in ("rendement_net", "profit_factor", "trades",
                                                                             "max_drawdown", "sharpe", "economique")}
            print(f"{quote} univers {size or 'tous'}: net {m['rendement_net']:+.1%} trades {m['trades']}", flush=True)
    best = max((v["sharpe"], k, kk) for k, d in res["intervalles"].items() for kk, v in d.items()) if res["intervalles"] else None
    if best:
        k, kk = best[1], best[2]
        n_obs = int(res["intervalles"][k][kk]["annees"] * ppy(k.split("_")[1]))
        res["meilleur"] = {"cle": f"{k}/{kk}", "sharpe": best[0], "n_essais": len(trial_sr),
                           "dsr": deflated_sharpe(best[0] / math.sqrt(ppy(k.split('_')[1])), n_obs, len(trial_sr),
                                                  float(np.var(trial_sr)))}
    return res


def walk_forward_select(eqs: dict[str, pd.Series], ppy_: float, n_seg: int = 4) -> dict[str, Any]:
    """Pour chaque segment, choisit la variante au meilleur Sharpe sur TOUT le passé, puis mesure le segment.
    (Les positions en cours à la frontière sont celles de la variante : approximation documentée.)"""
    common = pd.concat(eqs, axis=1).dropna()
    if len(common) < 200:
        return {}
    cuts = np.linspace(len(common) // 3, len(common) - 1, n_seg + 1).astype(int)
    chain = 1.0
    segs = []
    for a_, b_ in zip(cuts[:-1], cuts[1:], strict=True):
        past = common.iloc[:a_].pct_change().dropna()
        sh = (past.mean() / past.std()).replace([np.inf, -np.inf], np.nan).fillna(-9)
        pick = str(sh.idxmax())
        seg_ret = float(common[pick].iloc[b_] / common[pick].iloc[a_] - 1)
        chain *= 1 + seg_ret
        segs.append({"debut": int(common.index[a_]), "fin": int(common.index[b_]), "variante": pick, "rendement": seg_ret})
    return {"segments": segs, "rendement_chaine": chain - 1}


def part_exec(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    out: dict[str, Any] = {}
    raw = load_universe(store, a.base, ["EUR"])
    for interval in a.intervals.split(","):
        data = derive(raw, interval, a.base)
        P = build_panel(data, make_variant(interval, "base"))
        for ex in ("market", "limit", "hybrid"):
            cfg = make_variant(interval, "base", execution=ex)
            r = run_xsection(data, cfg, panel=P)
            m = xs_metrics(r, ppy(interval))
            out[f"{interval}_{ex}"] = {k: m[k] for k in ("rendement_net", "trades", "profit_factor", "frais_eur",
                                                         "frais_economises_eur", "limit_remplis", "limit_manques",
                                                         "repli_marche", "max_drawdown", "economique")}
            print(f"exec {interval} {ex:6s} net {m['rendement_net']:+.1%} frais {m['frais_eur']:.2f} économisés "
                  f"{m['frais_economises_eur']:.2f} manqués {m['limit_manques']}", flush=True)
    return out


STRAT_FILTERS = {
    "aucun": {},
    "tendance": {"trend_ema": 200, "mtf_mult": 4},
    "tendance_vol": {"trend_ema": 200, "vol_pct": 0.8},
    "complet": {"trend_ema": 200, "mtf_mult": 4, "vol_pct": 0.8, "liq_pct": 0.2, "min_move_cost": 1.0},
}
STRAT_EXITS = {"suiveur_3atr": {}, "protection_benefice": {"breakeven_r": 1.0, "atr_trail_mult": 2.5}}


def part_strat(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    from backend.core.config import load_strategy_params

    params = load_strategy_params()
    raw = load_universe(store, a.base, ["EUR"], min_rows=1000 if SMOKE else int(2 * 365 * 24))
    if not raw:
        return {"erreur": "aucun marché avec au moins 2 ans de données"}
    # marchés avec >= 2 ans de données (biais de survivance : survivants actuels — documenté)
    lens = sorted(raw, key=lambda m: -raw[m]["close"].notna().sum())[: a.strat_markets]
    raw = {m: raw[m] for m in lens}
    lab = Lab(Database(a.lab_db), "RÉEL Bitvavo (recherche)", 7, hosting_eur_month=7.19)
    fams = {"trend_following": S.TrendFollowing, "breakout": S.Breakout, "swing": S.Swing}
    out: dict[str, Any] = {"marches": sorted(raw)}
    for interval in a.intervals.split(","):
        data = derive(raw, interval, a.base)
        for m, df in data.items():
            df.attrs["market"] = m
        b = bars_per_day(interval)
        for ex_name, ex_kw in STRAT_EXITS.items():
            cfg = replace(BTConfig(), latency_bars=1, cooldown_bars=max(1, int(b)), **ex_kw)
            for fam, maker in fams.items():
                base_p = dict(params[fam])

                def make(p: dict[str, Any], maker: Any = maker, base_p: dict[str, Any] = base_p, fam: str = fam,
                         ex_name: str = ex_name) -> S.Strategy:
                    return S.Filtered({**STRAT_FILTERS[p["filtre"]], "name": f"{fam}_{p['filtre']}_{ex_name}"}, maker(base_p))

                try:
                    r = lab.evaluate(data, make, {"filtre": "aucun"}, {"filtre": list(STRAT_FILTERS)}, cfg,
                                     n_folds=4, purge=int(10 * b) + 50, min_train=max(300, len(next(iter(data.values()))) // 3))
                except ValueError as e:
                    out[f"{interval}_{fam}_{ex_name}"] = {"erreur": str(e)}
                    continue
                h = r["holdout"]
                key = f"{interval}_{fam}_{ex_name}"
                out[key] = {"filtres_par_fold": [p["filtre"] for p in r["params_par_fold"]],
                            "wf_oos_net": r["walk_forward_oos"]["net_return"], "wf_trades": r["walk_forward_oos"]["n_trades"],
                            "holdout_interne_net": h["net_return"], "pf": h["profit_factor"], "trades": h["n_trades"],
                            "frais_x2": r["holdout_frais_doubles"]["net_return"], "sans_couts": r["diagnostic_sans_couts"]["net_return"],
                            "dsr": r["deflated_sharpe"], "promu": r["promotion"]["promu"],
                            "non_satisfaits": r["promotion"]["non_satisfaits"]}
                print(f"strat {key:40s} wf {out[key]['wf_oos_net']:+.1%} hold {h['net_return']:+.1%} PF {h['profit_factor']:.2f} "
                      f"trades {h['n_trades']} filtres {out[key]['filtres_par_fold']}", flush=True)
            # hybride : poids = Sharpe d'entraînement des familles filtrées (aucune info du test)
    out["n_essais"] = lab.n_trials
    return out


def part_models(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    from scipy.stats import spearmanr
    from sklearn.linear_model import BayesianRidge, LogisticRegression
    from sklearn.metrics import brier_score_loss
    from sklearn.mixture import GaussianMixture
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from backend.features.indicators import feature_frame
    from backend.models.predict import _logit, brier_skill_ci

    interval = a.model_interval
    raw = load_universe(store, a.base, ["EUR"])
    data = derive(raw, interval, a.base)
    b = bars_per_day(interval)
    h = max(1, int(round(b)))  # horizon : 1 jour
    rt = 2 * 0.0025 + 0.002
    cfg = scaled_cfg(interval)
    P = build_panel(data, cfg)
    frames = []
    for m, df in data.items():
        f = feature_frame(df)
        f["mom_rel"] = P["mom"][m] - P["mom"].median(axis=1)
        f["liq"] = np.log1p(P["liq"][m])
        fwd = df["close"].shift(-h) / df["open"].shift(-1) - 1
        f["y"] = fwd
        f["market"] = m
        f["t"] = df.index
        f["ok"] = (P["liq"][m] >= cfg.min_liquidity_quote) & (P["age"][m] >= cfg.min_age_bars)
        frames.append(f[f["ok"]])
    D = pd.concat(frames).replace([np.inf, -np.inf], np.nan).dropna()
    D = D.sort_values("t")
    feats = [c for c in D.columns if c not in ("y", "market", "t", "ok")]
    times = np.sort(D["t"].unique())
    cuts = np.linspace(len(times) * 0.4, len(times), 4).astype(int)
    preds: dict[str, list[pd.DataFrame]] = {}

    def models() -> dict[str, Any]:
        import lightgbm as lgb
        import xgboost as xgb

        return {
            "logistique": ("clf", make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=500))),
            "lightgbm": ("clf", lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=15,
                                                   min_child_samples=200, subsample=0.8, subsample_freq=1,
                                                   colsample_bytree=0.8, verbose=-1, random_state=7)),
            "xgboost": ("clf", xgb.XGBClassifier(n_estimators=300, learning_rate=0.03, max_depth=4, subsample=0.8,
                                                 colsample_bytree=0.8, min_child_weight=50, random_state=7)),
            "bayesien_ridge": ("reg", make_pipeline(StandardScaler(), BayesianRidge())),
            "quantile_lgbm_q50": ("q", 0.5),
        }

    import lightgbm as lgb

    for k in range(len(cuts) - 1):
        t_train_end = times[max(0, cuts[k] - h - 1)]  # purge : étiquettes chevauchantes exclues
        t_a, t_b = times[cuts[k]], times[cuts[k + 1] - 1]
        tr = D[D["t"] < t_train_end]
        te = D[(D["t"] >= t_a) & (D["t"] <= t_b)]
        cal_cut = tr["t"].quantile(0.8)
        trn, cal = tr[tr["t"] < cal_cut], tr[tr["t"] >= cal_cut]
        for name, (kind, mdl) in models().items():
            pr = te[["t", "market", "y"]].copy()
            if kind == "clf":
                mdl.fit(trn[feats], (trn["y"] > rt).astype(int))
                raw_c = mdl.predict_proba(cal[feats])[:, 1].reshape(-1, 1)
                platt = LogisticRegression().fit(_logit(raw_c), (cal["y"] > rt).astype(int))
                pr["p"] = platt.predict_proba(_logit(mdl.predict_proba(te[feats])[:, 1].reshape(-1, 1)))[:, 1]
                pr["base"] = float((cal["y"] > rt).mean())
                pr["score"] = pr["p"]
            elif kind == "reg":
                mdl.fit(tr[feats], tr["y"])
                pr["score"] = mdl.predict(te[feats])
            else:
                qs = {}
                for q in (0.1, 0.5, 0.9):
                    qm = lgb.LGBMRegressor(objective="quantile", alpha=q, n_estimators=300, learning_rate=0.03,
                                           num_leaves=15, min_child_samples=200, verbose=-1, random_state=7)
                    qm.fit(tr[feats], tr["y"])
                    qs[q] = qm.predict(te[feats])
                pr["score"] = qs[0.5]
                pr["q10"], pr["q90"] = qs[0.1], qs[0.9]
            preds.setdefault(name, []).append(pr)
        # référence simple : momentum relatif seul (aucun apprentissage)
        pr = te[["t", "market", "y"]].copy()
        pr["score"] = te["mom_rel"]
        preds.setdefault("reference_momentum_relatif", []).append(pr)
    out: dict[str, Any] = {"intervalle": interval, "horizon_bougies": h, "cout_aller_retour": rt,
                           "n_lignes": len(D), "n_marches": int(D["market"].nunique()), "modeles": {}}
    for name, parts in preds.items():
        pr = pd.concat(parts)
        ts_keep = np.sort(pr["t"].unique())[::h]  # dates NON chevauchantes pour l'économie
        ic_t, spread_t = [], []
        for t_, g in pr.groupby("t"):
            if len(g) < 8:
                continue
            ic_t.append(spearmanr(g["score"], g["y"]).statistic)
            if t_ in set(ts_keep):
                top = g.nlargest(max(1, len(g) // 5), "score")["y"].mean()
                spread_t.append(top - rt - g["y"].mean())
        ic = np.array([x for x in ic_t if np.isfinite(x)])
        sp = np.array(spread_t)
        row: dict[str, Any] = {"ic_moyen": float(ic.mean()) if len(ic) else float("nan"),
                               "ic_ic95": block_bootstrap_ci(ic, np.mean, block=20) if len(ic) > 40 else None,
                               "top_quintile_net_vs_univers": float(sp.mean()) if len(sp) else float("nan"),
                               "top_quintile_ic95": block_bootstrap_ci(sp, np.mean, block=10) if len(sp) > 30 else None}
        if "p" in pr:
            yb = (pr["y"] > rt).astype(float).to_numpy()
            sk, lo, hi = brier_skill_ci(yb, pr["p"].to_numpy(), pr["base"].to_numpy(), block=200)
            row.update({"brier": float(brier_score_loss(yb, pr["p"])), "brier_skill": sk, "brier_skill_ic95": (lo, hi)})
        if "q10" in pr:
            row["couverture_q10_q90"] = float(((pr["y"] >= pr["q10"]) & (pr["y"] <= pr["q90"])).mean())
        ci = row["top_quintile_ic95"]
        row["retenu"] = bool(ci and ci[0] > 0 and row["ic_ic95"] and row["ic_ic95"][0] > 0)
        out["modeles"][name] = row
        print(f"modele {name:28s} IC {row['ic_moyen']:+.4f} {row['ic_ic95']} top-q net {row['top_quintile_net_vs_univers']:+.4f} "
              f"{ci} retenu={row['retenu']}", flush=True)
    # régimes : règles vs mélange gaussien (BTC), ablation sur la référence
    btc = data.get("BTC-EUR")
    if btc is not None:
        from backend.models.regime import Regime, classify_regime
        rules = pd.Series(classify_regime(btc.ffill()), index=btc.index)
        X = pd.DataFrame({"r": np.log(btc["close"]).diff(int(7 * b)), "v": np.log(btc["close"]).diff().rolling(int(14 * b)).std()}).dropna()
        first_fit = X.index[X.index < times[cuts[0]]]
        gm = GaussianMixture(3, random_state=7).fit(X.loc[first_fit])  # ajusté sur le passé uniquement
        lab_ = pd.Series(gm.predict(X), index=X.index)
        bull_comp = int(pd.Series(X.loc[first_fit, "r"]).groupby(lab_.loc[first_fit]).mean().idxmax())
        ref = pd.concat(preds["reference_momentum_relatif"])
        res_reg = {}
        for rname, mask_fn in (("toujours", lambda t: True), ("regles_haussier", lambda t: rules.get(t) == Regime.BULLISH.value),
                               ("gmm_haussier", lambda t: lab_.get(t) == bull_comp)):
            vals = []
            for t_, g in ref.groupby("t"):
                if len(g) < 8 or not mask_fn(t_):
                    continue
                vals.append(g.nlargest(max(1, len(g) // 5), "score")["y"].mean() - rt)
            v = np.array(vals[::h])
            res_reg[rname] = {"n": len(v), "net_moyen_par_periode": float(v.mean()) if len(v) else float("nan"),
                              "ic95": block_bootstrap_ci(v, np.mean, block=10) if len(v) > 30 else None}
        out["regimes"] = res_reg
    return out


def part_venue(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    from backend.execution.venue import FeeSchedule, compare_routes

    snaps = sorted(Path(a.snapshots).glob("snapshot_*.json"))
    if not snaps:
        return {"erreur": "aucun instantané de carnet"}
    snap = json.loads(snaps[-1].read_text())
    usdc_eur = None
    try:
        u = store.load_real("USDC-EUR", "1h")
        r = np.log(u["close"]).diff().dropna()
        dev = (u["close"] / u["close"].rolling(24 * 30, min_periods=24).median() - 1).abs()
        usdc_eur = {"vol_journaliere": float(r.std() * math.sqrt(24)), "ecart_max_vs_mediane_30j": float(dev.max())}
    except Exception as e:  # noqa: BLE001
        print(f"USDC-EUR indisponible : {e}", flush=True)
    res = compare_routes(snap["rows"], FeeSchedule(), usdc_eur)
    res["instantane_ts"] = snap["ts"]
    res["fx_usdc_eur"] = usdc_eur
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", required=True, choices=["xs", "exec", "strat", "models", "venue"])
    ap.add_argument("--base", default="1h")
    ap.add_argument("--intervals", default="1h,2h,4h,8h,12h,1d")
    ap.add_argument("--quotes", default="EUR,USDC")
    ap.add_argument("--model-interval", default="4h")
    ap.add_argument("--strat-markets", type=int, default=10)
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--snapshots", default=str(ROOT / "data" / "snapshots"))
    ap.add_argument("--lab-db", default=str(ROOT / "data" / "lab" / "lab3.db"))
    ap.add_argument("--out", default=str(ROOT / "reports-ci"))
    ap.add_argument("--synthetic-smoke", action="store_true")
    a = ap.parse_args()
    global SMOKE
    SMOKE = a.synthetic_smoke
    store = CandleStore(a.root)
    t0 = time.time()
    fn = {"xs": part_xs, "exec": part_exec, "strat": part_strat, "models": part_models, "venue": part_venue}[a.part]
    res = fn(a, store)
    res["_meta"] = {"part": a.part, "duree_s": round(time.time() - t0, 1), "periode": "RECHERCHE (jusqu'au 25/11/2026)",
                    "donnees": "SYNTHÉTIQUES (test de code)" if SMOKE else "RÉELLES (API publique Bitvavo)"}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, f"phase3_{a.part}_{a.intervals.replace(',', '-')}.json").write_text(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE3_JSON_DEBUT")
    print(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE3_JSON_FIN")


if __name__ == "__main__":
    main()
