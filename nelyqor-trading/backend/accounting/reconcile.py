"""Réconciliation soldes internes / échange et détection des flux externes manuels."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..core.num import ZERO, D


@dataclass
class ReconResult:
    ok: bool
    diffs: dict[str, Decimal] = field(default_factory=dict)
    identified_flows: list[dict[str, Any]] = field(default_factory=list)
    unidentified: dict[str, Decimal] = field(default_factory=dict)


def exchange_totals(balance: list[dict[str, Any]]) -> dict[str, Decimal]:
    return {b["symbol"]: D(b.get("available", 0)) + D(b.get("inOrder", 0)) for b in balance}


def reconcile(expected: dict[str, Decimal], balance: list[dict[str, Any]], deposits: list[dict[str, Any]],
              withdrawals: list[dict[str, Any]], tolerance: dict[str, Decimal] | None = None) -> ReconResult:
    """Compare les totaux ; explique les écarts par l'historique de dépôts/retraits ; le reste est non identifié.
    `deposits`/`withdrawals` : nouveaux mouvements depuis le dernier rapprochement (format Bitvavo)."""
    tol = tolerance or {}
    actual = exchange_totals(balance)
    syms = set(expected) | set(actual)
    diffs = {s: actual.get(s, ZERO) - expected.get(s, ZERO) for s in syms}
    flows: list[dict[str, Any]] = []
    explained: dict[str, Decimal] = {}
    for d in deposits:
        if d.get("status", "completed") in ("completed", "success"):
            explained[d["symbol"]] = explained.get(d["symbol"], ZERO) + D(d["amount"])
            flows.append({"kind": "deposit", **d})
    for w in withdrawals:
        if w.get("status", "completed") in ("completed", "success", "awaiting_processing", "processing"):
            amt = D(w["amount"]) + D(w.get("fee", 0))
            explained[w["symbol"]] = explained.get(w["symbol"], ZERO) - amt
            flows.append({"kind": "withdrawal", **w})
    unidentified = {}
    for s, dlt in diffs.items():
        rest = dlt - explained.get(s, ZERO)
        if abs(rest) > tol.get(s, D("1e-8")):
            unidentified[s] = rest
    return ReconResult(not unidentified, {s: v for s, v in diffs.items() if v}, flows, unidentified)
