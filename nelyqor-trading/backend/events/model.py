"""Modèle d'événement normalisé (une OBSERVATION d'une source ; plusieurs observations forment un groupe)."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from . import lexicon
from .safety import (
    MAX_SUMMARY,
    MAX_TITLE,
    clean_text,
    host_matches,
    host_of,
    normalize_title,
    registrable_domain,
    safe_url,
)


def event_id(canonical_url: str | None, norm_title: str) -> str:
    """Identifiant STABLE : empreinte de l'URL canonique et du titre normalisé."""
    return hashlib.sha256(f"{canonical_url or ''}|{norm_title}".encode()).hexdigest()[:24]


@dataclass
class Event:
    id: str
    source: str                 # nom de l'adaptateur (ex. « google_news_crypto_fr »)
    source_kind: str            # agregateur | media | officiel | depot | plateforme | blockchain
    title: str                  # texte nettoyé, borné (DONNÉE, jamais instruction)
    norm_title: str
    url: str | None             # URL canonique (jamais suivie)
    publisher: str              # domaine éditeur (ex. reuters.com), base de l'indépendance
    published_ms: int | None    # date DÉCLARÉE par la source (peut manquer, être fausse ou future)
    received_ms: int            # première réception PAR NOUS (référence point-in-time)
    summary: str = ""
    official: bool = False      # émis par une source officielle (domaine vérifié)
    entities: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    rumor_terms: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    # renseignés par l'hygiène / la collecte
    cluster: str = ""           # id de la première observation du groupe (quasi-doublons)
    dup: str = ""               # "" | exact | quasi
    processed_ms: int | None = None
    fetched_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def make_event(*, source: str, source_kind: str, title: object, url: object, received_ms: int,
               published_ms: int | None = None, summary: object = "", publisher_url: object = None,
               official_domains: tuple[str, ...] = ()) -> Event | None:
    """Construit une observation nettoyée ; None si le titre est vide après nettoyage."""
    clean_title, f1 = clean_text(title, MAX_TITLE)
    if not clean_title:
        return None
    clean_summary, f2 = clean_text(summary, MAX_SUMMARY)
    flags = sorted(set(f1) | {f"resume_{x}" for x in f2})
    canon = safe_url(url)
    if url and canon is None:
        flags.append("url_refusee")
    pub_canon = safe_url(publisher_url) if publisher_url else None
    host = host_of(pub_canon or canon)
    publisher = registrable_domain(host) if host else ""
    official = bool(host) and any(host_matches(host, d) for d in official_domains)
    if official_domains and host and not official:
        flags.append("domaine_non_officiel")
    norm = normalize_title(clean_title)
    norm_all = normalize_title(f"{clean_title} {clean_summary}")
    entities, categories, assets = lexicon.detect(norm_all)
    if published_ms is not None and published_ms > received_ms + 10 * 60_000:
        flags.append("date_future")
    return Event(id=event_id(canon, norm), source=source, source_kind=source_kind, title=clean_title,
                 norm_title=norm, url=canon, publisher=publisher, published_ms=published_ms,
                 received_ms=received_ms, summary=clean_summary, official=official, entities=entities,
                 categories=categories, assets=assets, rumor_terms=lexicon.rumor_terms(norm), flags=flags)
