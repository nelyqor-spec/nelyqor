"""Hygiène informationnelle : doublons exacts et quasi-doublons, recyclage, corroboration, rumeur,
authenticité par source officielle. Tout est POINT-IN-TIME : un calcul « à l'instant t » n'utilise que des
observations reçues à t ou avant."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .model import Event

NEAR_DUP_JACCARD = 0.6
MIN_TOKENS = 4
RECYCLED_AGE_MS = 24 * 3_600_000      # date déclarée bien antérieure à la première réception
RESURFACED_AGE_MS = 24 * 3_600_000    # déjà vu (quasi-doublon) il y a plus de 24 h
NEAR_DUP_WINDOW_MS = 7 * 86_400_000   # fenêtre de rapprochement

STOPWORDS = frozenset(
    "the a an of to in on for and or is are was be by with as at from its it this that after amid over says "
    "le la les de des du un une et ou en au aux pour par sur dans est sont a ce cette qui que selon apres "
    "plus avec son sa ses".split())


def tokens(norm_title: str) -> frozenset[str]:
    return frozenset(t for t in norm_title.split() if t not in STOPWORDS and len(t) > 1)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class _Entry:
    id: str
    cluster: str
    received_ms: int
    toks: frozenset[str]


class Deduplicator:
    """Index des observations connues (exactes par id, approchées par jetons du titre normalisé)."""

    def __init__(self, threshold: float = NEAR_DUP_JACCARD, window_ms: int = NEAR_DUP_WINDOW_MS) -> None:
        self.threshold = threshold
        self.window_ms = window_ms
        self.by_id: dict[str, _Entry] = {}
        self.seen_source_id: set[tuple[str, str]] = set()
        self.cluster_first_ms: dict[str, int] = {}
        self._inv: dict[str, list[_Entry]] = defaultdict(list)

    def add(self, ev: Event) -> None:
        e = _Entry(ev.id, ev.cluster or ev.id, ev.received_ms, tokens(ev.norm_title))
        self.seen_source_id.add((ev.source, ev.id))
        if ev.id not in self.by_id:
            self.by_id[ev.id] = e
            for t in e.toks:
                self._inv[t].append(e)
        c = e.cluster
        self.cluster_first_ms[c] = min(self.cluster_first_ms.get(c, ev.received_ms), ev.received_ms)

    def find(self, ev: Event) -> tuple[str, str]:
        """(type de doublon, groupe) : ("exact", c) | ("quasi", c) | ("", ev.id)."""
        if ev.id in self.by_id:
            return "exact", self.by_id[ev.id].cluster
        toks = tokens(ev.norm_title)
        if len(toks) < MIN_TOKENS:
            return "", ev.id
        best, best_s = None, 0.0
        cands: dict[str, _Entry] = {}
        for t in toks:
            for e in self._inv.get(t, ()):
                cands[e.id] = e
        for e in cands.values():
            if abs(ev.received_ms - e.received_ms) > self.window_ms or len(e.toks) < MIN_TOKENS:
                continue
            s = jaccard(toks, e.toks)
            if s > best_s:
                best, best_s = e, s
        if best is not None and best_s >= self.threshold:
            return "quasi", best.cluster
        return "", ev.id


def classify(ev: Event, dedup: Deduplicator) -> Event | None:
    """Rattache une observation au groupe existant et pose les drapeaux de recyclage.
    Rend None si cette source a déjà livré exactement cette observation (simple répétition du flux)."""
    if (ev.source, ev.id) in dedup.seen_source_id:
        return None
    kind, cluster = dedup.find(ev)
    ev.dup, ev.cluster = kind, cluster
    flags = set(ev.flags)
    if ev.published_ms is not None and ev.received_ms - ev.published_ms > RECYCLED_AGE_MS:
        flags.add("recycle_date_ancienne")
    first = dedup.cluster_first_ms.get(cluster)
    if kind and first is not None and ev.received_ms - first > RESURFACED_AGE_MS:
        flags.add("recycle_deja_vu")
    ev.flags = sorted(flags)
    dedup.add(ev)
    return ev


def group(events: Iterable[Event]) -> dict[str, list[Event]]:
    out: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        out[e.cluster or e.id].append(e)
    for v in out.values():
        v.sort(key=lambda e: e.received_ms)
    return dict(out)


@dataclass(frozen=True)
class Assessment:
    cluster: str
    first_received_ms: int
    n_observations: int
    n_sources: int
    n_publishers: int          # domaines éditeurs distincts = indice de corroboration
    official: bool             # confirmé par une source officielle
    rumor: bool                # termes de rumeur ET aucune source officielle
    recycled: bool
    injection: bool


def assess(cluster_events: list[Event], as_of_ms: int) -> Assessment | None:
    """Évaluation d'un groupe À L'INSTANT `as_of_ms` (seules les observations déjà reçues comptent)."""
    obs = [e for e in cluster_events if e.received_ms <= as_of_ms]
    if not obs:
        return None
    first = min(obs, key=lambda e: e.received_ms)
    publishers = {e.publisher or e.source for e in obs}
    official = any(e.official for e in obs)
    rumor_words = any(e.rumor_terms for e in obs)
    return Assessment(
        cluster=first.cluster or first.id, first_received_ms=first.received_ms, n_observations=len(obs),
        n_sources=len({e.source for e in obs}), n_publishers=len(publishers), official=official,
        rumor=rumor_words and not official,
        recycled=any(f.startswith("recycle_") for f in first.flags),
        injection=any("formulation_injonctive" in e.flags for e in obs))
