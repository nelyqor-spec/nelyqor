"""Simulateur d'exécution au niveau des TRANSACTIONS publiques et des CARNETS (phase 4, LAB uniquement).

Aucun ordre n'est envoyé : on rejoue un ordre HYPOTHÉTIQUE contre le flux réel des transactions publiques
Bitvavo (`GET /{market}/trades` : id, timestamp, amount, price, side = côté de l'agresseur) et, quand ils
existent, des instantanés de carnet (`GET /{market}/book`).

Modèle d'un ordre limité ACHAT au prix P (symétrique pour la vente) :
1. soumission à t0, actif à t0 + latence (aller) ; postOnly : rejeté si, à l'activation, le meilleur ask <= P ;
2. file d'attente devant nous Q0 = taille affichée au niveau P à l'activation x multiplicateur de scénario
   (P meilleur que le meilleur bid -> Q0 = 0) ; sans carnet : estimation fournie (médiane observée) ;
3. une transaction VENDEUSE (agresseur vendeur) à un prix < P TRAVERSE notre niveau : remplissage complet du
   reliquat (priorité prix-temps) ; à prix == P elle consomme d'abord Q, puis nous remplit (partiellement) ;
4. annulations : entre deux carnets, la baisse de taille au niveau P non expliquée par les transactions est
   attribuée à la file devant nous avec un coefficient de scénario (0 = pessimiste) ;
5. une bougie ou un prix qui TOUCHE P ne remplit jamais à lui seul ;
6. expiration (TTL fixe ou adaptatif) puis annulation avec latence : des remplissages restent possibles
   pendant ce délai ; le reliquat est soit abandonné (coût d'opportunité mesuré), soit exécuté au marché
   (repli) en parcourant la profondeur du carnet (glissement) ;
7. sélection adverse : variation du prix de référence 1 / 5 / 30 min après chaque remplissage.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Scenario:
    name: str
    latency_ms: int
    cancel_latency_ms: int
    queue_mult: float  # x taille affichée devant nous (icebergs, arrivées pendant la latence)
    cancel_credit: float  # part des annulations du niveau attribuée à la file devant nous
    at_price_fills: bool = True  # les transactions AU prix limite peuvent nous remplir après la file


SCENARIOS = {
    "pessimiste": Scenario("pessimiste", 500, 500, 1.5, 0.0, at_price_fills=False),
    "intermediaire": Scenario("intermediaire", 200, 250, 1.0, 0.5),
    "optimiste": Scenario("optimiste", 50, 100, 0.7, 1.0),
}


@dataclass
class Order:
    side: str  # buy | sell
    price: float
    amount: float  # quantité en actif de base
    t_submit: int
    ttl_ms: int
    post_only: bool = True
    fallback_market: bool = False


@dataclass
class Fill:
    ts: int
    price: float
    amount: float
    maker: bool


@dataclass
class ExecResult:
    status: str  # filled | partial | unfilled | rejected_post_only
    fills: list[Fill] = field(default_factory=list)
    queue_ahead_initial: float = 0.0
    fallback: list[Fill] = field(default_factory=list)

    @property
    def filled(self) -> float:
        return sum(f.amount for f in self.fills)

    @property
    def avg_price(self) -> float | None:
        q = self.filled
        return sum(f.price * f.amount for f in self.fills) / q if q > 0 else None


class Tape:
    """Transactions publiques triées par horodatage (ms) + carnets optionnels."""

    def __init__(self, trades: list[dict[str, Any]], books: list[dict[str, Any]] | None = None) -> None:
        tr = sorted(trades, key=lambda t: (int(t["timestamp"]), str(t.get("id", ""))))
        self.ts = np.array([int(t["timestamp"]) for t in tr], dtype=np.int64)
        self.px = np.array([float(t["price"]) for t in tr])
        self.qty = np.array([float(t["amount"]) for t in tr])
        self.side = np.array([1 if t["side"] == "buy" else -1 for t in tr], dtype=np.int8)  # agresseur
        self.books = sorted(books or [], key=lambda b: int(b["ts"]))
        self.book_ts = [int(b["ts"]) for b in self.books]

    def book_at(self, t: int, max_age_ms: int = 30_000) -> dict[str, Any] | None:
        k = bisect.bisect_right(self.book_ts, t) - 1
        if k < 0 or t - self.book_ts[k] > max_age_ms:
            return None
        return self.books[k]

    def ref_price(self, t: int) -> float | None:
        """Prix de référence à t : milieu du carnet récent, sinon dernière transaction <= t."""
        b = self.book_at(t)
        if b and b.get("bids") and b.get("asks"):
            return (float(b["bids"][0][0]) + float(b["asks"][0][0])) / 2
        k = int(np.searchsorted(self.ts, t, side="right")) - 1
        return float(self.px[k]) if k >= 0 else None

    def window(self, t0: int, t1: int) -> range:
        a = int(np.searchsorted(self.ts, t0, side="right"))
        b = int(np.searchsorted(self.ts, t1, side="right"))
        return range(a, b)


def level_size(book: dict[str, Any] | None, side: str, price: float) -> float | None:
    """Taille affichée au niveau `price` du côté passif de l'ordre (bids pour un achat)."""
    if not book:
        return None
    lv = book.get("bids" if side == "buy" else "asks") or []
    if not lv:
        return None
    best = float(lv[0][0])
    if (side == "buy" and price > best) or (side == "sell" and price < best):
        return 0.0  # amélioration du meilleur prix : personne devant nous
    for p, a in lv:
        if abs(float(p) - price) <= 1e-12 * max(1.0, price):
            return float(a)
    return 0.0  # niveau vide au moment de l'arrivée : nous sommes premiers à ce prix


