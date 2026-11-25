"""Simulateur d'exécution au niveau des transactions : un contact de prix ne remplit jamais à lui seul."""
from backend.research.lob_sim import (
    SCENARIOS,
    Order,
    Scenario,
    Tape,
    evaluate_order,
    simulate_limit,
    summarize,
)

MID = SCENARIOS["intermediaire"]
NOLAT = Scenario("test", 0, 0, 1.0, 0.0)


def tr(ts, px, amt, side="sell"):
    return {"id": str(ts), "timestamp": ts, "price": str(px), "amount": str(amt), "side": side}


def book(ts, bids, asks):
    return {"ts": ts, "bids": [[str(p), str(a)] for p, a in bids], "asks": [[str(p), str(a)] for p, a in asks]}


def test_touch_without_queue_knowledge_never_fills():
    tape = Tape([tr(1000, 100, 5), tr(2000, 100, 5)])
    r = simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), NOLAT)  # file inconnue -> infinie
    assert r.status == "unfilled"
    r = simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), SCENARIOS["pessimiste"], queue_estimate=0.0)
    assert r.status == "unfilled"  # pessimiste : seules les traversées remplissent


def test_trade_through_fills_fully():
    tape = Tape([tr(1000, 99.9, 0.01)])
    r = simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), SCENARIOS["pessimiste"])
    assert r.status == "filled" and r.filled == 1.0 and r.avg_price == 100.0


def test_queue_consumed_before_partial_fill():
    tape = Tape([tr(1000, 100, 1.5), tr(2000, 100, 1.0)], [book(0, [(100, 2.0)], [(100.1, 3)])])
    r = simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), NOLAT)
    assert r.queue_ahead_initial == 2.0
    assert r.status == "partial" and abs(r.filled - 0.5) < 1e-12


def test_buyer_initiated_trades_do_not_fill_a_bid():
    tape = Tape([tr(1000, 99, 5, side="buy")], [book(0, [(100, 0.0)], [(100.1, 3)])])
    assert simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), NOLAT).status == "unfilled"


def test_post_only_rejected_when_crossing():
    tape = Tape([tr(1000, 99, 5)], [book(0, [(99.9, 1)], [(100.0, 1)])])
    assert simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), NOLAT).status == "rejected_post_only"


def test_latency_and_ttl_respected():
    tape = Tape([tr(100, 99, 5), tr(20_000, 99, 5)], [book(0, [(100, 0.0)], [(100.1, 1)])])
    sc = Scenario("lat", 500, 0, 1.0, 0.0)  # traversée avant activation, puis après expiration
    assert simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), sc).status == "unfilled"


def test_cancellations_credit_queue():
    books = [book(0, [(100, 5.0)], [(100.1, 1)]), book(5000, [(100, 1.0)], [(100.1, 1)])]
    tape = Tape([tr(6000, 100, 1.5)], books)
    no_credit = simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), Scenario("a", 0, 0, 1.0, 0.0))
    credit = simulate_limit(tape, Order("buy", 100.0, 1.0, 0, 10_000), Scenario("b", 0, 0, 1.0, 1.0))
    assert no_credit.filled == 0.0 and credit.filled > 0.0


def test_fallback_walks_book_and_costs_compare():
    books = [book(0, [(99.9, 1)], [(100.1, 0.4), (100.3, 10)])]
    tape = Tape([tr(10, 100, 0.1, "buy"), tr(3_600_000 * 5, 101, 0.1, "buy")], books)
    o = Order("buy", 99.9, 1.0, 0, 1000, fallback_market=True)
    res = evaluate_order(tape, o, NOLAT, 0.0015, 0.0025, queue_estimate=1.0)
    assert res["part_remplie"] == 0.0
    assert res["cout_marche_bps"] > 0  # achat au marché au-dessus du milieu + frais
    s = summarize([res])
    assert s["n"] == 1 and s["cout_effectif_marche_bps"] is not None
