"""Comptabilité sur fixtures calculées à la main : dépôts/retraits manuels, frais multi-devises,
fonds réservés, TWR, P&L, réconciliation, fiscalité 2086."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.accounting.fiscal import Acquisition, Cession, compute_2086
from backend.accounting.ledger import ExternalFlow, Fill, Ledger, twr
from backend.accounting.reconcile import reconcile
from backend.portfolio.risk import net_round_trip


def test_round_trip_formula():
    # (110/100) * 0.9975 * 0.9975 - 1
    assert net_round_trip(D(100), D(110), D("0.0025"), D("0.0025")) == D("1.1") * D("0.9975") ** 2 - 1


def test_buy_sell_eur_fees_counted_once():
    L = Ledger(D(100))
    L.apply_fill(Fill(1, "BTC-EUR", "buy", D("0.001"), D(50000), D("0.125"), "EUR"))  # 50 + 0.125
    assert L.s.cash["EUR"] == D("49.875")
    assert L.s.positions["BTC"].cost_eur == D("50.125")
    L.apply_fill(Fill(2, "BTC-EUR", "sell", D("0.001"), D(55000), D("0.1375"), "EUR"))  # 55 - 0.1375
    assert L.s.cash["EUR"] == D("49.875") + D("54.8625")
    assert L.s.realized_eur == D("54.8625") - D("50.125")
    assert L.s.fees_eur == D("0.2625")
    # P&L = valeur - initial - apports ; frais déjà inclus, non déduits deux fois
    assert L.pnl_trading({}) == L.s.realized_eur


def test_fee_in_base_currency_reduces_quantity():
    L = Ledger(D(100))
    L.apply_fill(Fill(1, "ETH-EUR", "buy", D("0.02"), D(2500), D("0.00005"), "ETH"), {"ETH": D(2500)})
    assert L.s.positions["ETH"].qty == D("0.01995")
    assert L.s.cash["EUR"] == D(50)
    assert L.s.fees_eur == D("0.125")


def test_fee_in_third_currency():
    L = Ledger(D(100))
    L.s.cash["BNB"] = D(1)
    L.apply_fill(Fill(1, "BTC-EUR", "buy", D("0.001"), D(50000), D("0.001"), "BNB"), {"BNB": D(300)})
    assert L.s.cash["BNB"] == D("0.999") and L.s.fees_eur == D("0.3")
    L2 = Ledger(D(100))
    L2.apply_fill(Fill(1, "BTC-EUR", "buy", D("0.001"), D(50000), D("0.001"), "XYZ"))
    assert L2.s.fees_unconverted == [("XYZ", D("0.001"))]


def test_oversell_refused():
    L = Ledger(D(100))
    with pytest.raises(ValueError):
        L.apply_fill(Fill(1, "BTC-EUR", "sell", D("0.001"), D(50000), D(0), "EUR"))


def test_manual_deposit_withdrawal_and_pnl():
    L = Ledger(D(100))
    L.apply_fill(Fill(1, "BTC-EUR", "buy", D("0.001"), D(50000), D(0), "EUR"))
    L.apply_flow(ExternalFlow(2, "deposit", "EUR", D(50), D(50)))
    L.apply_flow(ExternalFlow(3, "withdrawal", "EUR", D(20), D(20)))
    px = {"BTC": D(60000)}
    assert L.value_eur(px) == D(50) + D(50) - D(20) + D(60)
    assert L.s.net_contributions_eur == D(30)
    assert L.pnl_trading(px) == D(10)  # seule la hausse BTC


def test_twr_independent_of_flows():
    # 100 -> 110 (+10 %), dépôt 100 -> 210 -> 231 (+10 %) => TWR = 21 %
    series = [(0, D(100), D(0)), (1, D(110), D(100)), (2, D(231), D(0))]
    assert twr(series) == D("0.21")


def test_reserved_funds_not_double_counted():
    bal = [{"symbol": "EUR", "available": "60", "inOrder": "40"}, {"symbol": "BTC", "available": "0.001", "inOrder": "0"}]
    r = reconcile({"EUR": D(100), "BTC": D("0.001")}, bal, [], [])
    assert r.ok and r.unidentified == {}


def test_reconcile_identifies_manual_deposit_and_flags_unknown():
    bal = [{"symbol": "EUR", "available": "150", "inOrder": "0"}]
    r = reconcile({"EUR": D(100)}, bal, [{"symbol": "EUR", "amount": "50", "status": "completed", "txId": "a"}], [])
    assert r.ok and r.identified_flows[0]["kind"] == "deposit"
    r2 = reconcile({"EUR": D(100)}, bal, [], [])
    assert not r2.ok and r2.unidentified == {"EUR": D(50)}
    r3 = reconcile({"EUR": D(100)}, [{"symbol": "EUR", "available": "79", "inOrder": "0"}], [],
                   [{"symbol": "EUR", "amount": "20", "fee": "1", "status": "completed"}])
    assert r3.ok


def test_2086_formula_and_threshold():
    # Achat 100 €, puis cession de 60 € alors que le portefeuille vaut 120 €
    res = compute_2086([Acquisition(1, D(100))], [Cession(2, "BTC", D(60), D("0.15"), D(120))])
    ln = res["lignes"][0]
    assert ln["fraction_imputee"] == D("50.00")  # 100 * 60 / 120
    assert ln["plus_value"] == D("10.00")
    assert res["sous_seuil_305"] is True
    res2 = compute_2086([Acquisition(1, D(1000))], [Cession(2, "BTC", D(400), D(1), D(1000))])
    assert res2["sous_seuil_305"] is False  # seuil sur les prix de cession, pas sur le gain
    assert res2["total_plus_values"] == D("0.00")


def test_decimal_conversion_from_numpy_and_float():
    import numpy as np

    from backend.core.num import D as conv
    assert conv(np.float64(0.1)) == D("0.1")
    assert conv(0.1) == D("0.1") and conv("1.50") == D("1.50") and conv(3) == D(3)


def test_property_pnl_equals_realized_plus_unrealized_random_sequences():
    """Propriété : P&L (par la valeur) = réalisé + latent, quelles que soient les séquences et devises de frais.
    La version initiale comptait deux fois les frais prélevés en jetons : ce test l'aurait détecté."""
    import random

    from backend.core.num import D as conv

    rnd = random.Random(42)
    for _ in range(300):
        L = Ledger(D(1000))
        qty = D(0)
        price = D(100)
        for _ in range(rnd.randint(1, 12)):
            price = (price * conv(rnd.uniform(0.9, 1.1))).quantize(D("0.01"))
            fee_ccy = rnd.choice(["EUR", "ETH"])
            if qty > D("0.5") and rnd.random() < 0.5:
                amt = (qty * conv(rnd.uniform(0.1, 0.9))).quantize(D("0.0001"))
                fee = (amt * D("0.0025")).quantize(D("0.00000001")) if fee_ccy == "ETH" else (amt * price * D("0.0025"))
                if fee_ccy == "ETH" and amt + fee > qty:
                    continue
                L.apply_fill(Fill(0, "ETH-EUR", "sell", amt, price, fee, fee_ccy), {"ETH": price})
                qty -= amt + (fee if fee_ccy == "ETH" else 0)
            else:
                amt = conv(rnd.uniform(0.1, 2)).quantize(D("0.0001"))
                fee = (amt * D("0.0025")) if fee_ccy == "ETH" else (amt * price * D("0.0025"))
                L.apply_fill(Fill(0, "ETH-EUR", "buy", amt, price, fee, fee_ccy), {"ETH": price})
                qty += amt - (fee if fee_ccy == "ETH" else 0)
        px = {"ETH": price}
        assert abs(L.s.positions["ETH"].qty - qty) < D("1e-12")
        total = L.s.realized_eur + L.unrealized_eur(px)
        assert abs(L.pnl_trading(px) - total) < D("1e-9"), (L.pnl_trading(px), total)