def would_cross(book: dict[str, Any] | None, tape: Tape, side: str, price: float, t: int) -> bool:
    """postOnly : l'ordre prendrait-il de la liquidité à l'activation ?"""
    if book and book.get("asks") and book.get("bids"):
        return price >= float(book["asks"][0][0]) if side == "buy" else price <= float(book["bids"][0][0])
    k = int(np.searchsorted(tape.ts, t, side="right")) - 1
    if k < 0:
        return False
    last = tape.px[k]
    # sans carnet : prudence — un achat au-dessus du dernier prix échangé est réputé croiser
    return price > last if side == "buy" else price < last


def simulate_limit(tape: Tape, o: Order, sc: Scenario, queue_estimate: float | None = None) -> ExecResult:
    t_act = o.t_submit + sc.latency_ms
    book = tape.book_at(t_act)
    if o.post_only and would_cross(book, tape, o.side, o.price, t_act):
        return ExecResult("rejected_post_only")
    q0 = level_size(book, o.side, o.price)
    if q0 is None:
        q0 = queue_estimate if queue_estimate is not None else float("inf")
    q = q0 * sc.queue_mult if np.isfinite(q0) else float("inf")
    res = ExecResult("unfilled", queue_ahead_initial=float(q))
    rem = o.amount
    t_end = t_act + o.ttl_ms + sc.cancel_latency_ms
    passive = -1 if o.side == "buy" else 1  # agresseur qui nous remplit (vendeur pour un achat)
    last_book = book
    for k in tape.window(t_act, t_end):
        if rem <= 1e-15:
            break
        tk = int(tape.ts[k])
        # annulations entre carnets successifs (si disponibles)
        if sc.cancel_credit > 0 and np.isfinite(q) and q > 0 and tape.books:
            nb = tape.book_at(tk, max_age_ms=10**12)
            if nb is not None and last_book is not None and nb is not last_book:
                before, after = level_size(last_book, o.side, o.price), level_size(nb, o.side, o.price)
                if before and after is not None and after < before:
                    traded = float(tape.qty[[x for x in tape.window(int(last_book["ts"]), int(nb["ts"]))
                                             if tape.side[x] == passive and tape.px[x] == o.price]].sum())
                    cancels = max(0.0, before - after - traded)
                    q = max(0.0, q - sc.cancel_credit * cancels * min(1.0, q / before))
                last_book = nb
        if tape.side[k] != passive:
            continue
        p, a = float(tape.px[k]), float(tape.qty[k])
        through = p < o.price if o.side == "buy" else p > o.price
        at = p == o.price
        if through:
            res.fills.append(Fill(tk, o.price, rem, True))
            rem = 0.0
        elif at and sc.at_price_fills:
            if q >= a:
                q -= a
                continue
            x = min(rem, a - max(q, 0.0))
            q = 0.0
            if x > 0:
                res.fills.append(Fill(tk, o.price, x, True))
                rem -= x
    if res.fills:
        res.status = "filled" if rem <= 1e-12 * o.amount else "partial"
    if rem > 1e-15 and o.fallback_market:
        res.fallback = market_fill(tape, o.side, rem, t_end + sc.latency_ms)
    return res


