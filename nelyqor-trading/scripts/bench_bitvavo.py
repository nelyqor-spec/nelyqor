#!/usr/bin/env python3
"""Banc de performances Bitvavo sur données PUBLIQUES (aucune clé, aucun ordre, aucun compte).

Mesure, pendant une durée bornée (--minutes) :
  1. latence REST (/time, /markets, /ticker/book, /ticker/24h, /{marché}/candles) : médiane, p90, p99 ;
  2. en-têtes de limite (bitvavo-ratelimit-limit / -remaining / -resetat) et poids réellement décompté ;
  3. WebSocket public (wss://ws.bitvavo.com/v2/, canaux ticker, book, trades) : délai horodatage de l'échange ->
     réception, débit, reconnexions FORCÉES (fermeture volontaire du socket, temps de reprise), messages manquants
     (ruptures de nonce du carnet), silences ;
  4. temps d'analyse de TOUT l'univers par le moteur RÉEL du projet (backend.runner.Engine) en SIMULATION :
     refresh_markets, refresh_tickers, refresh_candles (toutes les bougies voulues), manage_positions, decide ;
     CPU, mémoire (tracemalloc, /proc, resource ; psutil s'il est présent) ;
  5. données obsolètes : part des marchés jugés périmés par la règle du moteur (data_age_ms > max_stale_ms) et âge.

Respect des limites d'API (obligatoire) : plafond PROPRE de poids par minute (--poids-par-minute, défaut 400, soit
40 % de la limite publique de 1000), réserve sur l'en-tête `remaining`, pause jusqu'à `resetat`, et ARRÊT IMMÉDIAT
de tout le banc (REST et WebSocket) sur HTTP 429 ou erreur 105 (bannissement temporaire). Aucune nouvelle tentative.

Le résultat JSON est imprimé entre les lignes BENCH_JSON_DEBUT et BENCH_JSON_FIN (et écrit dans --out si donné).
Il ne contient que des mesures et des données de marché publiques : ni adresse IP, ni nom d'hôte, ni secret.

  python scripts/bench_bitvavo.py --minutes 9 --out ../bench/bench_bitvavo.json

Ce banc ne vérifie PAS l'exécution des ordres (aucun ordre n'est envoyé) et une machine GitHub Actions n'est pas le
serveur définitif : voir docs/INFRASTRUCTURE.md.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import json
import os
import platform
import resource
import sys
import tempfile
import time
import tracemalloc
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

REST_URL = "https://api.bitvavo.com/v2"
WS_URL = "wss://ws.bitvavo.com/v2/"
MARQUEUR_DEBUT = "BENCH_JSON_DEBUT"
MARQUEUR_FIN = "BENCH_JSON_FIN"
LIMITE_PUBLIQUE = 1000  # poids par minute documenté par Bitvavo (vérifié par l'en-tête bitvavo-ratelimit-limit)
CODES_ARRET = {105}  # « rate limit exceeded / banned until » : arrêt immédiat
# Poids SUPPOSÉS pour le plafond propre (prudents) ; le poids réellement décompté est mesuré par les en-têtes.
POIDS_SUPPOSES = {"/time": 1, "/markets": 1, "/ticker/book": 25, "/ticker/24h": 25, "candles": 1}
MARCHES_WS = ["BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR", "ADA-EUR", "DOGE-EUR", "LINK-EUR", "AVAX-EUR"]
MARCHES_BOUGIES = ["BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR", "ADA-EUR"]


# ------------------------------------------------------------------ statistiques
def percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Percentile par interpolation linéaire (méthode « linear » de numpy) sur une liste DÉJÀ triée."""
    if not sorted_vals:
        raise ValueError("liste vide")
    if not 0 <= q <= 100:
        raise ValueError("q hors de [0, 100]")
    pos = (len(sorted_vals) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo))


def summarize(values: Sequence[float], ndigits: int = 2) -> dict[str, Any]:
    """n, min, médiane, p90, p99, max, moyenne. Liste vide : n=0 et valeurs nulles (None)."""
    if not values:
        return {"n": 0, "min": None, "mediane": None, "p90": None, "p99": None, "max": None, "moyenne": None}
    s = sorted(float(v) for v in values)
    return {"n": len(s), "min": round(s[0], ndigits), "mediane": round(percentile(s, 50), ndigits),
            "p90": round(percentile(s, 90), ndigits), "p99": round(percentile(s, 99), ndigits),
            "max": round(s[-1], ndigits), "moyenne": round(sum(s) / len(s), ndigits)}


