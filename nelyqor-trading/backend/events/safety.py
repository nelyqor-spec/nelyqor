"""Hygiène de sécurité des contenus externes (données NON FIABLES).

- Texte : entités HTML décodées, balises supprimées par un balayage LINÉAIRE (aucune expression à retour
  arrière), blocs `<script>`/`<style>` retirés, caractères de contrôle et TOUTE la catégorie Unicode Cf
  (bidi, largeur nulle, word joiner, trait d'union conditionnel, caractères TAG U+E0000–E007F) retirés,
  normalisation NFKC AVANT la détection, espaces normalisés, longueur BORNÉE. Les comparaisons isolées
  (`BTC < 50k`) sont conservées : ce ne sont pas des balises, et le texte n'est JAMAIS rendu en HTML.
- URL : seuls `http`/`https` sont acceptés (pas de `javascript:`, `data:`, `file:`…), pas d'identifiants
  intégrés, pas d'adresse IP, hôte valide, longueur bornée ; forme canonique (sans fragment ni suivi).
- Formulations injonctives (injection de prompt, appels à acheter/vendre) : SIGNALÉES, jamais suivies.
- XML : DTD / entités refusées (XXE, bombe à expansion), taille bornée avant analyse.
"""
from __future__ import annotations

import html
import ipaddress
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_TITLE = 300
MAX_SUMMARY = 500
MAX_URL = 2048
MAX_DOC_BYTES = 5_000_000

INJECTION_FLAG = "formulation_injonctive"
_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
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
_SAFE_MSG = re.compile(r"[^0-9A-Za-zÀ-ÿ .,:;_()/'+=%-]")
# suffixes publics à deux niveaux les plus courants (approximation SANS dépendance)
_SECOND_LEVEL = {"co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "co.jp", "co.kr", "com.br", "co.in",
                 "europa.eu", "gouv.fr", "com.cn", "co.nz", "com.sg", "com.tr"}
_BLOCKS = ("script", "style")


def has_injection_flag(flags: list[str]) -> bool:
    """Injonction relevée dans le titre OU le résumé (drapeaux préfixés, p. ex. `resume_…`)."""
    return any(f.endswith(INJECTION_FLAG) for f in flags)


def strip_invisible(t: str) -> str:
    """Retire contrôles C0/C1 et toute la catégorie Unicode Cf (dont TAG, bidi, ZW*, U+2060, U+00AD)."""
    t = _CTRL.sub("", t)
    return "".join(c for c in t if unicodedata.category(c) != "Cf")


def _strip_markup(t: str) -> tuple[str, bool]:
    """Balayage linéaire : supprime `<script>…</script>`, `<style>…</style>`, commentaires et balises
    (`<` suivi d'une lettre, `/`, `!` ou `?`, jusqu'au `>` suivant, sans `<` intermédiaire)."""
    low = t.lower()
    out: list[str] = []
    i, n, changed = 0, len(t), False
    while i < n:
        j = t.find("<", i)
        if j < 0:
            out.append(t[i:])
            break
        out.append(t[i:j])
        nxt = t[j + 1] if j + 1 < n else ""
        if not (nxt.isalpha() or nxt in "/!?"):
            out.append("<")  # comparaison isolée : conservée telle quelle (texte brut)
            i = j + 1
            continue
        block = next((b for b in _BLOCKS if low.startswith(b, j + 1)), None)
        if block is not None:
            end = low.find(f"</{block}", j)
            if end < 0:
                changed, i = True, n  # bloc non fermé : le reste est ignoré
                break
            close = t.find(">", end)
            changed, i = True, (n if close < 0 else close + 1)
            out.append(" ")
            continue
        if low.startswith("!--", j + 1):
            end = t.find("-->", j + 4)
            changed, i = True, (n if end < 0 else end + 3)
            out.append(" ")
            continue
        close = t.find(">", j + 1)
        lt = t.find("<", j + 1)
        if close < 0 or (0 <= lt < close):
            out.append("<")  # pas une balise complète
            i = j + 1
            continue
        changed, i = True, close + 1
        out.append(" ")
    return "".join(out), changed


def clean_text(value: object, max_len: int = MAX_TITLE) -> tuple[str, list[str]]:
    """Texte brut sûr (aucune balise, aucun invisible, longueur bornée) + drapeaux. Coût linéaire."""
    flags: list[str] = []
    if value is None:
        return "", flags
    t = str(value)
    cap = 8 * max_len + 4000  # on ne traite jamais une chaîne énorme en entier
    if len(t) > cap:
        t = t[:cap]
        flags.append("tronque")
    for _ in range(2):  # double encodage fréquent dans les flux (&amp;lt;b&amp;gt;)
        t = html.unescape(t)
    t = strip_invisible(t)
    t, changed = _strip_markup(t)
    if changed:
        flags.append("html_retire")
        t2, again = _strip_markup(t)  # balises reconstituées (`<<b>script>`) : neutralisées
        if again:
            t = t2.replace("<", " ").replace(">", " ")
    t = unicodedata.normalize("NFKC", t)
    t = strip_invisible(t)
    t = _WS.sub(" ", t).strip()
    if _INJECTION.search(t):
        flags.append(INJECTION_FLAG)
    if len(t) > max_len:
        t = t[:max_len].rstrip() + "…"
        if "tronque" not in flags:
            flags.append("tronque")
    return t, flags


def safe_message(value: object, max_len: int = 200) -> str:
    """Message de diagnostic (erreurs, hôtes) sûr pour un journal ou un résumé Markdown : caractères
    restreints (aucun accent grave, aucune balise, aucun contrôle)."""
    return _SAFE_MSG.sub("", strip_invisible(str(value)))[:max_len]


def normalize_title(title: str) -> str:
    """Titre comparable : NFKC, minuscules, sans accents, ponctuation -> espace."""
    t = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", title).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9$]+", " ", t)
    return _WS.sub(" ", t).strip()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return host.rsplit(".", 1)[-1].isdigit()  # formes numériques abrégées / TLD numérique


def safe_url(value: object) -> str | None:
    """URL canonique sûre, ou None si refusée. Ne déclenche AUCUNE requête."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or len(raw) > MAX_URL or _CTRL.search(raw) or any(
            unicodedata.category(c) == "Cf" for c in raw) or any(c in raw for c in " \t\r\n<>\"'`\\"):
        return None
    try:
        p = urlsplit(raw)
        port = p.port
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or p.username or p.password or "@" in p.netloc:
        return None
    host = (p.hostname or "").lower().rstrip(".")
    if not _HOST.match(host) or _is_ip(host):
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