def test_usdc_pair_accounting_and_fx():
    L = Ledger(D(0))
    L.s.cash["USDC"] = D(108)  # 100 EUR convertis à 1,08 USDC/EUR
    L.apply_fill(Fill(1, "BTC-USDC", "buy", D("0.001"), D(100000), D("0.05"), "USDC", quote_eur_rate=D("0.925")))
    assert L.s.cash["USDC"] == D("7.95")
    assert L.s.positions["BTC"].cost_eur == (D(100) + D("0.05")) * D("0.925")
    L.apply_fill(Fill(2, "BTC-USDC", "sell", D("0.001"), D(110000), D("0.055"), "USDC", quote_eur_rate=D("0.925")))
    assert L.s.cash["USDC"] == D("7.95") + D("110") - D("0.055")
    assert L.s.realized_eur == (D("110") - D("0.055") - D("100.05")) * D("0.925")
    # variation du change : visible dans la valeur, pas dans le réalisé
    v1, v2 = L.value_eur({"USDC": D("0.925")}), L.value_eur({"USDC": D("0.90")})
    assert v2 < v1


def test_non_eur_fill_without_rate_refused():
    L = Ledger(D(0))
    L.s.cash["USDC"] = D(100)
    with pytest.raises(ValueError):
        L.apply_fill(Fill(1, "BTC-USDC", "buy", D("0.0001"), D(100000), D(0), "USDC"))
