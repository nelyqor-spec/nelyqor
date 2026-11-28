"""Modèle d'événement normalisé (une OBSERVATION d'une source ; plusieurs observations forment un groupe)."""
from __future__ import annotations

import hashlib
import re
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

# reprises de dépêches d'agence : l'indépendance se juge à l'AGENCE, pas au site qui la republie
_AGENCY = (
    ("reuters.com", re.compile(r"(\(reuters\)|\breuters\s*[-–—:]|\bvia reuters\b|\bsource\s*:\s*reuters\b)", re.I)),
    ("afp.com", re.compile(r"(\(afp\)|\bavec afp\b|\bsource\s*:\s*afp\b|\bafp\s*[-–—:])", re.I)),
    ("apnews.com", re.compile(r"(\(ap\)|\bassociated press\b)", re.I)),
    ("bloomberg.com", re.compile(r"(\(bloomberg\)|\bvia bloomberg\b|\bbloomberg news\s*[-–—:])", re.I)),
)


def event_id(canonical_url: str | None, norm_title: str, salt: str = "") -> str:
    """Identifiant STABLE : empreinte de l'URL canonique (ou du guid) et du titre normalisé ; `salt` distingue
    les événements dérivés d'un ÉTAT (numéro/heure du relevé), qui peuvent légitimement se répéter."""
    base = f"{canonical_url or ''}|{norm_title}" + (f"|{salt}" if salt else "")  # sans sel : format historique
    return hashlib.sha256(base.encode()).hexdigest()[:24]


_STR = ("id", "source", "source_kind", "title", "norm_title", "publisher", "summary", "cluster", "dup",
        "date_precision", "agency")
_INT = ("received_ms",)
_OPT_INT = ("published_ms", "processed_ms", "fetched_ms", "run")
_LISTS = ("entities", "categories", "assets", "rumor_terms", "flags")


@dataclass
class Event:
    id: str
    source: str                 # nom de l'adaptateur (ex. « google_news_crypto_fr »)
    source_kind: str            # agregateur | media | officiel | depot | plateforme | blockchain
    title: str                  # texte nettoyé, borné (DONNÉE, jamais instruction)
    norm_title: str
    url: str | None             # URL canonique (jamais suivie)
    publisher: str              # domaine éditeur (ex. reuters.com) ; "" = INCONNU (non compté)
    published_ms: int | None    # date DÉCLARÉE par la source (peut manquer, être fausse ou future)
    received_ms: int            # première réception PAR NOUS (référence point-in-time)
    summary: str = ""
    official: bool = False      # émis par une source officielle (domaine vérifié)
    entities: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    rumor_terms: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    date_precision: str = ""    # "" = horodatage complet ; "jour" = date sans heure (pas de mesure de délai)
    agency: str = ""            # agence détectée (reprise de dépêche)
    # renseignés par l'hygiène / la collecte
    cluster: str = ""           # id de la première observation du groupe (quasi-doublons)
    dup: str = ""               # "" | exact | quasi
    match: float = 0.0          # similarité avec le groupe (1.0 = exact)
    run: int | None = None      # numéro du relevé (instant de début de la collecte)
    processed_ms: int | None = None
    fetched_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        """Lecture VALIDÉE (journal) : TypeError si un champ a un type inattendu."""
        if not isinstance(d, dict):
            raise TypeError("objet attendu")
        for k in _STR:
            if k in d and not isinstance(d[k], str):
                raise TypeError(k)
        for k in _INT:
            if not isinstance(d.get(k), int) or isinstance(d.get(k), bool):
                raise TypeError(k)
        for k in _OPT_INT:
            if d.get(k) is not None and (not isinstance(d[k], int) or isinstance(d[k], bool)):
                raise TypeError(k)
        for k in _LISTS:
            if k in d and not (isinstance(d[k], list) and all(isinstance(x, str) for x in d[k])):
                raise TypeError(k)
        if not d.get("id") or not isinstance(d.get("source"), str):
            raise TypeError("id/source")
        if d.get("url") is not None and not isinstance(d["url"], str):
            raise TypeError("url")
        if not isinstance(d.get("official", False), bool) or not isinstance(d.get("match", 0.0), int | float):
            raise TypeError("official/match")
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def detect_agency(text: str) -> str:
    for dom, pat in _AGENCY:
        if pat.search(text):
            return dom
    return ""


def make_event(*, source: str, source_kind: str, title: object, url: object, received_ms: int,
               published_ms: int | None = None, summary: object = "", publisher_url: object = None,
               official_domains: tuple[str, ...] = (), guid: object = None, id_salt: str = "",
               date_precision: str = "") -> Event | None:
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
    entities, categories, assets = lexicon.detect(norm_all, f"{clean_title} {clean_summary}")
    if published_ms is not None and published_ms > received_ms + 10 * 60_000:
        flags.append("date_future")
    key = canon
    if key is None and guid:
        g, _ = clean_text(guid, 300)
        key = f"guid:{g}" if g else None
    agency = detect_agency(f"{clean_title} {clean_summary}")
    if agency and agency != publisher:
        flags.append("reprise_agence")
    return Event(id=event_id(key, norm, id_salt), source=source, source_kind=source_kind, title=clean_title,
                 norm_title=norm, url=canon, publisher=publisher, published_ms=published_ms,
                 received_ms=received_ms, summary=clean_summary, official=official, entities=entities,
                 categories=categories, assets=assets, rumor_terms=lexicon.rumor_terms(norm), flags=flags,
                 date_precision=date_precision, agency=agency)
