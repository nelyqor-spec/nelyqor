#!/usr/bin/env python3
"""Collecte de MICROSTRUCTURE publique Bitvavo (aucune clé, aucun ordre) pour le simulateur d'exécution.

  # carnets (profondeur 25) + transactions en direct, pendant 20 min, toutes les 10 s :
  python scripts/collect_microstructure.py live --markets BTC-EUR,ETH-EUR --minutes 20 --every 10
  # historique des transactions publiques (pas de carnets historiques disponibles) :
  python scripts/collect_microstructure.py history --markets BTC-EUR --days 14 --time-budget 900

Stockage (jamais commité) : data/micro/books/<marché>/<jour>.jsonl.gz (append) et
data/micro/trades/<marché>.parquet (dédupliqué par id). Le budget de poids de l'API est respecté par le
client REST (en-têtes bitvavo-ratelimit-*). Une collecte n'est qu'un échantillon de périodes : la couverture
est imprimée et doit figurer dans le rapport.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from backend.core.config import ROOT  # noqa: E402
from backend.market_data.bitvavo_rest import BitvavoREST  # noqa: E402

DAY_MS = 86_400_000
DEFAULT_MARKETS = "BTC-EUR,ETH-EUR,SOL-EUR,XRP-EUR,ADA-EUR,DOGE-EUR,LINK-EUR,AVAX-EUR,SUI-EUR,PEPE-EUR"


def trades_path(root: Path, m: str) -> Path:
    return root / "trades" / f"{m}.parquet"


def save_trades(root: Path, m: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    p = trades_path(root, m)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)[["id", "timestamp", "amount", "price", "side"]]
    if p.exists():
        df = pd.concat([pd.read_parquet(p), df])
    df = df.drop_duplicates("id").sort_values("timestamp")
    df.to_parquet(p, index=False)
    return len(df)


def load_trades(root: Path, m: str) -> list[dict[str, Any]]:
    p = trades_path(root, m)
    if not p.exists():
        return []
    return pd.read_parquet(p).to_dict("records")  # type: ignore[no-any-return]


def load_books(root: Path, m: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((root / "books" / m).glob("*.jsonl.gz")):
        with gzip.open(f, "rt") as fh:
            out.extend(json.loads(line) for line in fh if line.strip())
    return out


async def history(rest: BitvavoREST, root: Path, markets: list[str], days: int, budget_s: float) -> dict[str, Any]:
    """Pagination par fenêtres de 24 h au plus (limite de l'endpoint public : end - start <= 24 h), du plus
    récent au plus ancien ; les fenêtres déjà couvertes sont sautées ; doublons éliminés par id."""
    t0 = time.time()
    now = int(time.time() * 1000)
    since = now - days * DAY_MS
    cov: dict[str, Any] = {}
    for m in markets:
        have = load_trades(root, m)
        ts_have = sorted(int(r["timestamp"]) for r in have)
        rows: list[dict[str, Any]] = []
        errors = 0
        w_end = now
        while w_end > since and time.time() - t0 < budget_s:
            w_start = max(since, w_end - DAY_MS + 1)
            if ts_have and ts_have[0] <= w_start + 3_600_000 and w_end <= ts_have[-1]:
                w_end = w_start - 1  # fenêtre déjà couverte (tolérance d'1 h au bord le plus ancien)
                continue
            lo = ts_have[-1] + 1 if ts_have and w_start <= ts_have[-1] < w_end else w_start  # reprise incrémentale
            end = w_end
            while time.time() - t0 < budget_s:
                try:
                    page = await rest.public_trades(m, limit=1000, start=lo, end=end)
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    print(f"{m} [{w_start},{end}] : {type(e).__name__} {str(e)[:160]}", flush=True)
                    page = []
                    if errors > 5:
                        break
                if not page:
                    break
                rows.extend(page)
                oldest = min(int(t["timestamp"]) for t in page)
                if len(page) < 1000 or oldest <= lo:
                    break
                end = oldest
            if errors > 5:
                break
            if lo > w_start and ts_have and ts_have[0] <= w_start + 3_600_000:
                w_end = ts_have[0] - 1 if ts_have[0] < w_start else w_start - 1
                ts_have = [ts_have[0], ts_have[-1]]
                if w_end <= since:
                    break
                continue
            w_end = w_start - 1
        n = save_trades(root, m, rows)
        allr = load_trades(root, m)
        cov[m] = {"transactions": n, "nouvelles": len(rows), "erreurs": errors,
                  "debut": min((int(r["timestamp"]) for r in allr), default=None),
                  "fin": max((int(r["timestamp"]) for r in allr), default=None)}
        print(f"historique {m}: {n} transactions ({len(rows)} nouvelles, {errors} erreurs)", flush=True)
        if time.time() - t0 >= budget_s:
            print("budget de temps atteint", flush=True)
            break
    return cov


async def live(rest: BitvavoREST, root: Path, markets: list[str], minutes: float, every: float, depth: int) -> dict[str, Any]:
    t_end = time.time() + minutes * 60
    last_ts: dict[str, int] = {m: int(time.time() * 1000) - 60_000 for m in markets}
    n_books = {m: 0 for m in markets}
    n_trades = {m: 0 for m in markets}
    buf: dict[str, list[dict[str, Any]]] = {m: [] for m in markets}
    while time.time() < t_end:
        tick = time.time()
        for m in markets:
            try:
                b = await rest.book(m, depth)
                ts = int(time.time() * 1000)
                day = datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d")
                f = root / "books" / m / f"{day}.jsonl.gz"
                f.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(f, "at") as fh:
                    fh.write(json.dumps({"ts": ts, "nonce": b.get("nonce"), "bids": b.get("bids", []),
                                         "asks": b.get("asks", [])}) + "\n")
                n_books[m] += 1
                tr = await rest.public_trades(m, limit=1000, start=last_ts[m])
                if tr:
                    buf[m].extend(tr)
                    last_ts[m] = max(int(t["timestamp"]) for t in tr)
                    n_trades[m] += len(tr)
            except Exception as e:  # noqa: BLE001
                print(f"{m}: {type(e).__name__} {str(e)[:120]}", flush=True)
        await asyncio.sleep(max(0.0, every - (time.time() - tick)))
    for m in markets:
        save_trades(root, m, buf[m])
    return {m: {"carnets": n_books[m], "transactions": n_trades[m]} for m in markets}


async def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("live", "history"):
        s = sub.add_parser(name)
        s.add_argument("--markets", default=DEFAULT_MARKETS)
        s.add_argument("--root", default=str(ROOT / "data" / "micro"))
        if name == "live":
            s.add_argument("--minutes", type=float, default=20)
            s.add_argument("--every", type=float, default=10)
            s.add_argument("--depth", type=int, default=25)
        else:
            s.add_argument("--days", type=int, default=14)
            s.add_argument("--time-budget", type=float, default=900)
    a = ap.parse_args()
    rest = BitvavoREST(os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2"))
    root = Path(a.root)
    mk = [m for m in a.markets.split(",") if m]
    if a.cmd == "live":
        cov = await live(rest, root, mk, a.minutes, a.every, a.depth)
    else:
        cov = await history(rest, root, mk, a.days, a.time_budget)
    print("MICRO_JSON_DEBUT")
    print(json.dumps({"mode": a.cmd, "couverture": cov, "utc": datetime.now(UTC).isoformat(timespec="seconds")}))
    print("MICRO_JSON_FIN")


if __name__ == "__main__":
    asyncio.run(main())
