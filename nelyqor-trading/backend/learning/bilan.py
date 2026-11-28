"""Bilan financier de la simulation permanente (phase 8), calculé UNIQUEMENT depuis le journal réel du moteur.

Lecture seule : fills, intentions, décisions, prédictions, instantanés d'equity, bougies. Aucune donnée n'est inventée :
une mesure impossible est publiée « non mesurable » avec sa raison. Montants en EUR, capital FICTIF.

Mesures :
- rendement brut (avant frais) et net (après frais), et net après coût d'hébergement (pro rata) ;
- frais de transaction cumulés ; coûts d'exécution (écart entre le prix exécuté et le prix de référence de
  l'intention, quand il existe) ;
- baisse maximale du capital (instantanés d'equity) ;
- nombre de transactions (fills, allers-retours fermés) ;
- qualité des décisions : taux de réussite, espérance nette, gain moyen / perte moyenne, par stratégie ;
- fiabilité des prédictions : score de Brier des prédictions réalisées (table `predictions`) ;
- comparaison avec les références passives : 100 € conservés, achat passif de BTC-EUR sur la même période.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from ..storage.db import Database

ND = "non mesurable"
DAY = 86_400_000


def D(x: Any) -> Decimal:
    return Decimal(str(x)) if x not in (None, "") else Decimal(0)


@dataclass
class Trade:
    """Aller-retour FERMÉ (FIFO), frais d'entrée et de sortie inclus."""
    market: str
    strategy: str
    entry_ts: int
    exit_ts: int
    qty: float
    cost_eur: float       # prix d'achat x quantité + frais d'achat (part de la quantité)
    proceeds_eur: float   # prix de vente x quantité - frais de vente
    fees_eur: float
    gross_pnl_eur: float  # avant frais
    net_pnl_eur: float    # après frais

    @property
    def net_ret(self) -> float:
        return self.net_pnl_eur / self.cost_eur if self.cost_eur > 0 else 0.0

    @property
    def win(self) -> bool:
        return self.net_pnl_eur > 0


def round_trips(db: Database, mode: str = "simulation") -> list[Trade]:
    rows = db.q("SELECT f.ts, f.market, f.side, f.amount, f.price, f.fee_eur, i.strategy FROM fills f "
                "LEFT JOIN intents i ON i.id = f.intent_id WHERE f.mode=? ORDER BY f.ts, f.id", (mode,))
    lots: dict[str, deque[list[Any]]] = defaultdict(deque)  # market -> [qty restante, prix, frais/unité, ts, stratégie]
    out: list[Trade] = []
    for r in rows:
        qty, px, fee = float(D(r["amount"])), float(D(r["price"])), float(D(r["fee_eur"]))
        if qty <= 0:
            continue
        if r["side"] == "buy":
            lots[r["market"]].append([qty, px, fee / qty, int(r["ts"]), r["strategy"] or "inconnue"])
            continue
        remaining, sell_fee_u = qty, fee / qty
        q = lots[r["market"]]
        while remaining > 1e-15 and q:
            lot = q[0]
            take = min(lot[0], remaining)
            cost = take * lot[1] + take * lot[2]
            proceeds = take * px - take * sell_fee_u
            fees = take * lot[2] + take * sell_fee_u
            out.append(Trade(r["market"], lot[4], lot[3], int(r["ts"]), take, cost, proceeds, fees,
                             take * (px - lot[1]), proceeds - cost))
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-15:
                q.popleft()
    return out