def market_fill(tape: Tape, side: str, amount: float, t: int, fallback_spread_bps: float = 10.0,
                impact_bps: float = 5.0) -> list[Fill]:
    """Ordre au marché : parcourt la profondeur du carnet récent ; sans carnet, dernier prix +/- demi-spread
    prudent + impact."""
    b = tape.book_at(t)
    lv = (b or {}).get("asks" if side == "buy" else "bids") or []
    fills, rem = [], amount
    for p, a in lv:
        x = min(rem, float(a))
        fills.append(Fill(t, float(p), x, False))
        rem -= x
        if rem <= 1e-15:
            return fills
    ref = tape.ref_price(t)
    if ref is None:
        return fills
    sgn = 1 if side == "buy" else -1
    worst = max([f.price for f in fills], default=ref) if side == "buy" else min([f.price for f in fills], default=ref)
    px = (worst if fills else ref) * (1 + sgn * (fallback_spread_bps / 2 + impact_bps) / 1e4)
    fills.append(Fill(t, px, rem, False))
    return fills


def markouts(tape: Tape, res: ExecResult, side: str, horizons_ms: tuple[int, ...] = (60_000, 300_000, 1_800_000)
             ) -> dict[str, float | None]:
    """Sélection adverse : rendement du prix de référence APRÈS remplissage, signé du point de vue de l'ordre
    (négatif = le marché est parti contre nous juste après le remplissage). En points de base."""
    out: dict[str, float | None] = {}
    if not res.fills:
        return {f"{h // 60_000}min": None for h in horizons_ms}
    sgn = 1 if side == "buy" else -1
    for h in horizons_ms:
        num = den = 0.0
        for f in res.fills:
            r = tape.ref_price(f.ts + h)
            if r is None:
                continue
            num += sgn * (r / f.price - 1) * 1e4 * f.amount
            den += f.amount
        out[f"{h // 60_000}min"] = num / den if den else None
    return out


def adaptive_ttl(tape: Tape, side: str, price: float, t: int, queue: float, amount: float,
                 lookback_ms: int = 3_600_000, k: float = 2.0, lo_ms: int = 60_000, hi_ms: int = 4 * 3_600_000) -> int:
    """Durée de validité adaptative : k x temps attendu pour que le flux vendeur (achat) à <= P consomme la
    file + notre taille, estimé sur l'heure PASSÉE (causal). Bornée [lo, hi]."""
    passive = -1 if side == "buy" else 1
    w = tape.window(t - lookback_ms, t)
    sel = [x for x in w if tape.side[x] == passive and (tape.px[x] <= price * 1.001 if side == "buy" else tape.px[x] >= price * 0.999)]
    rate = float(tape.qty[sel].sum()) / lookback_ms if sel else 0.0
    if rate <= 0:
        return hi_ms
    need = (queue if np.isfinite(queue) else 0.0) + amount
    return int(np.clip(k * need / rate, lo_ms, hi_ms))


