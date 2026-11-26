"""Hygiène informationnelle : doublons exacts et quasi-doublons, recyclage, corroboration, rumeur,
authenticité par source officielle. Tout est POINT-IN-TIME : un calcul « à l'instant t » n'utilise que des
observations reçues à t ou avant.

Garde-fous de rapprochement (un faux rapprochement fabrique de la corroboration ou de l'authenticité) :
- deux titres ne sont JAMAIS fusionnés si leurs entités, actifs ou nombres diffèrent
  (« SEC charges Binance » ≠ « SEC charges Coinbase », « 25 bps » ≠ « 50 bps ») ;
- un groupe contenant une observation officielle n'accepte qu'un doublon exact ou un recouvrement ≥ 0,85 ;
- l'index « déjà vu » (répétitions, identifiants, signatures de titre) est conservé à LONG TERME.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from . import lexicon
from .model import Event
from .safety import has_injection_flag

NEAR_DUP_JACCARD = 0.6
OFFICIAL_JACCARD = 0.85
MIN_TOKENS = 4
RECYCLED_AGE_MS = 24 * 3_600_000      # date déclarée bien antérieure à la première réception
RESURFACED_AGE_MS = 24 * 3_600_000    # déjà vu (quasi-doublon) il y a plus de 24 h
NEAR_DUP_WINDOW_MS = 7 * 86_400_000   # fenêtre de rapprochement par similarité

STOPWORDS = frozenset(
    "the a an of to in on for and or is are was be by with as at from its it this that after amid over says "
    "le la les de des du un une et ou en au aux pour par sur dans est sont a ce cette qui que selon apres "
    "plus avec son sa ses".split())


def tokens(norm_title: str) -> frozenset[str]:
    return frozenset(t for t in norm_title.split() if t not in STOPWORDS and len(t) > 1)


def numbers(norm_title: str) -> frozenset[str]:
    return frozenset(t for t in norm_title.split() if any(c.isdigit() for c in t))


def signature(norm_title: str) -> str:
    """Signature du titre (ensemble des jetons) pour l'index « déjà vu » à long terme ; "" si trop court."""
    toks = tokens(norm_title)
    if len(toks) < MIN_TOKENS:
        return ""
    return hashlib.sha256(" ".join(sorted(toks)).encode()).hexdigest()[:20]


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


Features = tuple[frozenset[str], frozenset[str], frozenset[str]]


def features(ev: Event) -> Features:
    """(entités, actifs, nombres) du TITRE seul."""
    ents, _, assets = lexicon.detect(ev.norm_title, ev.title)
    return frozenset(ents), frozenset(assets), numbers(ev.norm_title)


def compatible(a: Features, b: Features) -> bool:
    return a == b


@dataclass
class _Entry:
    id: str
    cluster: str
    received_ms: int
    toks: frozenset[str]
    feats: Features


class Deduplicator:
    """Index des observations connues : exactes par id (long terme), signatures de titre (long terme),
    quasi-doublons par jetons du titre normalisé (fenêtre glissante)."""

    def __init__(self, threshold: float = NEAR_DUP_JACCARD, window_ms: int = NEAR_DUP_WINDOW_MS) -> None:
        self.threshold = threshold
        self.window_ms = window_ms
        self.by_id: dict[str, str] = {}                 # id -> groupe
        self.seen_source_id: set[tuple[str, str]] = set()
        self.cluster_first_ms: dict[str, int] = {}
        self.by_sig: dict[str, str] = {}                # signature -> groupe
        self.official_clusters: set[str] = set()
        self._entries: dict[str, _Entry] = {}
        self._inv: dict[str, list[_Entry]] = defaultdict(list)

    def remember(self, source: str, ev_id: str, cluster: str, sig: str, received_ms: int,
                 official: bool = False) -> None:
        """Entrée compacte de l'index à long terme (sans texte)."""
        self.seen_source_id.add((source, ev_id))
        self.by_id.setdefault(ev_id, cluster)
        if sig:
            self.by_sig.setdefault(sig, cluster)
        self.cluster_first_ms[cluster] = min(self.cluster_first_ms.get(cluster, received_ms), received_ms)
        if official:
            self.official_clusters.add(cluster)

    def add(self, ev: Event) -> None:
        c = ev.cluster or ev.id
        self.remember(ev.source, ev.id, c, signature(ev.norm_title), ev.received_ms, ev.official)
        if ev.id not in self._entries:
            e = _Entry(ev.id, c, ev.received_ms, tokens(ev.norm_title), features(ev))
            self._entries[ev.id] = e
            for t in e.toks:
                self._inv[t].append(e)

    def find(self, ev: Event) -> tuple[str, str, float]:
        """(type de doublon, groupe, similarité) : ("exact", c, 1) | ("quasi", c, s) | ("", ev.id, 0)."""
        if ev.id in self.by_id:
            return "exact", self.by_id[ev.id], 1.0
        toks = tokens(ev.norm_title)
        if len(toks) < MIN_TOKENS:
            return "", ev.id, 0.0
        feats = features(ev)
        best, best_s = None, 0.0
        cands: dict[str, _Entry] = {}
        for t in toks:
            for e in self._inv.get(t, ()):
                cands[e.id] = e
        for e in cands.values():
            if abs(ev.received_ms - e.received_ms) > self.window_ms or len(e.toks) < MIN_TOKENS:
                continue
            if not compatible(feats, e.feats):
                continue
            s = jaccard(toks, e.toks)
            need = OFFICIAL_JACCARD if (ev.official or e.cluster in self.official_clusters) else self.threshold
            if s >= need and s > best_s:
                best, best_s = e, s
        if best is not None:
            return "quasi", best.cluster, best_s
        sig = signature(ev.norm_title)
        if sig and sig in self.by_sig:  # même titre (jetons identiques) vu hors fenêtre : réapparition
            c = self.by_sig[sig]
            if not ev.official and c not in self.official_clusters:
                return "quasi", c, 1.0
        return "", ev.id, 0.0


def classify(ev: Event, dedup: Deduplicator) -> Event | None:
    """Rattache une observation au groupe existant et pose les drapeaux de recyclage.
    Rend None si cette source a déjà livré exactement cette observation (simple répétition du flux)."""
    if (ev.source, ev.id) in dedup.seen_source_id:
        return None
    kind, cluster, sim = dedup.find(ev)
    ev.dup, ev.cluster, ev.match = kind, cluster, round(sim, 3)
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


def independent_key(e: Event) -> str:
    """Clé d'indépendance : l'agence pour une reprise de dépêche, sinon le domaine éditeur ; "" = inconnu."""
    return e.agency or e.publisher


@dataclass(frozen=True)
class Assessment:
    cluster: str
    first_received_ms: int
    n_observations: int
    n_sources: int
    n_publishers: int          # éditeurs/agences INDÉPENDANTS connus = indice de corroboration
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
    publishers = {k for k in (independent_key(e) for e in obs) if k}
    official = any(e.official for e in obs)
    rumor_words = any(e.rumor_terms for e in obs)
    return Assessment(
        cluster=first.cluster or first.id, first_received_ms=first.received_ms, n_observations=len(obs),
        n_sources=len({e.source for e in obs}), n_publishers=len(publishers), official=official,
        rumor=rumor_words and not official,
        recycled=any(f.startswith("recycle_") for f in first.flags),
        injection=any(has_injection_flag(e.flags) for e in obs))
