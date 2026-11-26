"""Hygiène de sécurité des contenus externes (données NON FIABLES).

- Texte : entités HTML décodées, balises supprimées, caractères de contrôle / bidi retirés, `<` `>` retirés
  (aucun HTML ne peut subsister), espaces normalisés, longueur BORNÉE.
- URL : seuls `http`/`https` sont acceptés (pas de `javascript:`, `data:`, `file:`…), pas d'identifiants
  intégrés, hôte valide, longueur bornée ; forme canonique (minuscules, sans fragment ni paramètres de suivi).
- Formulations injonctives (injection de prompt, appels à acheter/vendre) : SIGNALÉES, jamais suivies.
- XML : DTD / entités refusées (XXE, bombe à expansion), taille bornée avant analyse.
"""
from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_TITLE = 300
MAX_SUMMARY = 500
MAX_URL = 2048
MAX_DOC_BYTES = 5_000_000

_TAG = re.compile(r"<[^>]{0,10000}>", re.S)
_SCRIPT = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
# contrôles C0/C1 (sauf espaces usuels), formats bidi et largeur nulle
_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]")
_WS = re.compile(r"\s+")
_INJECTION = re.compile(
    r"(ignore\s+(all\s+|any\s+)?(previous|prior|above|the\s+above)|disregard\s+(all|previous|the)|"
    r"system\s+prompt|you\s+are\s+now|new\s+instructions|ignore[zr]?\s+les\s+instructions|"
    r"oublie[zr]?\s+(les|toutes)|tu\s+es\s+maintenant|"
    r"buy\s+now|sell\s+everything|achete[zr]?\s+(maintenant|tout|du)|vende[zr]?\s+tout|"
    r"guaranteed\s+(profit|returns?)|gains?\s+garantis?|100x)",
    re.I,
)
_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|cmpid|ocid|taid)$", re.I)
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)+$")
# suffixes publics à deux niveaux les plus courants (approximation SANS dépendance)
_SECOND_LEVEL = {"co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "co.jp", "co.kr", "com.br", "co.in",
                 "europa.eu", "gouv.fr", "com.cn", "co.nz", "com.sg", "com.tr"}


def clean_text(value: object, max_len: int = MAX_TITLE) -> tuple[str, list[str]]:
    """Texte brut sûr (aucune balise, aucun contrôle, longueur bornée) + drapeaux."""
    flags: list[str] = []
    if value is None:
        return "", flags
    t = str(value)
    if len(t) > 20 * max_len + 10_000:  # on ne traite jamais une chaîne énorme en entier
        t = t[: 20 * max_len + 10_000]
        flags.append("tronque")
    for _ in range(2):  # double encodage fréquent dans les flux (&amp;lt;b&amp;gt;)
        t = html.unescape(t)
    if _SCRIPT.search(t) or _TAG.search(t):
        flags.append("html_retire")
        t = _SCRIPT.sub(" ", t)
        t = _TAG.sub(" ", t)
    t = t.replace("<", " ").replace(">", " ")
    t = _CTRL.sub("", t)
    t = _WS.sub(" ", t).strip()
    if _INJECTION.search(t):
        flags.append("formulation_injonctive")
    if len(t) > max_len:
        t = t[:max_len].rstrip() + "…"
        if "tronque" not in flags:
            flags.append("tronque")
    return t, flags


def normalize_title(title: str) -> str:
    """Titre comparable : minuscules, sans accents, ponctuation -> espace."""
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9$]+", " ", t)
    return _WS.sub(" ", t).strip()


def safe_url(value: object) -> str | None:
    """URL canonique sûre, ou None si refusée. Ne déclenche AUCUNE requête."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or _CTRL.search(raw) or len(raw) > MAX_URL or any(c in raw for c in " \t\r\n<>\"'`\\"):
        return None
    try:
        p = urlsplit(raw)
        port = p.port
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or p.username or p.password or "@" in p.netloc:
        return None
    host = (p.hostname or "").lower().rstrip(".")
    if not _HOST.match(host):
        return None
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)])
    path = p.path or "/"
    return urlunsplit((p.scheme.lower(), netloc, path, query, ""))


def host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        h = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def registrable_domain(host: str) -> str:
    """Domaine « éditeur » approximatif (a.b.example.co.uk -> example.co.uk)."""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def host_matches(host: str, domain: str) -> bool:
    """Vrai si `host` est `domain` ou l'un de ses sous-domaines (sec.gov.evil.com n'est PAS sec.gov)."""
    host, domain = host.lower().strip("."), domain.lower().strip(".")
    return host == domain or host.endswith("." + domain)


def check_xml(text: str) -> None:
    """Refuse avant analyse les documents XML dangereux (DTD, entités) ou démesurés."""
    if len(text) > MAX_DOC_BYTES:
        raise ValueError("document trop volumineux")
    up = text.upper()
    if "<!DOCTYPE" in up or "<!ENTITY" in up:
        raise ValueError("DTD/entités XML refusées (protection XXE / bombe XML)")
