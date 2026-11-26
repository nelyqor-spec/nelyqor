"""Réaction du marché APRÈS réception : calcul vérifié à la main et absence d'anticipation (bougies fictives)."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.events import journal
from backend.events.hygiene import Deduplicator, classify
from backend.events.market import (
    INTERVAL_MS,
    TOLERANCE_MS,
    CandleCache,
    baseline_abs_return,
    parse_candles,
    reaction,
)
from backend.events.metrics import measure_reactions, source_report
from backend.events.model import make_event

M5 = INTERVAL_MS
BASE = 1_795_000_200_000 - (1_795_000_200_000 % M5)  # début de bougie aligné
H = 3_600_000


def candles(n: int = 400, start: int = BASE, p0: float = 100.0):  # type: ignore[no-untyped-def]
    # ouverture de la bougie i = 100 + i (prix fictifs, calcul manuel immédiat)
    return [(start + i * M5, p0 + i, p0 + i + 0.5, p0 + i - 0.5, p0 + i + 1, 1.0) for i in range(n)]


def test_parse_bitvavo_candles_sorted_and_cleaned() -> None:
    raw = [[BASE + M5, "101", "102", "100", "101.5", "3"], [BASE, "100", "101", "99", "100.5", "2"],
           ["x", "1"], [BASE + 2 * M5, "0", "0", "0", "0", "0"], [BASE + 3 * M5, "abc", "1", "1", "1", "1"]]
    c = parse_candles(raw)
    assert [x[0] for x in c] == [BASE, BASE + M5] and c[0][1] == 100.0
    with pytest.raises(ValueError):
        parse_candles({"error": "market not found"})


def test_reaction_by_hand() -> None:
    t0 = BASE + 2 * 60_000            # réception au milieu de la bougie 0
    now = t0 + 30 * H
    # départ : ouverture de la bougie 1 (101) ; 1 h : cible BASE+62 min -> bougie 13 (113)
    r1 = reaction(candles(), t0, 1, now)
    assert r1.status == "mesure" and r1.p0 == 101 and r1.ph == 113
    assert r1.ret == pytest.approx(113 / 101 - 1)
    r4 = reaction(candles(), t0, 4, now)   # cible BASE+242 min -> bougie 49
    assert r4.ph == 149 and r4.ret == pytest.approx(149 / 101 - 1)
    r24 = reaction(candles(), t0, 24, now)  # cible BASE+1442 min -> bougie 289
    assert r24.ph == 389 and r24.t_start == BASE + M5 and r24.t_end == BASE + 289 * M5


def test_no_lookahead_before_horizon_elapsed() -> None:
    t0 = BASE + 2 * 60_000
    cs = candles()  # les données « futures » sont présentes dans le jeu : elles ne doivent PAS servir
    limit = t0 + H + TOLERANCE_MS + M5
    early = reaction(cs, t0, 1, limit - 1)
    assert early.status == "horizon_non_ecoule" and early.ret is None and early.ph is None
    assert reaction(cs, t0, 1, limit).status == "mesure"
    # au moment même de la réception, aucun horizon n'a de valeur
    assert all(reaction(cs, t0, h, t0).ret is None for h in (1, 4, 24))


def test_unclosed_candles_ignored_and_gaps_detected() -> None:
    t0 = BASE + 2 * 60_000
    cs = [c for c in candles() if not (BASE + 10 * M5 <= c[0] <= BASE + 20 * M5)]  # trou autour de t0 + 1 h
    assert reaction(cs, t0, 1, t0 + 30 * H).status == "trou_de_donnees"
    # bougies démarrant après `now` (non clôturées) inutilisables même si présentes
    now = t0 + H + TOLERANCE_MS + M5
    future_only = [c for c in candles() if c[0] >= now]
    assert reaction(future_only, t0, 1, now).status == "trou_de_donnees"


def test_reference_is_reception_time_not_declared_date() -> None:
    published = BASE - 10 * H      # date déclarée bien antérieure (article recyclé)
    t0 = BASE + 2 * 60_000          # première réception
    cs = candles(start=BASE - 12 * H // M5 * M5, n=600)
    r = reaction(cs, t0, 1, t0 + 2 * H)
    assert r.t_start is not None and r.t_start >= t0 > published


def test_baseline_by_hand() -> None:
    cs = [(BASE + i * M5, p, p, p, p, 1.0) for i, p in enumerate([100.0] * 12 + [110.0] * 12)]
    # horizon 1 h = 12 bougies : 12 départs possibles, chacun 100 -> 110 = +10 %
    assert baseline_abs_return(cs, 1, M5) == pytest.approx(0.10)
    assert baseline_abs_return(cs[:5], 1, M5) is None


def _write_events(tmp: Path) -> list[str]:
    d = Deduplicator()
    t0 = BASE + 2 * 60_000
    specs = [("cryptoast", "Ethereum : la SEC approuve les ETF ether au comptant", "https://cryptoast.fr/1", t0, t0),
             ("coindesk", "Weather in Paris is mild today and tomorrow", "https://coindesk.com/2", t0, t0),  # non pertinent
             ("coindesk", "Bitcoin exchange files for bankruptcy protection", "https://coindesk.com/3", t0, t0 - 5 * 86_400_000)]
    evs = []
    for s, title, url, rec, pub in specs:
        e = make_event(source=s, source_kind="media", title=title, url=url, received_ms=rec, published_ms=pub)
        assert e is not None
        kept = classify(e, d)
        assert kept is not None
        evs.append(kept)
    journal.append_observations(tmp, evs)
    return [e.cluster for e in evs]


def test_measure_reactions_deferred_incremental_and_no_duplicates(tmp_path: Path) -> None:
    cids = _write_events(tmp_path)
    calls: list[tuple[str, int, int]] = []
    full = {"BTC-EUR": candles(n=2000, start=BASE - 100 * M5), "ETH-EUR": candles(n=2000, start=BASE - 100 * M5, p0=2000)}

    def fetch(market: str, start: int, end: int):  # type: ignore[no-untyped-def]
        calls.append((market, start, end))
        return [c for c in full[market] if start <= c[0] <= end]

    t0 = BASE + 2 * 60_000
    now1 = t0 + 5 * H  # 1 h et 4 h écoulés, pas 24 h
    s1 = measure_reactions(tmp_path, CandleCache(fetch), now1, eur_markets={"BTC-EUR", "ETH-EUR"})
    rows = list(journal.read(tmp_path / "reactions.jsonl"))
    assert s1["rows"] == 4 and {(r["market"], r["horizon_h"]) for r in rows} == {
        ("BTC-EUR", 1), ("BTC-EUR", 4), ("ETH-EUR", 1), ("ETH-EUR", 4)}
    assert {r["cluster"] for r in rows} == {cids[0]}  # non pertinent et recyclé exclus
    assert all(r["measured_ms"] >= r["t0_ms"] + r["horizon_h"] * H for r in rows)
    eth1 = next(r for r in rows if r["market"] == "ETH-EUR" and r["horizon_h"] == 1)
    # série décalée de 100 bougies : ouverture(BASE + k*5 min) = 2000 + 100 + k -> départ k=1, arrivée k=13
    assert eth1["p0"] == 2101 and eth1["ph"] == 2113 and eth1["ret"] == pytest.approx(round(2113 / 2101 - 1, 6))
    # deuxième passage au même instant : rien de nouveau
    assert measure_reactions(tmp_path, CandleCache(fetch), now1, {"BTC-EUR", "ETH-EUR"})["rows"] == 0
    # plus tard : seul l'horizon 24 h s'ajoute
    s3 = measure_reactions(tmp_path, CandleCache(fetch), t0 + 25 * H, {"BTC-EUR", "ETH-EUR"})
    assert s3["rows"] == 2
    rows = list(journal.read(tmp_path / "reactions.jsonl"))
    assert len(rows) == 6 and sorted(r["horizon_h"] for r in rows).count(24) == 2
    rep = source_report(journal.load_observations(tmp_path), [], rows, {1: 0.01, 4: 0.02, 24: 0.03})
    assert rep["sources"]["cryptoast"]["reaction_n"] == {"1h": 1, "4h": 1, "24h": 1}
    assert rep["reaction_btc_par_categorie"]["etf"]["1h"]["n"] == 1
    assert rep["reference_btc_abs_moyenne"]["24h"] == 0.03


def test_measure_reactions_network_failure_retried_later(tmp_path: Path) -> None:
    _write_events(tmp_path)
    t0 = BASE + 2 * 60_000
    s = measure_reactions(tmp_path, CandleCache(lambda m, a, b: None), t0 + 30 * H, {"ETH-EUR"})
    assert s["rows"] == 0 and s["echec_bougies"] >= 1
    assert not (tmp_path / "reactions.jsonl").exists() or not list(journal.read(tmp_path / "reactions.jsonl"))
