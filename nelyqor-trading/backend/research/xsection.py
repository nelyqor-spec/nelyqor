"""Moteur de recherche transversal : classe TOUS les marchés admissibles à chaque rééquilibrage et simule un
portefeuille réaliste (100 €, minimum 5 €/ordre, frais, spread estimé, glissement, latence d'une bougie).

Garanties anti-biais :
- éligibilité POINT-IN-TIME : un marché n'existe qu'à partir de sa première bougie réelle ; liquidité = médiane
  glissante du volume en devise de cotation calculée sur le PASSÉ ; ancienneté minimale de cotation ;
- toutes les caractéristiques à l'instant t n'utilisent que les bougies <= t ; exécution à l'ouverture t+1 ;
- aucun prix inventé : un marché sans bougie est inexécutable (position conservée, valorisée au dernier prix,
  décote si le marché disparaît définitivement) ;
- ordres limités : remplis UNIQUEMENT si une bougie ultérieure TRAVERSE le prix d'au moins `through_bps`
  (toucher ne suffit pas : file d'attente inconnue), et au plus `max_volume_share` du volume de la bougie.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..features import indicators as ind


@dataclass
class XSConfig:
    initial_eur: float = 100.0
    min_order_eur: float = 5.0
    top_n: int = 3
    buffer: int = 2  # hystérésis : une position n'est vendue que si elle sort du top (N + buffer)
    rebalance_bars: int = 6
    mom_lookback: int = 42
    trend_fast: int = 20
    trend_slow: int = 100
    vol_lookback: int = 42
    liq_lookback: int = 180
    min_liquidity_quote: float = 20_000.0  # volume médian PAR BOUGIE (devise de cotation)
    min_age_bars: int = 180
    regime_filter: bool = True  # BTC sous sa moyenne longue -> pas de nouvelle entrée
    regime_ema: int = 200
    min_score: float = 0.0
    weights: dict[str, float] = field(default_factory=lambda: {"mom": 1.0, "trend": 0.5, "volume": 0.25,
                                                               "vol": -0.25, "downside": -0.25})
    fee_taker: float = 0.0025
    fee_maker: float = 0.0015
    slippage_bps: float = 5.0
    spread_floor_bps: float = 5.0
    execution: str = "market"  # market | limit | hybrid
    limit_offset_bps: float = 5.0
    limit_ttl_bars: int = 2
    through_bps: float = 2.0
    max_volume_share: float = 0.05
    stop_atr: float = 0.0  # 0 = pas de stop de sécurité
    trail_atr: float = 0.0
    max_weight: float = 0.35
    reserve: float = 0.10
    risk_cut: float = 45.0  # au-delà, poids réduit (score de risque de volatilité)
    delisting_haircut: float = 0.5
    min_hold_bars: int = 0
    cost_hurdle_mult: float = 0.0  # entrée seulement si |momentum| > mult x coût aller-retour
    max_universe: int = 0  # 0 = tous ; sinon les N plus liquides À L'INSTANT t (point-in-time)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class XSResult:
    equity: pd.Series
    trades: list[dict[str, Any]]
    fees_eur: float
    turnover_eur: float
    exposure: float
    stats: dict[str, Any]


def corwin_schultz_spread(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Estimateur de spread de Corwin-Schultz (2012) à partir des seuls hauts/bas (causal, 2 bougies)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        hl = np.log(high / low) ** 2
        beta = hl + np.roll(hl, 1)
        gamma = np.log(np.maximum(high, np.roll(high, 1)) / np.minimum(low, np.roll(low, 1))) ** 2
        k = 3 - 2 * np.sqrt(2)
        alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
        s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    s[0] = np.nan
    return np.clip(np.nan_to_num(s, nan=0.0), 0, 0.05)


def build_panel(data: dict[str, pd.DataFrame], cfg: XSConfig) -> dict[str, pd.DataFrame]:
    """Matrices (temps x marché) des caractéristiques causales."""
    mk = sorted(data)
    close = pd.DataFrame({m: data[m]["close"] for m in mk})
    high = pd.DataFrame({m: data[m]["high"] for m in mk})
    low = pd.DataFrame({m: data[m]["low"] for m in mk})
    vol = pd.DataFrame({m: data[m]["volume"] for m in mk})
    qvol = vol * close
    logc = np.log(close)
    ret1 = logc.diff()
    mom = logc.diff(cfg.mom_lookback)
    mom_rel = mom.sub(mom.median(axis=1), axis=0)  # momentum RELATIF à l'univers du moment
    ema_f = close.ewm(span=cfg.trend_fast, adjust=False, min_periods=cfg.trend_fast).mean()
    ema_s = close.ewm(span=cfg.trend_slow, adjust=False, min_periods=cfg.trend_slow).mean()
    trend = (ema_f / ema_s - 1)
    rv = ret1.rolling(cfg.vol_lookback, min_periods=cfg.vol_lookback).std()
    downside = ret1.clip(upper=0).rolling(cfg.vol_lookback, min_periods=cfg.vol_lookback).std()
    rel_vol = qvol / qvol.shift(1).rolling(cfg.vol_lookback, min_periods=cfg.vol_lookback).mean()
    liq = qvol.shift(1).rolling(cfg.liq_lookback, min_periods=cfg.liq_lookback // 2).median()
    first = close.notna().cumsum()  # nb de bougies réelles depuis la cotation
    spread = pd.DataFrame({m: corwin_schultz_spread(high[m].to_numpy(float), low[m].to_numpy(float))
                           for m in mk}, index=close.index).rolling(30, min_periods=5).median()
    atr = pd.DataFrame({m: ind.atr(high[m].to_numpy(float), low[m].to_numpy(float), close[m].to_numpy(float))
                        for m in mk}, index=close.index)

    def z(df: pd.DataFrame) -> pd.DataFrame:
        """z-score transversal ; une caractéristique sans dispersion à l'instant t vaut 0 (neutre) au lieu de NaN
        (NaN rendait TOUS les scores invalides — défaut détecté par les tests)."""
        df = df.replace([np.inf, -np.inf], np.nan)
        mu, sd = df.mean(axis=1), df.std(axis=1).replace(0, np.nan)
        zz = df.sub(mu, axis=0).div(sd, axis=0).clip(-3, 3)
        return zz.where(~(sd.isna().to_numpy()[:, None] & df.notna().to_numpy()), 0.0)

    w = cfg.weights
    score = (w.get("mom", 0) * z(mom_rel) + w.get("trend", 0) * z(trend) + w.get("volume", 0) * z(np.log(rel_vol))
             + w.get("vol", 0) * z(rv) + w.get("downside", 0) * z(downside))
    return {"close": close, "open": pd.DataFrame({m: data[m]["open"] for m in mk}), "high": high, "low": low,
            "volume": vol, "qvol": qvol, "score": score, "mom": mom, "trend": trend, "rv": rv, "liq": liq,
            "age": first, "spread": spread, "atr": atr}


def eligible(panel: dict[str, pd.DataFrame], cfg: XSConfig, i: int) -> pd.Series:
    row = panel["close"].iloc[i]
    ok = row.notna() & (panel["age"].iloc[i] >= cfg.min_age_bars) & (panel["liq"].iloc[i] >= cfg.min_liquidity_quote)
    ok &= panel["score"].iloc[i].notna() & panel["trend"].iloc[i].notna()
    if cfg.max_universe and ok.sum() > cfg.max_universe:
        liq = panel["liq"].iloc[i].where(ok)
        keep = liq.nlargest(cfg.max_universe).index
        ok = ok & ok.index.isin(keep)
    return ok


def risk_score_vol(rv_bar: float, bars_per_day: float) -> float:
    """Score 0-100 de risque (volatilité journalière 1 % -> 0, 12 % -> 100). Pas une probabilité de perte."""
    daily = rv_bar * np.sqrt(bars_per_day)
    return float(np.clip((daily - 0.01) / 0.11, 0, 1) * 100)


def run_xsection(data: dict[str, pd.DataFrame], cfg: XSConfig, start: int = 0, end: int | None = None,
                 regime_market: str = "BTC-EUR", panel: dict[str, pd.DataFrame] | None = None) -> XSResult:
    P = panel or build_panel(data, cfg)
    idx = P["close"].index
    n = len(idx) if end is None else end
    mk = list(P["close"].columns)
    step_ms = float(np.median(np.diff(np.asarray(idx, dtype=np.int64))))
    bars_per_day = 86_400_000 / step_ms
    O, H, L, C, V = (P[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))  # noqa: E741
    mark = P["close"].ffill().to_numpy(float)
    last_valid = [int(np.flatnonzero(~np.isnan(C[:, j]))[-1]) if np.isfinite(C[:, j]).any() else -1 for j in range(len(mk))]
    spread = P["spread"].to_numpy(float)
    atr = P["atr"].to_numpy(float)
    reg = None
    if cfg.regime_filter and regime_market in P["close"]:
        rc = P["close"][regime_market]
        reg = (rc > rc.ewm(span=cfg.regime_ema, adjust=False, min_periods=cfg.regime_ema).mean()).to_numpy()
    cash = cfg.initial_eur
    pos: dict[int, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []  # ordres limités en attente
    trades: list[dict[str, Any]] = []
    fees = turnover = 0.0
    fees_saved = 0.0
    missed = filled_limits = fallback_market = 0
    eq = np.full(n - start, np.nan)
    exposed = 0
    slip = cfg.slippage_bps / 1e4

    def half_spread(j: int, i: int) -> float:
        return max(cfg.spread_floor_bps / 1e4, float(np.nan_to_num(spread[i, j]))) / 2

    def buy(j: int, i: int, eur: float, px: float, maker: bool, reason: str) -> None:
        nonlocal cash, fees, turnover, fees_saved
        fee_r = cfg.fee_maker if maker else cfg.fee_taker
        cost = eur * (1 + fee_r)
        if cost > cash:
            eur = cash / (1 + fee_r)
            cost = cash
        if eur < cfg.min_order_eur:
            return
        q = eur / px
        cash -= cost
        fees += eur * fee_r
        fees_saved += eur * (cfg.fee_taker - fee_r)
        turnover += eur
        p = pos.setdefault(j, {"q": 0.0, "cost": 0.0, "entry_i": i, "peak": px, "entry_px": px})
        p["q"] += q
        p["cost"] += cost

    def sell(j: int, i: int, px: float, maker: bool, reason: str, frac: float = 1.0) -> None:
        nonlocal cash, fees, turnover, fees_saved
        p = pos[j]
        q = p["q"] * frac
        gross = q * px
        if frac < 1.0 and (gross < cfg.min_order_eur or (p["q"] - q) * px < cfg.min_order_eur):
            frac, q, gross = 1.0, p["q"], p["q"] * px  # jamais de reliquat invendable (minimum Bitvavo)
        fee_r = cfg.fee_maker if maker else cfg.fee_taker
        cash += gross * (1 - fee_r)
        fees += gross * fee_r
        fees_saved += gross * (cfg.fee_taker - fee_r)
        turnover += gross
        cost_share = p["cost"] * (q / p["q"])
        trades.append({"market": mk[j], "entry_i": p["entry_i"], "exit_i": i, "pnl": gross * (1 - fee_r) - cost_share,
                       "ret": gross * (1 - fee_r) / cost_share - 1 if cost_share else 0.0, "reason": reason})
        p["q"] -= q
        p["cost"] -= cost_share
        if frac >= 1.0 or p["q"] * px < 1e-9:
            del pos[j]

    for i in range(start, n):
        # 0) ordres au marché planifiés à l'ouverture de i (décidés à la clôture de i-1)
        mkt_now = [od for od in pending if od.get("market_at") == i]
        pending = [od for od in pending if od.get("market_at") != i]
        for od in mkt_now:
            j = od["j"]
            if np.isnan(O[i, j]):
                if i + 1 < n:
                    pending.append({**od, "market_at": i + 1})  # trou : reporté, jamais exécuté à un prix inventé
                continue
            if od["side"] == "buy":
                buy(j, i, od["eur"], O[i, j] * (1 + slip + half_spread(j, i - 1)), False, od["reason"])
            elif j in pos:
                sell(j, i, O[i, j] * (1 - slip - half_spread(j, i - 1)), False, od["reason"])
        # 1) ordres limités en attente : remplis seulement si la bougie i TRAVERSE le prix
        still = []
        for od in pending:
            j = od["j"]
            if od.get("market_at") is not None:
                still.append(od)
                continue
            if np.isnan(L[i, j]):
                still.append(od)
                continue
            if od["side"] == "buy":
                through = L[i, j] < od["px"] * (1 - cfg.through_bps / 1e4)
                cap = V[i, j] * od["px"] * cfg.max_volume_share
                if through and cap >= cfg.min_order_eur:
                    buy(j, i, min(od["eur"], cap), od["px"], maker=True, reason="limite")
                    filled_limits += 1
                    continue
            else:
                through = H[i, j] > od["px"] * (1 + cfg.through_bps / 1e4)
                if through and j in pos:
                    sell(j, i, od["px"], maker=True, reason=od["reason"])
                    filled_limits += 1
                    continue
            od["ttl"] -= 1
            if od["ttl"] <= 0:
                if (cfg.execution == "hybrid" or od["side"] == "sell") and i + 1 < n:
                    # hybride : repli au marché ; une SORTIE n'est jamais abandonnée (repli marché)
                    fallback_market += 1
                    still.append({**od, "market_at": i + 1, "reason": od["reason"] + "_repli_marche"})
                else:
                    missed += 1  # occasion manquée : comptée, jamais supposée remplie
                continue
            still.append(od)
        pending = still
        # 2) protections intrabougie (stop / trailing) — hypothèse prudente : le stop passe avant tout gain
        for j in list(pos):
            p = pos[j]
            if i > last_valid[j]:
                sell(j, i, mark[last_valid[j], j] * (1 - cfg.delisting_haircut), False, "radiation")
                continue
            if np.isnan(L[i, j]):
                continue
            p["peak"] = max(p["peak"], C[i - 1, j] if i > 0 and not np.isnan(C[i - 1, j]) else p["peak"])
            levels = []
            if cfg.stop_atr > 0 and not np.isnan(atr[p["entry_i"], j]):
                levels.append(p["entry_px"] - cfg.stop_atr * atr[p["entry_i"], j])
            if cfg.trail_atr > 0 and i > 0 and not np.isnan(atr[i - 1, j]):
                levels.append(p["peak"] - cfg.trail_atr * atr[i - 1, j])
            if levels:
                lvl = max(levels)
                if L[i, j] <= lvl:
                    sell(j, i, min(lvl, O[i, j]) * (1 - slip - half_spread(j, i)), False, "stop")
        # 3) rééquilibrage à la clôture de i, exécution à partir de i+1
        if (i - start) % cfg.rebalance_bars == 0 and i + 1 < n:
            ok = eligible(P, cfg, i)
            sc = P["score"].iloc[i].where(ok).dropna().sort_values(ascending=False)
            rank = {mk.index(m): r for r, m in enumerate(sc.index)}
            risk_on = True if reg is None else bool(reg[i])
            # sorties : hors du top N+buffer ou plus admissible
            for j in list(pos):
                held = i - pos[j]["entry_i"]
                if held < cfg.min_hold_bars or any(od["j"] == j and od["side"] == "sell" for od in pending):
                    continue  # pas de seconde vente tant qu'une vente est en attente (anti-doublon)
                if rank.get(j, 10**9) >= cfg.top_n + cfg.buffer:
                    if cfg.execution in ("limit", "hybrid"):
                        pending.append({"j": j, "side": "sell", "px": C[i, j] * (1 + cfg.limit_offset_bps / 1e4),
                                        "ttl": cfg.limit_ttl_bars, "reason": "sortie_classement"})
                    else:
                        pending.append({"j": j, "side": "sell", "market_at": i + 1, "reason": "sortie_classement"})
            # entrées : meilleurs scores, si le régime le permet
            if risk_on:
                equity = cash + sum(p["q"] * mark[i, j] for j, p in pos.items())
                budget_total = equity * (1 - cfg.reserve)
                target = budget_total / cfg.top_n
                for m in sc.index[: cfg.top_n]:
                    j = mk.index(m)
                    if j in pos or any(od["j"] == j and od["side"] == "buy" for od in pending):
                        continue
                    if sc[m] <= cfg.min_score:
                        continue
                    rt_cost = 2 * (cfg.fee_taker + slip + half_spread(j, i))
                    mom_v = float(P["mom"].iloc[i, j])
                    if cfg.cost_hurdle_mult > 0 and not (mom_v > cfg.cost_hurdle_mult * rt_cost):
                        continue
                    rs = risk_score_vol(float(P["rv"].iloc[i, j]), bars_per_day)
                    w = min(cfg.max_weight * equity, target)
                    if rs > cfg.risk_cut:  # réduction au-delà de 45/100, sans exclusion de principe
                        w *= max(0.0, 1 - (rs - cfg.risk_cut) / (100 - cfg.risk_cut)) ** 2
                    committed = sum(od.get("eur", 0.0) for od in pending if od["side"] == "buy")
                    eur = min(w, (cash - committed) / (1 + cfg.fee_taker))
                    if eur < cfg.min_order_eur:
                        continue
                    if cfg.execution in ("limit", "hybrid"):
                        pending.append({"j": j, "side": "buy", "px": C[i, j] * (1 - cfg.limit_offset_bps / 1e4),
                                        "ttl": cfg.limit_ttl_bars, "eur": eur, "reason": "entree"})
                    else:
                        pending.append({"j": j, "side": "buy", "market_at": i + 1, "eur": eur, "reason": "entree"})
        if pos:
            exposed += 1
        eq[i - start] = cash + sum(p["q"] * mark[i, j] for j, p in pos.items())
    for j in list(pos):
        sell(j, n - 1, mark[n - 1, j] * (1 - slip - half_spread(j, n - 1)), False, "fin")
    eq[-1] = cash
    stats = {"limit_remplis": filled_limits, "limit_manques": missed, "repli_marche": fallback_market,
             "frais_economises_eur": float(fees_saved)}
    return XSResult(pd.Series(eq, index=idx[start:n]), trades, fees, turnover, exposed / max(1, n - start), stats)


def xs_metrics(r: XSResult, ppy: float, hosting_month: float = 0.0, initial: float = 100.0) -> dict[str, Any]:
    from .metrics import block_bootstrap_ci

    eq = r.equity.dropna()
    rets = eq.pct_change().dropna()
    pnl = np.array([t["pnl"] for t in r.trades])
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    years = len(eq) / ppy if ppy else 0.0
    out = {
        "rendement_net": float(eq.iloc[-1] / initial - 1), "trades": len(r.trades),
        "profit_factor": float(gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0),
        "max_drawdown": float((eq / eq.cummax() - 1).min()),
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0.0,
        "sortino": float(rets.mean() / rets[rets < 0].std() * np.sqrt(ppy)) if (rets < 0).sum() > 1 else 0.0,
        "frais_eur": r.fees_eur, "rotation_eur": r.turnover_eur, "rotation_x_capital_an": r.turnover_eur / initial / max(years, 1e-9),
        "exposition": r.exposure, "annees": years, **r.stats,
        "ic95_rendement": block_bootstrap_ci(rets.to_numpy(), lambda s: float(np.prod(1 + s) - 1),
                                             block=max(10, int(ppy / 52))) if len(rets) > 100 else (float("nan"),) * 2,
    }
    out["economique"] = {str(h): out["rendement_net"] - h * 12 * years / initial for h in (0, 3, 5, 7.19, 10)}
    return out