def max_drawdown(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def strategy_quality(trades: list[Trade]) -> dict[str, Any]:
    by: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by[t.strategy].append(t)
    out = {}
    for s, ts in sorted(by.items()):
        wins = [t.net_ret for t in ts if t.win]
        losses = [t.net_ret for t in ts if not t.win]
        out[s] = {"allers_retours": len(ts), "taux_reussite": round(len(wins) / len(ts), 4),
                  "esperance_nette": round(sum(t.net_ret for t in ts) / len(ts), 6),
                  "gain_moyen": round(sum(wins) / len(wins), 6) if wins else None,
                  "perte_moyenne": round(sum(losses) / len(losses), 6) if losses else None,
                  "resultat_net_eur": round(sum(t.net_pnl_eur for t in ts), 4)}
    return out


def prediction_reliability(db: Database) -> dict[str, Any]:
    rows = db.q("SELECT model, value, realized FROM predictions WHERE realized IS NOT NULL")
    by: dict[str, list[float]] = defaultdict(list)
    skipped = 0
    for r in rows:
        try:
            p = float(json.loads(r["value"]).get("p_up") if r["value"].startswith("{") else r["value"])
            y = float(json.loads(r["realized"]).get("up") if r["realized"].startswith("{") else r["realized"])
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue
        if 0 <= p <= 1 and y in (0.0, 1.0):
            by[r["model"]].append((p - y) ** 2)
        else:
            skipped += 1
    if not by:
        return {"statut": ND, "raison": "aucune prédiction réalisée et exploitable dans le journal", "ignorees": skipped}
    return {"brier_par_modele": {m: {"n": len(v), "brier": round(sum(v) / len(v), 4)} for m, v in by.items()},
            "reference_hasard": 0.25, "ignorees": skipped}


def execution_costs(db: Database, mode: str) -> dict[str, Any]:
    """Écart prix exécuté / prix de référence de l'intention (limite) ; un ordre au marché sans prix de référence
    enregistré n'est pas mesurable (publié comme tel)."""
    rows = db.q("SELECT f.side, f.amount, f.price, i.price AS ref FROM fills f JOIN intents i ON i.id=f.intent_id "
                "WHERE f.mode=?", (mode,))
    total, n, nd = 0.0, 0, 0
    for r in rows:
        ref = float(D(r["ref"]))
        if ref <= 0:
            nd += 1
            continue
        qty, px = float(D(r["amount"])), float(D(r["price"]))
        total += (px - ref) * qty if r["side"] == "buy" else (ref - px) * qty  # > 0 : défavorable
        n += 1
    return {"cout_execution_eur": round(total, 4) if n else None, "fills_mesures": n, "fills_sans_reference": nd,
            "note": "positif = défavorable ; frais exclus (comptés à part)"}


def passive_btc(db: Database, start_ms: int, end_ms: int) -> float | None:
    a = db.one("SELECT close FROM candles WHERE market='BTC-EUR' AND ts>=? ORDER BY ts LIMIT 1", (start_ms,))
    b = db.one("SELECT close FROM candles WHERE market='BTC-EUR' AND ts<=? ORDER BY ts DESC LIMIT 1", (end_ms,))
    if not a or not b or float(D(a["close"])) <= 0:
        return None
    return float(D(b["close"])) / float(D(a["close"])) - 1


def bilan(db: Database, initial_eur: Decimal, hosting_eur_month: Decimal, now_ms: int,
          mode: str = "simulation") -> dict[str, Any]:
    snaps = db.q("SELECT ts, equity_eur FROM equity_snapshots WHERE mode=? ORDER BY ts", (mode,))
    fills = db.one("SELECT COUNT(*) n, COALESCE(SUM(CAST(fee_eur AS REAL)),0) fees FROM fills WHERE mode=?", (mode,))
    trades = round_trips(db, mode)
    n_fills, fees = (int(fills["n"]), float(fills["fees"])) if fills else (0, 0.0)
    out: dict[str, Any] = {"mode": mode, "capital_initial_eur": float(initial_eur), "fictif": mode == "simulation",
                           "nombre_fills": n_fills, "frais_cumules_eur": round(fees, 4),
                           "allers_retours_fermes": len(trades)}
    if not snaps:
        out["rendement"] = {"statut": ND, "raison": "aucun instantané d'equity enregistré"}
    else:
        start, last = int(snaps[0]["ts"]), float(D(snaps[-1]["equity_eur"]))
        equity = [float(D(r["equity_eur"])) for r in snaps]
        net = last / float(initial_eur) - 1 if initial_eur > 0 else None
        gross = (last + out["frais_cumules_eur"]) / float(initial_eur) - 1 if initial_eur > 0 else None
        months = max(0.0, (now_ms - start) / (30.4375 * DAY))
        hosting = float(hosting_eur_month) * months
        out["rendement"] = {
            "brut_avant_frais": round(gross, 6) if gross is not None else None,
            "net_apres_frais": round(net, 6) if net is not None else None,
            "net_apres_hebergement": round((last - hosting) / float(initial_eur) - 1, 6) if initial_eur > 0 else None,
            "cout_hebergement_eur": round(hosting, 4), "mois": round(months, 3),
            "baisse_maximale": max_drawdown(equity), "debut_ts": start, "dernier_ts": int(snaps[-1]["ts"]),
            "equity_eur": last,
        }
        btc = passive_btc(db, start, now_ms)
        out["references_passives"] = {"cash_100_eur": 0.0,
                                      "achat_passif_btc": round(btc, 6) if btc is not None else ND}
    out["qualite_decisions"] = strategy_quality(trades)
    out["couts_execution"] = execution_costs(db, mode)
    out["fiabilite_predictions"] = prediction_reliability(db)
    out["avertissement"] = ("capital FICTIF ; aucune performance passée ou simulée ne garantit un résultat futur ; "
                            "la rentabilité reste à démontrer sur la durée")
    return out


def trades_as_dicts(trades: list[Trade]) -> list[dict[str, Any]]:
    return [{**asdict(t), "net_ret": t.net_ret, "win": t.win} for t in trades]
