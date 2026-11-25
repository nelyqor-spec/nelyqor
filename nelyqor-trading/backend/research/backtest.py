"""Backtest événementiel sur bougies, hypothèses conservatrices.

- Signal calculé à la clôture de t ; exécution à l'ouverture de t + latency_bars (jamais au prix de clôture vu).
- Achat : prix = ouverture * (1 + slippage) ; vente : ouverture * (1 - slippage) ; frais taker des deux côtés.
- Stops : si le plus bas touche le stop -> sortie à min(stop, ouverture) * (1 - slippage) (gap traversant).
- Ordre intrabougie inconnu (OHLC) : si stop ET objectif sont touchés dans la même bougie, on suppose le STOP.
- Trailing stop mis à jour uniquement avec les bougies clôturées.
- Minimum d'ordre et arrondi vers le bas ; aucune mise minimale forcée.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..features.indicators import atr
from ..portfolio.risk import position_cap
from ..strategies.base import Strategy


@dataclass
class BTConfig:
    initial_eur: float = 100.0
    fee_taker: float = 0.0025
    slippage_bps: float = 10.0
    latency_bars: int = 1
    min_order_eur: float = 5.0
    atr_stop_mult: float = 2.5
    atr_trail_mult: float = 3.0
    take_profit_partial_r: float = 2.0
    partial_fraction: float = 0.5
    max_positions: int = 4
    reserve_fraction: float = 0.10
    risk_score: float = 40.0  # score de risque fixe en backtest (pas de données de carnet historiques)
    min_hold_bars: int = 3
    cooldown_bars: int = 6
    fee_multiplier: float = 1.0
    spread_extra_bps: float = 0.0


@dataclass
class Trade:
    market: str
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    qty: float
    cost_eur: float
    proceeds_eur: float
    fees_eur: float
    reason: str
    bars: int

    @property
    def ret(self) -> float:
        return self.proceeds_eur / self.cost_eur - 1 if self.cost_eur else 0.0


@dataclass
class BTResult:
    strategy: str
    equity: pd.Series
    trades: list[Trade]
    fees_eur: float
    turnover_eur: float
    exposure: float
    config: BTConfig
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Pos:
    qty: float
    entry_px: float
    cost: float
    stop: float
    r_unit: float
    entry_i: int
    entry_ts: int
    fees: float
    partial_done: bool = False
    proceeds: float = 0.0
    peak_close: float = 0.0


def run_backtest(data: dict[str, pd.DataFrame], strategy: Strategy, cfg: BTConfig | None = None,
                 trade_start: int = 0) -> BTResult:
    """`trade_start` : aucune entrée avant cet indice (bougies antérieures = chauffe des indicateurs)."""
    cfg = cfg or BTConfig()
    fee = cfg.fee_taker * cfg.fee_multiplier
    slip = (cfg.slippage_bps + cfg.spread_extra_bps / 2) / 1e4
    markets = sorted(data)
    idx = data[markets[0]].index
    for m in markets:
        if not data[m].index.equals(idx):
            raise ValueError("toutes les séries doivent être alignées sur le même index")
    sig = {m: strategy.signals(data[m]) for m in markets}
    arr = {m: {k: data[m][k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")} for m in markets}
    atrs = {m: atr(arr[m]["high"], arr[m]["low"], arr[m]["close"]) for m in markets}
    cash = cfg.initial_eur
    pos: dict[str, _Pos] = {}
    trades: list[Trade] = []
    last_exit: dict[str, int] = {}
    fees_tot = turnover = 0.0
    eq = np.zeros(len(idx))
    exposed_bars = 0
    pending_entries: dict[int, list[str]] = {}
    pending_exits: dict[int, list[tuple[str, str]]] = {}
    L = cfg.latency_bars

    def close_pos(m: str, i: int, px: float, reason: str, frac: float = 1.0) -> None:
        nonlocal cash, fees_tot, turnover
        p = pos[m]
        q = p.qty * frac
        gross = q * px
        f = gross * fee
        cash += gross - f
        fees_tot += f
        turnover += gross
        p.proceeds += gross - f
        p.fees += f
        if frac >= 1.0:
            cost_share = p.cost
            trades.append(Trade(m, p.entry_ts, int(idx[i]), p.entry_px, px, q, cost_share, p.proceeds, p.fees, reason,
                                i - p.entry_i))
            del pos[m]
            last_exit[m] = i
        else:
            p.qty -= q

    for i in range(len(idx)):
        # 1) exécutions planifiées à l'ouverture de i
        for m, reason in pending_exits.pop(i, []):
            if m in pos:
                close_pos(m, i, arr[m]["open"][i] * (1 - slip), reason)
        for m in pending_entries.pop(i, []):
            if m in pos or len(pos) >= cfg.max_positions:
                continue
            o = arr[m]["open"][i]
            if np.isnan(o) or np.isnan(atrs[m][i - L]):
                continue
            equity = cash + sum(p.qty * arr[k]["open"][i] for k, p in pos.items())
            strength = float(sig[m].strength[i - L])
            cap = float(position_cap(_dec(equity), cfg.risk_score, 30 + 70 * strength))
            budget = min(cap, cash - equity * cfg.reserve_fraction) / (1 + fee)
            if budget < cfg.min_order_eur:
                continue
            px = o * (1 + slip)
            q = budget / px
            f = budget * fee
            cash -= budget + f
            fees_tot += f
            turnover += budget
            a = atrs[m][i - L]
            stop = px - cfg.atr_stop_mult * a
            pos[m] = _Pos(q, px, budget + f, stop, cfg.atr_stop_mult * a, i, int(idx[i]), f, peak_close=px)
        # 2) stops / objectifs intrabougie (conservateur)
        for m in list(pos):
            p = pos[m]
            if i == p.entry_i:
                # bougie d'entrée : on ne sait pas si le plus bas est avant ou après l'entrée -> stop seul, prudence
                if arr[m]["low"][i] <= p.stop:
                    close_pos(m, i, p.stop * (1 - slip), "stop")
                continue
            o, h, lo = arr[m]["open"][i], arr[m]["high"][i], arr[m]["low"][i]
            tp = p.entry_px + cfg.take_profit_partial_r * p.r_unit
            if lo <= p.stop:
                close_pos(m, i, min(p.stop, o) * (1 - slip), "stop")
                continue
            if not p.partial_done and h >= tp:
                close_pos(m, i, max(tp, o) * (1 - slip), "objectif_partiel", cfg.partial_fraction)
                p.partial_done = True
        # 3) fin de bougie : trailing, signaux (exécutés à i+L)
        for m in markets:
            c = arr[m]["close"][i]
            if m in pos:
                p = pos[m]
                p.peak_close = max(p.peak_close, c)
                p.stop = max(p.stop, p.peak_close - cfg.atr_trail_mult * atrs[m][i])
                held = i - p.entry_i
                if held >= cfg.min_hold_bars and sig[m].exit[i]:
                    pending_exits.setdefault(i + L, []).append((m, "invalidation"))
                elif held >= sig[m].max_bars:
                    pending_exits.setdefault(i + L, []).append((m, "duree_max"))
            elif i + L >= trade_start and sig[m].entry[i] and i - last_exit.get(m, -10**9) >= cfg.cooldown_bars:
                pending_entries.setdefault(i + L, []).append(m)
        if pos:
            exposed_bars += 1
        eq[i] = cash + sum(p.qty * arr[k]["close"][i] for k, p in pos.items())
    # positions ouvertes en fin de test : valorisées à la clôture moins frais et glissement de sortie
    last = len(idx) - 1
    for m in list(pos):
        close_pos(m, last, arr[m]["close"][last] * (1 - slip), "fin_de_test")
    eq[last] = cash
    return BTResult(strategy.name, pd.Series(eq, index=idx), trades, fees_tot, turnover, exposed_bars / len(idx), cfg)


def _dec(x: float) -> Any:
    from decimal import Decimal

    return Decimal(repr(float(x)))


def buy_and_hold(df: pd.DataFrame, initial: float = 100.0, fee: float = 0.0025, slippage_bps: float = 10) -> pd.Series:
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    px = o[0] * (1 + slippage_bps / 1e4)
    q = initial / (1 + fee) / px
    eq = q * c
    eq[-1] = q * c[-1] * (1 - slippage_bps / 1e4) * (1 - fee)
    return pd.Series(eq, index=df.index)


def cash_benchmark(index: pd.Index, initial: float = 100.0) -> pd.Series:
    return pd.Series(np.full(len(index), initial), index=index)
