#!/usr/bin/env python3
"""Mesure CPU/RAM/disque et latence d'un cycle moteur (scanner + décision) pendant que le LAB tourne en parallèle.
Le LAB tourne dans un sous-processus à priorité basse (nice 19) pour reproduire la priorité à l'exécution.
Univers simulé : N marchés avec bougies SYNTHÉTIQUES (aucun accès réseau requis)."""
from __future__ import annotations

import asyncio
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.clock import FakeClock  # noqa: E402
from backend.core.config import ROOT, Settings  # noqa: E402
from backend.research.synthetic import synthetic_candles  # noqa: E402
from backend.runner import Engine  # noqa: E402
from backend.storage.db import Database  # noqa: E402


class Pub:
    def __init__(self, clock: FakeClock, n_markets: int) -> None:
        self.clock = clock
        self.m = [{"market": f"C{i}-EUR", "base": f"C{i}", "quote": "EUR", "status": "trading", "pricePrecision": 5,
                   "minOrderInBaseAsset": "0.0001", "minOrderInQuoteAsset": "5", "orderTypes": ["market", "limit"]}
                  for i in range(n_markets)]
        self.df = {x["market"]: synthetic_candles(500, i, start_ms=clock.now_ms() - 500 * 3_600_000)
                   for i, x in enumerate(self.m)}

    async def markets(self):  # noqa: ANN201
        return self.m

    async def ticker_book(self):  # noqa: ANN201
        return [{"market": k, "bid": f"{d['close'].iloc[-1] * 0.9999:.4f}", "ask": f"{d['close'].iloc[-1] * 1.0001:.4f}",
                 "bidSize": "100", "askSize": "100"} for k, d in self.df.items()]

    async def ticker_24h(self):  # noqa: ANN201
        return [{"market": k, "last": f"{d['close'].iloc[-1]:.4f}", "volumeQuote": "1000000"} for k, d in self.df.items()]

    async def candles(self, market, interval, limit=500, **k):  # noqa: ANN001,ANN201
        d = self.df[market].tail(limit)
        return [[int(t), r.open, r.high, r.low, r.close, r.volume] for t, r in d.iterrows()]


async def run(n_markets: int) -> dict:
    tmp = Path(tempfile.mkdtemp())
    clock = FakeClock(int(time.time() * 1000))
    s = Settings(db_path=tmp / "b.db", role="engine", secrets_dir=tmp)
    eng = Engine(s, Database(s.db_path), Pub(clock, n_markets), clock)
    eng.start()
    lab = subprocess.Popen(["nice", "-n", "19", sys.executable, str(ROOT / "scripts/run_lab.py"), "--source",  # noqa: S603,S607
                            "synthetic", "--bars", "6000", "--db", str(tmp / "lab.db")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lat = []
    t_cpu = time.process_time()
    t0 = time.time()
    for _ in range(5):
        a = time.perf_counter()
        eng.last_decision_ms = 0
        await eng.step()
        lat.append(time.perf_counter() - a)
        clock.advance(60_000)
    wall = time.time() - t0
    cpu = time.process_time() - t_cpu
    lab_running = lab.poll() is None
    lab.wait()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    child_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    return {"marches": n_markets, "cycle_s_premier": round(lat[0], 3), "cycle_s_median": round(sorted(lat)[2], 3),
            "cycle_s_max": round(max(lat), 3), "cpu_moteur_s": round(cpu, 2), "mur_s": round(wall, 2),
            "rss_moteur_mo": round(rss_mb, 1), "rss_lab_mo": round(child_mb, 1),
            "lab_concurrent_pendant_mesure": lab_running,
            "taille_base_mo": round(os.path.getsize(tmp / "b.db") / 1e6, 2), "cpus": os.cpu_count()}


if __name__ == "__main__":
    res = [asyncio.run(run(n)) for n in (int(x) for x in (sys.argv[1:] or ["30", "100"]))]
    print(json.dumps(res, indent=2, ensure_ascii=False))
