"""Comparateur EUR/USDC : coût complet, profondeur insuffisante, risque de change, choix de route."""
from backend.execution.venue import FeeSchedule, choose_route, compare_routes, route_costs


def row(m, bid, ask, depth=1e6):
    b, q = m.split("-")
    return {"market": m, "base": b, "quote": q, "bid": bid, "ask": ask, **{f"depth_bid_{p}": depth for p in (0.001, 0.005, 0.01)},
            **{f"depth_ask_{p}": depth for p in (0.001, 0.005, 0.01)}}


def test_costs_and_choice():
    f = FeeSchedule()
    e, u, c = row("BTC-EUR", 99.95, 100.05), row("BTC-USDC", 107.9, 108.1), row("USDC-EUR", 0.9249, 0.9251)
    k = route_costs(e, u, c, 100, f)
    assert abs(k["eur"] - 2 * (0.0025 + 0.0005)) < 1e-9
    assert k["usdc_par_transaction"] > k["usdc_capital_conserve"]
    best, _ = choose_route(k)
    assert best in ("eur", "usdc_par_transaction", "usdc_capital_conserve")


def test_insufficient_depth_makes_route_unavailable():
    f = FeeSchedule()
    k = route_costs(row("X-EUR", 1, 1.001, depth=50), row("X-USDC", 1, 1.001), row("USDC-EUR", 0.92, 0.9201), 1000, f)
    assert k["eur"] is None


def test_fx_risk_penalizes_usdc_holding():
    f = FeeSchedule()
    k = route_costs(row("A-EUR", 1, 1.0005), row("A-USDC", 1, 1.0005), row("USDC-EUR", 0.92, 0.9201), 100, f,
                    fx_daily_vol=0.05, hold_days=10)
    assert choose_route(k, fx_risk_aversion=1.0)[0] != "usdc_capital_conserve"


def test_compare_routes_summary():
    rows = [row("A-EUR", 1, 1.001), row("A-USDC", 1.08, 1.0805), row("USDC-EUR", 0.92, 0.9201)]
    s = compare_routes(rows, FeeSchedule(), {"vol_journaliere": 0.0003})
    assert s["resume"]["taille_100_eur"]["marches_compares"] == 1
