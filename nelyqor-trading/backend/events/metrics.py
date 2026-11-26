"""Mesures par source (le choix des sources se fait sur ces chiffres, pas sur la réputation) et mesure
DIFFÉRÉE de la réaction du marché."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import journal
from .hygiene import Assessment, assess, group
from .market import HORIZONS_H, INTERVAL_MS, TOLERANCE_MS, CandleCache, reaction
from .model import Event
from .sources import SOURCES

H_MS = 3_600_000


def _q(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def relevant(cluster_events: list[Event]) -> bool:
    """Événement « pertinent » pour la mesure de réaction : non recyclé, avec actif, catégorie ou entité."""
    first = cluster_events[0]
    a = assess(cluster_events, first.received_ms)
    if a is None or a.recycled:
        return False
    return bool(first.assets or first.categories or first.entities)


def markets_for(ev: Event, eur_markets: set[str]) -> list[str]:
    out = ["BTC-EUR"]
    for a in ev.assets:
        m = f"{a.upper()}-EUR"
        if m != "BTC-EUR" and m in eur_markets:
            out.append(m)
    return out


def measure_reactions(data_dir: Path, cache: CandleCache, now_ms: int, eur_markets: set[str] | None = None,
                      max_rows: int = 3000) -> dict[str, Any]:
    """Mesure les horizons ÉCOULÉS pas encore mesurés ; ajoute à reactions.jsonl (ajout seul)."""
    eur = eur_markets if eur_markets is not None else set()
    done = {(r["cluster"], r["market"], int(r["horizon_h"])) for r in journal.read(data_dir / "reactions.jsonl")}
    groups = group(journal.load_observations(data_dir))
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for cid, evs in sorted(groups.items(), key=lambda kv: kv[1][0].received_ms):
        if len(rows) >= max_rows:
            stats["limite_atteinte"] += 1
            break
        if not relevant(evs):
            continue
        first = evs[0]
        t0 = first.received_ms
        for m in markets_for(first, eur):
            todo = [h for h in HORIZONS_H if (cid, m, h) not in done and now_ms >= t0 + h * H_MS + TOLERANCE_MS + INTERVAL_MS]
            if not todo:
                continue
            candles = cache.get(m, t0 - INTERVAL_MS, t0 + max(todo) * H_MS + TOLERANCE_MS + INTERVAL_MS)
            if candles is None:
                stats["echec_bougies"] += 1
                continue  # échec réseau : réessayé au prochain passage
            for h in todo:
                rc = reaction(candles, t0, h, now_ms)
                if rc.status == "horizon_non_ecoule":
                    continue
                stats[rc.status] += 1
                rows.append({"cluster": cid, "event_id": first.id, "source": first.source, "market": m,
                             "horizon_h": h, "t0_ms": t0, "status": rc.status, "ret": _r(rc.ret, 6),
                             "p0": rc.p0, "ph": rc.ph, "t_start": rc.t_start, "t_end": rc.t_end,
                             "measured_ms": now_ms, "categories": first.categories, "assets": first.assets})
    journal.append(data_dir / "reactions.jsonl", rows)
    return {"rows": len(rows), "requests": cache.requests, **stats}


def source_report(observations: list[Event], fetch_rows: list[dict[str, Any]],
                  reactions: list[dict[str, Any]] | None = None,
                  baseline: dict[int, float | None] | None = None) -> dict[str, Any]:
    groups = group(observations)
    by_source: dict[str, list[Event]] = defaultdict(list)
    for e in observations:
        by_source[e.source].append(e)
    clusters_of: dict[str, set[str]] = {s: {e.cluster or e.id for e in evs} for s, evs in by_source.items()}
    first_of = {cid: evs[0] for cid, evs in groups.items()}
    final: dict[str, Assessment] = {}
    for cid, evs in groups.items():
        asm = assess(evs, evs[-1].received_ms)
        if asm is not None:
            final[cid] = asm

    fetch_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in fetch_rows:
        fetch_by[str(r.get("source"))].append(r)

    react_by: dict[tuple[str, int], list[float]] = defaultdict(list)
    for r in reactions or []:
        if r.get("status") == "mesure" and r.get("market") == "BTC-EUR" and r.get("ret") is not None:
            react_by[(str(r["source"]), int(r["horizon_h"]))].append(abs(float(r["ret"])))

    names = sorted(set(by_source) | set(fetch_by))
    out: dict[str, Any] = {}
    for s in names:
        evs = by_source.get(s, [])
        fr = [r for r in fetch_by.get(s, []) if not r.get("skipped")]
        lat = [(e.received_ms - e.published_ms) / 60_000 for e in evs
               if e.published_ms is not None and e.received_ms >= e.published_ms]
        proc = [float(e.processed_ms - e.fetched_ms) for e in evs if e.processed_ms is not None and e.fetched_ms]
        cl = clusters_of.get(s, set())
        only = [c for c in cl if {e.source for e in groups[c]} == {s}]
        firsts = [c for c in cl if first_of[c].source == s]
        leads = []
        for c in firsts:
            others = [e.received_ms for e in groups[c] if e.source != s]
            if others:
                leads.append((min(others) - first_of[c].received_ms) / 60_000)
        spec = SOURCES.get(s)
        out[s] = {
            "kind": spec.kind if spec else (evs[0].source_kind if evs else ""),
            "cost": spec.cost if spec else "0 €",
            "disponibilite": {
                "requetes": len(fr), "succes": sum(1 for r in fr if r.get("ok")),
                "taux_succes": _r(sum(1 for r in fr if r.get("ok")) / len(fr)) if fr else None,
                "http": dict(Counter(str(r.get("http")) for r in fr)),
                "erreurs": dict(Counter(str(r.get("error"))[:60] for r in fr if r.get("error")).most_common(3)),
                "duree_ms_mediane": _r(statistics.median([r.get("duration_ms") or 0 for r in fr]), 0) if fr else None,
            },
            "observations": len(evs),
            "groupes": len(cl),
            "groupes_exclusifs": len(only),
            "premier_a_publier": len(firsts),
            "avance_min_mediane": _r(statistics.median(leads), 1) if leads else None,
            "taux_doublons": _r(sum(1 for e in evs if e.dup) / len(evs)) if evs else None,
            "repetitions_flux": sum(int(r.get("n_repeat") or 0) for r in fr),
            "delai_publication_reception_min": {"mediane": _r(_q(lat, 0.5), 1), "p90": _r(_q(lat, 0.9), 1),
                                                "n": len(lat),
                                                "part_sup_24h": _r(sum(1 for x in lat if x > 1440) / len(lat))
                                                if lat else None},
            "sans_date_ou_future": sum(1 for e in evs if e.published_ms is None or "date_future" in e.flags),
            "delai_traitement_ms": {"mediane": _r(_q(proc, 0.5), 1), "max": _r(max(proc), 1) if proc else None},
            "recycles": sum(1 for e in evs if any(f.startswith("recycle_") for f in e.flags)),
            "rumeurs": sum(1 for c in cl if c in final and final[c].rumor),
            "corroboration_moyenne": _r(statistics.mean([final[c].n_publishers for c in cl if c in final]), 2)
            if cl else None,
            "groupes_confirmes_officiel": sum(1 for c in cl if c in final and final[c].official),
            "injonctions_signalees": sum(1 for e in evs if "formulation_injonctive" in e.flags),
            "reaction_btc_abs_moyenne": {f"{h}h": _r(statistics.mean(react_by[(s, h)]), 5) if react_by[(s, h)] else None
                                         for h in HORIZONS_H},
            "reaction_n": {f"{h}h": len(react_by[(s, h)]) for h in HORIZONS_H},
        }
    overlap: dict[str, dict[str, int]] = {}
    for a in names:
        row = {}
        for b in names:
            if a < b:
                n = len(clusters_of.get(a, set()) & clusters_of.get(b, set()))
                if n:
                    row[b] = n
        if row:
            overlap[a] = row
    by_cat: dict[tuple[str, int], list[float]] = defaultdict(list)
    for r in reactions or []:
        if r.get("status") == "mesure" and r.get("market") == "BTC-EUR" and r.get("ret") is not None:
            for c in r.get("categories") or ["(aucune)"]:
                by_cat[(str(c), int(r["horizon_h"]))].append(abs(float(r["ret"])))
    cats = sorted({c for c, _ in by_cat})
    par_categorie = {c: {f"{h}h": {"n": len(by_cat[(c, h)]),
                                   "abs_moyenne": _r(statistics.mean(by_cat[(c, h)]), 5) if by_cat[(c, h)] else None}
                         for h in HORIZONS_H} for c in cats}
    return {"sources": out, "recouvrement": overlap, "groupes_total": len(groups),
            "reaction_btc_par_categorie": par_categorie,
            "reference_btc_abs_moyenne": {f"{h}h": _r(v, 5) for h, v in (baseline or {}).items()}}
