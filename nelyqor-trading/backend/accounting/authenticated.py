"""Capital AUTHENTIFIÉ (phase 5, risque D6).

Le capital qui dimensionne un ACHAT réel ne peut plus venir du seul journal (fills + flux externes) : un journal
falsifié — hier par une API compromise écrivant dans la base partagée, demain par toute autre voie — gonflerait
la taille des ordres réels. Ici :

- les soldes sont ceux que le moteur lit lui-même chez Bitvavo (`balance()`, requête signée avec ses clés) ;
- ils sont comparés au journal du moteur (périmètre du robot, actifs exclus à l'ouverture ignorés) ;
- écart au-delà de max(tolérance EUR, fraction × capital authentifié) (frais, arrondis), soldes indisponibles ou
  plus vieux que `auth_balance_max_age_ms` => `ok=False` : AUCUN nouvel achat (ventes et protections permises) ;
- sinon le dimensionnement utilise min(journal, soldes) : une valeur gonflée d'un côté seulement est sans effet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core.config import HardLimits
from ..core.num import ZERO, D, fmt


@dataclass
class AuthCapital:
    ok: bool
    ts: int  # instant de LECTURE des soldes chez Bitvavo
    reasons: list[str] = field(default_factory=list)
    equity_eur: Decimal = ZERO      # EUR (disponible + en ordre) + positions du périmètre, valorisées au bid
    eur_total: Decimal = ZERO       # EUR disponible + en ordre (comparable au journal)
    eur_available: Decimal = ZERO   # EUR LIBRE chez Bitvavo (plafond d'un achat)
    ledger_equity_eur: Decimal = ZERO
    ledger_cash_eur: Decimal = ZERO
    divergence_eur: Decimal = ZERO
    tolerance_eur: Decimal = ZERO
    details: dict[str, str] = field(default_factory=dict)

    def sizing(self, ledger_equity: Decimal, ledger_cash: Decimal) -> tuple[Decimal, Decimal]:
        """(equity, trésorerie) pour dimensionner : le MINIMUM du journal et des soldes authentifiés."""
        if not self.ok:
            return ZERO, ZERO
        return min(ledger_equity, self.equity_eur), min(ledger_cash, self.eur_total)

    def as_display(self) -> dict[str, Any]:
        return {"ok": self.ok, "ts": self.ts, "raisons": self.reasons, "capital_authentifie_eur": fmt(self.equity_eur),
                "eur_disponible_authentifie": fmt(self.eur_available), "equity_journal_eur": fmt(self.ledger_equity_eur),
                "ecart_eur": fmt(self.divergence_eur), "tolerance_eur": fmt(self.tolerance_eur), "detail": self.details}


def unavailable(now_ms: int, why: str) -> AuthCapital:
    return AuthCapital(False, now_ms, [f"soldes Bitvavo non vérifiables : {why}"])


def authenticate_capital(balance: list[dict[str, Any]], fetched_ms: int, now_ms: int,
                         ledger_cash: dict[str, Decimal], ledger_positions: dict[str, Decimal],
                         excluded: set[str], prices_eur: dict[str, Decimal], limits: HardLimits) -> AuthCapital:
    reasons: list[str] = []
    if now_ms - fetched_ms > limits.auth_balance_max_age_ms:
        return AuthCapital(False, fetched_ms, [f"soldes Bitvavo périmés ({(now_ms - fetched_ms) // 1000} s)"])
    actual: dict[str, Decimal] = {}
    avail_eur = ZERO
    try:
        for b in balance:
            sym = str(b["symbol"])
            if sym in excluded:
                continue
            a, io = D(b.get("available", 0)), D(b.get("inOrder", 0))
            if a < 0 or io < 0:
                raise ValueError(f"solde négatif {sym}")
            actual[sym] = actual.get(sym, ZERO) + a + io
            if sym == "EUR":
                avail_eur += a
    except (KeyError, ValueError, ArithmeticError, TypeError) as err:
        return AuthCapital(False, fetched_ms, [f"réponse de solde invalide : {err}"])
    expected: dict[str, Decimal] = {k: v for k, v in ledger_cash.items() if k not in excluded}
    for s, q in ledger_positions.items():
        if s not in excluded:
            expected[s] = expected.get(s, ZERO) + q

    def value(sym: str, qty: Decimal) -> Decimal | None:
        if sym == "EUR":
            return qty
        px = prices_eur.get(sym)
        return qty * px if px is not None else None

    eq_auth = ZERO
    eq_led = ZERO
    div = ZERO
    details: dict[str, str] = {}
    for sym in sorted(set(actual) | set(expected)):
        a, e = actual.get(sym, ZERO), expected.get(sym, ZERO)
        va, ve, vd = value(sym, a), value(sym, e), value(sym, abs(a - e))
        if va is None or ve is None or vd is None:
            if a != e or a > 0:
                reasons.append(f"prix EUR inconnu pour {sym} : capital non évaluable")
            continue
        eq_auth += va
        eq_led += ve
        if a != e:
            div += vd
            details[sym] = f"soldes {fmt(a)} / journal {fmt(e)}"
    tol = max(limits.auth_divergence_tolerance_eur, limits.auth_divergence_tolerance_fraction * eq_auth)
    if div > tol:
        reasons.append(f"écart journal/soldes Bitvavo {fmt(div.quantize(D('0.01')))} € > tolérance "
                       f"{fmt(tol.quantize(D('0.01')))} € : aucun nouvel achat")
    if eq_auth <= 0:
        reasons.append("capital authentifié nul")
    return AuthCapital(not reasons, fetched_ms, reasons, eq_auth, actual.get("EUR", ZERO), avail_eur, eq_led,
                       expected.get("EUR", ZERO), div, tol, details)
