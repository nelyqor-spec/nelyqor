"""Comptabilité en EUR (Decimal) : positions au coût moyen pondéré, P&L réalisé/latent, frais, TWR.

P&L_trading = valeur_actuelle - valeur_initiale - apports_nets
TWR : produit des rendements de sous-périodes séparées par chaque flux externe.
Les commissions sont comptées UNE seule fois : dans le coût d'acquisition (achat) ou en déduction du
produit de cession (vente), et totalisées séparément pour information uniquement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core.num import ZERO, D


@dataclass
class Fill:
    ts: int
    market: str
    side: str
    amount: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    fee_eur: Decimal | None = None
    id: str = ""


@dataclass
class ExternalFlow:
    ts: int
    kind: str  # deposit | withdrawal
    symbol: str
    amount: Decimal
    value_eur: Decimal
    identified: bool = True


@dataclass
class Position:
    symbol: str
    qty: Decimal = ZERO
    cost_eur: Decimal = ZERO  # coût total incluant frais d'achat

    @property
    def avg_cost(self) -> Decimal:
        return self.cost_eur / self.qty if self.qty > 0 else ZERO


@dataclass
class LedgerState:
    cash: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)
    realized_eur: Decimal = ZERO
    fees_eur: Decimal = ZERO
    fees_unconverted: list[tuple[str, Decimal]] = field(default_factory=list)
    net_contributions_eur: Decimal = ZERO
    initial_value_eur: Decimal = ZERO


class Ledger:
    def __init__(self, initial_cash_eur: Decimal = ZERO) -> None:
        self.s = LedgerState(cash={"EUR": initial_cash_eur}, initial_value_eur=initial_cash_eur)

    def apply_fill(self, f: Fill, fee_rates_eur: dict[str, Decimal] | None = None) -> None:
        base, quote = f.market.split("-")
        if quote != "EUR":
            raise NotImplementedError("comptabilité V1 : paires en EUR uniquement (USDC non activé)")
        rates = {"EUR": D(1), **(fee_rates_eur or {})}
        fee_eur = f.fee_eur if f.fee_eur is not None else (f.fee * rates[f.fee_currency] if f.fee_currency in rates else None)
        pos = self.s.positions.setdefault(base, Position(base))
        gross = f.amount * f.price
        if fee_eur is None:
            self.s.fees_unconverted.append((f.fee_currency, f.fee))
            fee_eur = ZERO
        self.s.fees_eur += fee_eur
        if f.side == "buy":
            # Frais en EUR : payés en plus du montant ; frais dans la crypto achetée : réduisent la quantité reçue.
            if f.fee_currency == "EUR":
                self._cash("EUR", -(gross + f.fee))
                pos.qty += f.amount
            elif f.fee_currency == base:
                pos.qty += f.amount - f.fee
                self._cash("EUR", -gross)
            else:
                self._cash("EUR", -gross)
                self._cash(f.fee_currency, -f.fee)
                pos.qty += f.amount
            pos.cost_eur += gross + fee_eur
        else:
            if pos.qty < f.amount - D("1e-12"):
                raise ValueError(f"vente {f.amount} {base} > position {pos.qty}")
            cost_out = pos.avg_cost * f.amount
            if f.fee_currency == "EUR":
                proceeds = gross - f.fee
                self._cash("EUR", proceeds)
                pos.qty -= f.amount
            elif f.fee_currency == base:
                proceeds = gross - fee_eur
                self._cash("EUR", gross)
                pos.qty -= f.amount + f.fee
            else:
                proceeds = gross - fee_eur
                self._cash("EUR", gross)
                self._cash(f.fee_currency, -f.fee)
                pos.qty -= f.amount
            pos.cost_eur -= cost_out
            self.s.realized_eur += proceeds - cost_out
            if pos.qty <= D("1e-12"):
                pos.qty, pos.cost_eur = max(pos.qty, ZERO), ZERO if pos.qty <= 0 else pos.cost_eur

    def apply_flow(self, fl: ExternalFlow) -> None:
        sign = D(1) if fl.kind == "deposit" else D(-1)
        if fl.symbol == "EUR":
            self._cash("EUR", sign * fl.amount)
        else:
            pos = self.s.positions.setdefault(fl.symbol, Position(fl.symbol))
            if sign > 0:
                pos.qty += fl.amount
                pos.cost_eur += fl.value_eur  # valeur au transfert entrant (hypothèse documentée)
            else:
                ratio = fl.amount / pos.qty if pos.qty > 0 else D(1)
                pos.cost_eur -= pos.cost_eur * ratio
                pos.qty -= fl.amount
        self.s.net_contributions_eur += sign * fl.value_eur

    def _cash(self, sym: str, delta: Decimal) -> None:
        self.s.cash[sym] = self.s.cash.get(sym, ZERO) + delta

    def value_eur(self, prices_eur: dict[str, Decimal]) -> Decimal:
        v = self.s.cash.get("EUR", ZERO)
        for sym, amt in self.s.cash.items():
            if sym != "EUR" and amt:
                v += amt * prices_eur.get(sym, ZERO)
        for sym, p in self.s.positions.items():
            if p.qty > 0:
                v += p.qty * prices_eur.get(sym, ZERO)
        return v

    def unrealized_eur(self, prices_eur: dict[str, Decimal]) -> Decimal:
        return sum((p.qty * prices_eur.get(s, ZERO) - p.cost_eur for s, p in self.s.positions.items() if p.qty > 0),
                   ZERO)

    def pnl_trading(self, prices_eur: dict[str, Decimal]) -> Decimal:
        return self.value_eur(prices_eur) - self.s.initial_value_eur - self.s.net_contributions_eur

    def summary(self, prices_eur: dict[str, Decimal], hosting_cost_eur: Decimal = ZERO) -> dict[str, Any]:
        pnl = self.pnl_trading(prices_eur)
        return {
            "valeur_eur": self.value_eur(prices_eur), "cash_eur": self.s.cash.get("EUR", ZERO),
            "pnl_trading_eur": pnl, "realise_eur": self.s.realized_eur,
            "latent_eur": self.unrealized_eur(prices_eur), "frais_eur": self.s.fees_eur,
            "apports_nets_eur": self.s.net_contributions_eur, "cout_hebergement_eur": hosting_cost_eur,
            "resultat_economique_eur": pnl - hosting_cost_eur,
            "frais_non_convertis": self.s.fees_unconverted,
        }


def twr(values_and_flows: list[tuple[int, Decimal, Decimal]]) -> Decimal:
    """TWR à partir d'une suite (ts, valeur_avant_flux, flux_net) triée.
    Chaque sous-période : r = V_fin_avant_flux / (V_debut_apres_flux) - 1."""
    if len(values_and_flows) < 2:
        return ZERO
    g = D(1)
    _, v0, f0 = values_and_flows[0]
    start = v0 + f0
    for _, v, f in values_and_flows[1:]:
        if start > 0:
            g *= v / start
        start = v + f
    return g - 1
