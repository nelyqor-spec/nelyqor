#!/usr/bin/env python3
"""Recherche — phase 4 (rôle LAB : aucune clé, aucun ordre). Données <= 25/11/2026 = RECHERCHE (déjà vues).

  --part decomp    écart marché / limite décomposé (commissions, spread, prix, décalage, non-exécutions)
  --part lobsim    ordres rejoués sur les TRANSACTIONS publiques réelles (+ carnets collectés) : 3 scénarios
  --part rotation  pourquoi 4 h ? phase du rééquilibrage, agrégation, univers effectif, détention, hypothèses H1-H7
  --part regime    ablation du filtre BTC vs 6 alternatives, exposition constante, régimes aléatoires par blocs
  --part ai        IA en FILTRE SECONDAIRE uniquement (jamais déclencheur), comparée à une règle simple
  --part sizing    égal / volatilité / Kelly fractionné / CVaR / robuste ; réduction > 45/100 conservée

Les hypothèses H1-H7 et toutes les grilles ci-dessous sont PRÉ-ENREGISTRÉES (écrites avant exécution) et
comptées dans le nombre d'essais. Aucun résultat de cette phase n'est une validation indépendante.
JSON imprimé entre PHASE4_JSON_DEBUT / PHASE4_JSON_FIN.
"""
from __future__ import annotations

import argparse
import asyncio
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

import scripts.research_phase3 as R3  # noqa: E402
from backend.core.config import ROOT  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.research.metrics import deflated_sharpe  # noqa: E402
from backend.research.xsection import XSConfig, build_panel, eligible  # noqa: E402
from backend.research.xsection_v2 import (  # noqa: E402
    EXEC_MODELS,
    SIZING,
    XS2Options,
    block_shuffle,
    order_effects,
    regime_exposures,
    run_xs2,
)

SMOKE = False
TRIALS: list[tuple[str, float]] = []  # (intervalle, Sharpe par observation) de chaque essai (DSR)


def universe(store: CandleStore, base: str, quote: str = "EUR") -> dict[str, pd.DataFrame]:
    R3.SMOKE = SMOKE
    return R3.load_universe(store, base, [quote])