# ------------------------------------------------------------------ limites d'API
class Halt(Exception):
    """Arrêt de TOUT le banc (429, bannissement) : aucune nouvelle requête REST ni WebSocket."""


class BudgetEpuise(Exception):
    """L'attente imposée par le budget dépasserait l'échéance de la phase : la phase s'arrête proprement."""


class RateGuard:
    """Plafond propre de poids par minute (fenêtre glissante), réserve sur l'en-tête `remaining`, arrêt sur 429."""

    def __init__(self, cap_per_min: int = 400, reserve: int = 150,
                 mono: Callable[[], float] = time.monotonic, wall_ms: Callable[[], int] | None = None,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        self.cap, self.reserve = cap_per_min, reserve
        self.mono, self.sleep = mono, sleep
        self.wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self.window: list[tuple[float, int]] = []
        self.remaining: int | None = None
        self.limit: int | None = None
        self.reset_at_ms: int | None = None
        self.halted: str | None = None
        self.pauses = 0
        self.pause_s = 0.0
        self.weight_sent = 0
        self.min_remaining: int | None = None

    def _wait_s(self, weight: int) -> float:
        now = self.mono()
        self.window = [(t, w) for t, w in self.window if now - t < 60.0]
        if self.reset_at_ms is not None and self.wall_ms() >= self.reset_at_ms:
            self.remaining = None  # fenêtre de l'échange écoulée : l'ancienne valeur ne vaut plus
        if self.remaining is not None and self.remaining - weight < self.reserve:
            if self.reset_at_ms is None:
                return 5.0
            return max(0.0, (self.reset_at_ms - self.wall_ms()) / 1000) + 0.25
        used = sum(w for _, w in self.window)
        if used + weight > self.cap and self.window:
            return max(0.0, self.window[0][0] + 60.0 - now) + 0.05
        return 0.0

    async def acquire(self, weight: int, deadline: float | None = None) -> None:
        while True:
            if self.halted:
                raise Halt(self.halted)
            wait = self._wait_s(weight)
            if wait <= 0:
                self.window.append((self.mono(), weight))
                self.weight_sent += weight
                return
            if deadline is not None and self.mono() + wait > deadline:
                raise BudgetEpuise(f"attente {wait:.1f} s au-delà de l'échéance")
            self.pauses += 1
            self.pause_s += wait
            await self.sleep(wait)

    def observe(self, status: int, headers: httpx.Headers | dict[str, str], data: Any) -> None:
        """Lit les en-têtes de limite ; 429 ou erreur 105 => arrêt définitif (Halt levé par l'appelant)."""
        h = {k.lower(): v for k, v in headers.items()}
        for key, attr in (("bitvavo-ratelimit-remaining", "remaining"), ("bitvavo-ratelimit-limit", "limit"),
                          ("bitvavo-ratelimit-resetat", "reset_at_ms")):
            if h.get(key) is not None:
                with contextlib.suppress(ValueError):
                    setattr(self, attr, int(h[key]))
        if self.remaining is not None:
            self.min_remaining = self.remaining if self.min_remaining is None else min(self.min_remaining,
                                                                                       self.remaining)
        code = data.get("errorCode") if isinstance(data, dict) else None
        if status == 429:
            self.halted = "HTTP 429 (trop de requêtes) : banc arrêté, aucune nouvelle tentative"
        elif code in CODES_ARRET:
            self.halted = f"erreur Bitvavo {code} (limite dépassée) : banc arrêté"

    def report(self) -> dict[str, Any]:
        return {"plafond_propre_poids_par_minute": self.cap, "reserve": self.reserve,
                "limite_annoncee_entete": self.limit, "remaining_min_observe": self.min_remaining,
                "poids_envoye_suppose": self.weight_sent, "pauses": self.pauses,
                "pause_totale_s": round(self.pause_s, 1), "arret": self.halted}


# ------------------------------------------------------------------ REST
class RestProbe:
    """GET publics chronométrés, sans nouvelle tentative (un 429 arrête tout)."""

    def __init__(self, guard: RateGuard, base_url: str = REST_URL, transport: httpx.AsyncBaseTransport | None = None,
                 timeout_s: float = 10.0) -> None:
        self.guard = guard
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_s, transport=transport,
                                        headers={"User-Agent": "nelyqor-bench/1"})
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.status: dict[str, Counter[str]] = defaultdict(Counter)
        self.weight_obs: dict[str, list[int]] = defaultdict(list)
        self.errors: Counter[str] = Counter()
        self.headers_seen: set[str] = set()
        self.deadline: float | None = None

    async def get(self, path: str, label: str, weight: int, params: dict[str, Any] | None = None) -> Any:
        obs = self.weight_obs.get(label)
        if obs and len(obs) >= 3:
            weight = max(1, max(obs))  # poids SUPPOSÉ remplacé par le plus fort poids MESURÉ (prudent)
        await self.guard.acquire(weight, self.deadline)
        before, before_reset = self.guard.remaining, self.guard.reset_at_ms
        t0 = time.perf_counter()
        try:
            resp = await self.client.get(self.base_url + path, params=params)
        except httpx.HTTPError as e:
            self.errors[type(e).__name__] += 1
            self.status[label]["reseau"] += 1
            raise
        dt_ms = (time.perf_counter() - t0) * 1000
        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = None
        self.headers_seen |= {k.lower() for k in resp.headers if k.lower().startswith("bitvavo-ratelimit")}
        self.guard.observe(resp.status_code, resp.headers, data)
        self.status[label][str(resp.status_code)] += 1
        after = self.guard.remaining
        if (before is not None and after is not None and before_reset == self.guard.reset_at_ms
                and before >= after):
            self.weight_obs[label].append(before - after)  # même fenêtre : poids réellement décompté
        if self.guard.halted:
            raise Halt(self.guard.halted)
        if resp.status_code >= 400 or (isinstance(data, dict) and "errorCode" in data):
            self.errors[f"HTTP {resp.status_code}"] += 1
            raise httpx.HTTPStatusError(f"{label} : HTTP {resp.status_code}", request=resp.request, response=resp)
        self.lat[label].append(dt_ms)
        return data

    def report(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for label in sorted(set(self.lat) | set(self.status)):
            w = self.weight_obs.get(label, [])
            out[label] = {"latence_ms": summarize(self.lat.get(label, [])), "statuts": dict(self.status[label]),
                          "poids_observe": summarize(w, 1) if w else None}
        return {"points": out, "erreurs": dict(self.errors), "entetes_limite_vus": sorted(self.headers_seen)}

    async def aclose(self) -> None:
        await self.client.aclose()


class GuardedPublic:
    """Client public pour le MOTEUR (même interface que BitvavoREST public) passant par le RateGuard : contrairement
    à BitvavoREST, AUCUNE nouvelle tentative sur 429 (exigence du banc)."""

    def __init__(self, probe: RestProbe) -> None:
        self.p = probe

    async def time(self) -> int:
        return int((await self.p.get("/time", "/time", POIDS_SUPPOSES["/time"]))["time"])

    async def markets(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.p.get("/markets", "/markets", POIDS_SUPPOSES["/markets"])
        return res

    async def ticker_book(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.p.get("/ticker/book", "/ticker/book", POIDS_SUPPOSES["/ticker/book"])
        return res

    async def ticker_24h(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.p.get("/ticker/24h", "/ticker/24h", POIDS_SUPPOSES["/ticker/24h"])
        return res

    async def candles(self, market: str, interval: str, limit: int = 500, **_: Any) -> list[list[Any]]:
        res: list[list[Any]] = await self.p.get(f"/{market}/candles", "/{market}/candles", POIDS_SUPPOSES["candles"],
                                                {"interval": interval, "limit": limit})
        return res


async def measure_skew(pub: GuardedPublic, n: int = 3) -> dict[str, Any]:
    """Décalage heure de l'échange − heure locale (milieu de l'aller-retour), comme Engine.measure_clock_skew."""
    vals = []
    for _ in range(n):
        t0 = time.time() * 1000
        server = await pub.time()
        t1 = time.time() * 1000
        vals.append(server - (t0 + t1) / 2)
    s = sorted(vals)
    return {"ms": round(percentile(s, 50), 1), "mesures": len(s)}


async def rest_loop(probe: RestProbe, markets: Sequence[str], duration_s: float, pause_s: float = 1.0) -> int:
    """Rotation des points d'accès pendant duration_s ; /markets et /ticker/24h (lourds) un cycle sur cinq."""
    pub = GuardedPublic(probe)
    end = time.monotonic() + duration_s
    probe.deadline = end
    cycle = 0
    while time.monotonic() < end:
        calls: list[Callable[[], Awaitable[Any]]] = [pub.time, pub.ticker_book]
        calls.append(functools.partial(pub.candles, markets[cycle % len(markets)], "1h", limit=100))
        if cycle % 5 == 0:
            calls += [pub.markets, pub.ticker_24h]
        for call in calls:
            if time.monotonic() >= end:
                break
            try:
                await call()
            except BudgetEpuise:
                return cycle  # le budget propre ne laisse plus de requête avant l'échéance : fin propre
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                pass  # compté dans probe.errors ; jamais de rafale de nouvelles tentatives
            await asyncio.sleep(pause_s)
        cycle += 1
    return cycle


# ------------------------------------------------------------------ WebSocket
def _default_connect(url: str) -> Any:
    import websockets

    return websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=10, max_size=2 ** 22)


class WsProbe:
    """Flux public : délai échange -> réception, débit, reconnexions forcées, ruptures de nonce du carnet."""

    def __init__(self, url: str, markets: Sequence[str], channels: Sequence[str] = ("ticker", "book", "trades"),
                 connect: Callable[[str], Any] | None = None, skew_ms: float = 0.0,
                 guard: RateGuard | None = None, recv_timeout_s: float = 5.0, backoff_s: float = 1.0) -> None:
        self.url, self.markets, self.channels = url, list(markets), list(channels)
        self.connect = connect or _default_connect
        self.skew_ms = skew_ms
        self.guard = guard
        self.recv_timeout_s = recv_timeout_s
        self.backoff_s = backoff_s
        self.counts: Counter[str] = Counter()
        self.delays: dict[str, list[float]] = defaultdict(list)
        self.without_ts: Counter[str] = Counter()
        self.last_nonce: dict[str, int] = {}
        self.gaps_live = 0
        self.missing_live = 0
        self.missing_reconnect = 0
        self.nonce_regressions = 0
        self.after_reconnect: set[str] = set()
        self.last_seen: dict[str, float] = {}
        self.max_silence: dict[str, float] = {}
        self.reprises: list[dict[str, Any]] = []
        self.connections = 0
        self.forced = 0
        self.unplanned = 0
        self.errors: Counter[str] = Counter()
        self.halted: str | None = None
        self.active_s = 0.0
        self.first_connect_ms: float | None = None

    def subscribe_msg(self) -> dict[str, Any]:
        return {"action": "subscribe", "channels": [{"name": c, "markets": self.markets} for c in self.channels]}

    @staticmethod
    def exchange_ts(msg: dict[str, Any]) -> int | None:
        """Horodatage de l'échange en MILLISECONDES. Bitvavo horodate le carnet du WebSocket en nanosecondes
        (≈ 1,8e18, constaté au run 45903651762) et les transactions en millisecondes : l'unité est déduite de
        l'ordre de grandeur (ns > 1e17, µs > 1e14, sinon ms)."""
        ts = msg.get("timestamp")
        if isinstance(ts, str) and ts.isdigit():
            ts = int(ts)
        if not (isinstance(ts, int | float) and not isinstance(ts, bool) and ts > 0):
            return None
        if ts > 1e17:
            return int(ts // 1_000_000)
        if ts > 1e14:
            return int(ts // 1_000)
        return int(ts)

    def handle(self, msg: dict[str, Any], recv_wall_ms: float, recv_mono: float) -> str | None:
        ev = msg.get("event")
        if "errorCode" in msg:
            self.errors[str(msg.get("errorCode"))] += 1
            if msg.get("errorCode") in CODES_ARRET:
                self.halted = f"WebSocket : erreur Bitvavo {msg.get('errorCode')} : banc arrêté"
            return "erreur"
        if not isinstance(ev, str):
            return None
        if ev == "subscribed":
            return "subscribed"
        self.counts[ev] += 1
        market = str(msg.get("market", "?"))
        key = f"{ev}:{market}"
        if key in self.last_seen:
            gap = recv_mono - self.last_seen[key]
            self.max_silence[key] = max(self.max_silence.get(key, 0.0), gap)
        self.last_seen[key] = recv_mono
        ts = self.exchange_ts(msg)
        if ts is None:
            self.without_ts[ev] += 1
        else:
            self.delays[ev].append(recv_wall_ms + self.skew_ms - ts)
        if ev == "book" and msg.get("nonce") is not None:
            try:
                nonce = int(msg["nonce"])
            except (TypeError, ValueError):
                return "donnee"
            prev = self.last_nonce.get(market)
            if prev is not None:
                if nonce <= prev:
                    self.nonce_regressions += 1
                elif nonce != prev + 1:
                    if market in self.after_reconnect:
                        self.missing_reconnect += nonce - prev - 1
                    else:
                        self.gaps_live += 1
                        self.missing_live += nonce - prev - 1
            self.after_reconnect.discard(market)
            self.last_nonce[market] = nonce
        return "donnee"

    async def run(self, duration_s: float, reconnect_every_s: float, max_forced: int = 3) -> None:
        start = time.monotonic()
        end = start + duration_s
        backoff = self.backoff_s
        pending: dict[str, Any] | None = None  # reprise en cours de mesure
        while time.monotonic() < end and not self.halted and not (self.guard and self.guard.halted):
            t_conn0 = time.monotonic()
            forced_this = False
            try:
                async with self.connect(self.url) as ws:
                    t_open = time.monotonic()
                    self.connections += 1
                    if self.first_connect_ms is None:
                        self.first_connect_ms = round((t_open - t_conn0) * 1000, 1)
                    if pending is not None:
                        pending["connexion_ms"] = round((t_open - pending["_t_close"]) * 1000, 1)
                    await ws.send(json.dumps(self.subscribe_msg()))
                    session_end = end
                    if self.forced < max_forced and reconnect_every_s > 0:
                        session_end = min(end, t_open + reconnect_every_s)
                    while True:
                        now = time.monotonic()
                        if now >= session_end or self.halted or (self.guard and self.guard.halted):
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=min(self.recv_timeout_s,
                                                                                session_end - now))
                        except TimeoutError:
                            continue
                        rm, rw = time.monotonic(), time.time() * 1000
                        try:
                            msg = json.loads(raw)
                        except (TypeError, ValueError):
                            self.errors["json"] += 1
                            continue
                        kind = self.handle(msg, rw, rm) if isinstance(msg, dict) else None
                        if pending is not None:
                            if kind == "subscribed" and pending.get("souscription_ms") is None:
                                pending["souscription_ms"] = round((rm - pending["_t_close"]) * 1000, 1)
                            if kind == "donnee":
                                pending["premiere_donnee_ms"] = round((rm - pending["_t_close"]) * 1000, 1)
                                pending.pop("_t_close")
                                self.reprises.append(pending)
                                pending = None
                    self.active_s += time.monotonic() - t_open
                    if time.monotonic() < end and not self.halted and self.forced < max_forced \
                            and reconnect_every_s > 0:
                        forced_this = True
                        self.forced += 1
                        self.after_reconnect = set(self.last_nonce)
                        await ws.close()
                        pending = {"_t_close": time.monotonic(), "connexion_ms": None, "souscription_ms": None,
                                   "premiere_donnee_ms": None}
                    backoff = self.backoff_s
            except Exception as e:  # noqa: BLE001 — coupure non planifiée : comptée puis reprise avec backoff
                self.unplanned += 1
                self.errors[type(e).__name__] += 1
                self.after_reconnect = set(self.last_nonce)
                if pending is None:
                    pending = {"_t_close": time.monotonic(), "connexion_ms": None, "souscription_ms": None,
                               "premiere_donnee_ms": None, "non_planifiee": True}
                await asyncio.sleep(min(backoff, max(0.0, end - time.monotonic())))
                backoff = min(30.0, backoff * 2)
                continue
            if not forced_this:
                break
        if pending is not None:
            pending.pop("_t_close", None)
            pending["incomplete"] = True
            self.reprises.append(pending)

    def report(self) -> dict[str, Any]:
        dur = max(self.active_s, 1e-9)
        sil = sorted(self.max_silence.items(), key=lambda kv: -kv[1])[:5]
        return {
            "url": self.url, "marches": self.markets, "canaux": self.channels,
            "connexion_initiale_ms": self.first_connect_ms, "connexions": self.connections,
            "reconnexions_forcees": self.forced, "coupures_non_planifiees": self.unplanned,
            "reprises": self.reprises,
            "reprise_premiere_donnee_ms": summarize([float(r["premiere_donnee_ms"]) for r in self.reprises
                                                     if r.get("premiere_donnee_ms") is not None], 1),
            "messages": dict(self.counts), "debit_msg_par_s": {k: round(v / dur, 2) for k, v in self.counts.items()},
            "duree_active_s": round(self.active_s, 1),
            "delai_echange_reception_ms": {k: summarize(v, 1) for k, v in self.delays.items()},
            "messages_sans_horodatage": dict(self.without_ts),
            "carnet": {"ruptures_nonce_en_continu": self.gaps_live, "mises_a_jour_manquantes_en_continu":
                       self.missing_live, "mises_a_jour_manquantes_pendant_reconnexions": self.missing_reconnect,
                       "nonces_regressifs": self.nonce_regressions},
            "silences_max_s": {k: round(v, 2) for k, v in sil}, "erreurs": dict(self.errors), "arret": self.halted,
        }


# ------------------------------------------------------------------ ressources
def proc_status() -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("VmRSS", "VmHWM"):
                out[k] = round(int(v.split()[0]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return out


def memory_snapshot() -> dict[str, Any]:
    snap: dict[str, Any] = {"ru_maxrss_mo": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
    st = proc_status()
    if st:
        snap["rss_mo"] = st.get("VmRSS")
        snap["rss_pic_mo"] = st.get("VmHWM")
    try:
        import psutil  # type: ignore[import-not-found]

        snap["psutil_rss_mo"] = round(psutil.Process().memory_info().rss / 1e6 / 1.048576, 1)
    except Exception:  # noqa: BLE001,S110 — psutil facultatif
        pass
    return snap


async def timed(fn: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    w0, c0 = time.perf_counter(), time.process_time()
    await fn()
    return {"mur_s": round(time.perf_counter() - w0, 3), "cpu_s": round(time.process_time() - c0, 3)}


def staleness(states: dict[str, Any], now_ms: int, skew_ms: int, max_stale_ms: int,
              quote: str | None = "EUR") -> dict[str, Any]:
    """Part des marchés jugés PÉRIMÉS par la règle du moteur (data_age_ms > max_stale_ms) et âge des données."""
    from backend.market_data.universe import data_age_ms

    sel = [st for st in states.values() if quote is None or st.info.quote == quote]
    ages = [float(data_age_ms(st, now_ms, skew_ms)) for st in sel if st.ts_received_ms]
    stale = sum(1 for a in ages if a > max_stale_ms)
    return {"marches": len(sel), "sans_donnee": len(sel) - len(ages),
            "sans_horodatage_echange": sum(1 for st in sel if st.ts_received_ms and not st.ts_market_ms),
            "perimes": stale, "part_perimes": round(stale / len(ages), 4) if ages else None,
            "seuil_ms": max_stale_ms, "age_ms": summarize(ages, 0)}


# ------------------------------------------------------------------ moteur réel en SIMULATION
async def bench_engine(pub: Any, max_candle_markets: int = 0, tmpdir: Path | None = None) -> dict[str, Any]:
    """Balayage complet de l'univers par backend.runner.Engine (simulation, aucune clé : secrets_dir vide)."""
    from backend.core.config import Settings
    from backend.market_data.universe import check_eligibility
    from backend.runner import INTERVAL_MS, Engine
    from backend.storage.db import Database

    tmp = tmpdir or Path(tempfile.mkdtemp(prefix="nelyqor-bench-"))
    (tmp / "secrets").mkdir(parents=True, exist_ok=True)  # vide : aucune clé, réel impossible
    s = Settings(db_path=tmp / "bench.db", role="engine", secrets_dir=tmp / "secrets",
                 real_trading_server_flag=False)
    mem0 = memory_snapshot()
    eng = Engine(s, Database(s.db_path), pub)
    eng.start()
    assert eng.real_exec is None and eng.real_exchange is None, "le banc doit rester en simulation"
    res: dict[str, Any] = {"memoire_avant": mem0}
    res["decalage_horloge"] = await timed(eng.measure_clock_skew)
    res["decalage_horloge"]["ms"] = eng.clock_skew_ms
    res["refresh_markets"] = await timed(eng.refresh_markets)
    res["refresh_tickers_avec_24h"] = await timed(eng.refresh_tickers)
    lim = eng.limits.max_stale_ms
    res["perimes_apres_tickers"] = staleness(eng.states, eng.clock.now_ms(), eng.clock_skew_ms, lim)
    now = eng.clock.now_ms()
    eligible = [m for m, st in eng.states.items()
                if check_eligibility(st, eng.limits, now, clock_skew_ms=eng.clock_skew_ms).eligible]
    wanted = list(dict.fromkeys(sorted(eng.held_positions("simulation")) + eligible))
    if max_candle_markets > 0:
        wanted = wanted[:max_candle_markets]
    res["univers"] = {"marches": len(eng.markets), "marches_eur": sum(1 for m in eng.markets.values()
                                                                        if m.quote == "EUR"),
                      "eligibles": len(eligible), "bougies_demandees": len(wanted)}
    t_c = time.perf_counter()
    cpu_c = time.process_time()
    done = errs = 0
    stopped = None
    for m in wanted:  # le moteur procède par paquets de 20 par cycle ; ici tout l'univers voulu, marché par marché
        try:
            await eng.refresh_candles([m])
            done += 1
        except BudgetEpuise as e:
            stopped = str(e)
            break
        except httpx.HTTPError:
            errs += 1
    res["refresh_candles_univers"] = {"marches": done, "erreurs": errs, "interrompu": stopped, "mur_s": round(time.perf_counter() - t_c, 3),
                                      "cpu_s": round(time.process_time() - cpu_c, 3),
                                      "cycles_moteur_necessaires": -(-len(wanted) // 20),
                                      "note": "le moteur réel charge au plus 20 marchés par cycle de décision (60 s)"}
    res["perimes_apres_bougies_sans_rafraichir"] = staleness(eng.states, eng.clock.now_ms(), eng.clock_skew_ms, lim)
    res["refresh_tickers"] = await timed(eng.refresh_tickers)  # chaque cycle de 5 s, juste avant la décision
    res["perimes_avant_decision"] = staleness(eng.states, eng.clock.now_ms(), eng.clock_skew_ms, lim)
    now = eng.clock.now_ms()
    res["bougies_perimees"] = sum(1 for m in wanted if m not in eng.candles
                                  or int(eng.candles[m].index[-1]) + 2 * INTERVAL_MS <= now)

    async def manage_and_decide() -> None:
        for ex in eng.execs():
            await eng.manage_positions(ex)
        await eng.decide()

    decisions = [await timed(manage_and_decide) for _ in range(3)]
    res["decision_univers"] = {"premiere": decisions[0], "suivantes": decisions[1:],
                               "lignes_scanner": len(eng.db.kv_get("scanner", {}).get("rows", [])),
                               "propositions": sum(len(r.get("propositions", []))
                                                   for r in eng.db.kv_get("scanner", {}).get("rows", []))}
    tracemalloc.start()
    await manage_and_decide()
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    res["decision_tracemalloc"] = {"pic_alloue_mo": round(peak / 1e6, 2), "residuel_mo": round(cur / 1e6, 2)}
    res["memoire_apres"] = memory_snapshot()
    with contextlib.suppress(OSError):
        res["taille_base_mo"] = round(os.path.getsize(s.db_path) / 1e6, 2)
    res["mode"] = eng.exec.mode if eng.exec else None
    res["top_volume_eur"] = [m for m, _ in sorted(((m, st.quote_volume_24h) for m, st in eng.states.items()
                                                    if st.info.quote == "EUR"), key=lambda kv: -kv[1])[:8]]
    return res


# ------------------------------------------------------------------ orchestration
def environment() -> dict[str, Any]:
    cpu = ""
    with contextlib.suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    mem = None
    with contextlib.suppress(OSError, ValueError, IndexError):
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                mem = round(int(line.split()[1]) / 1024 / 1024, 1)
    return {"python": platform.python_version(), "systeme": platform.system(), "architecture": platform.machine(),
            "cpus": os.cpu_count(), "cpu_modele": cpu, "memoire_go": mem,
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
            "runner_os": os.environ.get("RUNNER_OS"), "runner_arch": os.environ.get("RUNNER_ARCH")}


PROUVE = ["latence et gigue des points d'accès PUBLICS depuis la machine de mesure (un runner GitHub, pas le serveur "
          "définitif ; région du runner inconnue)",
          "poids réellement décompté par Bitvavo et marge par rapport à la limite publique",
          "comportement du flux WebSocket public : délais, débit, reprise après fermeture volontaire",
          "coût CPU / mémoire du balayage complet de l'univers par le moteur réel en SIMULATION",
          "part des marchés jugés périmés par la règle du moteur au moment de la décision"]
NE_PROUVE_PAS = ["l'exécution réelle des ordres (aucun ordre, aucune clé, aucun point d'accès privé)",
                 "la latence depuis le serveur définitif (à remesurer sur la machine retenue)",
                 "la tenue sur 24 h / plusieurs jours (mesure de quelques minutes)",
                 "la rentabilité d'une stratégie ; le Short reste expérimental"]


async def run_bench(args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None,
                    connect: Callable[[str], Any] | None = None) -> dict[str, Any]:
    t0 = time.monotonic()
    total = args.minutes * 60
    guard = RateGuard(args.poids_par_minute, args.reserve)
    probe = RestProbe(guard, args.rest_url, transport)
    pub = GuardedPublic(probe)
    out: dict[str, Any] = {"version": 1, "debut_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                           "parametres": {"minutes": args.minutes, "poids_par_minute": args.poids_par_minute,
                                          "reserve": args.reserve, "reconnexion_toutes_s": args.reconnexion_s,
                                          "moteur": not args.sans_moteur},
                           "environnement": environment(), "aucune_cle": True, "aucun_ordre": True}
    code = 0
    try:
        try:
            out["decalage_horloge"] = await measure_skew(pub)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            out["decalage_horloge"] = {"ms": 0.0, "erreur": repr(e)[:300]}
        skew = float(out["decalage_horloge"]["ms"])
        ws_markets = list(MARCHES_WS)
        if not args.sans_moteur:
            probe.deadline = t0 + total * 0.45
            try:
                out["moteur"] = await bench_engine(pub, args.max_marches_bougies)
                ws_markets = out["moteur"].get("top_volume_eur") or ws_markets
            except (BudgetEpuise, httpx.HTTPError, KeyError, TypeError, ValueError) as e:
                out["moteur"] = {"incomplet": repr(e)[:300]}
        remaining = max(1.0, total - (time.monotonic() - t0) - min(5.0, total * 0.05))
        ws = WsProbe(args.ws_url, ws_markets[:8], connect=connect, skew_ms=skew, guard=guard)
        rest_task = asyncio.create_task(rest_loop(probe, MARCHES_BOUGIES, remaining, args.pause))
        ws_task = asyncio.create_task(ws.run(remaining, args.reconnexion_s, args.max_reconnexions))
        results = await asyncio.gather(rest_task, ws_task, return_exceptions=True)
        if isinstance(results[0], BaseException) and not isinstance(results[0], Halt | BudgetEpuise):
            out["erreur_rest"] = repr(results[0])[:300]
        out["cycles_rest"] = results[0] if isinstance(results[0], int) else None
        out["websocket"] = ws.report()
        if ws.halted:
            guard.halted = guard.halted or ws.halted
    except Halt:
        pass
    except BudgetEpuise as e:
        out["budget"] = str(e)
    except httpx.HTTPError as e:  # réseau indisponible (proxy, DNS) : résultat partiel, jamais de rafale
        out["erreur_reseau"] = repr(e)[:300]
        code = 4
    finally:
        out["rest"] = probe.report()
        out["limites_api"] = guard.report()
        await probe.aclose()
    if guard.halted:
        out["arret"] = guard.halted
        code = 3
    elif not any(probe.lat.values()):
        out["aucune_mesure"] = "aucune réponse REST valide (réseau bloqué ?) : résultat inexploitable"
        code = 4
    out["duree_s"] = round(time.monotonic() - t0, 1)
    out["ce_que_cela_prouve"] = PROUVE
    out["ce_que_cela_ne_prouve_pas"] = NE_PROUVE_PAS
    out["code_sortie"] = code
    return out


def emit(result: dict[str, Any], out_path: str | None = None) -> str:
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    return f"{MARQUEUR_DEBUT}\n{text}\n{MARQUEUR_FIN}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float, default=8.0, help="durée totale (défaut 8 min)")
    ap.add_argument("--poids-par-minute", type=int, default=400, help="plafond PROPRE de poids/min (≤ 1000)")
    ap.add_argument("--reserve", type=int, default=150, help="réserve sur l'en-tête remaining")
    ap.add_argument("--pause", type=float, default=1.0, help="pause entre deux requêtes REST de la rotation (s)")
    ap.add_argument("--reconnexion-s", type=float, default=90.0, help="fermeture volontaire du WS toutes les N s")
    ap.add_argument("--max-reconnexions", type=int, default=3)
    ap.add_argument("--max-marches-bougies", type=int, default=0, help="0 = toutes les bougies voulues")
    ap.add_argument("--sans-moteur", action="store_true", help="ne pas mesurer le balayage par le moteur")
    ap.add_argument("--rest-url", default=REST_URL)
    ap.add_argument("--ws-url", default=WS_URL)
    ap.add_argument("--out", default=None, help="fichier JSON de sortie")
    a = ap.parse_args(argv)
    if not 0 < a.minutes <= 14:
        ap.error("--minutes doit être dans ]0, 14] (budget CI de 15 min)")
    if not 0 < a.poids_par_minute <= LIMITE_PUBLIQUE:
        ap.error("--poids-par-minute doit être dans ]0, 1000]")
    return a


def main(argv: Sequence[str] | None = None) -> int:
    os.environ.setdefault("NELYQOR_REAL_TRADING_ALLOWED", "false")
    if os.environ.get("NELYQOR_REAL_TRADING_ALLOWED", "").lower() == "true":
        print("refus : le banc ne tourne jamais avec le mode réel autorisé", file=sys.stderr)
        return 2
    args = parse_args(argv)
    result = asyncio.run(run_bench(args))
    print(emit(result, args.out))
    return int(result["code_sortie"])


if __name__ == "__main__":
    sys.exit(main())
