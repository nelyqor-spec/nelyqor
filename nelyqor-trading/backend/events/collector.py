"""Collecte : une requête par source, mesure de disponibilité, analyse, normalisation, hygiène, journal.

Le client HTTP :
- n'envoie AUCUNE clé ni identité personnelle (User-Agent générique ; `NELYQOR_EVENTS_UA` permet au
  propriétaire d'en déclarer un autre, p. ex. exigé par la SEC — jamais codé en dur) ;
- borne la taille lue et la durée ; ne suit une redirection que vers le même hôte ou un hôte autorisé ;
- ne suit JAMAIS les liens contenus dans les articles."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from . import journal, parsers
from .hygiene import NEAR_DUP_WINDOW_MS, Deduplicator, classify
from .model import Event, make_event
from .safety import MAX_DOC_BYTES, host_matches, host_of
from .sources import SourceSpec

DEFAULT_UA = "nelyqor-events/0.1 (collecte personnelle de flux publics; sans contact)"


@dataclass
class FetchResult:
    status: int | None
    text: str
    error: str = ""
    duration_ms: int = 0
    nbytes: int = 0


Fetcher = Callable[[SourceSpec], FetchResult]
Clock = Callable[[], int]


def now_ms() -> int:
    return int(time.time() * 1000)


def http_fetcher(timeout_s: float = 15.0, max_bytes: int = MAX_DOC_BYTES, transport: Any = None) -> Fetcher:
    import httpx

    ua = os.environ.get("NELYQOR_EVENTS_UA", "").strip() or DEFAULT_UA
    client = httpx.Client(timeout=timeout_s, follow_redirects=False, transport=transport,
                          headers={"User-Agent": ua, "Accept": "application/json, application/xml, text/xml, */*"})

    def fetch(spec: SourceSpec) -> FetchResult:
        t0 = time.monotonic()
        url = spec.url
        base_host = host_of(spec.url)
        try:
            for _ in range(4):
                with client.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = urljoin(url, r.headers.get("location", ""))
                        h = host_of(loc)
                        if not loc.startswith("https://") or not (
                                host_matches(h, base_host) or any(host_matches(h, a) for a in spec.allowed_hosts)):
                            return FetchResult(r.status_code, "", f"redirection refusée vers {h[:80]}",
                                               int((time.monotonic() - t0) * 1000))
                        url = loc
                        continue
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            return FetchResult(r.status_code, "", "réponse trop volumineuse",
                                               int((time.monotonic() - t0) * 1000), len(buf))
                    text = buf.decode(r.encoding or "utf-8", errors="replace")
                    err = "" if r.status_code == 200 else f"HTTP {r.status_code}"
                    return FetchResult(r.status_code, text, err, int((time.monotonic() - t0) * 1000), len(buf))
            return FetchResult(None, "", "trop de redirections", int((time.monotonic() - t0) * 1000))
        except httpx.HTTPError as e:
            return FetchResult(None, "", f"{type(e).__name__}: {str(e)[:160]}", int((time.monotonic() - t0) * 1000))

    return fetch


def _raw_items(spec: SourceSpec, text: str, state: dict[str, Any], data_dir: Path, t_ms: int) -> list[dict[str, Any]]:
    if spec.fmt == "feed":
        return parsers.parse_feed(text)
    if spec.fmt == "gdelt":
        return parsers.parse_gdelt(text)
    if spec.fmt == "edgar":
        return parsers.parse_edgar(text, spec.company)
    if spec.fmt == "bitvavo_markets":
        cur = parsers.parse_bitvavo_markets(text)
        prev = state.get("bitvavo_marches") or {}
        state["bitvavo_marches"] = cur
        return parsers.diff_markets(prev, cur)
    if spec.fmt in ("mempool_fees", "blockchain_stats"):
        vals = parsers.parse_mempool_fees(text) if spec.fmt == "mempool_fees" else parsers.parse_blockchain_stats(text)
        journal.append(data_dir / "metriques.jsonl", [{"source": spec.name, "ts_ms": t_ms, **vals}])
        key = f"metrique_{spec.fmt}"
        before = state.get(key)
        state[key] = vals
        return parsers.metric_events(spec.fmt, vals, before if isinstance(before, dict) else None)
    raise ValueError(f"format inconnu : {spec.fmt}")


def collect(specs: list[SourceSpec], data_dir: Path, fetch: Fetcher, clock: Clock = now_ms,
            time_budget_s: float = 300.0, pause_s: float = 0.0) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    start = clock()
    # tout l'historique : répétitions et doublons exacts reconnus à vie ; quasi-doublons dans la fenêtre seulement
    history = journal.load_observations(data_dir)
    dedup = Deduplicator(window_ms=NEAR_DUP_WINDOW_MS)
    for e in history:
        dedup.add(e)
    state = journal.load_state(data_dir)
    rows: list[dict[str, Any]] = []
    total_new = 0
    for spec in specs:
        if (clock() - start) / 1000 > time_budget_s:
            rows.append({"ts_ms": clock(), "source": spec.name, "ok": False, "http": None, "error": "budget de temps",
                         "skipped": True})
            continue
        res = fetch(spec)
        t_recv = clock()
        row: dict[str, Any] = {"ts_ms": t_recv, "source": spec.name, "kind": spec.kind, "http": res.status,
                               "duration_ms": res.duration_ms, "bytes": res.nbytes, "error": res.error[:200],
                               "ok": res.status == 200 and not res.error, "cost": spec.cost}
        new: list[Event] = []
        counts = {"n_items": 0, "n_repeat": 0, "n_exact": 0, "n_quasi": 0, "n_rejected": 0}
        if row["ok"]:
            try:
                raws = _raw_items(spec, res.text, state, data_dir, t_recv)
            except (ValueError, KeyError, TypeError, IndexError) as e:
                raws = []
                row["ok"] = False
                row["error"] = f"format: {type(e).__name__}: {str(e)[:160]}"
            counts["n_items"] = len(raws)
            for raw in raws:
                ev = make_event(source=spec.name, source_kind=spec.kind, title=raw.get("title"), url=raw.get("url"),
                                received_ms=t_recv, published_ms=raw.get("published"), summary=raw.get("summary", ""),
                                publisher_url=raw.get("publisher_url"), official_domains=spec.official_domains)
                if ev is None:
                    counts["n_rejected"] += 1
                    continue
                if raw.get("asset") and raw["asset"] not in ev.assets:
                    ev.assets = sorted({*ev.assets, str(raw["asset"])[:12]})
                ev.fetched_ms = t_recv
                kept = classify(ev, dedup)
                if kept is None:
                    counts["n_repeat"] += 1
                    continue
                if kept.dup == "exact":
                    counts["n_exact"] += 1
                elif kept.dup == "quasi":
                    counts["n_quasi"] += 1
                kept.processed_ms = clock()
                new.append(kept)
        journal.append_observations(data_dir, new)
        row.update(counts)
        row["n_new"] = len(new)
        row["n_new_clusters"] = sum(1 for e in new if not e.dup)
        rows.append(row)
        total_new += len(new)
        if pause_s:
            time.sleep(pause_s)
    journal.append(data_dir / "collectes.jsonl", rows)
    journal.save_state(data_dir, state)
    return {"sources": len(specs), "ok": sum(1 for r in rows if r.get("ok")), "new": total_new,
            "duration_s": round((clock() - start) / 1000, 1), "rows": rows}
