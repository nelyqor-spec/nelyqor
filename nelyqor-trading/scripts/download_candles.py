#!/usr/bin/env python3
"""Programme AUTONOME de téléchargement et de validation des bougies publiques Bitvavo (aucune clé).

Incrémental et reprenable : relancer la commande reprend où elle s'était arrêtée.
Stockage : Parquet zstd + manifeste (provenance, empreinte sha256, rapport de validation) sous
<root>/real/bitvavo/<MARCHÉ>/<intervalle>.parquet. Ne produit JAMAIS de données synthétiques.

  python scripts/download_candles.py --markets BTC-EUR,ETH-EUR,SOL-EUR --interval 1h --days 1100
  python scripts/download_candles.py --all-eur --interval 1h --days 1100      # univers complet (survivance)
  python scripts/download_candles.py --report                                 # couverture et qualité
Code de sortie 2 si une validation échoue (doublons contradictoires, OHLC incohérents...).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import ROOT  # noqa: E402
from backend.data.downloader import STEP_MS, coverage_report, download_market  # noqa: E402
from backend.data.store import CandleStore  # noqa: E402
from backend.market_data.bitvavo_rest import BitvavoREST  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="BTC-EUR,ETH-EUR,SOL-EUR")
    ap.add_argument("--all-eur", action="store_true", help="tous les marchés EUR, y compris suspendus (biais de survivance)")
    ap.add_argument("--interval", default="1h", choices=list(STEP_MS))
    ap.add_argument("--days", type=int, default=1100)
    ap.add_argument("--root", default=os.environ.get("NELYQOR_MARKET_DATA", str(ROOT / "data" / "market")))
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    store = CandleStore(a.root)
    if a.report:
        print(json.dumps(coverage_report(store, a.interval), indent=2))
        return 0
    base = os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2")
    rest = BitvavoREST(base)
    if a.all_eur:
        mk = [m["market"] for m in await rest.markets() if m.get("quote") == "EUR"]
    else:
        mk = a.markets.split(",")
    failed = False
    for m in mk:
        try:
            r = await download_market(rest, store, m, a.interval, a.days, base_url=base)
        except Exception as e:  # noqa: BLE001 — un marché en échec n'arrête pas les autres ; relancer reprend
            print(f"{m}: ÉCHEC {type(e).__name__}: {e}", flush=True)
            failed = True
            continue
        ok = r.report.get("ok", True)
        failed |= not ok
        print(f"{m}: +{r.new_rows} bougies (total {r.total_rows}, {r.requests} requêtes, "
              f"trous {r.report.get('missing_intervals')}, validation {'OK' if ok else 'ÉCHEC'})", flush=True)
    await rest.aclose()
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