def evaluate_order(tape: Tape, o: Order, sc: Scenario, fee_maker: float, fee_taker: float,
                   queue_estimate: float | None = None, horizon_ms: int = 4 * 3_600_000) -> dict[str, Any]:
    """Compare l'ordre limité à un ordre au marché soumis au même instant, coûts complets inclus.
    Retour : coût d'exécution (pb, signé : positif = plus cher qu'au prix de référence de décision), part
    remplie, coût d'opportunité du reliquat (variation de prix jusqu'à l'horizon), sélection adverse."""
    ref0 = tape.ref_price(o.t_submit)
    r = simulate_limit(tape, o, sc, queue_estimate)
    sgn = 1 if o.side == "buy" else -1
    mkt = market_fill(tape, o.side, o.amount, o.t_submit + sc.latency_ms)
    out: dict[str, Any] = {"statut": r.status, "part_remplie": r.filled / o.amount if o.amount else 0.0,
                           "file_initiale": r.queue_ahead_initial, "ref": ref0}
    if ref0 is None:
        out["statut"] = "sans_reference"
        return out

    def cost_bps(fills: list[Fill], fee: float) -> float | None:
        q = sum(f.amount for f in fills)
        if q <= 0:
            return None
        avg = sum(f.price * f.amount for f in fills) / q
        return float((sgn * (avg / ref0 - 1) + fee) * 1e4)

    out["cout_marche_bps"] = cost_bps(mkt, fee_taker)
    out["cout_limite_bps"] = cost_bps(r.fills, fee_maker)
    rem = o.amount - r.filled
    later = tape.ref_price(o.t_submit + horizon_ms)
    out["opportunite_reliquat_bps"] = (float(sgn * (later / ref0 - 1) * 1e4) * rem / o.amount
                                       if later is not None and rem > 0 else 0.0)
    if r.fallback:
        out["cout_repli_bps"] = cost_bps(r.fallback, fee_taker)
    out["selection_adverse"] = markouts(tape, r, o.side)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrégats par scénario : taux de remplissage, coûts, sélection adverse, coût total pondéré."""
    def mean(key: str, sub: str | None = None) -> float | None:
        v = [(x[key][sub] if sub else x[key]) for x in rows if x.get(key) is not None and (sub is None or x[key].get(sub) is not None)]
        v = [float(t) for t in v if t is not None and np.isfinite(t)]
        return float(np.mean(v)) if v else None

    n = len(rows)
    st = {s: sum(1 for x in rows if x["statut"] == s) for s in ("filled", "partial", "unfilled", "rejected_post_only")}
    eff = []
    for x in rows:
        if x.get("cout_marche_bps") is None:
            continue
        f = float(x.get("part_remplie", 0.0))
        lim = float(x.get("cout_limite_bps") or 0.0)
        if f >= 1:
            rest_total = 0.0
        elif x.get("cout_repli_bps") is not None:
            rest_total = (1 - f) * float(x["cout_repli_bps"])  # reliquat exécuté au marché
        else:
            rest_total = float(x.get("opportunite_reliquat_bps") or 0.0)  # déjà pondéré par le reliquat
        eff.append({"marche": x["cout_marche_bps"], "limite_effectif": f * lim + rest_total})
    return {"n": n, "statuts": st, "taux_remplissage_moyen": mean("part_remplie"),
            "cout_marche_bps": mean("cout_marche_bps"), "cout_limite_rempli_bps": mean("cout_limite_bps"),
            "opportunite_reliquat_bps": mean("opportunite_reliquat_bps"),
            "selection_adverse_5min_bps": mean("selection_adverse", "5min"),
            "selection_adverse_30min_bps": mean("selection_adverse", "30min"),
            "cout_effectif_limite_bps": float(np.mean([e["limite_effectif"] for e in eff])) if eff else None,
            "cout_effectif_marche_bps": float(np.mean([e["marche"] for e in eff])) if eff else None}
