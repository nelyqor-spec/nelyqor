"""QUANT : transforme signaux + contexte en intentions. Ne signe rien, ne contourne pas GUARDIAN.

Pour chaque marché éligible : attendre est l'option par défaut. Un achat n'est proposé que si
l'avantage net estimé (après frais, spread, probabilité de non-exécution) reste positif avec marge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from ..core.config import HardLimits
from ..core.num import ZERO, D
from ..features import indicators as ind
from ..market_data.models import MarketInfo
from ..market_data.universe import MarketState, spread_bps
from ..models.regime import Regime, classify_regime
from ..strategies.base import Strategy
from .actions import best_action, compare_entry
from .risk import (
    RiskInputs,
    breakeven_move,
    normalize_liquidity,
    normalize_volatility,
    position_cap,
    risk_score,
)
from .sizing import size_buy


@dataclass
class Proposal:
    market: str
    action: str  # acheter | vendre | reduire | attendre | conserver
    strategy: str
    amount_quote: Decimal = ZERO
    amount_base: Decimal = ZERO
    order_type: str = "market"
    limit_price: Decimal | None = None
    post_only: bool = False
    risk: float = 0.0
    confidence: float = 0.0
    edge: float = 0.0
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HeldPosition:
    market: str
    qty: Decimal
    avg_cost: Decimal
    entry_ts: int
    strategy: str
    stop: Decimal
    bars_held: int = 0


def forward_outcomes(df: pd.DataFrame, entry: np.ndarray, horizon: int) -> np.ndarray:
    """Rendements bruts réalisés après les signaux PASSÉS de la règle : achat à l'ouverture t+1, revente à la
    clôture t+1+horizon. Seuls des signaux NON CHEVAUCHANTS sont retenus (sinon les observations sont
    dépendantes et l'incertitude est sous-estimée — défaut de la version initiale)."""
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    last = len(c) - horizon - 2
    out = []
    nxt = 0
    for i in np.flatnonzero(entry[: max(0, last + 1)]):
        if i < nxt:
            continue
        out.append(c[i + 1 + horizon] / o[i + 1] - 1)
        nxt = int(i) + horizon + 1
    return np.asarray(out, dtype=float)


def expected_edge(df: pd.DataFrame, signals_entry: np.ndarray, horizon: int) -> tuple[float, float, int]:
    """Compatibilité : (moyenne, écart-type, n) des issues passées non chevauchantes."""
    r = forward_outcomes(df, signals_entry, horizon)
    if len(r) < 5:
        return 0.0, 1.0, int(len(r))
    return float(np.mean(r)), float(np.std(r, ddof=1)), int(len(r))


def choose_fraction(outcomes_net: np.ndarray, cap_frac: float, lam: float = 0.1, gamma: float = 0.5,
                    uncertainty: float = 0.0, kelly_fraction: float = 0.25, shrink_k: float = 30.0,
                    grid: tuple[float, ...] = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)) -> dict[str, float]:
    """Fraction de l'equity à engager (0 = rester en euros).
    - Scénarios = issues passées NETTES de coûts, moyenne rétrécie vers 0 (n/(n+k)) + scénarios de krach.
    - J(f) = moyenne(log(1 + f r)) - lambda * CVaR95(-f r) - gamma * incertitude * f ; J(0) = 0 (euros).
    - Kelly fractionné borné sur la moyenne rétrécie ; on retient min(argmax J, Kelly, plafond).
    Aucune probabilité de succès n'est produite ni affichée.
    lambda/gamma : hyperparamètres de départ (0,1 / 0,5), à calibrer sur données d'ENTRAÎNEMENT uniquement.
    Avec lambda = 1, la pénalité CVaR (krach -35 % inclus) dominait toujours : jamais d'investissement."""
    from .risk import cvar, fractional_kelly

    n = len(outcomes_net)
    if n < 20 or cap_frac <= 0:
        return {"f": 0.0, "f_J": 0.0, "f_kelly": 0.0, "J": 0.0, "n": float(n), "mean_shrunk": 0.0}
    mu = float(np.mean(outcomes_net))
    shrink = n / (n + shrink_k)
    scen = (outcomes_net - mu) + mu * shrink  # même dispersion, espérance rétrécie
    scen = np.concatenate([scen, [np.expm1(np.log1p(-0.35)), -0.10]])  # stress : krach -35 %, -10 %
    best_f, best_j = 0.0, 0.0
    for g in grid:
        f = g * cap_frac
        if f <= 0:
            continue
        rp = f * scen
        if np.any(1 + rp <= 0):
            continue
        j = float(np.mean(np.log1p(rp)) - lam * cvar(-rp) - gamma * uncertainty * f)
        if j > best_j:
            best_f, best_j = f, j
    f_k = fractional_kelly(float(np.mean(scen)), float(np.var(scen, ddof=1)), kelly_fraction, cap_frac)
    return {"f": float(min(best_f, f_k)), "f_J": best_f, "f_kelly": f_k, "J": best_j, "n": float(n),
            "mean_shrunk": float(np.mean(scen))}


