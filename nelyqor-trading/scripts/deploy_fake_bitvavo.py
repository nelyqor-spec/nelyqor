#!/usr/bin/env python3
"""FAUX Bitvavo PUBLIC pour la vérification de déploiement (scripts/deploy_check.sh) — données SYNTHÉTIQUES.

Aucun point d'accès privé : pas de clé, pas d'ordre, pas de solde. Sert /v2/time, /v2/markets, /v2/ticker/book,
/v2/ticker/24h et /v2/{marché}/candles, comme le Bitvavo public, pour que le moteur tourne en SIMULATION dans
une pile Docker isolée d'internet. Pilotage du test (réseau interne de la pile uniquement) :
    POST /control/mode/<normal|stale|error|hang>
      normal : réponses fraîches ; stale : carnet horodaté 1 h dans le passé (données PÉRIMÉES) ;
      error  : HTTP 503 ; hang : aucune réponse (délai d'attente du client).
    GET  /control/stats  : compteurs de requêtes par chemin.
Usage : python scripts/deploy_fake_bitvavo.py [port]
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.research.synthetic import synthetic_candles  # noqa: E402

MARKET = {"status": "trading", "quote": "EUR", "pricePrecision": 5, "minOrderInBaseAsset": "0.0001",
          "minOrderInQuoteAsset": "5", "maxOrderInQuoteAsset": "1000000", "quantityDecimals": 8,
          "notionalDecimals": 2, "orderTypes": ["market", "limit", "stopLoss", "stopLossLimit"]}
MARKETS = [{**MARKET, "market": f"{b}-EUR", "base": b} for b in ("BTC", "ETH", "SOL")]
N = 600
_START = int(time.time() * 1000) // 3_600_000 * 3_600_000 - (N - 1) * 3_600_000
DF = {m["market"]: synthetic_candles(N, 3 + k, start_ms=_START, p0=[50000.0, 3000.0, 150.0][k])
      for k, m in enumerate(MARKETS)}
STATE: dict[str, Any] = {"mode": "normal", "stats": {}}
LOCK = threading.Lock()


def _px(m: str) -> float:
    return float(DF[m]["close"].iloc[-1])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:  # journal discret
        pass

    def _send(self, code: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/control/mode/"):
            mode = self.path.rsplit("/", 1)[1]
            if mode in ("normal", "stale", "error", "hang"):
                STATE["mode"] = mode
                return self._send(200, {"mode": mode})
        self._send(404, {"error": "inconnu"})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        with LOCK:
            STATE["stats"][path] = STATE["stats"].get(path, 0) + 1
        if path == "/control/stats":
            return self._send(200, {"mode": STATE["mode"], "stats": STATE["stats"]})
        if path == "/health":
            return self._send(200, {"ok": True})
        mode = STATE["mode"]
        if mode == "hang":
            time.sleep(120)
            return None
        if mode == "error":
            return self._send(503, {"error": "indisponible (simulé)"})
        now = int(time.time() * 1000)
        ts = now - 3_600_000 if mode == "stale" else now
        if path == "/v2/time":
            return self._send(200, {"time": now})
        if path == "/v2/markets":
            return self._send(200, MARKETS)
        if path == "/v2/ticker/book":
            return self._send(200, [{"market": m["market"], "bid": f"{_px(m['market']) * 0.9999:.2f}", "bidSize": "2",
                                     "ask": f"{_px(m['market']) * 1.0001:.2f}", "askSize": "2", "timestamp": ts}
                                    for m in MARKETS])
        if path == "/v2/ticker/24h":
            return self._send(200, [{"market": m["market"], "last": f"{_px(m['market']):.2f}", "volumeQuote": "5000000",
                                     "timestamp": ts} for m in MARKETS])
        parts = path.split("/")
        if len(parts) == 4 and parts[3] == "candles" and parts[2] in DF:
            d = DF[parts[2]]
            return self._send(200, [[int(t), f"{r.open:.2f}", f"{r.high:.2f}", f"{r.low:.2f}", f"{r.close:.2f}",
                                     f"{r.volume:.4f}"] for t, r in d.iloc[::-1].iterrows()])
        self._send(404, {"errorCode": 110, "error": "point d'accès non servi par le faux Bitvavo"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"faux Bitvavo public (synthétique) sur :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()  # noqa: S104 — réseau interne de la pile de test
