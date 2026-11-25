#!/usr/bin/env python3
"""Instantané RÉEL des conditions d'exécution Bitvavo (API publique, sans clé) : meilleurs prix, spreads,
profondeur du carnet à ±0,1/0,5/1 %, volumes 24 h, pour les marchés EUR et USDC + le marché de conversion
USDC-EUR. Sert à la comparaison EUR/USDC (les spreads et la profondeur ne sont PAS dans les bougies).
Un instantané n'est qu'un point dans le temps : à répéter (GitHub Actions planifié) pour une distribution.

  python scripts/market_snapshot.py --out data/snapshots --depth-markets 60
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
from backend.market_data.bitvavo_rest import BitvavoREST  # noqa: E402


def depth_within(levels: list[list[str]], mid: float, pct: float, side: str) -> float:
    tot = 0.0
    for p, a in levels:
        px = float(p)
        if (side == "asks" and px > mid * (1 + pct)) or (side == "bids" and px < mid * (1 - pct)):
            break
        tot += px * float(a)
    return tot


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "snapshots"))
    ap.add_argument("--quotes", default="EUR,USDC")
    ap.add_argument("--depth-markets", type=int, default=60)
    a = ap.parse_args()
    rest = BitvavoREST(os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2"))
    ts = int(time.time() * 1000)
    markets = {m["market"]: m for m in await rest.markets()}
    books = {b["market"]: b for b in await rest.ticker_book()}
    vols = {t["market"]: t for t in await rest.ticker_24h()}
    quotes = a.quotes.split(",")
    rows = []
    for name, m in markets.items():
        if m.get("quote") not in quotes and name != "USDC-EUR":
            continue
        b = books.get(name, {})
        bid, ask = float(b.get("bid") or 0), float(b.get("ask") or 0)
        mid = (bid + ask) / 2 if bid and ask else 0
        rows.append({"market": name, "base": m.get("base"), "quote": m.get("quote"), "status": m.get("status"),
                     "bid": bid, "ask": ask, "spread_bps": (ask - bid) / mid * 1e4 if mid else None,
                     "volume_quote_24h": float(vols.get(name, {}).get("volumeQuote") or 0),
                     "min_quote": m.get("minOrderInQuoteAsset"), "fee_category": m.get("feeCategory")})
    # profondeur : marchés les plus actifs de chaque devise + leur jumeau dans l'autre devise
    by_vol = sorted(rows, key=lambda r: -r["volume_quote_24h"])
    bases = []
    for r in by_vol:
        if r["base"] not in bases:
            bases.append(r["base"])
        if len(bases) >= a.depth_markets:
            break
    want = {f"{b}-{q}" for b in bases for q in quotes} | {"USDC-EUR"}
    for r in rows:
        if r["market"] not in want or not r["bid"]:
            continue
        try:
            ob = await rest.book(r["market"], depth=200)
        except Exception as e:  # noqa: BLE001
            r["depth_error"] = str(e)[:80]
            continue
        mid = (r["bid"] + r["ask"]) / 2
        for pct in (0.001, 0.005, 0.01):
            r[f"depth_bid_{pct}"] = depth_within(ob.get("bids", []), mid, pct, "bids")
            r[f"depth_ask_{pct}"] = depth_within(ob.get("asks", []), mid, pct, "asks")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    snap = {"ts": ts, "source": "API publique Bitvavo (instantané réel)", "rows": rows}
    (out / f"snapshot_{ts}.json").write_text(json.dumps(snap))
    print(json.dumps({"ts": ts, "marches": len(rows), "avec_profondeur": sum("depth_bid_0.001" in r for r in rows),
                      "budget_restant": rest.budget.remaining}))
    await rest.aclose()


if __name__ == "__main__":
    asyncio.run(main())
