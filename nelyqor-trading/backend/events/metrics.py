"""Mesures par source (le choix des sources se fait sur ces chiffres, pas sur la réputation) et mesure
DIFFÉRÉE de la réaction du marché.

Règles de mesure :
- les observations d'AMORÇAGE (premier relevé réussi d'une source : arriéré du flux) et les dates sans heure
  sont exclues des délais et des réactions ;
- « premier à publier » se décide par NUMÉRO DE RELEVÉ : deux sources du même relevé sont ex æquo (l'ordre
  d'interrogation n'est pas une avance) ;
- réactions : seuls les groupes reçus au plus 2 h après leur date déclarée (exacte) sont mesurés ; plusieurs
  événements d'un même relevé partagent la même fenêtre de prix, d'où une pondération 1/N par
  (relevé, marché, horizon).
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import journal
from .hygiene import Assessment, assess, group, independent_key
from .market import HORIZONS_H, INTERVAL_MS, TOLERANCE_MS, CandleCache, reaction
from .model import Event
from .safety import has_injection_flag
from .sources import SOURCES

H_MS = 3_600_000
MAX_REACTION_LAG_MS = 2 * H_MS
SAME_RUN_TOLERANCE_MS = 10 * 60_000  # sans numéro de relevé : réceptions à moins de 10 min = même relevé


def _q(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def run_key(e: Event) -> int:
    return e.run if e.run is not None else e.received_ms


def same_run(a: Event, b: Event) -> bool:
    if a.run is not None and b.run is not None:
        return a.run == b.run
    return abs(a.received_ms - b.received_ms) < SAME_RUN_TOLERANCE_MS


def bootstrap_ids(observations: list[Event]) -> set[str]:
    """Observations d'amorçage : drapeau `amorcage` ; à défaut de drapeau pour une source, celles de son
    premier relevé présent dans le journal."""
    by_source: dict[str, list[Event]] = defaultdict(list)
    for e in observations:
        by_source[e.source].append(e)
    out: set[str] = set()
    for evs in by_source.values():
        flagged = [e for e in evs if "amorcage" in e.flags]
        if flagged:
            out |= {f"{e.source}|{e.id}" for e in flagged}
            continue
        first = min(evs, key=run_key)
        out |= {f"{e.source}|{e.id}" for e in evs if same_run(e, first)}
    return out


def timed(e: Event) -> bool:
    """Date déclarée exploitable pour un délai (présente, avec heure)."""
    return e.published_ms is not None and e.date_precision == ""


def relevant(cluster_events: list[Event]) -> bool:
    """Événement « pertinent » pour la mesure de réaction : non recyclé, hors amorçage, daté avec heure,
    reçu ≤ 2 h après sa date déclarée, avec actif, catégorie ou entité."""
    first = cluster_events[0]
    a = assess(cluster_events, first.received_ms)
    if a is None or a.recycled or "amorcage" in first.flags or not timed(first):
        return False
    assert first.published_ms is not None
    if first.received_ms - first.published_ms > MAX_REACTION_LAG_MS:
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
                      max_rows: int = 3000, since_ms: int | None = None) -> dict[str, Any]:
    """Mesure les horizons ÉCOULÉS pas encore mesurés ; ajoute à reactions.jsonl (ajout seul)."""
    eur = eur_markets if eur_markets is not None else set()
    done = {(r.get("cluster"), r.get("market"), r.get("horizon_h")) for r in journal.read(data_dir / "reactions.jsonl")}
    groups = group(journal.load_observations(data_dir, since_ms=since_ms))
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
            todo = [h for h in HORIZONS_H
                    if (cid, m, h) not in done and now_ms >= t0 + h * H_MS + TOLERANCE_MS + INTERVAL_MS]
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
                             "horizon_h": h, "t0_ms": t0, "run": run_key(first), "status": rc.status,
                             "ret": _r(rc.ret, 6), "p0": rc.p0, "ph": rc.ph, "t_start": rc.t_start,
                             "t_end": rc.t_end, "measured_ms": now_ms, "categories": first.categories,
                             "assets": first.assets})
    journal.append(data_dir / "reactions.jsonl", rows)
    return {"rows": len(rows), "requests": cache.requests, **stats}


def reaction_weights(reactions: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """Réactions mesurées avec poids 1/N, N = nombre de réactions du même (relevé, marché, horizon)."""
    ok = [r for r in reactions if r.get("status") == "mesure" and isinstance(r.get("ret"), int | float)]
    n: Counter[tuple[Any, Any, Any]] = Counter((r.get("run", r.get("t0_ms")), r.get("market"), r.get("horizon_h"))
                                                for r in ok)
    return [(r, 1.0 / n[(r.get("run", r.get("t0_ms")), r.get("market"), r.get("horizon_h"))]) for r in ok]


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    w = sum(p[1] for p in pairs)
    return sum(v * p for v, p in pairs) / w if w else None


def source_report(observations: list[Event], fetch_rows: list[dict[str, Any]],
                  reactions: list[dict[str, Any]] | None = None,
                  baseline: dict[int, float | None] | None = None) -> dict[str, Any]:
    groups = group(observations)
    by_source: dict[str, list[Event]] = defaultdict(list)
    for e in observations:
        by_source[e.source].append(e)
    clusters_of: dict[str, set[str]] = {s: {e.cluster or e.id for e in evs} for s, evs in by_source.items()}
    final: dict[str, Assessment] = {}
    for cid, evs in groups.items():
        asm = assess(evs, evs[-1].received_ms)
        if asm is not None:
            final[cid] = asm
    boot = bootstrap_ids(observations)

    # premier à publier, par relevé (ex æquo dans un même relevé)
    strict_first: dict[str, list[str]] = defaultdict(list)
    tie_first: dict[str, list[str]] = defaultdict(list)
    leads: dict[str, list[float]] = defaultdict(list)
    for cid, evs in groups.items():
        firsts: dict[str, Event] = {}
        for e in evs:
            if e.source not in firsts or run_key(e) < run_key(firsts[e.source]):
                firsts[e.source] = e
        if len(firsts) < 2:
            continue
        earliest = min(firsts.values(), key=run_key)
        leaders = [s for s, e in firsts.items() if same_run(e, earliest)]
        if len(leaders) == 1:
            s = leaders[0]
            strict_first[s].append(cid)
            others = [e.received_ms for src, e in firsts.items() if src != s]
            leads[s].append((min(others) - earliest.received_ms) / 60_000)
        else:
            for s in leaders:
                tie_first[s].append(cid)

    fetch_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in fetch_rows:
        fetch_by[str(r.get("source"))].append(r)

    weighted = reaction_weights(reactions or [])
    react_by: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    by_cat: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for r, w in weighted:
        if r.get("market") != "BTC-EUR":
            continue
        v = abs(float(r["ret"]))
        react_by[(str(r.get("source")), int(r["horizon_h"]))].append((v, w))
        for c in r.get("categories") or ["(aucune)"]:
            by_cat[(str(c), int(r["horizon_h"]))].append((v, w))

    names = sorted(set(by_source) | set(fetch_by))
    out: dict[str, Any] = {}
    for s in names:
        evs = by_source.get(s, [])
        fr = [r for r in fetch_by.get(s, []) if not r.get("skipped")]
        measured = [e for e in evs if f"{e.source}|{e.id}" not in boot]
        lat = [(e.received_ms - e.published_ms) / 60_000 for e in measured
               if timed(e) and e.published_ms is not None and e.received_ms >= e.published_ms]
        proc = [float(e.processed_ms - e.fetched_ms) for e in evs if e.processed_ms is not None and e.fetched_ms]
        cl = clusters_of.get(s, set())
        only = [c for c in cl if {e.source for e in groups[c]} == {s}]
        spec = SOURCES.get(s)
        ok_n = sum(1 for r in fr if r.get("ok"))
        out[s] = {
            "kind": spec.kind if spec else (evs[0].source_kind if evs else ""),
            "cost": spec.cost if spec else "0 €",
            "disponibilite": {
                "requetes": len(fr), "succes": ok_n,
                "taux_succes": _r(ok_n / len(fr)) if fr else None,
                "vides": sum(1 for r in fr if r.get("statut") == "vide"),
                "http": dict(Counter(str(r.get("http")) for r in fr)),
                "erreurs": dict(Counter(str(r.get("error"))[:60] for r in fr if r.get("error")).most_common(3)),
                "duree_ms_mediane": _r(statistics.median([r.get("duration_ms") or 0 for r in fr]), 0) if fr else None,
            },
            "observations": len(evs),
            "amorcage": len(evs) - len(measured),
            "groupes": len(cl),
            "groupes_exclusifs": len(only),
            "premier_a_publier": len(strict_first.get(s, [])),
            "premier_ex_aequo": len(tie_first.get(s, [])),
            "avance_min_mediane": _r(statistics.median(leads[s]), 1) if leads.get(s) else None,
            "taux_doublons": _r(sum(1 for e in evs if e.dup) / len(evs)) if evs else None,
            "repetitions_flux": sum(int(r.get("n_repeat") or 0) for r in fr),
            "delai_publication_reception_min": {"mediane": _r(_q(lat, 0.5), 1), "p90": _r(_q(lat, 0.9), 1),
                                                "n": len(lat),
                                                "part_sup_24h": _r(sum(1 for x in lat if x > 1440) / len(lat))
                                                if lat else None},
            "sans_date_ou_future": sum(1 for e in evs if not timed(e) or "date_future" in e.flags),
            "delai_traitement_ms": {"mediane": _r(_q(proc, 0.5), 1), "max": _r(max(proc), 1) if proc else None},
            "recycles": sum(1 for e in evs if any(f.startswith("recycle_") for f in e.flags)),
            "rumeurs": sum(1 for c in cl if c in final and final[c].rumor),
            "corroboration_moyenne": _r(statistics.mean([final[c].n_publishers for c in cl if c in final]), 2)
            if cl else None,
            "groupes_confirmes_officiel": sum(1 for c in cl if c in final and final[c].official),
            "injonctions_signalees": sum(1 for e in evs if has_injection_flag(e.flags)),
            "editeur_inconnu": sum(1 for e in evs if not independent_key(e)),
            "reaction_btc_abs_moyenne": {f"{h}h": _r(_wmean(react_by[(s, h)]), 5) for h in HORIZONS_H},
            "reaction_n": {f"{h}h": len(react_by[(s, h)]) for h in HORIZONS_H},
            "reaction_n_effectif": {f"{h}h": _r(sum(w for _, w in react_by[(s, h)]), 2) for h in HORIZONS_H},
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
    cats = sorted({c for c, _ in by_cat})
    par_categorie = {c: {f"{h}h": {"n": len(by_cat[(c, h)]),
                                   "n_effectif": _r(sum(w for _, w in by_cat[(c, h)]), 2),
                                   "abs_moyenne": _r(_wmean(by_cat[(c, h)]), 5)}
                         for h in HORIZONS_H} for c in cats}
    return {"sources": out, "recouvrement": overlap, "groupes_total": len(groups),
            "reaction_btc_par_categorie": par_categorie,
            "reference_btc_abs_moyenne": {f"{h}h": _r(v, 5) for h, v in (baseline or {}).items()}}