def tolerant_derive(raw: dict[str, pd.DataFrame], interval: str) -> dict[str, pd.DataFrame]:
    """Agrégation TOLÉRANTE : une bougie agrégée existe dès qu'au moins une bougie 1 h existe (Bitvavo n'émet
    pas de bougie sans transaction : une heure absente n'est pas forcément un trou). Contrôle de sensibilité."""
    out = {}
    for m, df in raw.items():
        idx = pd.to_datetime(df.index, unit="ms", utc=True)
        d = df[["open", "high", "low", "close", "volume"]].set_axis(idx)
        agg = d.resample(R3.RULES[interval], label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(how="all")
        agg.index = pd.Index(agg.index.as_unit("ms").astype("int64"), dtype="int64")
        out[m] = agg
    return R3.align(out)


def metrics(r: Any, interval: str, extra: dict[str, Any] | None = None, count: bool = True) -> dict[str, Any]:
    from backend.research.xsection import xs_metrics
    m = xs_metrics(r, R3.ppy(interval))
    eq = r.equity.dropna()
    h = len(eq) // 2
    keep = {k: m[k] for k in ("rendement_net", "trades", "profit_factor", "max_drawdown", "sharpe", "frais_eur",
                              "exposition", "economique", "limit_remplis", "limit_manques", "repli_marche",
                              "frais_economises_eur")}
    keep["moitie_1"] = float(eq.iloc[h] / eq.iloc[0] - 1) if h > 1 else None
    keep["moitie_2"] = float(eq.iloc[-1] / eq.iloc[h] - 1) if h > 1 else None
    keep["par_annee"] = R3.per_year(r.equity)
    rets = eq.pct_change().dropna()
    keep["annees"] = m["annees"]
    keep["cvar95_bougie"] = float(-rets[rets <= rets.quantile(0.05)].mean()) if len(rets) > 50 else None
    if count:
        TRIALS.append((interval, m["sharpe"] / math.sqrt(R3.ppy(interval))))
    if extra:
        keep.update(extra)
    return keep


def dsr_of(best_sharpe_ann: float, interval: str, years: float, n_prior: int = 106) -> dict[str, Any]:
    """DSR : nombre d'essais = tous les essais (phase 3 + phase 4) ; variance des Sharpe estimée sur les essais
    du MÊME intervalle (les Sharpe par bougie ne sont pas comparables entre intervalles)."""
    n = len(TRIALS) + n_prior
    same = [s for iv, s in TRIALS if iv == interval]
    var = float(np.var(same)) if len(same) > 1 else 0.0
    return {"n_essais_cumules": n, "dsr": deflated_sharpe(best_sharpe_ann / math.sqrt(R3.ppy(interval)),
                                                          int(years * R3.ppy(interval)), n, var)}


def log(*x: Any) -> None:
    print(*x, flush=True)


# ------------------------------------------------------------------------------------------- décomposition
def part_decomp(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    raw = universe(store, a.base)
    out: dict[str, Any] = {}
    for interval in a.intervals.split(","):
        data = R3.derive(raw, interval, a.base)
        for variant in ("base", "detention_min_3j"):
            cfg = R3.make_variant(interval, variant)
            P = build_panel(data, cfg)
            res, eff = {}, {}
            for ex in EXEC_MODELS:
                r = run_xs2(data, cfg, XS2Options(exec_model=ex, forward_bars=max(1, int(R3.bars_per_day(interval)))), panel=P)
                res[ex] = metrics(r, interval, count=False)
                if ex in ("limit", "hybrid"):
                    eff[ex] = order_effects(r.stats["ordres"])
                log(f"decomp {interval} {variant:16s} {ex:22s} net {res[ex]['rendement_net']:+.1%} frais {res[ex]['frais_eur']:.1f}")
            g = {k: v["rendement_net"] for k, v in res.items()}
            attrib = {"commissions_maker_vs_taker": g["market_maker"] - g["market"],
                      "spread_et_glissement": g["market_maker_nospread"] - g["market_maker"],
                      "prix_cloture_vs_ouverture_suivante": g["close_maker"] - g["market_maker_nospread"],
                      "decalage_limite_5pb": g["limit_ideal"] - g["close_maker"],
                      "non_executions_et_selection": g["limit"] - g["limit_ideal"],
                      "total_limite_moins_marche": g["limit"] - g["market"],
                      "repli_hybride_vs_limite": g["hybrid"] - g["limit"]}
            key = f"{interval}_{variant}"
            out[key] = {"modeles": res, "attribution_points": attrib, "effets_ordres": eff}
            if variant == "base":
                sens = {}
                grid = {"through_bps": [0.0, 10.0, 25.0], "max_volume_share": [0.01], "limit_ttl_bars": [2 * cfg.limit_ttl_bars, 4 * cfg.limit_ttl_bars],
                        "limit_offset_bps": [0.0, 15.0]}
                for k, vals in grid.items():
                    for v in vals:
                        c2 = replace(cfg, **{k: v})
                        r = run_xs2(data, c2, XS2Options(exec_model="limit"), panel=P)
                        sens[f"{k}={v}"] = metrics(r, interval, count=False)["rendement_net"]
                        log(f"sensibilite {interval} {k}={v}: limite {sens[f'{k}={v}']:+.1%}")
                out[key]["sensibilite_limite"] = sens
    return out


# ------------------------------------------------------------------------------------------- lobsim
def part_lobsim(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    from backend.market_data.bitvavo_rest import BitvavoREST
    from backend.research.lob_sim import SCENARIOS, Order, Tape, adaptive_ttl, evaluate_order, summarize
    from scripts.collect_microstructure import history, load_books, load_trades

    micro = Path(a.micro)
    out: dict[str, Any] = {"donnees": "RÉELLES : transactions publiques Bitvavo ; carnets seulement pour la période collectée en direct"}
    # 1) ordres limités réellement émis par la rotation 4 h (variante de base) sur les 14 derniers jours de données
    raw = universe(store, a.base)
    data = R3.derive(raw, "4h", a.base)
    cfg = R3.make_variant("4h", "base")
    P = build_panel(data, cfg)
    r = run_xs2(data, cfg, XS2Options(exec_model="limit"), panel=P)
    last_ts = int(P["close"].index[-1])
    recent = [o for o in r.stats["ordres"] if o["ts"] >= last_ts - a.lob_days * 86_400_000]
    out["ordres_rotation_recents"] = len(recent)
    need = sorted({o["market"] for o in recent})
    if not SMOKE and need:
        rest = BitvavoREST(os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2"))
        out["collecte_marches_rotation"] = asyncio.run(history(rest, micro, need, a.lob_days + 1, a.lob_budget))
    rows_bar_vs_lob: list[dict[str, Any]] = []
    per_sc: dict[str, list[dict[str, Any]]] = {s: [] for s in SCENARIOS}
    step = 4 * 3_600_000
    for o in recent:
        trades = load_trades(micro, o["market"])
        if not trades:
            continue
        tape = Tape(trades, load_books(micro, o["market"]))
        t0 = o["ts"] + step  # décision à la clôture de la bougie (index = ouverture) -> soumission à la clôture
        if tape.ts.size == 0 or t0 < tape.ts[0] or t0 + step > tape.ts[-1]:
            continue
        ref = tape.ref_price(t0)
        if not ref or not o.get("px_limite"):
            continue
        amt = (o.get("eur") or 30.0) / ref
        bar_fill = o["statut"] == "rempli"
        rowc = {"market": o["market"], "side": o["side"], "barre_rempli": bar_fill}
        for sname, sc in SCENARIOS.items():
            ev = evaluate_order(tape, Order(o["side"], float(o["px_limite"]), amt, t0, step), sc, 0.0015, 0.0025)
            per_sc[sname].append(ev)
            rowc[sname] = ev["statut"] in ("filled", "partial")
        rows_bar_vs_lob.append(rowc)
    out["rotation_rejouee"] = {"n": len(rows_bar_vs_lob), "par_scenario": {s: summarize(v) for s, v in per_sc.items() if v}}
    if rows_bar_vs_lob:
        conf = {}
        for s in SCENARIOS:
            conf[s] = {"barre_oui_lob_oui": sum(1 for x in rows_bar_vs_lob if x["barre_rempli"] and x[s]),
                       "barre_oui_lob_non": sum(1 for x in rows_bar_vs_lob if x["barre_rempli"] and not x[s]),
                       "barre_non_lob_oui": sum(1 for x in rows_bar_vs_lob if not x["barre_rempli"] and x[s]),
                       "barre_non_lob_non": sum(1 for x in rows_bar_vs_lob if not x["barre_rempli"] and not x[s])}
        out["rotation_rejouee"]["accord_modele_bougies_vs_transactions"] = conf
    # 2) grille systématique sur les marchés liquides collectés : toutes les 4 h, achat ET vente, 3 décalages
    grid: dict[str, Any] = {}
    for m in a.grid_markets.split(","):
        trades = load_trades(micro, m)
        if len(trades) < 1000:
            continue
        books = load_books(micro, m)
        tape = Tape(trades, books)
        qest = None
        if books:
            sizes = [float(b["bids"][0][1]) for b in books if b.get("bids")]
            qest = float(np.median(sizes)) if sizes else None
        t = int(tape.ts[0]) + 3_600_000
        res_m: dict[str, list[dict[str, Any]]] = {}
        bar_agree: dict[str, list[int]] = {}
        while t + step + 3_600_000 < int(tape.ts[-1]):
            ref = tape.ref_price(t)
            if ref:
                for side in ("buy", "sell"):
                    for off in (0.0, 5.0, 15.0):
                        px = ref * (1 - off / 1e4) if side == "buy" else ref * (1 + off / 1e4)
                        amt = 30.0 / ref
                        # modèle « bougies » sur la même fenêtre : traversée >= 2 pb du prix
                        w = tape.window(t, t + step)
                        lo = float(tape.px[list(w)].min()) if len(w) else np.inf
                        hi = float(tape.px[list(w)].max()) if len(w) else -np.inf
                        bar = (lo < px * (1 - 2e-4)) if side == "buy" else (hi > px * (1 + 2e-4))
                        for sname, sc in SCENARIOS.items():
                            for ttl_kind in ("fixe_4h", "adaptatif"):
                                ttl = step if ttl_kind == "fixe_4h" else adaptive_ttl(tape, side, px, t, qest or 0.0, amt)
                                ev = evaluate_order(tape, Order(side, px, amt, t, ttl, fallback_market=False), sc,
                                                    0.0015, 0.0025, queue_estimate=qest)
                                k = f"{side}_{int(off)}pb_{sname}_{ttl_kind}"
                                res_m.setdefault(k, []).append(ev)
                                if ttl_kind == "fixe_4h":
                                    bar_agree.setdefault(f"{side}_{int(off)}pb_{sname}", []).append(
                                        int(bar) * 2 + int(ev["statut"] in ("filled", "partial")))
            t += step
        grid[m] = {"periode": [int(tape.ts[0]), int(tape.ts[-1])], "transactions": int(tape.ts.size),
                   "carnets": len(books), "file_estimee_mediane": qest,
                   "resultats": {k: summarize(v) for k, v in res_m.items()},
                   "accord_bougies": {k: {"barre_oui_lob_oui": v.count(3), "barre_oui_lob_non": v.count(2),
                                          "barre_non_lob_oui": v.count(1), "barre_non_lob_non": v.count(0)}
                                      for k, v in bar_agree.items()}}
        log(f"lobsim grille {m}: {tape.ts.size} transactions, {len(books)} carnets, {len(res_m)} cellules")
    out["grille"] = grid
    return out


# ------------------------------------------------------------------------------------------- rotation
H_SCORES = ("H1_force_relative_multi", "H2_ajuste_volatilite", "H3_confirmation_multi_horizon", "H4_momentum_residuel_btc")
H_OPTS = ("H5_rotation_si_gain_net", "H6_sortie_regime_defavorable", "H7_reserve_dynamique")


def custom_scores(P: dict[str, pd.DataFrame], cfg: XSConfig, interval: str) -> dict[str, pd.DataFrame]:
    b = R3.bars_per_day(interval)
    logc = np.log(P["close"])

    def z(df: pd.DataFrame) -> pd.DataFrame:
        df = df.replace([np.inf, -np.inf], np.nan)
        sd = df.std(axis=1).replace(0, np.nan)
        return df.sub(df.mean(axis=1), axis=0).div(sd, axis=0).clip(-3, 3).fillna(0).where(df.notna())

    moms = {d: logc.diff(max(2, int(d * b))) for d in (3, 7, 14)}
    rel = {d: m.sub(m.median(axis=1), axis=0) for d, m in moms.items()}
    h1 = (z(rel[3]) + z(rel[7]) + z(rel[14])) / 3
    h2 = z(P["mom"] / (P["rv"] * np.sqrt(max(1, int(7 * b)))))
    allpos = (rel[3] > 0) & (rel[7] > 0) & (rel[14] > 0)
    h3 = h1.where(allpos)
    r1 = logc.diff()
    out = {"H1_force_relative_multi": h1, "H2_ajuste_volatilite": h2, "H3_confirmation_multi_horizon": h3}
    btc = next((c for c in P["close"].columns if c.startswith("BTC-")), None)
    if btc:
        rb = r1[btc]
        lb = max(10, int(30 * b))
        var_b = rb.rolling(lb, min_periods=lb // 2).var()
        beta = r1.rolling(lb, min_periods=lb // 2).cov(rb).div(var_b, axis=0)
        resid = r1 - beta.mul(rb, axis=0)
        out["H4_momentum_residuel_btc"] = z(resid.rolling(max(2, int(7 * b)), min_periods=2).sum())
    return out


def part_rotation(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    raw = universe(store, a.base)
    out: dict[str, Any] = {"diagnostics": {}, "phase": {}, "agregation_tolerante": {}, "detention": {}, "hypotheses": {}}
    for interval in a.intervals.split(","):
        data = R3.derive(raw, interval, a.base)
        cfg0 = R3.make_variant(interval, "base")
        P = build_panel(data, cfg0)
        idx = P["close"].index
        n_elig = [int(eligible(P, cfg0, i).sum()) for i in range(0, len(idx), max(1, len(idx) // 300))]
        nan_frac = float(P["close"].isna().to_numpy()[:, P["close"].notna().any().to_numpy()].mean())
        hours = sorted({int(pd.Timestamp(int(idx[i]), unit="ms", tz="UTC").hour) for i in range(0, min(len(idx), 20 * cfg0.rebalance_bars), cfg0.rebalance_bars)})
        out["diagnostics"][interval] = {"univers_eligible_moyen": float(np.mean(n_elig)), "univers_eligible_max": int(max(n_elig)),
                                        "part_bougies_manquantes": nan_frac, "heure_reequilibrage_utc_phase0": hours,
                                        "rebalance_bars": cfg0.rebalance_bars, "premiere_bougie": int(idx[0])}
        log(f"diag {interval}: univers moyen {np.mean(n_elig):.0f}, bougies manquantes {nan_frac:.1%}, heure {hours}")
        rb = cfg0.rebalance_bars
        phases = list(range(rb)) if rb <= 6 else list(range(0, rb, max(1, rb // 8)))
        for variant in ("base", "detention_min_3j"):
            cfg = R3.make_variant(interval, variant)
            ph = {}
            for p in phases:
                r = run_xs2(data, cfg, XS2Options(phase=p), panel=P)
                ph[str(p)] = metrics(r, interval)
                log(f"phase {interval} {variant} p={p}: net {ph[str(p)]['rendement_net']:+.1%}")
            v = np.array([x["rendement_net"] for x in ph.values()])
            out["phase"][f"{interval}_{variant}"] = {"par_phase": ph, "min": float(v.min()), "mediane": float(np.median(v)),
                                                     "max": float(v.max()), "part_positive": float((v > 0).mean())}
        if interval != a.base:
            td = tolerant_derive(raw, interval)
            Pt = build_panel(td, cfg0)
            for variant in ("base", "detention_min_3j"):
                r = run_xs2(td, R3.make_variant(interval, variant), XS2Options(), panel=Pt)
                out["agregation_tolerante"][f"{interval}_{variant}"] = metrics(r, interval)
                log(f"agregation tolerante {interval} {variant}: {out['agregation_tolerante'][f'{interval}_{variant}']['rendement_net']:+.1%}")
        if interval in ("1h", "4h", "1d"):
            for days in (0, 1, 2, 3, 4, 5, 7):
                cfg = R3.make_variant(interval, "base", min_hold_bars=int(days * R3.bars_per_day(interval)))
                r = run_xs2(data, cfg, XS2Options(), panel=P)
                out["detention"][f"{interval}_{days}j"] = metrics(r, interval)
                log(f"detention {interval} {days}j: {out['detention'][f'{interval}_{days}j']['rendement_net']:+.1%}")
        scores = custom_scores(P, cfg0, interval)
        exps = regime_exposures(P, cfg0)
        gv = P["rv"].median(axis=1).to_numpy(float) * math.sqrt(R3.bars_per_day(interval))
        dyn = exps["btc"] * np.clip(0.03 / np.where(gv > 0, gv, np.nan), 0.25, 1.0)
        for hold_days in (0, 3):
            for h in H_SCORES + H_OPTS:
                cfg = R3.make_variant(interval, "base", min_hold_bars=int(hold_days * R3.bars_per_day(interval)))
                kw: dict[str, Any] = {}
                opt = XS2Options()
                if h in scores:
                    kw["score"] = scores[h]
                elif h == "H5_rotation_si_gain_net":
                    opt = XS2Options(rotation_hurdle=1.0)
                elif h == "H6_sortie_regime_defavorable":
                    opt = XS2Options(exit_on_risk_off=True)
                elif h == "H7_reserve_dynamique":
                    kw["exposure"] = np.nan_to_num(dyn)
                else:
                    continue
                r = run_xs2(data, cfg, opt, panel=P, **kw)
                k = f"{interval}_{h}_detention{hold_days}j"
                out["hypotheses"][k] = metrics(r, interval)
                log(f"hyp {k}: net {out['hypotheses'][k]['rendement_net']:+.1%} DD {out['hypotheses'][k]['max_drawdown']:.0%}")
    allh = [(v["sharpe"], k) for k, v in out["hypotheses"].items()]
    if allh:
        s, k = max(allh)
        iv = k.split("_")[0]
        out["meilleure_hypothese"] = {"cle": k, "sharpe": s, **dsr_of(s, iv, out["hypotheses"][k]["annees"])}
    out["n_essais_partie"] = len(TRIALS)
    return out


# ------------------------------------------------------------------------------------------- régime
def part_regime(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    raw = universe(store, a.base)
    out: dict[str, Any] = {}
    rng = np.random.default_rng(11)
    for interval in a.intervals.split(","):
        data = R3.derive(raw, interval, a.base)
        cfg = R3.make_variant(interval, "base")
        P = build_panel(data, cfg)
        ex = regime_exposures(P, cfg)
        ones = np.ones(len(P["close"]))
        runs: dict[str, np.ndarray] = {"aucun_filtre": ones, **{f"seul_{k}": v for k, v in ex.items()}}
        for k, v in ex.items():
            if k != "btc":
                runs[f"btc_et_{k}"] = ex["btc"] * v
        runs["vote_majoritaire"] = (np.mean(np.vstack(list(ex.values())), axis=0) > 0.5).astype(float)
        f_on = float(np.nanmean(ex["btc"]))
        runs["exposition_constante_egale"] = ones * f_on
        res = {}
        for k, e in runs.items():
            r = run_xs2(data, cfg, XS2Options(), panel=P, exposure=e)
            res[k] = metrics(r, interval, {"part_temps_actif": float(np.mean(e > 0)), "exposition_moyenne_cible": float(np.mean(e))})
            log(f"regime {interval} {k:32s} net {res[k]['rendement_net']:+.1%} DD {res[k]['max_drawdown']:.0%}")
        # régime aléatoire de même proportion et même persistance (blocs de 30 jours)
        blk = max(5, int(30 * R3.bars_per_day(interval)))
        rnd = []
        for _ in range(a.shuffles):
            e = block_shuffle(ex["btc"], blk, rng)
            rnd.append(metrics(run_xs2(data, cfg, XS2Options(), panel=P, exposure=e), interval, count=False)["rendement_net"])
        real = res["seul_btc"]["rendement_net"]
        out[interval] = {"variantes": res, "aleatoire_par_blocs": {"n": len(rnd), "mediane": float(np.median(rnd)) if rnd else None,
                                                                   "q05": float(np.quantile(rnd, 0.05)) if rnd else None,
                                                                   "q95": float(np.quantile(rnd, 0.95)) if rnd else None,
                                                                   "rang_du_filtre_btc": float(np.mean(np.array(rnd) < real)) if rnd else None},
                         "part_temps_btc_haussier": f_on}
        # minutage du BTC seul : acheter-conserver, filtré, exposition constante (coûts 0,30 % par bascule)
        btc = P["close"].get("BTC-EUR")
        if btc is not None:
            r1 = btc.pct_change().fillna(0).to_numpy()
            sig = np.r_[0, ex["btc"][:-1]]  # position décidée à la clôture précédente
            sw = np.abs(np.diff(np.r_[0, sig]))
            timed = np.prod(1 + sig * r1 - sw * 0.003) - 1
            const = np.prod(1 + f_on * r1) - 1
            out[interval]["btc_seul"] = {"acheter_conserver": float(np.prod(1 + r1) - 1), "filtre_btc": float(timed),
                                         "exposition_constante": float(const), "bascules": int(sw.sum())}
    return out


# ------------------------------------------------------------------------------------------- IA secondaire
def part_ai(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    interval = a.ai_interval
    raw = universe(store, a.base)
    data = R3.derive(raw, interval, a.base)
    cfg = R3.make_variant(interval, "base")
    P = build_panel(data, cfg)
    b = R3.bars_per_day(interval)
    idx = P["close"].index
    n = len(idx)
    ex = regime_exposures(P, cfg)
    breadth = pd.Series(ex["proportion_haussiere"], index=idx)
    H = max(1, int(3 * b))
    C = P["close"]
    fwd = C.shift(-H) / C - 1
    fwd1 = C.shift(-max(1, int(b))) / C - 1
    rt = 2 * (cfg.fee_taker + cfg.slippage_bps / 1e4) + P["spread"].fillna(0.001)
    rows = []
    for i in range(0, n - H, cfg.rebalance_bars):
        ok = eligible(P, cfg, i)
        sc = P["score"].iloc[i].where(ok).dropna().sort_values(ascending=False).head(10)
        for m in sc.index:
            rows.append({"i": i, "market": m, "score": sc[m], "rang": list(sc.index).index(m), "mom": P["mom"].iloc[i][m],
                         "trend": P["trend"].iloc[i][m], "rv": P["rv"].iloc[i][m], "liq": np.log1p(P["liq"].iloc[i][m]),
                         "spread": P["spread"].iloc[i][m], "btc": ex["btc"][i], "breadth": breadth.iloc[i],
                         "y_net": fwd.iloc[i][m] - rt.iloc[i][m], "y1": fwd1.iloc[i][m]})
    D = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna()
    feats = ["score", "rang", "mom", "trend", "rv", "liq", "spread", "btc", "breadth"]
    D["bad"] = (D["y_net"] < 0).astype(int)
    D["extreme"] = (D["y1"] < -0.10).astype(int)
    cuts = np.linspace(n // 3, n - H, 5).astype(int)
    out: dict[str, Any] = {"intervalle": interval, "n_candidats": len(D), "horizon_bougies": H}
    flags: dict[str, pd.Series] = {"lightgbm": pd.Series(False, index=D.index), "logistique": pd.Series(False, index=D.index),
                                   "regle_volatilite": pd.Series(False, index=D.index), "regle_tendance": pd.Series(False, index=D.index)}
    cls: dict[str, dict[str, list[float]]] = {}
    ext_scores: dict[str, list[tuple[float, float]]] = {"lightgbm": [], "regle_volatilite": []}
    for lo, hi in zip(cuts[:-1], cuts[1:], strict=True):
        tr = D[D["i"] < lo - H]
        te = D[(D["i"] >= lo) & (D["i"] < hi)]
        if len(tr) < 200 or len(te) < 20 or tr["bad"].nunique() < 2:
            continue
        base_rate = tr["bad"].mean()
        models = {"lightgbm": lgb.LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=15, min_child_samples=50, verbose=-1),
                  "logistique": make_pipeline(StandardScaler(), LogisticRegression(max_iter=500))}
        for name, mdl in models.items():
            mdl.fit(tr[feats], tr["bad"])
            p_tr = mdl.predict_proba(tr[feats])[:, 1]
            thr = float(np.quantile(p_tr, 0.8))  # écarte les 20 % de candidats jugés les pires (seuil appris)
            p = mdl.predict_proba(te[feats])[:, 1]
            flags[name].loc[te.index] = p > thr
            c = cls.setdefault(name, {"brier_skill": [], "auc": []})
            c["brier_skill"].append(1 - brier_score_loss(te["bad"], p) / max(1e-12, brier_score_loss(te["bad"], np.full(len(te), base_rate))))
            if te["bad"].nunique() == 2:
                c["auc"].append(roc_auc_score(te["bad"], p))
        vthr = float(tr["rv"].quantile(0.8))
        flags["regle_volatilite"].loc[te.index] = te["rv"] > vthr
        flags["regle_tendance"].loc[te.index] = te["trend"] < 0
        if tr["extreme"].nunique() == 2:
            em = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=15, min_child_samples=50, verbose=-1)
            em.fit(tr[feats], tr["extreme"])
            pe = em.predict_proba(te[feats])[:, 1]
            ext_scores["lightgbm"].extend(zip(pe, te["extreme"], strict=True))
            ext_scores["regle_volatilite"].extend(zip(te["rv"].rank(pct=True), te["extreme"], strict=True))
    out["classification_mauvaises_opportunites"] = {k: {kk: float(np.mean(vv)) if vv else None for kk, vv in v.items()} for k, v in cls.items()}
    # effet ÉCONOMIQUE : candidats écartés (score -> NaN) uniquement sur les périodes de test
    t0, t1 = int(cuts[0]), int(cuts[-1])
    base_r = run_xs2(data, cfg, XS2Options(), panel=P)
    eqb = base_r.equity
    base_test = float(eqb.iloc[t1 - 1] / eqb.iloc[t0] - 1)
    econ = {"reference_sans_filtre": base_test}
    for name, fl in flags.items():
        S = P["score"].copy()
        for (i, m) in D.loc[fl[fl].index, ["i", "market"]].itertuples(index=False):
            S.iat[int(i), S.columns.get_loc(m)] = np.nan
        r = run_xs2(data, cfg, XS2Options(), panel=P, score=S)
        econ[name] = float(r.equity.iloc[t1 - 1] / r.equity.iloc[t0] - 1)
        metrics(r, interval)
        log(f"ia filtre {name:18s} periode test {econ[name]:+.1%} (reference {base_test:+.1%}), écartés {int(fl.sum())}")
    out["filtre_economique_periode_test"] = econ
    for k, v in ext_scores.items():
        if v:
            s, y = np.array([x[0] for x in v]), np.array([x[1] for x in v])
            top = s >= np.quantile(s, 0.9)
            out.setdefault("pertes_extremes", {})[k] = {"n": len(v), "taux_base": float(y.mean()),
                                                       "taux_dans_decile_superieur": float(y[top].mean()),
                                                       "auc": float(roc_auc_score(y, s)) if len(set(y)) == 2 else None}
    # probabilité de remplissage d'un ordre limité (journal d'ordres du modèle à bougies)
    rl = run_xs2(data, cfg, XS2Options(exec_model="limit"), panel=P)
    od = pd.DataFrame([o for o in rl.stats["ordres"] if o["side"] == "buy" and o["statut"] in ("rempli", "manque")])
    if len(od) > 200:
        mi = {m: k for k, m in enumerate(P["close"].columns)}
        od["j"] = od["market"].map(mi)
        od["rv"] = [P["rv"].iat[int(i), int(j)] for i, j in zip(od["decision_i"], od["j"], strict=True)]
        od["spread"] = [P["spread"].iat[int(i), int(j)] for i, j in zip(od["decision_i"], od["j"], strict=True)]
        od["trend"] = [P["trend"].iat[int(i), int(j)] for i, j in zip(od["decision_i"], od["j"], strict=True)]
        od["y"] = (od["statut"] == "rempli").astype(int)
        od = od.replace([np.inf, -np.inf], np.nan).dropna(subset=["rv", "spread", "trend"])
        cut = int(len(od) * 0.6)
        tr, te = od.iloc[:cut], od.iloc[cut:]
        f2 = ["rv", "spread", "trend"]
        mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500)).fit(tr[f2], tr["y"])
        p = mdl.predict_proba(te[f2])[:, 1]
        bins = pd.qcut(tr["rv"], 3, duplicates="drop", retbins=True)[1]
        rule_map = tr.groupby(pd.cut(tr["rv"], bins, include_lowest=True), observed=False)["y"].mean()
        pr = pd.cut(te["rv"].clip(bins[0], bins[-1]), bins, include_lowest=True).map(rule_map).astype(float).fillna(tr["y"].mean())
        base = np.full(len(te), tr["y"].mean())
        bs0 = brier_score_loss(te["y"], base)
        out["probabilite_remplissage"] = {"n_test": len(te), "taux_remplissage": float(te["y"].mean()),
                                          "brier_skill_logistique": float(1 - brier_score_loss(te["y"], p) / bs0),
                                          "brier_skill_regle_terciles_vol": float(1 - brier_score_loss(te["y"], pr) / bs0),
                                          "avertissement": "étiquettes issues du modèle à BOUGIES (pas de transactions) : à recalibrer sur lobsim"}
    # classification de régime : logistique sur caractéristiques BTC/marché vs règle EMA
    btc = P["close"]["BTC-EUR"] if "BTC-EUR" in P["close"] else None
    if btc is not None:
        lr = np.log(btc)
        Xr = pd.DataFrame({"r7": lr.diff(int(7 * b)), "r30": lr.diff(int(30 * b)), "v": lr.diff().rolling(int(14 * b)).std(),
                           "breadth": breadth}, index=idx)
        ew = np.log(C).diff().mean(axis=1)
        y = (ew.rolling(int(7 * b)).sum().shift(-int(7 * b)) > 0).astype(float)
        dfr = pd.concat([Xr, y.rename("y")], axis=1).dropna()
        expo = np.zeros(n)
        for lo, hi in zip(cuts[:-1], cuts[1:], strict=True):
            trr = dfr[dfr.index < idx[max(0, lo - int(7 * b))]]
            ter = dfr[(dfr.index >= idx[lo]) & (dfr.index < idx[min(hi, n - 1)])]
            if len(trr) < 200 or len(ter) == 0:
                continue
            mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500)).fit(trr[["r7", "r30", "v", "breadth"]], trr["y"])
            pp = mdl.predict_proba(ter[["r7", "r30", "v", "breadth"]])[:, 1]
            pos = np.searchsorted(np.asarray(idx), np.asarray(ter.index))
            expo[pos] = (pp > 0.5).astype(float)
        rr = run_xs2(data, cfg, XS2Options(), panel=P, exposure=np.where(np.arange(n) < t0, ex["btc"], expo))
        metrics(rr, interval)
        out["classification_regime"] = {"logistique_periode_test": float(rr.equity.iloc[t1 - 1] / rr.equity.iloc[t0] - 1),
                                        "regle_btc_periode_test": base_test}
    return out


# ------------------------------------------------------------------------------------------- dimensionnement
def part_sizing(a: argparse.Namespace, store: CandleStore) -> dict[str, Any]:
    raw = universe(store, a.base)
    out: dict[str, Any] = {}
    for interval in a.intervals.split(","):
        data = R3.derive(raw, interval, a.base)
        for variant in ("base", "detention_min_3j"):
            cfg = R3.make_variant(interval, variant)
            P = build_panel(data, cfg)
            for sz in SIZING:
                r = run_xs2(data, cfg, XS2Options(sizing=sz), panel=P)
                k = f"{interval}_{variant}_{sz}"
                out[k] = metrics(r, interval)
                log(f"sizing {k:40s} net {out[k]['rendement_net']:+.1%} DD {out[k]['max_drawdown']:.0%} expo {out[k]['exposition']:.0%}")
        if interval == "4h":
            cfg = R3.make_variant(interval, "base")
            P = build_panel(data, cfg)
            sens = {}
            for k, c2, o2 in (("reserve_0.3", replace(cfg, reserve=0.3), XS2Options(sizing="volatilite")),
                              ("poids_max_0.2", replace(cfg, max_weight=0.2), XS2Options(sizing="volatilite")),
                              ("vol_cible_3pc", cfg, XS2Options(sizing="volatilite", target_daily_vol=0.03)),
                              ("vol_cible_6pc", cfg, XS2Options(sizing="volatilite", target_daily_vol=0.06)),
                              ("kelly_0.1", cfg, XS2Options(sizing="kelly", kelly_fraction=0.1)),
                              ("cvar_1pc", cfg, XS2Options(sizing="cvar", cvar_budget=0.01))):
                r = run_xs2(data, c2, o2, panel=P)
                sens[k] = metrics(r, interval)
                log(f"sizing sensibilite {k}: net {sens[k]['rendement_net']:+.1%} DD {sens[k]['max_drawdown']:.0%}")
            out["sensibilite_4h"] = sens
    out["regle_risque"] = "réduction quadratique du poids au-delà de 45/100 conservée dans toutes les méthodes"
    return out


def main() -> None:
    global SMOKE
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", required=True, choices=["decomp", "lobsim", "rotation", "regime", "ai", "sizing"])
    ap.add_argument("--base", default="1h")
    ap.add_argument("--intervals", default="1h,4h,1d")
    ap.add_argument("--ai-interval", default="4h")
    ap.add_argument("--shuffles", type=int, default=30)
    ap.add_argument("--micro", default=str(ROOT / "data" / "micro"))
    ap.add_argument("--grid-markets", default="BTC-EUR,ETH-EUR,SOL-EUR,XRP-EUR,ADA-EUR,DOGE-EUR")
    ap.add_argument("--lob-days", type=int, default=14)
    ap.add_argument("--lob-budget", type=float, default=900)
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--out", default=str(ROOT / "reports-ci"))
    ap.add_argument("--synthetic-smoke", action="store_true")
    a = ap.parse_args()
    SMOKE = a.synthetic_smoke
    store = CandleStore(a.root)
    t0 = time.time()
    fn = {"decomp": part_decomp, "lobsim": part_lobsim, "rotation": part_rotation, "regime": part_regime,
          "ai": part_ai, "sizing": part_sizing}[a.part]
    res = fn(a, store)
    res["_meta"] = {"part": a.part, "duree_s": round(time.time() - t0, 1), "essais_comptes": len(TRIALS),
                    "periode": "RECHERCHE (données <= 25/11/2026, déjà consultées)",
                    "donnees": "SYNTHÉTIQUES (test de code)" if SMOKE else "RÉELLES (API publique Bitvavo)"}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, f"phase4_{a.part}_{a.intervals.replace(',', '-')}.json").write_text(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE4_JSON_DEBUT")
    print(json.dumps(res, default=str, ensure_ascii=False))
    print("PHASE4_JSON_FIN")


if __name__ == "__main__":
    main()