def correlation_haircut(rets: pd.Series, held_returns: dict[str, Any] | None, window: int = 500) -> tuple[float, float]:
    """Réduction de taille si le candidat est fortement corrélé à des positions déjà détenues.
    Renvoie (facteur multiplicatif dans [0.5, 1], corrélation max)."""
    if not held_returns:
        return 1.0, 0.0
    cmax = 0.0
    a = rets.tail(window)
    for r in held_returns.values():
        b = r.reindex(a.index)
        ok = a.notna() & b.notna()
        if ok.sum() > 50:
            cmax = max(cmax, float(np.corrcoef(a[ok], b[ok])[0, 1]))
    return (1.0 if cmax <= 0.5 else max(0.5, 1 - (cmax - 0.5))), cmax


class DecisionEngine:
    def __init__(self, strategies: list[Strategy], limits: HardLimits, fee_taker: Decimal, fee_maker: Decimal,
                 risk_weights: dict[str, float] | None = None, uncertainty_k: float = 2.0) -> None:
        self.strategies = strategies
        self.limits = limits
        self.fee_taker, self.fee_maker = fee_taker, fee_maker
        self.risk_weights = risk_weights
        self.k = uncertainty_k

    def evaluate_entry(self, m: MarketInfo, st: MarketState, df: pd.DataFrame, equity: Decimal,
                       available: Decimal, exposure: Decimal, held: HeldPosition | None,
                       depth_quote: Decimal, held_returns: dict[str, Any] | None = None) -> list[Proposal]:
        out: list[Proposal] = []
        if len(df) < 250:
            return [Proposal(m.market, "attendre", "-", reasons=["historique insuffisant (<250 bougies)"])]
        c = df["close"].to_numpy(dtype=float)
        reg = classify_regime(df)[-1]
        daily_vol = float(np.nanstd(np.diff(np.log(c[-24 * 14:]))) * np.sqrt(24))
        rets = df["close"].pct_change()
        corr_factor, corr_max = correlation_haircut(rets, held_returns)
        spread = spread_bps(st.best_bid, st.best_ask)
        spread_frac = (spread or D(100)) / D(10000)
        rt_cost = float(breakeven_move(self.fee_taker, self.fee_taker, spread_frac / 2))  # coût aller-retour
        for s in self.strategies:
            sig = s.signals(df)
            if not sig.entry[-1]:
                continue
            horizon = min(24, sig.max_bars)
            gross = forward_outcomes(df, sig.entry, horizon)
            net = gross - rt_cost  # coûts complets déduits AVANT toute estimation
            n = len(net)
            mean = float(np.mean(net)) if n else 0.0
            sd = float(np.std(net, ddof=1)) if n > 1 else 1.0
            se = sd / np.sqrt(max(n, 1))
            edge_low = mean - self.k * se  # borne basse prudente de l'espérance nette
            uncertainty = float(np.clip(se / max(abs(mean), 1e-4), 0, 1)) if n >= 5 else 1.0
            ri = RiskInputs(volatility=normalize_volatility(daily_vol),
                            liquidity=normalize_liquidity(float(depth_quote), float(available) * 0.3 or 1.0),
                            bearish=1.0 if reg == Regime.BEARISH.value else (0.5 if reg == Regime.VOLATILE.value else 0.0),
                            anomaly=float(st.anomaly_score), uncertainty=uncertainty)
            rs = risk_score(ri, self.risk_weights)
            conf = float(100 * np.clip(sig.strength[-1], 0, 1) * (1 - uncertainty * 0.5))
            p = Proposal(m.market, "attendre", s.name, risk=rs, confidence=conf, edge=edge_low,
                         details={"regime": str(reg), "esperance_nette": mean, "ecart_type": sd,
                                  "n_signaux_independants": n, "cout_aller_retour": rt_cost,
                                  "spread_bps": float(spread) if spread is not None else None,
                                  "correlation_max_positions": corr_max, "label": "estimation non calibrée"})
            if n < 20:
                p.reasons.append(f"seulement {n} signaux passés indépendants (< 20) : pas d'estimation fiable")
                out.append(p)
                continue
            if edge_low <= 0:
                p.reasons.append(f"espérance nette prudente {edge_low:.4f} <= 0 après frais, spread et incertitude")
                out.append(p)
                continue
            if held is not None:
                losing = st.last_price is not None and st.last_price < held.avg_cost
                if losing:
                    p.reasons.append("renforcement d'une position perdante : refusé sans thèse indépendante validée")
                    out.append(p)
                    continue
            cap_eur = position_cap(equity, rs, conf)
            cap_frac = float(cap_eur / equity) if equity > 0 else 0.0
            ch = choose_fraction(net, cap_frac, uncertainty=uncertainty)
            f = ch["f"] * corr_factor
            p.details.update({"fraction_J": ch["f_J"], "fraction_kelly": ch["f_kelly"], "J": ch["J"],
                              "facteur_correlation": corr_factor, "fraction_retenue": f})
            if f <= 0:
                p.reasons.append("l'objectif log-croissance/CVaR préfère rester en euros (J(f) <= 0)")
                out.append(p)
                continue
            atr_v = float(ind.atr(df["high"].to_numpy(float), df["low"].to_numpy(float), c)[-1])
            stress = D(str(min(0.5, 3 * atr_v / c[-1] + 0.1)))
            sz = size_buy(equity=equity, available_quote=available, current_exposure=exposure,
                          position_value=(held.qty * (st.last_price or ZERO)) if held else ZERO, risk=rs,
                          confidence=conf, limits=self.limits, market=m, fee_rate=self.fee_taker,
                          depth_quote=depth_quote, stress_loss_frac=stress, max_stress_loss_eur=equity * D("0.05"),
                          optimizer_target=equity * D(str(round(f, 6))))
            p.reasons += sz.reasons
            p.details["contrainte_liante"] = sz.binding
            if sz.amount_quote <= 0:
                out.append(p)
                continue
            # Comparaison systématique : marché / limité / attendre / rester en euros (espérances nettes historiques)
            acts = compare_entry(df, sig.entry, horizon, float(self.fee_taker), float(self.fee_maker),
                                 half_spread=float(spread_frac) / 2, slip=0.001, limit_offset=0.001, ttl=2)
            choice = best_action([a for a in acts if a.action != "acheter_limite" or m.supports("limit")])
            p.details["comparaison_actions"] = {a.action: {"esperance": a.esperance, "borne_basse": a.borne_basse,
                                                           **a.details} for a in acts}
            if choice.action.startswith("attendre"):
                p.reasons.append(f"comparateur : {choice.action} (meilleure borne basse)")
                out.append(p)
                continue
            p.action, p.amount_quote = "acheter", sz.amount_quote
            p.details["stop"] = float(c[-1] - 2.5 * atr_v)
            if choice.action == "acheter_limite" and st.best_bid:
                lim = min(st.best_bid, D(str(c[-1] * 0.999)))
                p.order_type, p.limit_price, p.post_only = "limit", m.round_price(lim), True
            out.append(p)
        if not out:
            out.append(Proposal(m.market, "attendre", "-", reasons=["aucun signal d'entrée"]))
        return out

    def evaluate_exit(self, m: MarketInfo, st: MarketState, df: pd.DataFrame, pos: HeldPosition,
                      min_hold_bars: int = 3) -> Proposal:
        strat = next((s for s in self.strategies if s.name == pos.strategy), None)
        price = st.best_bid or st.last_price
        if price is None:
            return Proposal(m.market, "conserver", pos.strategy, reasons=["prix indisponible : pas de décision"])
        value = pos.qty * price
        if value < m.min_order_quote or pos.qty < m.min_order_base:
            return Proposal(m.market, "conserver", pos.strategy, reasons=["poussière sous les minimums : invendable"])
        if price <= pos.stop:
            return Proposal(m.market, "vendre", pos.strategy, amount_base=m.round_amount(pos.qty),
                            reasons=[f"stop atteint ({pos.stop})"])
        if strat is not None and pos.bars_held >= min_hold_bars:
            sig = strat.signals(df)
            if sig.exit[-1]:
                return Proposal(m.market, "vendre", pos.strategy, amount_base=m.round_amount(pos.qty),
                                reasons=["invalidation de la thèse"])
            if pos.bars_held >= sig.max_bars:
                return Proposal(m.market, "vendre", pos.strategy, amount_base=m.round_amount(pos.qty),
                                reasons=["durée maximale de la stratégie"])
        return Proposal(m.market, "conserver", pos.strategy, reasons=["thèse intacte"])
