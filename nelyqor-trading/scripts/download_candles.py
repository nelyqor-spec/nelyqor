#!/usr/bin/env python3
"""Télécharge l'historique de bougies Bitvavo (API publique, sans clé) dans la base SQLite.

À lancer sur le serveur (l'environnement de développement n'a pas accès à api.bitvavo.com).
Respecte le budget de poids de l'API. Usage :
  python scripts/download_candles.py --markets BTC-EUR,ETH-EUR,SOL-EUR --interval 1h --days 1100
  python scripts/download_candles.py --all-eur --interval 1h --days 365
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import ROOT  # noqa: E402
from backend.market_data.bitvavo_rest import BitvavoREST  # noqa: E402
from backend.storage.db import Database  # noqa: E402

STEP = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
        "1d": 86_400_000}


async def fetch(rest: BitvavoREST, db: Database, market: str, interval: str, days: int) -> int:
    step = STEP[interval]
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    n = 0
    cursor = end
    while cursor > start:
        rows = await rest.candles(market, interval, limit=1440, start=max(start, cursor - 1440 * step), end=cursor)
        if not rows:
            break
        now = int(time.time() * 1000)
        done = [r for r in rows if int(r[0]) + step <= now]  # bougies clôturées uniquement
        with db.tx() as c:
            c.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)",
                          [(market, interval, int(r[0]), r[1], r[2], r[3], r[4], r[5], now, "bitvavo") for r in done])
        n += len(done)
        oldest = min(int(r[0]) for r in rows)
        if oldest >= cursor:
            break
        cursor = oldest - 1
    return n


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="BTC-EUR,ETH-EUR,SOL-EUR")
    ap.add_argument("--all-eur", action="store_true")
    ap.add_argument("--interval", default="1h", choices=list(STEP))
    ap.add_argument("--days", type=int, default=1100)
    ap.add_argument("--db", default=os.environ.get("NELYQOR_DB", str(ROOT / "data" / "nelyqor.db")))
    a = ap.parse_args()
    rest = BitvavoREST(os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2"))
    db = Database(a.db)
    if a.all_eur:
        mk = [m["market"] for m in await rest.markets() if m.get("quote") == "EUR" and m.get("status") == "trading"]
    else:
        mk = a.markets.split(",")
    for m in mk:
        n = await fetch(rest, db, m, a.interval, a.days)
        print(f"{m}: {n} bougies", flush=True)
    await rest.aclose()


if __name__ == "__main__":
    asyncio.run(main())
