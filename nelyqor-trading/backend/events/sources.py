"""Registre des sources GRATUITES, sans clé ni inscription. Aucune n'est « de confiance » a priori : elles
sont retenues ou écartées au vu des MESURES (`metrics`). `verified` = format confirmé par une requête réelle
(voir docs/EVENEMENTS.md) ; une source non vérifiée est collectée quand même, sa disponibilité est mesurée.

X (Twitter) n'est PAS une source : l'API est payante et ses conditions interdisent la collecte automatisée
hors API ; aucun scraping."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

OFFICIAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "bce_presse": ("ecb.europa.eu",),
    "fed_presse": ("federalreserve.gov",),
    "amf_actualites": ("amf-france.org",),
    "esma_actualites": ("esma.europa.eu",),
    "sec_presse": ("sec.gov",),
}
ALL_OFFICIAL = tuple(sorted({d for v in OFFICIAL_DOMAINS.values() for d in v}))

EDGAR_COMPANIES: dict[str, tuple[str, str]] = {  # clé -> (CIK sur 10 chiffres, nom)
    "tesla": ("0001318605", "Tesla"),
    "strategy": ("0001050446", "Strategy (MicroStrategy)"),
    "coinbase": ("0001679788", "Coinbase"),
    "block": ("0001512673", "Block"),
    "mara": ("0001507605", "MARA Holdings"),
}


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: str        # agregateur | media | officiel | depot | plateforme | blockchain
    fmt: str         # feed | gdelt | edgar | bitvavo_markets | mempool_fees | blockchain_stats
    url: str
    cost: str = "0 €"
    terms: str = ""  # conditions d'usage à respecter (résumé)
    official_domains: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = field(default_factory=tuple)  # redirections tolérées (sinon même hôte)
    company: str = ""
    verified: bool = False
    min_interval_s: int = 0  # espacement minimal entre deux collectes (respect des conditions)


def _gnews(q: str, lang: str) -> str:
    hl, gl, ceid = ("fr", "FR", "FR:fr") if lang == "fr" else ("en-US", "US", "US:en")
    return f"https://news.google.com/rss/search?q={quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"


GDELT_QUERY = "(bitcoin OR crypto OR cryptocurrency OR ethereum OR stablecoin)"

SOURCES: dict[str, SourceSpec] = {s.name: s for s in [
    SourceSpec("gdelt_crypto", "agregateur", "gdelt",
               "https://api.gdeltproject.org/api/v2/doc/doc?query=" + quote(GDELT_QUERY)
               + "&mode=artlist&format=json&maxrecords=75&timespan=3h&sort=datedesc",
               terms="GDELT : usage libre avec citation ; ≤ 1 requête / 5 s", min_interval_s=5),
    SourceSpec("gnews_crypto_fr", "agregateur", "feed", _gnews("bitcoin OR crypto when:1d", "fr"),
               terms="Google Actualités RSS : usage personnel, titres + lien, pas de republication"),
    SourceSpec("gnews_crypto_en", "agregateur", "feed", _gnews("bitcoin OR crypto when:1d", "en")),
    SourceSpec("gnews_musk", "agregateur", "feed", _gnews('"elon musk" (bitcoin OR dogecoin OR crypto) when:1d', "en")),
    SourceSpec("gnews_macro", "agregateur", "feed",
               _gnews("(Fed OR ECB OR inflation OR \"interest rates\") crypto when:1d", "en")),
    SourceSpec("gnews_regulation", "agregateur", "feed",
               _gnews("(SEC OR MiCA OR AMF OR ESMA) crypto when:1d", "en")),
    SourceSpec("coindesk", "media", "feed", "https://www.coindesk.com/arc/outboundfeeds/rss/",
               terms="usage personnel ; redistribution interdite"),
    SourceSpec("cointelegraph", "media", "feed", "https://cointelegraph.com/rss"),
    SourceSpec("decrypt", "media", "feed", "https://decrypt.co/feed"),
    SourceSpec("cryptoast", "media", "feed", "https://cryptoast.fr/feed/"),
    SourceSpec("journal_du_coin", "media", "feed", "https://journalducoin.com/feed/"),
    SourceSpec("bce_presse", "officiel", "feed", "https://www.ecb.europa.eu/rss/press.html",
               official_domains=OFFICIAL_DOMAINS["bce_presse"]),
    SourceSpec("fed_presse", "officiel", "feed", "https://www.federalreserve.gov/feeds/press_all.xml",
               official_domains=OFFICIAL_DOMAINS["fed_presse"]),
    SourceSpec("amf_actualites", "officiel", "feed", "https://www.amf-france.org/fr/flux-rss/display/21",
               official_domains=OFFICIAL_DOMAINS["amf_actualites"]),
    SourceSpec("esma_actualites", "officiel", "feed", "https://www.esma.europa.eu/rss.xml",
               official_domains=OFFICIAL_DOMAINS["esma_actualites"]),
    SourceSpec("sec_presse", "officiel", "feed", "https://www.sec.gov/news/pressreleases.rss",
               official_domains=OFFICIAL_DOMAINS["sec_presse"],
               terms="SEC : ≤ 10 requêtes/s, User-Agent déclaré exigé"),
    *[SourceSpec(f"edgar_{k}", "depot", "edgar", f"https://data.sec.gov/submissions/CIK{cik}.json",
                 official_domains=("sec.gov",), company=name,
                 terms="SEC fair access : ≤ 10 requêtes/s, User-Agent déclaré exigé")
      for k, (cik, name) in EDGAR_COMPANIES.items()],
    SourceSpec("bitvavo_statut", "plateforme", "feed", "https://status.bitvavo.com/history.rss",
               terms="page de statut publique ; existence d'un flux NON confirmée"),
    SourceSpec("bitvavo_marches", "plateforme", "bitvavo_markets", "https://api.bitvavo.com/v2/markets",
               official_domains=("bitvavo.com",), terms="API publique Bitvavo : limite de poids 1000/min"),
    SourceSpec("mempool_frais", "blockchain", "mempool_fees", "https://mempool.space/api/v1/fees/recommended",
               terms="mempool.space : API publique, usage raisonnable"),
    SourceSpec("blockchain_stats", "blockchain", "blockchain_stats", "https://api.blockchain.info/stats",
               terms="blockchain.com : API publique, usage raisonnable"),
]}


def select(names: str | None) -> list[SourceSpec]:
    if not names:
        return list(SOURCES.values())
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in SOURCES]
    if unknown:
        raise KeyError(f"sources inconnues : {unknown}")
    return [SOURCES[n] for n in wanted]
