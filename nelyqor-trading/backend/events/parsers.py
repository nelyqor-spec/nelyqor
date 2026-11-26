"""Analyseurs PURS (aucun accès réseau) : RSS 2.0, Atom, GDELT DOC 2.0 (artlist JSON), SEC EDGAR
(submissions JSON), Bitvavo (/v2/markets), mempool.space et blockchain.info (métriques).

Chaque analyseur rend des dictionnaires « bruts » (titre, url, date déclarée…) transformés ensuite en
`Event` par `model.make_event` (nettoyage centralisé). Volume borné (`limit`)."""
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
    """RFC 822 (RSS), ISO 8601 (Atom/EDGAR) ou AAAAMMJJTHHMMSSZ (GDELT) -> ms UTC ; None si illisible."""
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
            out.append({"title": entry.findtext(f"{_ATOM}title"), "url": link,
                        "published": parse_date_ms(entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")),
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
        out.append({"title": title, "url": it.findtext("link"), "published": parse_date_ms(pub),
                    "summary": it.findtext("description") or "", "publisher_url": publisher_url})
        if len(out) >= limit:
            break
    return out


def parse_gdelt(text: str, limit: int = 100) -> list[RawItem]:
    """GDELT DOC 2.0, mode=artlist&format=json : {"articles": [{url, title, seendate, domain, language}]}."""
    if not text.strip():
        return []  # GDELT rend un corps vide quand aucun article ne correspond
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


def parse_edgar(text: str, company: str, limit: int = 20) -> list[RawItem]:
    """SEC EDGAR submissions JSON (data.sec.gov/submissions/CIK##########.json) : dépôts récents."""
    data = json.loads(text)
    cik = int(data.get("cik", 0))
    rec = (data.get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    out: list[RawItem] = []
    for i, form in enumerate(forms):
        if form not in EDGAR_FORMS:
            continue
        acc = str(rec["accessionNumber"][i])
        doc = str((rec.get("primaryDocument") or [""] * len(forms))[i] or "")
        desc = str((rec.get("primaryDocDescription") or [""] * len(forms))[i] or "")
        accepted = (rec.get("acceptanceDateTime") or [None] * len(forms))[i]
        published = parse_date_ms(accepted) or parse_date_ms((rec.get("filingDate") or [None] * len(forms))[i])
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
        items = str((rec.get("items") or [""] * len(forms))[i] or "")
        title = f"{company} — dépôt SEC {form}" + (f" ({desc})" if desc and desc != form else "") + \
            (f" items {items}" if items else "")
        out.append({"title": title, "url": url, "published": published, "summary": ""})
        if len(out) >= limit:
            break
    return out


def parse_bitvavo_markets(text: str) -> dict[str, str]:
    """/v2/markets -> {marché: statut} (trading | halted | auction…)."""
    data = json.loads(text)
    return {str(m["market"]): str(m.get("status", "")) for m in data if isinstance(m, dict) and "market" in m}


def diff_markets(prev: dict[str, str], cur: dict[str, str]) -> list[RawItem]:
    """Changements de statut entre deux relevés (radiation, suspension, nouvelle cotation)."""
    if not prev:
        return []  # premier relevé : pas de référence, aucun événement inventé
    out: list[RawItem] = []
    for m in sorted(set(prev) | set(cur)):
        a, b = prev.get(m), cur.get(m)
        if a == b:
            continue
        if b is None:
            t = f"Bitvavo : le marché {m} a disparu de la liste (radiation probable)"
        elif a is None:
            t = f"Bitvavo : nouveau marché {m} (statut {b})"
        else:
            t = f"Bitvavo : le marché {m} passe du statut {a} au statut {b}"
            if b == "halted":
                t += " (trading suspendu)"
        out.append({"title": t, "url": f"https://api.bitvavo.com/v2/markets?market={m}", "published": None,
                    "summary": "", "asset": m.split("-")[0].lower()})
    return out


def parse_mempool_fees(text: str) -> dict[str, float]:
    d = json.loads(text)
    return {k: float(d[k]) for k in ("fastestFee", "halfHourFee", "hourFee", "economyFee", "minimumFee") if k in d}


def parse_blockchain_stats(text: str) -> dict[str, float]:
    d = json.loads(text)
    keys = ("hash_rate", "n_tx", "minutes_between_blocks", "n_blocks_mined", "difficulty", "market_price_usd")
    return {k: float(d[k]) for k in keys if isinstance(d.get(k), int | float)}


def metric_events(name: str, cur: dict[str, float], prev: dict[str, float] | None) -> list[RawItem]:
    """Événements dérivés de métriques on-chain (seuils fixes, documentés)."""
    out: list[RawItem] = []
    if name == "mempool_fees" and "fastestFee" in cur:
        f = cur["fastestFee"]
        p = (prev or {}).get("fastestFee")
        # franchissement du seuil absolu (pas de répétition tant que les frais restent hauts) ou triplement
        if (f >= 100 and (p is None or p < 100)) or (p is not None and 0 < p < 100 and f >= 3 * p and f >= 20):
            out.append({"title": f"Frais Bitcoin élevés : {f:.0f} sat/vB (précédent {p if p is not None else 'n/a'})",
                        "url": "https://mempool.space/", "published": None, "summary": "", "asset": "btc"})
    if name == "blockchain_stats" and prev and cur.get("hash_rate") and prev.get("hash_rate"):
        r = cur["hash_rate"] / prev["hash_rate"] - 1
        if r <= -0.2:
            out.append({"title": f"Chute du hashrate Bitcoin : {r:+.0%} depuis le relevé précédent",
                        "url": "https://www.blockchain.com/explorer/charts/hash-rate", "published": None,
                        "summary": "", "asset": "btc"})
    return out
