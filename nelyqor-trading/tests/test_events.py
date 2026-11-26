"""Intelligence événementielle : analyse des formats, modèle, hygiène, collecte, mesures (données FICTIVES,
aucun accès réseau)."""
from __future__ import annotations

import json
from pathlib import Path

from backend.events import journal, lexicon, parsers
from backend.events.collector import FetchResult, collect
from backend.events.hygiene import Deduplicator, assess, classify, group
from backend.events.metrics import source_report
from backend.events.model import event_id, make_event
from backend.events.safety import normalize_title, safe_url
from backend.events.sources import SOURCES, SourceSpec

T0 = 1_795_000_000_000  # instant fictif (novembre 2026)
H = 3_600_000

GNEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>bitcoin - Google Actualités</title>
<item><title>La BCE baisse ses taux directeurs, le bitcoin grimpe - Les Echos</title>
<link>https://news.google.com/rss/articles/CBMiAAA?oc=5</link>
<pubDate>Tue, 24 Nov 2026 08:00:00 GMT</pubDate>
<description>&lt;a href="https://news.google.com/x"&gt;La BCE baisse&lt;/a&gt;</description>
<source url="https://www.lesechos.fr">Les Echos</source></item>
<item><title>Elon Musk says Tesla will accept Dogecoin again - Reuters</title>
<link>https://news.google.com/rss/articles/CBMiBBB?oc=5</link>
<pubDate>Tue, 24 Nov 2026 09:30:00 GMT</pubDate>
<source url="https://www.reuters.com">Reuters</source></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Statut</title>
<entry><title>Retraits SOL retardés</title><link rel="alternate" href="https://status.example.com/incidents/42"/>
<updated>2026-11-24T10:15:00Z</updated><summary>Incident en cours</summary></entry>
<entry><title>Maintenance planifiée</title><link href="https://status.example.com/incidents/41"/>
<published>2026-11-23T22:00:00+01:00</published></entry>
</feed>"""

GDELT = json.dumps({"articles": [
    {"url": "https://www.coindesk.com/markets/2026/11/24/bitcoin-etf-inflows/?utm_source=gdelt",
     "title": "Bitcoin ETF inflows hit record", "seendate": "20261124T101500Z", "domain": "coindesk.com",
     "language": "English"},
    {"url": "https://www.cryptoast.fr/hack-exchange/", "title": "Piratage d'une plateforme : 40 M$ dérobés",
     "seendate": "20261124T094500Z", "domain": "cryptoast.fr", "language": "French"}]})

EDGAR = json.dumps({"cik": "1318605", "name": "Tesla, Inc.", "filings": {"recent": {
    "accessionNumber": ["0001628280-26-000111", "0000950170-26-000222", "0001318605-26-000333"],
    "filingDate": ["2026-11-24", "2026-11-20", "2026-11-19"],
    "acceptanceDateTime": ["2026-11-24T16:05:12.000Z", "2026-11-20T21:30:00.000Z", "2026-11-19T12:00:00.000Z"],
    "form": ["8-K", "4", "10-Q"], "items": ["8.01", "", ""],
    "primaryDocument": ["tsla-8k.htm", "xslF345X05/wk-form4.xml", "tsla-10q.htm"],
    "primaryDocDescription": ["8-K", "FORM 4", "10-Q"]}}})


def ms(iso: str) -> int:
    v = parsers.parse_date_ms(iso)
    assert v is not None
    return v


# ---------- analyse des formats ----------
def test_parse_rss_google_news_publisher_and_dates() -> None:
    items = parsers.parse_feed(GNEWS_RSS)
    assert [i["title"] for i in items] == ["La BCE baisse ses taux directeurs, le bitcoin grimpe",
                                          "Elon Musk says Tesla will accept Dogecoin again"]
    assert items[0]["publisher_url"] == "https://www.lesechos.fr"
    assert items[1]["published"] == ms("2026-11-24T09:30:00Z")
    ev = make_event(source="gnews", source_kind="agregateur", title=items[0]["title"], url=items[0]["url"],
                    received_ms=T0, published_ms=items[0]["published"], summary=items[0]["summary"],
                    publisher_url=items[0]["publisher_url"])
    assert ev is not None and ev.publisher == "lesechos.fr" and "<" not in ev.summary


def test_parse_atom() -> None:
    items = parsers.parse_feed(ATOM)
    assert items[0]["url"] == "https://status.example.com/incidents/42"
    assert items[0]["published"] == ms("2026-11-24T10:15:00Z")
    assert items[1]["published"] == ms("2026-11-23T21:00:00Z")  # fuseau +01:00 converti en UTC
    assert items[1]["url"].endswith("/41")


def test_parse_gdelt_and_empty_body() -> None:
    items = parsers.parse_gdelt(GDELT)
    assert len(items) == 2 and items[0]["published"] == ms("2026-11-24T10:15:00Z")
    assert items[0]["publisher_url"] == "https://coindesk.com/"
    assert parsers.parse_gdelt("") == [] and parsers.parse_gdelt('{"articles": []}') == []


def test_parse_edgar_filters_forms_and_builds_urls() -> None:
    items = parsers.parse_edgar(EDGAR, "Tesla")
    assert [i["title"].split("SEC ")[1].split(" ")[0] for i in items] == ["8-K", "10-Q"]  # formulaire 4 ignoré
    assert items[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1318605/000162828026000111/tsla-8k.htm"
    assert items[0]["published"] == ms("2026-11-24T16:05:12Z")
    ev = make_event(source="edgar_tesla", source_kind="depot", title=items[0]["title"], url=items[0]["url"],
                    received_ms=T0, official_domains=("sec.gov",))
    assert ev is not None and ev.official and "tesla" in ev.entities


def test_parse_dates_robust() -> None:
    assert parsers.parse_date_ms("20261124T101500Z") == ms("2026-11-24T10:15:00Z")
    assert parsers.parse_date_ms("Tue, 24 Nov 2026 10:15:00 +0000") == ms("2026-11-24T10:15:00Z")
    for bad in (None, "", "demain", "9999-99-99", "x" * 10_000):
        assert parsers.parse_date_ms(bad) is None


def test_bitvavo_markets_diff_detects_halt_and_delisting() -> None:
    prev = parsers.parse_bitvavo_markets(json.dumps([{"market": "BTC-EUR", "status": "trading"},
                                                     {"market": "XYZ-EUR", "status": "trading"},
                                                     {"market": "ABC-EUR", "status": "trading"}]))
    cur = {"BTC-EUR": "trading", "ABC-EUR": "halted", "NEW-EUR": "auction"}
    assert parsers.diff_markets({}, cur) == []  # premier relevé : rien d'inventé
    d = {i["asset"]: i["title"] for i in parsers.diff_markets(prev, cur)}
    assert set(d) == {"abc", "xyz", "new"}
    assert "suspendu" in d["abc"] and "radiation" in d["xyz"]
    ev = make_event(source="bitvavo_marches", source_kind="plateforme", title=d["xyz"], url=None, received_ms=T0)
    assert ev is not None and "radiation" in ev.categories


def test_onchain_metric_events_thresholds() -> None:
    fees = parsers.parse_mempool_fees('{"fastestFee": 12, "halfHourFee": 10, "hourFee": 8, "economyFee": 4, "minimumFee": 2}')
    assert parsers.metric_events("mempool_fees", fees, None) == []
    assert len(parsers.metric_events("mempool_fees", {"fastestFee": 40.0}, {"fastestFee": 12.0})) == 1  # x3
    assert len(parsers.metric_events("mempool_fees", {"fastestFee": 120.0}, None)) == 1                # seuil absolu
    assert parsers.metric_events("mempool_fees", {"fastestFee": 130.0}, {"fastestFee": 120.0}) == []  # déjà haut
    st = parsers.parse_blockchain_stats('{"hash_rate": 7.0e11, "n_tx": 400000, "market_price_usd": "x"}')
    assert "market_price_usd" not in st
    assert len(parsers.metric_events("blockchain_stats", {"hash_rate": 5.0e11}, st)) == 1


# ---------- modèle normalisé ----------
def test_event_id_stable_and_canonical() -> None:
    a = make_event(source="s1", source_kind="media", title="Bitcoin  ETF approuvé !",
                   url="https://WWW.Example.com/a?utm_source=x&id=3#frag", received_ms=T0)
    b = make_event(source="s2", source_kind="media", title="bitcoin etf approuve",
                   url="https://www.example.com/a?id=3", received_ms=T0 + H)
    assert a is not None and b is not None and a.id == b.id
    assert a.id == event_id("https://www.example.com/a?id=3", normalize_title("Bitcoin ETF approuvé"))
    c = make_event(source="s1", source_kind="media", title="Bitcoin ETF rejeté", url="https://www.example.com/a?id=3",
                   received_ms=T0)
    assert c is not None and c.id != a.id


def test_lexicon_entities_categories_assets() -> None:
    e, c, a = lexicon.detect(normalize_title("Elon Musk : Tesla acceptera de nouveau le Dogecoin"))
    assert {"elon_musk", "tesla"} <= set(e) and a == ["doge"]
    e, c, a = lexicon.detect(normalize_title("La BCE baisse ses taux directeurs ; l'inflation ralentit"))
    assert "bce" in e and {"taux", "inflation"} <= set(c)
    # tickers ambigus : « ton », « sol », « link » ne sont pas des actifs sans cashtag
    _, _, a = lexicon.detect(normalize_title("Le ton monte au sol, voir le link"))
    assert a == []
    _, _, a = lexicon.detect(normalize_title("Le $SOL et l'ether s'envolent"))
    assert a == ["eth", "sol"]
    _, c, _ = lexicon.detect(normalize_title("Exchange hacked: $40M drained, withdrawals halted"))
    assert {"piratage", "radiation"} <= set(c)


# ---------- hygiène ----------
def _ev(source: str, title: str, url: str, received: int, published: int | None = None, publisher: str | None = None,
        official: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    ev = make_event(source=source, source_kind="media", title=title, url=url, received_ms=received,
                    published_ms=published, publisher_url=publisher, official_domains=official)
    assert ev is not None
    return ev


def test_dedup_exact_quasi_and_feed_repeats() -> None:
    d = Deduplicator()
    a = classify(_ev("gnews_a", "SEC approves spot Ether ETF applications from BlackRock",
                     "https://news.google.com/rss/articles/1", T0, publisher="https://www.reuters.com"), d)
    assert a is not None and a.dup == "" and a.cluster == a.id
    # même article reçu via une autre requête Google : doublon EXACT
    b = classify(_ev("gnews_b", "SEC approves spot Ether ETF applications from BlackRock",
                     "https://news.google.com/rss/articles/1", T0 + 60_000, publisher="https://www.reuters.com"), d)
    assert b is not None and b.dup == "exact" and b.cluster == a.id
    # autre formulation d'un autre éditeur : QUASI-doublon
    c = classify(_ev("coindesk", "BlackRock spot Ether ETF applications approved by SEC",
                     "https://www.coindesk.com/x", T0 + 120_000), d)
    assert c is not None and c.dup == "quasi" and c.cluster == a.id
    # sans rapport
    u = classify(_ev("coindesk", "Solana validators upgrade client software tonight", "https://www.coindesk.com/y",
                     T0 + 180_000), d)
    assert u is not None and u.dup == "" and u.cluster == u.id
    # la même source relivre le même item (flux inchangé) : pas une nouvelle observation
    assert classify(_ev("coindesk", "BlackRock spot Ether ETF applications approved by SEC",
                        "https://www.coindesk.com/x", T0 + H), d) is None


def test_recycled_old_date_and_resurfaced() -> None:
    d = Deduplicator()
    old = classify(_ev("m", "Exchange XYZ files for bankruptcy protection in Delaware", "https://a.com/1",
                       T0, published=T0 - 3 * 86_400_000), d)
    assert old is not None and "recycle_date_ancienne" in old.flags
    fresh = classify(_ev("m", "Major exchange halts withdrawals after security incident", "https://a.com/2", T0), d)
    assert fresh is not None and not fresh.flags
    again = classify(_ev("n", "Major exchange halts all withdrawals after security incident", "https://b.com/9",
                         T0 + 3 * 86_400_000, published=T0 + 3 * 86_400_000), d)
    assert again is not None and again.dup == "quasi" and "recycle_deja_vu" in again.flags
    assert assess(group([old])[old.cluster], T0) is not None
    assert assess(group([old])[old.cluster], T0).recycled  # type: ignore[union-attr]


def test_corroboration_point_in_time_and_independence() -> None:
    d = Deduplicator()
    t = "Binance to delist three tokens next week, exchange says"
    evs = [classify(_ev("gnews_a", t, "https://news.google.com/1", T0, publisher="https://www.reuters.com"), d),
           classify(_ev("gnews_b", t, "https://news.google.com/1", T0 + 1000, publisher="https://www.reuters.com"), d),
           classify(_ev("gdelt", "Binance will delist three tokens next week", "https://www.coindesk.com/z", T0 + H), d),
           classify(_ev("ct", "Binance to delist three tokens next week", "https://cointelegraph.com/q", T0 + 2 * H), d)]
    g = group([e for e in evs if e is not None])
    assert len(g) == 1
    (cl,) = g.values()
    early = assess(cl, T0 + 30 * 60_000)
    late = assess(cl, T0 + 3 * H)
    assert early is not None and late is not None
    assert early.n_publishers == 1 and early.n_sources == 2   # deux requêtes Google, UN seul éditeur
    assert late.n_publishers == 3 and late.n_observations == 4
    assert assess(cl, T0 - 1) is None                          # rien n'est connu avant la première réception


def test_rumor_flag_and_official_confirmation() -> None:
    d = Deduplicator()
    r = classify(_ev("cryptoast", "Selon des sources, la SEC aurait approuvé l'ETF Solana de BlackRock",
                     "https://cryptoast.fr/r", T0), d)
    assert r is not None and "selon des sources" in r.rumor_terms and "aurait" in r.rumor_terms
    a = assess([r], T0)
    assert a is not None and a.rumor and not a.official
    off = classify(_ev("sec_presse", "La SEC a approuvé l'ETF Solana de BlackRock",
                       "https://www.sec.gov/newsroom/press-releases/2026-150", T0 + H, official=("sec.gov",)), d)
    assert off is not None and off.official and off.cluster == r.cluster
    before = assess([r, off], T0 + H - 1)
    assert before is not None and before.rumor  # avant le communiqué : rumeur
    a2 = assess([r, off], T0 + H)
    assert a2 is not None and a2.official and not a2.rumor  # confirmée par la source officielle
    e = _ev("sec_presse", "Faux communiqué", "https://www.sec.gov.evil.example/press", T0, official=("sec.gov",))
    assert not e.official and "domaine_non_officiel" in e.flags


# ---------- collecte et mesures ----------
class FakeClock:
    def __init__(self, t: int) -> None:
        self.t = t

    def __call__(self) -> int:
        self.t += 7  # chaque lecture avance de 7 ms (délai de traitement mesurable)
        return self.t


def _fetcher(responses: dict[str, FetchResult], calls: list[str]):  # type: ignore[no-untyped-def]
    def fetch(spec: SourceSpec) -> FetchResult:
        calls.append(spec.url)
        return responses.get(spec.name, FetchResult(None, "", "ConnectError: refusé"))
    return fetch


def test_collect_measures_latency_availability_and_never_follows_links(tmp_path: Path) -> None:
    specs = [SOURCES["gnews_crypto_fr"], SOURCES["gdelt_crypto"], SOURCES["coindesk"]]
    recv = ms("2026-11-24T10:30:00Z")
    calls: list[str] = []
    resp = {"gnews_crypto_fr": FetchResult(200, GNEWS_RSS, "", 120, len(GNEWS_RSS)),
            "gdelt_crypto": FetchResult(200, GDELT, "", 300, len(GDELT)),
            "coindesk": FetchResult(503, "", "HTTP 503", 50, 0)}
    res = collect(specs, tmp_path, _fetcher(resp, calls), clock=FakeClock(recv))
    assert calls == [s.url for s in specs]  # une requête par source, aucun lien d'article suivi
    assert res["ok"] == 2 and res["new"] == 4
    obs = journal.load_observations(tmp_path)
    by_title = {e.title: e for e in obs}
    e = by_title["Bitcoin ETF inflows hit record"]
    assert e.published_ms is not None and abs(e.received_ms - e.published_ms - 15 * 60_000) < 1000  # 10:15 -> 10:30
    assert e.processed_ms is not None and e.fetched_ms is not None and e.processed_ms > e.fetched_ms
    rep = source_report(obs, list(journal.read(tmp_path / "collectes.jsonl")))
    s = rep["sources"]
    assert s["coindesk"]["disponibilite"] == {"requetes": 1, "succes": 0, "taux_succes": 0.0, "http": {"503": 1},
                                              "erreurs": {"HTTP 503": 1}, "duree_ms_mediane": 50}
    # délais 15 et 45 min (quantile « supérieur » sur deux valeurs), à la seconde près
    assert s["gdelt_crypto"]["delai_publication_reception_min"]["mediane"] == 45.0
    assert s["gdelt_crypto"]["delai_publication_reception_min"]["p90"] == 45.0
    assert s["gnews_crypto_fr"]["delai_publication_reception_min"]["n"] == 2
    assert s["gnews_crypto_fr"]["cost"] == "0 €"


def test_collect_journal_append_only_and_repeats_not_relogged(tmp_path: Path) -> None:
    spec = [SOURCES["gnews_crypto_fr"]]
    resp = {"gnews_crypto_fr": FetchResult(200, GNEWS_RSS, "", 10, 1)}
    collect(spec, tmp_path, _fetcher(resp, []), clock=FakeClock(T0))
    p = journal.observations_path(tmp_path, T0)
    before = p.read_bytes()
    with p.open("a") as f:
        f.write('{"tronque": \n')  # ligne coupée (arrêt brutal) : ignorée à la relecture
    res = collect(spec, tmp_path, _fetcher(resp, []), clock=FakeClock(T0 + H))
    assert res["new"] == 0 and res["rows"][0]["n_repeat"] == 2
    assert p.read_bytes().startswith(before)  # rien de réécrit
    assert len(journal.load_observations(tmp_path)) == 2
    assert len(list(journal.read(tmp_path / "collectes.jsonl"))) == 2


def test_report_coverage_overlap_and_dup_rate(tmp_path: Path) -> None:
    d = Deduplicator()
    t = "Coinbase suffers major outage during market selloff today"
    evs = [classify(_ev("a", t, "https://x.com/1", T0), d),
           classify(_ev("b", "Coinbase suffers major outage during market selloff", "https://y.com/1", T0 + 10 * 60_000), d),
           classify(_ev("b", "Kraken lists new token for euro trading pairs", "https://y.com/2", T0), d)]
    obs = [e for e in evs if e is not None]
    rep = source_report(obs, [])
    a, b = rep["sources"]["a"], rep["sources"]["b"]
    assert rep["groupes_total"] == 2 and rep["recouvrement"] == {"a": {"b": 1}}
    assert a["premier_a_publier"] == 1 and a["avance_min_mediane"] == 10.0
    assert b["groupes_exclusifs"] == 1 and b["taux_doublons"] == 0.5
    assert a["corroboration_moyenne"] == 2.0


def test_collect_survives_malformed_and_hostile_documents(tmp_path: Path) -> None:
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
            '<rss><channel><item><title>&lol2;</title></item></channel></rss>')
    resp = {"coindesk": FetchResult(200, bomb, "", 1, len(bomb)),
            "gdelt_crypto": FetchResult(200, "<html>pas du json</html>", "", 1, 10),
            "cointelegraph": FetchResult(200, "<rss><channel><item><title>ok", "", 1, 10)}
    specs = [SOURCES["coindesk"], SOURCES["gdelt_crypto"], SOURCES["cointelegraph"]]
    res = collect(specs, tmp_path, _fetcher(resp, []), clock=FakeClock(T0))
    assert res["ok"] == 0 and res["new"] == 0
    assert all(r["error"].startswith("format:") for r in res["rows"])


def test_canonical_url_examples() -> None:
    assert safe_url("https://Example.COM:443/a?b=1&utm_medium=x#top") == "https://example.com/a?b=1"
    assert safe_url("http://example.com") == "http://example.com/"
