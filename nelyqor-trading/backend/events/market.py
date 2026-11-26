"""Réaction du marché APRÈS la première réception d'un événement (bougies publiques Bitvavo).

Absence d'anticipation (garantie par construction et testée) :
- l'instant de référence est `first_received_ms` (notre réception), jamais la date déclarée ;
- le prix de départ est l'OUVERTURE de la première bougie qui commence à ou après la réception (un prix
  observable après coup), le prix d'arrivée l'ouverture de la première bougie commençant à t0 + horizon ;
- un horizon n'est mesuré qu'une fois ÉCOULÉ (now >= t0 + horizon + tolérance + 1 intervalle) ; seules les bougies
  clôturées avant `now` sont utilisées ; avant cela le résultat est « horizon_non_ecoule », sans valeur.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HORIZONS_H = (1, 4, 24)
INTERVAL = "5m"
INTERVAL_MS = 5 * 60_000
TOLERANCE_MS = 2 * INTERVAL_MS
CHUNK = 1440  # bougies par requête (limite Bitvavo)

Candle = tuple[int, float, float, float, float, float]  # ts début, o, h, l, c, v
CandleFetcher = Callable[[str, int, int], list[Candle] | None]  # (marché, début, fin) ; None = échec


def parse_candles(payload: Any) -> list[Candle]:
    """Réponse Bitvavo [[ts, "o", "h", "l", "c", "v"], ...] (ordre décroissant) -> liste croissante."""
    out: list[Candle] = []
    if not isinstance(payload, list):
        raise ValueError("bougies : liste attendue")
    for row in payload:
        if not isinstance(row, list | tuple) or len(row) < 6:
            continue
        try:
            c = (int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        except (TypeError, ValueError):
            continue
        if c[1] > 0 and c[4] > 0:
            out.append(c)
    out.sort(key=lambda c: c[0])
    return out


def _first_open_at_or_after(candles: list[Candle], t: int, now_ms: int, interval_ms: int) -> tuple[int, float] | None:
    for c in candles:
        if c[0] + interval_ms > now_ms:  # bougie non clôturée au moment de la mesure : ignorée
            break
        if c[0] >= t:
            return c[0], c[1]
    return None


@dataclass(frozen=True)
class Reaction:
    horizon_h: int
    status: str            # mesure | horizon_non_ecoule | trou_de_donnees
    ret: float | None = None
    p0: float | None = None
    ph: float | None = None
    t_start: int | None = None
    t_end: int | None = None


def reaction(candles: list[Candle], t0_ms: int, horizon_h: int, now_ms: int,
             interval_ms: int = INTERVAL_MS, tolerance_ms: int = TOLERANCE_MS) -> Reaction:
    target = t0_ms + horizon_h * 3_600_000
    if now_ms < target + tolerance_ms + interval_ms:  # la bougie d'arrivée la plus tardive admise doit être close
        return Reaction(horizon_h, "horizon_non_ecoule")
    a = _first_open_at_or_after(candles, t0_ms, now_ms, interval_ms)
    b = _first_open_at_or_after(candles, target, now_ms, interval_ms)
    if a is None or b is None or a[0] - t0_ms > tolerance_ms or b[0] - target > tolerance_ms:
        return Reaction(horizon_h, "trou_de_donnees")
    return Reaction(horizon_h, "mesure", b[1] / a[1] - 1.0, a[1], b[1], a[0], b[0])


def baseline_abs_return(candles: list[Candle], horizon_h: int, interval_ms: int,
                        event_t0s: list[int] | None = None, period: tuple[int, int] | None = None) -> float | None:
    """Rendement absolu moyen de RÉFÉRENCE sur l'horizon : départs pris sur la même période que les réactions
    (`period`), en EXCLUANT toute fenêtre [départ, départ + horizon] qui recoupe une fenêtre d'événement
    [t0, t0 + horizon] (« des instants sans événement »)."""
    h_ms = horizon_h * 3_600_000
    step = h_ms // interval_ms
    by_ts = {c[0]: c[1] for c in candles}
    t0s = sorted(event_t0s or [])
    import bisect

    vals = []
    for c in candles:
        end = c[0] + step * interval_ms
        if end not in by_ts:
            continue
        if period is not None and not (period[0] <= c[0] <= period[1]):
            continue
        if t0s:
            # recouvrement si t0 ∈ ]c0 - h, c0 + h[
            i = bisect.bisect_right(t0s, c[0] - h_ms)
            if i < len(t0s) and t0s[i] < c[0] + h_ms:
                continue
        vals.append(abs(by_ts[end] / c[1] - 1))
    return sum(vals) / len(vals) if vals else None


class CandleCache:
    """Bougies 5 min par blocs alignés de CHUNK bougies (une requête par bloc et par marché)."""

    def __init__(self, fetch: CandleFetcher) -> None:
        self.fetch = fetch
        self.blocks: dict[tuple[str, int], list[Candle] | None] = {}
        self.requests = 0

    def get(self, market: str, start_ms: int, end_ms: int) -> list[Candle] | None:
        span = CHUNK * INTERVAL_MS
        out: list[Candle] = []
        for b in range(start_ms // span, end_ms // span + 1):
            key = (market, b)
            if key not in self.blocks:
                self.requests += 1
                self.blocks[key] = self.fetch(market, b * span, (b + 1) * span - 1)
            blk = self.blocks[key]
            if blk is None:
                return None
            out.extend(blk)
        return sorted({c[0]: c for c in out}.values(), key=lambda c: c[0])


def bitvavo_candle_fetcher(timeout_s: float = 15.0, transport: Any = None) -> CandleFetcher:
    import httpx

    from .collector import user_agent

    client = httpx.Client(timeout=timeout_s, headers={"User-Agent": user_agent()}, follow_redirects=False,
                          transport=transport)

    def fetch(market: str, start_ms: int, end_ms: int) -> list[Candle] | None:
        if not market.replace("-", "").isalnum() or len(market) > 20:
            return None
        try:
            r = client.get(f"https://api.bitvavo.com/v2/{market}/candles",
                           params={"interval": INTERVAL, "start": start_ms, "end": end_ms, "limit": CHUNK})
            if r.status_code != 200:
                return None
            return parse_candles(r.json())
        except (httpx.HTTPError, ValueError):
            return None

    return fetch
