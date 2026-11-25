"""Téléchargement incrémental et reprenable des bougies publiques Bitvavo (aucune clé requise).

- Reprise : le point de départ est la dernière bougie validée du manifeste ; chaque fenêtre téléchargée est
  fusionnée, validée et écrite atomiquement avant de passer à la suivante (une interruption ne perd au plus
  qu'une fenêtre et ne corrompt rien).
- Fenêtres de `limit` bougies avec `start`/`end` explicites ; l'ordre des lignes renvoyées n'est pas supposé
  (tri + déduplication) ; seules les bougies CLÔTURÉES sont conservées.
- Le budget de poids de l'API et le backoff sont gérés par BitvavoREST.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .store import COLS, REAL, CandleStore
from .validate import validate_candles

STEP_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
           "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000}


@dataclass
class DownloadResult:
    market: str
    interval: str
    requests: int
    new_rows: int
    total_rows: int
    report: dict[str, Any]


def _rows_to_df(rows: list[list[Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame([r[:6] for r in rows], columns=COLS)
    df["ts"] = df["ts"].astype("int64")
    for c in COLS[1:]:
        df[c] = df[c].astype(float)
    return df


async def download_market(rest: Any, store: CandleStore, market: str, interval: str, days: int,
                          now_ms: int | None = None, limit: int = 1440, base_url: str = "") -> DownloadResult:
    step = STEP_MS[interval]
    now = now_ms or int(time.time() * 1000)
    man = store.read_manifest(REAL, "bitvavo", market, interval)
    existing = store.read(REAL, "bitvavo", market, interval) if man else pd.DataFrame(columns=COLS)
    start = (int(man["last_ts"]) + step) if man and man.get("last_ts") is not None else \
        (now - days * 86_400_000) // step * step
    requests = 0
    new_rows = 0
    first_dl = man.get("first_download_ms") if man else now
    rep_d: dict[str, Any] = man.get("validation", {}) if man else {}
    cursor = start
    while cursor + step <= now:
        # Fenêtre d'EXACTEMENT `limit` bougies : `end` = dernier instant avant la bougie suivante.
        # (Avec une borne inclusive et des lignes les plus récentes en premier, une fenêtre de limit+1
        # bougies ferait perdre silencieusement la plus ancienne — défaut détecté par les tests.)
        nxt = min(cursor + limit * step, now // step * step + step)
        end = nxt - 1
        rows = await rest.candles(market, interval, limit=limit, start=cursor, end=end)
        requests += 1
        chunk = _rows_to_df(rows)
        chunk = chunk[(chunk["ts"] >= cursor) & (chunk["ts"] < nxt)]
        if len(rows) >= limit and len(chunk) and int(chunk["ts"].min()) > cursor:
            raise RuntimeError("réponse tronquée par la limite : fenêtre incomplète (réduire limit)")
        if len(chunk):
            merged = pd.concat([existing, chunk], ignore_index=True)
            clean, quarantine, rep = validate_candles(merged, step, now)
            new_rows += len(clean) - len(existing)
            existing = clean
            rep_d = rep.as_dict()
            store.write(existing, REAL, "bitvavo", market, interval, {
                "endpoint": f"GET /{market}/candles?interval={interval}", "api_base": base_url,
                "first_download_ms": first_dl, "last_download_ms": int(time.time() * 1000),
                "validation": rep_d, "quarantined_rows": len(quarantine),
                "note": "Données de marché réelles issues de l'API publique Bitvavo, sans transformation.",
            })
        cursor = nxt
    return DownloadResult(market, interval, requests, new_rows, len(existing), rep_d)


def coverage_report(store: CandleStore, interval: str) -> list[dict[str, Any]]:
    out = []
    for m in store.markets():
        man = store.read_manifest(REAL, "bitvavo", m, interval)
        if man:
            v = man.get("validation", {})
            out.append({"market": m, "rows": man["rows"], "first_ts": man["first_ts"], "last_ts": man["last_ts"],
                        "missing_intervals": v.get("missing_intervals"), "ok": v.get("ok"),
                        "extreme_returns": v.get("extreme_returns"), "sha256": man["sha256"][:16]})
    return out
