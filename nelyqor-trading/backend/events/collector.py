"""Collecte : une requête par source, mesure de disponibilité, analyse, normalisation, hygiène, journal.

Le client HTTP :
- n'envoie AUCUNE clé ni identité personnelle (User-Agent générique ; `NELYQOR_EVENTS_UA` permet au
  propriétaire d'en déclarer un autre, p. ex. exigé par la SEC — jamais codé en dur) ;
- borne la taille lue et la durée (échéance GLOBALE par requête et par source, même si le serveur distille
  sa réponse octet par octet) ; ne suit une redirection que vers le même hôte en HTTPS ou un hôte autorisé ;
- ne suit JAMAIS les liens contenus dans les articles.

Chaque collecte porte un numéro de relevé (`run` = instant de début). Le premier relevé réussi d'une source
est marqué `amorcage` (arriéré du flux) : exclu des délais et des réactions."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from . import journal, parsers
from .hygiene import NEAR_DUP_WINDOW_MS, Deduplicator, classify, signature
from .model import Event, make_event
from .safety import MAX_DOC_BYTES, host_matches, host_of, safe_message
from .sources import SourceSpec

DEFAULT_UA = "nelyqor-events/0.1 (collecte personnelle de flux publics; sans contact)"
HISTORY_MS = 30 * 86_400_000  # observations complètes rechargées (l'index long terme couvre le reste)


def user_agent() -> str:
    return safe_message(os.environ.get("NELYQOR_EVENTS_UA", ""), 200).strip() or DEFAULT_UA


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


def http_fetcher(timeout_s: float = 15.0, max_bytes: int = MAX_DOC_BYTES, transport: Any = None,
                 request_deadline_s: float = 20.0, source_deadline_s: float = 30.0) -> Fetcher:
    import httpx

    client = httpx.Client(timeout=timeout_s, follow_redirects=False, transport=transport,
                          headers={"User-Agent": user_agent(),
                                   "Accept": "application/json, application/xml, text/xml, */*"})

    def fetch(spec: SourceSpec) -> FetchResult:
        t0 = time.monotonic()

        def ms() -> int:
            return int((time.monotonic() - t0) * 1000)

        url = spec.url
        base_host = host_of(spec.url)
        try:
            for _ in range(4):
                t_req = time.monotonic()
                with client.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = urljoin(url, r.headers.get("location", ""))
                        h = host_of(loc)
                        if not loc.startswith("https://") or not (
                                host_matches(h, base_host) or any(host_matches(h, a) for a in spec.allowed_hosts)):
                            return FetchResult(r.status_code, "", "redirection refusée vers " + safe_message(h, 80), ms())
                        url = loc
                        continue
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            return FetchResult(r.status_code, "", "réponse trop volumineuse", ms(), len(buf))
                        now = time.monotonic()
                        if now - t_req > request_deadline_s or now - t0 > source_deadline_s:
                            return FetchResult(r.status_code, "", "échéance dépassée", ms(), len(buf))
                    text = buf.decode(r.encoding or "utf-8", errors="replace")
                    err = "" if r.status_code == 200 else f"HTTP {r.status_code}"
                    return FetchResult(r.status_code, text, err, ms(), len(buf))
                if time.monotonic() - t0 > source_deadline_s:
                    return FetchResult(None, "", "échéance dépassée", ms())
            return FetchResult(None, "", "trop de redirections", ms())
        except httpx.HTTPError as e:
            return FetchResult(None, "", safe_message(f"{type(e).__name__}: {e}", 160), ms())

    return fetch


class RefusedSnapshot(ValueError):
    """Relevé d'état jugé incohérent (vide, partiel) : refusé sans toucher à l'état."""


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
        pending = state.get("bitvavo_disparitions") or {}
        raws, new_state, new_pending, refused = parsers.update_markets(prev, cur, pending, t_ms)
        if refused:
            raise RefusedSnapshot(refused)
        state["bitvavo_marches"] = new_state
        state["bitvavo_disparitions"] = new_pending
        return raws
    if spec.fmt in ("mempool_fees", "blockchain_stats"):
        vals = parsers.parse_mempool_fees(text) if spec.fmt == "mempool_fees" else parsers.parse_blockchain_stats(text)
        journal.append(data_dir / "metriques.jsonl", [{"source": spec.name, "ts_ms": t_ms, **vals}])
        key = f"metrique_{spec.fmt}"
        before = state.get(key)
        state[key] = vals
        return parsers.metric_events(spec.fmt, vals, before if isinstance(before, dict) else None, t_ms)
    raise ValueError(f"format inconnu : {spec.fmt}")


def _load_dedup(data_dir: Path, start: int) -> Deduplicator:
    dedup = Deduplicator(window_ms=NEAR_DUP_WINDOW_MS)
    index_path = data_dir / "index.jsonl"
    recent = journal.load_observations(data_dir, since_ms=start - HISTORY_MS)
    if not index_path.exists():  # première fois (ou journal antérieur à l'index) : index construit une fois
        every = journal.load_observations(data_dir)
        journal.append(index_path, [_index_row(e) for e in every])
    for d in journal.load_index(data_dir):
        dedup.remember(d["s"], d["i"], d["c"], d.get("g", ""), d["t"], bool(d.get("o")))
    for e in recent:
        dedup.add(e)
    return dedup


def _index_row(e: Event) -> dict[str, Any]:
    return {"s": e.source, "i": e.id, "c": e.cluster or e.id, "g": signature(e.norm_title), "t": e.received_ms,
            "o": e.official}


def collect(specs: list[SourceSpec], data_dir: Path, fetch: Fetcher, clock: Clock = now_ms,
            time_budget_s: float = 300.0, pause_s: float = 0.0) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    start = clock()
    dedup = _load_dedup(data_dir, start)
    state = journal.load_state(data_dir)
    primed = set(state.get("sources_amorcees") or [])
    rows: list[dict[str, Any]] = []
    total_new = 0
    for spec in specs:
        if (clock() - start) / 1000 > time_budget_s:
            rows.append({"ts_ms": clock(), "run": start, "source": spec.name, "ok": False, "statut": "echec",
                         "http": None, "error": "budget de temps", "skipped": True})
            continue
        res = fetch(spec)
        t_recv = clock()
        row: dict[str, Any] = {"ts_ms": t_recv, "run": start, "source": spec.name, "kind": spec.kind,
                               "http": res.status, "duration_ms": res.duration_ms, "bytes": res.nbytes,
                               "error": safe_message(res.error), "ok": res.status == 200 and not res.error,
                               "cost": spec.cost}
        new: list[Event] = []
        counts = {"n_items": 0, "n_repeat": 0, "n_exact": 0, "n_quasi": 0, "n_rejected": 0}
        first_success = spec.name not in primed
        if row["ok"]:
            try:
                raws = _raw_items(spec, res.text, state, data_dir, t_recv)
            except (ValueError, KeyError, TypeError, IndexError) as e:
                raws = []
                row["ok"] = False
                row["error"] = safe_message(f"format: {type(e).__name__}: {e}")
            counts["n_items"] = len(raws)
            for raw in raws:
                ev = make_event(source=spec.name, source_kind=spec.kind, title=raw.get("title"), url=raw.get("url"),
                                received_ms=t_recv, published_ms=raw.get("published"), summary=raw.get("summary", ""),
                                publisher_url=raw.get("publisher_url"), official_domains=spec.official_domains,
                                guid=raw.get("guid"), id_salt=str(raw.get("id_salt") or ""),
                                date_precision=str(raw.get("precision") or ""))
                if ev is None:
                    counts["n_rejected"] += 1
                    continue
                if raw.get("asset") and raw["asset"] not in ev.assets:
                    ev.assets = sorted({*ev.assets, safe_message(raw["asset"], 12)})
                extra = [f for f in raw.get("flags") or [] if isinstance(f, str)]
                if first_success:
                    extra.append("amorcage")
                ev.flags = sorted({*ev.flags, *extra})
                ev.fetched_ms = t_recv
                ev.run = start
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
            if row["ok"]:
                primed.add(spec.name)
        # « vide » : réponse 200 valide mais sans aucun item (p. ex. GDELT sans article) ≠ succès avec contenu
        row["statut"] = ("vide" if counts["n_items"] == 0 else "ok") if row["ok"] else "echec"
        journal.append_observations(data_dir, new)
        journal.append(data_dir / "index.jsonl", [_index_row(e) for e in new])
        row.update(counts)
        row["n_new"] = len(new)
        row["n_new_clusters"] = sum(1 for e in new if not e.dup)
        row["amorcage"] = bool(first_success and row["ok"])
        rows.append(row)
        total_new += len(new)
        if pause_s:
            time.sleep(pause_s)
    state["sources_amorcees"] = sorted(primed)
    journal.append(data_dir / "collectes.jsonl", rows)
    journal.save_state(data_dir, state)
    return {"sources": len(specs), "ok": sum(1 for r in rows if r.get("ok")), "new": total_new,
            "duration_s": round((clock() - start) / 1000, 1), "rows": rows}
