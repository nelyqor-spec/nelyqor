#!/usr/bin/env python3
"""Laboratoire : walk-forward + holdout de chaque famille de stratégies, modèles prédictifs, rapport.

Rôle LAB : aucune clé, aucune exécution. Usage :
  python scripts/run_lab.py --source db      # bougies Bitvavo réelles stockées (scripts/download_candles.py)
  python scripts/run_lab.py --source synthetic  # fixtures SYNTHÉTIQUES (aucune valeur probante)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

os.environ["NELYQOR_ROLE"] = "lab"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from backend.core.config import ROOT, load_strategy_params  # noqa: E402
from backend.models.predict import fit_and_evaluate  # noqa: E402
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
MAKERS = {"momentum": S.Momentum, "breakout": S.Breakout, "mean_reversion": S.MeanReversion,
          "trend_following": S.TrendFollowing, "swing": S.Swing, "rebound": S.Rebound}


def load_db(db: Database, markets: list[str], interval: str) -> dict[str, pd.DataFrame]:
    out = {}
    for m in markets:
        rows = db.q("SELECT ts, open, high, low, close, volume FROM candles WHERE market=? AND interval=? ORDER BY ts",
                    (m, interval))
        if rows:
            out[m] = pd.DataFrame([dict(r) for r in rows]).astype(float).set_index("ts")
            out[m].index = out[m].index.astype("int64")
    if not out:
        return out
    common = sorted(set.intersection(*(set(df.index) for df in out.values())))
    return {m: df.loc[common] for m, df in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["db", "synthetic"], default="db")
    ap.add_argument("--markets", default="BTC-EUR,ETH-EUR,SOL-EUR")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--bars", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--db", default=os.environ.get("NELYQOR_DB", str(ROOT / "data" / "nelyqor.db")))
    a = ap.parse_args()
    db = Database(a.db)
    markets = a.markets.split(",")
    if a.source == "db":
        data = load_db(db, markets, a.interval)
        if not data or len(next(iter(data.values()))) < 3000:
            sys.exit("Données réelles insuffisantes en base (>= 3000 bougies communes requises). "
                     "Lancer scripts/download_candles.py sur le serveur, ou --source synthetic.")
        source = f"Bitvavo {a.interval} (réel, {len(next(iter(data.values())))} bougies)"
    else:
        data = synthetic_universe(markets, a.bars, seed=a.seed)
        source = SYNTHETIC_LABEL
    params = load_strategy_params()
    sim = params["simulation"]
    cfg = BTConfig(fee_taker=float(sim["fee_taker"]), slippage_bps=float(sim["slippage_bps"]),
                   latency_bars=int(sim["latency_bars"]))
    lab = Lab(db, source, a.seed)
    t0 = time.time()
    results = []
    for name, maker in MAKERS.items():
        out = lab.evaluate(data, maker, params[name], GRIDS[name], cfg)
        h = out["holdout"]
        results.append({"strategy": name, "wf_oos_net": out["walk_forward_oos"]["net_return"],
                        "wf_oos_trades": out["walk_forward_oos"]["n_trades"], "holdout_net": h["net_return"],
                        "holdout_doubled_fees": out["holdout_frais_doubles"]["net_return"],
                        "holdout_wide_spread": out["holdout_spread_elargi"]["net_return"],
                        "pf": h["profit_factor"], "trades": h["n_trades"], "maxdd": h["max_drawdown"],
                        "sharpe": h["sharpe"], "ci95_trade": h["ci95_mean_trade"], "dsr": out["deflated_sharpe"],
                        "stabilite_parametres": out["stabilite_parametres"], "regimes": out["rendement_par_regime"],
                        "promu": out["promotion"]["promu"], "non_satisfaits": out["promotion"]["non_satisfaits"],
                        "buy_hold": out["benchmark_buy_hold"]})
        print(f"{name:16s} holdout {h['net_return']:+.2%} PF {h['profit_factor']:.2f} trades {h['n_trades']:4d} "
              f"promu={out['promotion']['promu']}", flush=True)
    # modèles prédictifs (référence logistique vs LightGBM), premier marché
    first = next(iter(data.values()))
    models = []
    for kind in ("logistic", "lightgbm"):
        for hz in (1, 6, 24):
            try:
                rep, _ = fit_and_evaluate(first, hz, cost=0.005, kind=kind, seed=a.seed)
                models.append({"modele": kind, "horizon_bougies": hz, "brier": rep.brier, "brier_reference": rep.brier_base,
                               "statut": rep.label, "n_test": rep.n_test})
            except Exception as e:  # noqa: BLE001
                models.append({"modele": kind, "horizon_bougies": hz, "erreur": str(e)})
    bh = results[0]["buy_hold"]
    report = {"date": datetime.now(UTC).isoformat(timespec="seconds"), "source": source, "marches": markets,
              "n_essais_total": lab.n_trials, "duree_s": round(time.time() - t0, 1), "resultats": results,
              "modeles": models, "buy_hold_btc": bh.get("BTC-EUR", next(iter(bh.values()))), "benchmark_euros": 0.0,
              "conclusion": ("Aucune stratégie promue : rester en simulation." if not any(r["promu"] for r in results)
                             else "Au moins une stratégie satisfait les critères sur ces données ; validation "
                                  "indépendante sur données réelles requise avant tout usage réel.")}
    db.kv_set("lab_report", report)
    rp = ROOT / "reports"
    rp.mkdir(exist_ok=True)
    tag = "synthetique" if a.source == "synthetic" else "bitvavo"
    (rp / f"lab_report_{tag}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({"conclusion": report["conclusion"], "essais": lab.n_trials, "duree_s": report["duree_s"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
