#!/usr/bin/env python3
"""Programme AUTONOME de téléchargement et de validation des bougies publiques Bitvavo (aucune clé).

Incrémental et reprenable : relancer la commande reprend où elle s'était arrêtée.
Stockage : Parquet zstd + manifeste (provenance, empreinte sha256, rapport de validation) sous
<root>/real/bitvavo/<MARCHÉ>/<intervalle>.parquet. Ne produit JAMAIS de données synthétiques.
Aucune bougie n'est inventée avant la première cotation : le premier horodatage renvoyé fait foi.

  python scripts/download_candles.py --markets BTC-EUR,ETH-EUR --interval 1h --days 1100
  python scripts/download_candles.py --universe --quotes EUR,USDC --interval 1h --days 1100 --max-markets 100
  python scripts/download_candles.py --report --interval 1h
Code de sortie 2 si une validation échoue (doublons contradictoires, OHLC incohérents...).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import ROOT  # noqa: E402
from backend.data.downloader import STEP_MS, coverage_report, download_market  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.market_data.bitvavo_rest import BitvavoREST  # noqa: E402


async def universe(rest: BitvavoREST, quotes: list[str], max_markets: int) -> list[str]:
    """Univers : marchés cotés dans les devises demandées, y compris suspendus (limite la survivance).
    L'ordre (volume 24 h actuel) ne sert qu'à prioriser la COLLECTE ; la recherche filtre la liquidité
    point-in-time à partir des bougies (jamais avec le volume d'aujourd'hui)."""
    mk = [m for m in await rest.markets() if m.get("quote") in quotes]
    try:
        vol = {t["market"]: float(t.get("volumeQuote") or 0) for t in await rest.ticker_24h()}
    except Exception:  # noqa: BLE001
        vol = {}
    mk.sort(key=lambda m: -vol.get(m["market"], 0.0))
    return [m["market"] for m in mk][:max_markets] if max_markets else [m["market"] for m in mk]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="BTC-EUR,ETH-EUR,SOL-EUR")
    ap.add_argument("--universe", action="store_true", help="univers complet des devises --quotes")
    ap.add_argument("--all-eur", action="store_true", help="alias historique de --universe --quotes EUR")
    ap.add_argument("--quotes", default="EUR")
    ap.add_argument("--max-markets", type=int, default=0)
    ap.add_argument("--interval", default="1h", choices=list(STEP_MS))
    ap.add_argument("--days", type=int, default=1100)
    ap.add_argument("--pause", type=float, default=0.05, help="pause entre requêtes (s), en plus du budget de poids")
    ap.add_argument("--time-budget", type=float, default=0, help="arrêt propre après N secondes (reprise au run suivant)")
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    store = CandleStore(a.root)
    if a.report:
        print(json.dumps(coverage_report(store, a.interval), indent=2))
        return 0
    base = os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2")
    rest = BitvavoREST(base)
    if a.universe or a.all_eur:
        mk = await universe(rest, a.quotes.split(",") if not a.all_eur else ["EUR"], a.max_markets)
    else:
        mk = a.markets.split(",")
    failed = False
    t0 = time.time()
    done = 0
    for m in mk:
        if a.time_budget and time.time() - t0 > a.time_budget:
            print(f"budget de temps atteint après {done} marchés : reprise au prochain run", flush=True)
            break
        try:
            r = await download_market(rest, store, m, a.interval, a.days, base_url=base, pause_s=a.pause)
        except Exception as e:  # noqa: BLE001 — un marché en échec n'arrête pas les autres ; relancer reprend
            print(f"{m}: ÉCHEC {type(e).__name__}: {str(e)[:160]}", flush=True)
            failed = True
            continue
        done += 1
        ok = r.report.get("ok", True)
        failed |= not ok
        print(f"{m} {a.interval}: +{r.new_rows} (total {r.total_rows}, {r.requests} req., trous "
              f"{r.report.get('missing_intervals')}, {'OK' if ok else 'ÉCHEC'})", flush=True)
    print(f"marchés traités : {done}/{len(mk)} ; budget API restant {rest.budget.remaining}", flush=True)
    await rest.aclose()
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
