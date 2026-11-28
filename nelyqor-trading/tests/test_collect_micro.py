"""Collecte de transactions publiques : pagination en fenêtres <= 24 h, reprise sans doublon."""
import asyncio
import time

import pandas as pd

from scripts.collect_microstructure import DAY_MS, history, load_trades


class FakeREST:
    def __init__(self, trades):
        self.trades = trades
        self.calls = 0

    async def public_trades(self, market, limit=1000, start=None, end=None):
        self.calls += 1
        if start is not None and end is not None and end - start > DAY_MS:
            raise ValueError("start/end > 24 h")  # comme l'API réelle
        sel = [t for t in self.trades if (start is None or t["timestamp"] >= start) and (end is None or t["timestamp"] <= end)]
        return sorted(sel, key=lambda t: -t["timestamp"])[:limit]


def test_history_paginates_daily_windows_and_resumes(tmp_path):
    now = int(time.time() * 1000)
    trades = [{"id": str(k), "timestamp": now - k * 30_000, "amount": "0.01", "price": "100", "side": "buy"} for k in range(9000)]
    api = FakeREST(trades)
    cov = asyncio.run(history(api, tmp_path, ["BTC-EUR"], 3, 60))
    assert cov["BTC-EUR"]["erreurs"] == 0
    got = load_trades(tmp_path, "BTC-EUR")
    expected = [t for t in trades if t["timestamp"] >= now - 3 * DAY_MS]
    assert abs(len(got) - len(expected)) <= 1 and pd.Series([g["id"] for g in got]).is_unique  # bord de fenêtre
    calls = api.calls
    asyncio.run(history(api, tmp_path, ["BTC-EUR"], 3, 60))  # reprise : fenêtres couvertes sautées
    assert api.calls - calls <= 3 and len(load_trades(tmp_path, "BTC-EUR")) == len(got)
