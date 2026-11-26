"""Analyseurs PURS (aucun accès réseau) : RSS 2.0, Atom, GDELT DOC 2.0 (artlist JSON), SEC EDGAR
(submissions JSON), Bitvavo (/v2/markets), mempool.space et blockchain.info (métriques).

Chaque analyseur rend des dictionnaires « bruts » (titre, url, date déclarée, précision de la date…)
transformés ensuite en `Event` par `model.make_event` (nettoyage centralisé). Volume borné (`limit`)."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET  # noqa: S405 — DTD/entités refusées par check_xml avant analyse
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .safety import check_xml

RawItem = dict[str, Any]
_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_date_ms(value: str | None) -> int | None:
    """RFC 822 (RSS), ISO 8601 (Atom) ou AAAAMMJJTHHMMSSZ (GDELT) -> ms UTC ; None si illisible."""
    if not value:
        return None
    v = value.strip()[:64]
    try:
        if len(v) == 16 and v[8] == "T" and v.endswith("Z"):  # GDELT
            return int(datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp() * 1000)
        if v[:4].isdigit() and len(v) >= 10 and v[4] == "-":
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        else:
            d = parsedate_to_datetime(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return int(d.timestamp() * 1000)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def date_precision(value: str | None) -> str:
    """"jour" si la date n'a pas d'heure réelle (AAAA-MM-JJ seule, ou 00:00:00 pile — typique de l'AMF ou
    d'une date de dépôt) : elle ne doit PAS servir à mesurer un délai ; "" sinon."""
    if not value:
        return ""
    v = value.strip()
    if len(v) == 10 and v[4] == "-":
        return "jour"
    if " 00:00:00" in v or "T00:00:00" in v:
        return "jour"
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(text: str, limit: int = 100) -> list[RawItem]:
    """RSS 2.0 ou Atom. Lève ValueError si le document est dangereux ou illisible."""
    check_xml(text)
    try:
        root = ET.fromstring(text)  # noqa: S314 — DTD refusée par check_xml
    except ET.ParseError as e:
        raise ValueError(f"XML illisible : {e}") from e
    out: list[RawItem] = []
    if _local(root.tag) == "feed":  # Atom
        for entry in root.iter(f"{_ATOM}entry"):
            link = None
            for ln in entry.findall(f"{_ATOM}link"):
                if ln.get("rel", "alternate") == "alternate":
                    link = ln.get("href")
                    break
            raw_date = entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")
            out.append({"title": entry.findtext(f"{_ATOM}title"), "url": link, "guid": entry.findtext(f"{_ATOM}id"),
                        "published": parse_date_ms(raw_date), "precision": date_precision(raw_date),
                        "summary": entry.findtext(f"{_ATOM}summary") or ""})
            if len(out) >= limit:
                break
        return out
    for it in root.iter("item"):
        src = it.find("source")  # Google Actualités : éditeur réel
        pub = it.findtext("pubDate")
        if pub is None:
            pub = it.findtext("{http://purl.org/dc/elements/1.1/}date")
        title = it.findtext("title") or ""
        publisher_url = src.get("url") if src is not None else None
        if src is not None and src.text and title.endswith(" - " + src.text):
            title = title[: -len(" - " + src.text)]
        out.append({"title": title, "url": it.findtext("link"), "guid": it.findtext("guid"),
                    "published": parse_date_ms(pub), "precision": date_precision(pub),
                    "summary": it.findtext("description") or "", "publisher_url": publisher_url})
        if len(out) >= limit:
            break
    return out


def parse_gdelt(text: str, limit: int = 100) -> list[RawItem]:
    """GDELT DOC 2.0, mode=artlist&format=json : {"articles": [{url, title, seendate, domain, language}]}.
    Un corps vide (aucun article) rend [] : la collecte le compte « vide », distinct d'un succès."""
    if not text.strip():
        return []
    data = json.loads(text)
    out: list[RawItem] = []
    for a in (data.get("articles") or [])[:limit]:
        if not isinstance(a, dict):
            continue
        out.append({"title": a.get("title"), "url": a.get("url"), "published": parse_date_ms(a.get("seendate")),
                    "summary": "", "publisher_url": f"https://{a['domain']}/" if a.get("domain") else None,
                    "language": a.get("language")})
    return out


EDGAR_FORMS = frozenset({"8-K", "10-Q", "10-K", "6-K", "20-F", "S-1", "S-3", "424B5", "SC 13D", "SC 13G",
                         "8-K/A", "10-K/A", "10-Q/A"})


def edgar_acceptance_ms(value: str | None) -> int | None:
    """`acceptanceDateTime` de l'API submissions : malgré le suffixe « Z », l'heure est celle de la SEC
    (America/New_York) — p. ex. les résultats de Tesla, publiés à 16:05 heure de New York, y figurent en
    « T16:05:xx.000Z ». On l'interprète donc en heure de New York (heure d'été gérée). Présomption
    DOCUMENTÉE (docs/EVENEMENTS.md), à confirmer sur données réelles : drapeau `edgar_fuseau_presume`."""
    if not value:
        return None
    try:
        from zoneinfo import ZoneInfo

        naive = datetime.fromisoformat(value.strip()[:19])
        return int(naive.replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
    except (ValueError, KeyError, ImportError, OSError):
        return None


def parse_edgar(text: str, company: str, limit: int = 20) -> list[RawItem]:
    """SEC EDGAR submissions JSON (data.sec.gov/submissions/CIK##########.json) : dépôts récents.
    Sans `acceptanceDateTime`, la date de dépôt (jour seul) n'est PAS utilisée comme horodatage."""
    data = json.loads(text)
    cik = int(data.get("cik", 0))
    rec = (data.get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    n = len(forms)
    out: list[RawItem] = []
    for i, form in enumerate(forms):
        if form not in EDGAR_FORMS:
            continue
        acc = str(rec["accessionNumber"][i])
        doc = str((rec.get("primaryDocument") or [""] * n)[i] or "")
        desc = str((rec.get("primaryDocDescription") or [""] * n)[i] or "")
        accepted = (rec.get("acceptanceDateTime") or [None] * n)[i]
        published = edgar_acceptance_ms(accepted)
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        items = str((rec.get("items") or [""] * n)[i] or "")
        title = f"{company} — dépôt SEC {form}" + (f" ({desc})" if desc and desc != form else "") + \
            (f" items {items}" if items else "")
        filing_day = str((rec.get("filingDate") or [""] * n)[i] or "")
        out.append({"title": title, "url": url, "published": published,
                    "precision": "" if published is not None else ("jour" if filing_day else ""),
                    "summary": f"déposé le {filing_day}" if filing_day else "",
                    "flags": ["edgar_fuseau_presume"] if published is not None else ["date_jour_seulement"]})
        if len(out) >= limit:
            break
    return out


def parse_bitvavo_markets(text: str) -> dict[str, str]:
    """/v2/markets -> {marché: statut} (trading | halted | auction…)."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("liste de marchés attendue")
    return {str(m["market"]): str(m.get("status", "")) for m in data if isinstance(m, dict) and "market" in m}


MAX_MARKET_DROP = 0.10  # relevé refusé s'il compte plus de 10 % de marchés en moins que le précédent


def update_markets(prev: dict[str, str], cur: dict[str, str], pending: dict[str, int],
                   releve_ms: int) -> tuple[list[RawItem], dict[str, str], dict[str, int], str]:
    """Écarts entre deux relevés -> (événements, nouvel état, disparitions en attente, motif de refus).

    - Relevé VIDE ou en baisse de plus de 10 % : REFUSÉ (panne, réponse partielle) ; l'état est conservé.
    - Disparition : confirmée seulement si le marché manque sur DEUX relevés consécutifs.
    - Premier relevé : aucune référence, aucun événement inventé.
    Le numéro de relevé entre dans l'identifiant (un même changement peut se reproduire plus tard)."""
    if not cur:
        return [], prev, pending, "relevé vide refusé"
    if prev and len(cur) < (1 - MAX_MARKET_DROP) * len(prev):
        return [], prev, pending, f"relevé refusé : {len(cur)} marchés contre {len(prev)} au précédent"
    if not prev:
        return [], dict(cur), {}, ""
    out: list[RawItem] = []
    state = dict(cur)
    new_pending: dict[str, int] = {}

    def item(m: str, t: str) -> RawItem:
        return {"title": t, "url": f"https://api.bitvavo.com/v2/markets?market={m}", "published": releve_ms,
                "summary": "", "asset": m.split("-")[0].lower(), "id_salt": str(releve_ms)}

    for m in sorted(set(prev) | set(cur)):
        a, b = prev.get(m), cur.get(m)
        if b is None:
            if m in pending:
                out.append(item(m, f"Bitvavo : le marché {m} a disparu de la liste (radiation probable, "
                                   f"absent de deux relevés consécutifs)"))
            else:
                new_pending[m] = releve_ms
                state[m] = a or ""  # conservé jusqu'à confirmation
            continue
        if a is None:
            out.append(item(m, f"Bitvavo : nouveau marché {m} (statut {b})"))
        elif a != b:
            t = f"Bitvavo : le marché {m} passe du statut {a} au statut {b}"
            out.append(item(m, t + (" (trading suspendu)" if b == "halted" else "")))
    return out, state, new_pending, ""


def parse_mempool_fees(text: str) -> dict[str, float]:
    d = json.loads(text)
    return {k: float(d[k]) for k in ("fastestFee", "halfHourFee", "hourFee", "economyFee", "minimumFee") if k in d}


def parse_blockchain_stats(text: str) -> dict[str, float]:
    d = json.loads(text)
    keys = ("hash_rate", "n_tx", "minutes_between_blocks", "n_blocks_mined", "difficulty", "market_price_usd")
    return {k: float(d[k]) for k in keys if isinstance(d.get(k), int | float)}


def metric_events(name: str, cur: dict[str, float], prev: dict[str, float] | None,
                  releve_ms: int | None = None) -> list[RawItem]:
    """Événements dérivés de métriques on-chain (seuils fixes, documentés). Datés du relevé."""
    out: list[RawItem] = []
    salt = str(releve_ms) if releve_ms is not None else ""
    if name == "mempool_fees" and "fastestFee" in cur:
        f = cur["fastestFee"]
        p = (prev or {}).get("fastestFee")
        # franchissement du seuil absolu (pas de répétition tant que les frais restent hauts) ou triplement
        if (f >= 100 and (p is None or p < 100)) or (p is not None and 0 < p < 100 and f >= 3 * p and f >= 20):
            out.append({"title": f"Frais Bitcoin élevés : {f:.0f} sat/vB (précédent {p if p is not None else 'n/a'})",
                        "url": "https://mempool.space/", "published": releve_ms, "summary": "", "asset": "btc",
                        "id_salt": salt})
    if name == "blockchain_stats" and prev and cur.get("hash_rate") and prev.get("hash_rate"):
        r = cur["hash_rate"] / prev["hash_rate"] - 1
        if r <= -0.2:
            out.append({"title": f"Chute du hashrate Bitcoin : {r:+.0%} depuis le relevé précédent",
                        "url": "https://www.blockchain.com/explorer/charts/hash-rate", "published": releve_ms,
                        "summary": "", "asset": "btc", "id_salt": salt})
    return out
