"""Moteur transversal v2 (phase 4) — extension SÉPARÉE de `xsection.py`.

`xsection.py` fait partie de l'empreinte des candidats prospectifs figés le 25/11/2026 (commit 9003293) : il ne
doit plus être modifié. Ce module réutilise ses briques (panneau causal, éligibilité, spread) et ajoute :

- des MODÈLES D'EXÉCUTION de décomposition (marché, marché avec frais maker, sans spread, au prix de clôture,
  limite idéale, limite réaliste, hybride) pour attribuer l'écart marché/limite ;
- un JOURNAL D'ORDRES (décision, prix, remplissage, rendement ultérieur) pour mesurer les transactions évitées,
  la sélection adverse et rejouer les ordres sur des transactions réelles (simulateur `lob_sim`) ;
- un score et une EXPOSITION fournis de l'extérieur (régimes alternatifs, ablations, exposition constante) ;
- plusieurs méthodes de DIMENSIONNEMENT (égal, volatilité, Kelly fractionné, CVaR, robuste corrélation) ;
- un seuil de rotation : une position n'est remplacée que si le gain attendu dépasse le coût de la rotation ;
- un décalage de phase du rééquilibrage (contrôle des effets d'horloge).

Mêmes garanties anti-biais que la v1 : caractéristiques <= t, exécution à partir de t+1, un ordre limité n'est
rempli que si une bougie ultérieure TRAVERSE son prix (toucher ne suffit pas), sorties jamais abandonnées.
La réduction de taille au-delà d'un score de risque de 45/100 est conservée dans toutes les méthodes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .xsection import XSConfig, XSResult, build_panel, eligible, risk_score_vol

EXEC_MODELS = ("market", "market_maker", "market_maker_nospread", "close_maker", "limit_ideal", "limit", "hybrid")
SIZING = ("egal", "volatilite", "kelly", "cvar", "robuste")


@dataclass
class XS2Options:
    exec_model: str = "market"
    sizing: str = "egal"
    phase: int = 0  # décalage (en bougies) du rééquilibrage
    rotation_hurdle: float = 0.0  # remplacement seulement si écart de momentum > k x coût aller-retour
    exit_on_risk_off: bool = False  # régime défavorable : sortie des positions (sinon : plus d'entrée seulement)
    target_daily_vol: float = 0.04
    kelly_fraction: float = 0.25
    cvar_budget: float = 0.02  # perte moyenne au-delà du VaR95 journalier tolérée par position (fraction du capital)
    forward_bars: int = 6  # horizon du rendement ultérieur journalisé pour chaque ordre
    record_orders: bool = True


def btc_regime(P: dict[str, pd.DataFrame], cfg: XSConfig, market: str = "BTC-EUR") -> np.ndarray:
    rc = P["close"][market]
    return (rc > rc.ewm(span=cfg.regime_ema, adjust=False, min_periods=cfg.regime_ema).mean()).to_numpy(float)


def run_xs2(data: dict[str, pd.DataFrame], cfg: XSConfig, opt: XS2Options | None = None, start: int = 0,
            end: int | None = None, panel: dict[str, pd.DataFrame] | None = None, score: pd.DataFrame | None = None,
            exposure: np.ndarray | None = None) -> XSResult:
    """`exposure` : tableau [0, 1] par bougie (None = filtre BTC de la config si `cfg.regime_filter`, sinon 1).
    0 = aucune nouvelle entrée ; une valeur intermédiaire réduit le budget investi (le reste reste en euros)."""
    o = opt or XS2Options()
    if o.exec_model not in EXEC_MODELS or o.sizing not in SIZING:
        raise ValueError("modèle d'exécution ou de dimensionnement inconnu")
    P = panel or build_panel(data, cfg)
    S = score if score is not None else P["score"]
    idx = P["close"].index
    n = len(idx) if end is None else end
    mk = list(P["close"].columns)
    step_ms = float(np.median(np.diff(np.asarray(idx, dtype=np.int64))))
    bpd = 86_400_000 / step_ms
    O, H, L, C, V = (P[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))  # noqa: E741
    mark = P["close"].ffill().to_numpy(float)
    last_valid = [int(np.flatnonzero(~np.isnan(C[:, j]))[-1]) if np.isfinite(C[:, j]).any() else -1 for j in range(len(mk))]
    spread = P["spread"].to_numpy(float)
    rv = P["rv"].to_numpy(float)
    mom = P["mom"].to_numpy(float)
    logret = np.log(P["close"]).diff().to_numpy(float)
    if exposure is None:
        exposure = btc_regime(P, cfg) if (cfg.regime_filter and "BTC-EUR" in P["close"]) else np.ones(len(idx))
    exposure = np.nan_to_num(np.asarray(exposure, float), nan=0.0).clip(0, 1)
    ex = o.exec_model
    use_limit = ex in ("limit", "hybrid")
    maker_all = ex in ("market_maker", "market_maker_nospread", "close_maker", "limit_ideal")
    no_spread = ex in ("market_maker_nospread", "close_maker", "limit_ideal")
    cash = cfg.initial_eur
    pos: dict[int, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fees = turnover = fees_saved = 0.0
    missed = filled_limits = fallback_market = 0
    eq = np.full(n - start, np.nan)
    exposed = 0
    slip = 0.0 if no_spread else cfg.slippage_bps / 1e4

    def half_spread(j: int, i: int) -> float:
        if no_spread:
            return 0.0
        return max(cfg.spread_floor_bps / 1e4, float(np.nan_to_num(spread[i, j]))) / 2

    def fwd(j: int, i: int) -> float:
        k = min(n - 1, i + o.forward_bars)
        a, b = C[i, j], mark[k, j]
        return float(b / a - 1) if a > 0 and np.isfinite(b) else float("nan")

    def log_order(od: dict[str, Any], status: str, i: int, px: float | None = None) -> None:
        if o.record_orders and "oid" in od:
            orders[od["oid"]].update({"statut": status, "fill_i": i, "fill_px": px})

    def new_order(od: dict[str, Any], i: int) -> dict[str, Any]:
        if o.record_orders:
            od["oid"] = len(orders)
            orders.append({"decision_i": i, "ts": int(idx[i]), "market": mk[od["j"]], "side": od["side"],
                           "px_limite": od.get("px"), "eur": od.get("eur"), "close_decision": float(C[i, od["j"]]),
                           "rendement_ulterieur": fwd(od["j"], i), "statut": "en_attente", "reason": od["reason"]})
        return od

    def buy(j: int, i: int, eur: float, px: float, maker: bool) -> bool:
        nonlocal cash, fees, turnover, fees_saved
        fee_r = cfg.fee_maker if maker else cfg.fee_taker
        cost = eur * (1 + fee_r)
        if cost > cash:
            eur, cost = cash / (1 + fee_r), cash
        if eur < cfg.min_order_eur:
            return False
        cash -= cost
        fees += eur * fee_r
        fees_saved += eur * (cfg.fee_taker - fee_r)
        turnover += eur
        p = pos.setdefault(j, {"q": 0.0, "cost": 0.0, "entry_i": i, "peak": px, "entry_px": px})
        p["q"] += eur / px
        p["cost"] += cost
        return True

    def sell(j: int, i: int, px: float, maker: bool, reason: str) -> None:
        nonlocal cash, fees, turnover, fees_saved
        p = pos.pop(j)
        gross = p["q"] * px
        fee_r = cfg.fee_maker if maker else cfg.fee_taker
        cash += gross * (1 - fee_r)
        fees += gross * fee_r
        fees_saved += gross * (cfg.fee_taker - fee_r)
        turnover += gross
        trades.append({"market": mk[j], "entry_i": p["entry_i"], "exit_i": i, "pnl": gross * (1 - fee_r) - p["cost"],
                       "ret": gross * (1 - fee_r) / p["cost"] - 1 if p["cost"] else 0.0, "reason": reason})

    def size(j: int, i: int, equity: float, target: float) -> float:
        w = min(cfg.max_weight * equity, target)
        daily = float(rv[i, j]) * np.sqrt(bpd) if np.isfinite(rv[i, j]) else np.nan
        if o.sizing == "volatilite" and daily > 0:
            w *= float(np.clip(o.target_daily_vol / daily, 0.0, 1.0))  # jamais au-delà de la cible : pas de levier
        elif o.sizing in ("kelly", "cvar", "robuste"):
            lb = max(10, int(30 * bpd))
            r = logret[max(0, i - lb + 1): i + 1, j]
            r = r[np.isfinite(r)]
            if len(r) < 10:
                return 0.0
            if o.sizing == "kelly":
                m, v = float(r.mean()) * bpd, float(r.var()) * bpd  # par jour
                f = 0.0 if v <= 0 or m <= 0 else min(0.35, o.kelly_fraction * m / v)
                w = min(w, f * equity)
            elif o.sizing == "cvar":
                # rendements agrégés par jour (approximation : somme de bougies consécutives)
                k = max(1, int(round(bpd)))
                d = np.add.reduceat(r, np.arange(0, len(r), k)) if len(r) >= k else r
                q = np.quantile(d, 0.05)
                tail = -float(d[d <= q].mean()) if (d <= q).any() else 0.0
                if tail > 0:
                    w = min(w, o.cvar_budget * equity / tail)
            else:  # robuste : pénalité de corrélation moyenne avec les positions détenues
                cs = []
                rj = logret[max(0, i - lb + 1): i + 1, j]
                for jj in pos:
                    rr = logret[max(0, i - lb + 1): i + 1, jj]
                    ok = np.isfinite(rj) & np.isfinite(rr)
                    if ok.sum() >= 10 and rj[ok].std() > 0 and rr[ok].std() > 0:
                        cs.append(float(np.corrcoef(rj[ok], rr[ok])[0, 1]))
                avg = max(0.0, float(np.mean(cs))) if cs else 0.0
                if daily > 0:
                    w *= float(np.clip(o.target_daily_vol / daily, 0.0, 1.0))
                w /= 1 + avg
        rs = risk_score_vol(float(rv[i, j]), bpd)
        if rs > cfg.risk_cut:  # conservée : réduction au-delà de 45/100
            w *= max(0.0, 1 - (rs - cfg.risk_cut) / (100 - cfg.risk_cut)) ** 2
        return float(w)

    for i in range(start, n):
        # 0) ordres planifiés à l'ouverture de i
        due = [od for od in pending if od.get("market_at") == i]
        pending = [od for od in pending if od.get("market_at") != i]
        for od in due:
            j = od["j"]
            if np.isnan(O[i, j]):
                if i + 1 < n:
                    pending.append({**od, "market_at": i + 1})
                continue
            if ex in ("close_maker", "limit_ideal") and "px" in od:
                px, maker = od["px"], True
            else:
                sgn = 1 if od["side"] == "buy" else -1
                px, maker = O[i, j] * (1 + sgn * (slip + half_spread(j, i - 1))), maker_all
            if od["side"] == "buy":
                if buy(j, i, od["eur"], px, maker):
                    log_order(od, "rempli_marche" if not maker else "rempli", i, px)
            elif j in pos:
                sell(j, i, px, maker, od["reason"])
                log_order(od, "rempli", i, px)
        # 1) ordres limités réalistes : traversée exigée, plafond de volume
        still = []
        for od in pending:
            j = od["j"]
            if od.get("market_at") is not None or np.isnan(L[i, j]):
                still.append(od)
                continue
            if od["side"] == "buy":
                cap = V[i, j] * od["px"] * cfg.max_volume_share
                if L[i, j] < od["px"] * (1 - cfg.through_bps / 1e4) and cap >= cfg.min_order_eur:
                    if buy(j, i, min(od["eur"], cap), od["px"], maker=True):
                        filled_limits += 1
                        log_order(od, "rempli", i, od["px"])
                    continue
            elif H[i, j] > od["px"] * (1 + cfg.through_bps / 1e4) and j in pos:
                sell(j, i, od["px"], True, od["reason"])
                filled_limits += 1
                log_order(od, "rempli", i, od["px"])
                continue
            od["ttl"] -= 1
            if od["ttl"] <= 0:
                if (ex == "hybrid" or od["side"] == "sell") and i + 1 < n:
                    fallback_market += 1
                    still.append({**od, "market_at": i + 1, "px_ignored": True})
                    log_order(od, "repli_marche", i)
                else:
                    missed += 1
                    log_order(od, "manque", i)
                continue
            still.append(od)
        pending = still
        # 2) radiation (aucun stop dans les variantes v2 : comparabilité avec la base)
        for j in list(pos):
            if i > last_valid[j]:
                sell(j, i, mark[last_valid[j], j] * (1 - cfg.delisting_haircut), False, "radiation")
        # 3) rééquilibrage
        if (i - start - o.phase) % cfg.rebalance_bars == 0 and i + 1 < n and i - start >= o.phase:
            ok = eligible(P, cfg, i)
            sc = S.iloc[i].where(ok).dropna().sort_values(ascending=False)
            rank = {mk.index(m): r for r, m in enumerate(sc.index)}
            e = float(exposure[i])
            best_new = [mk.index(m) for m in sc.index[: cfg.top_n] if mk.index(m) not in pos]
            for j in list(pos):
                held = i - pos[j]["entry_i"]
                if held < cfg.min_hold_bars or any(od["j"] == j and od["side"] == "sell" for od in pending):
                    continue
                out_rank = rank.get(j, 10**9) >= cfg.top_n + cfg.buffer
                if o.exit_on_risk_off and e <= 0:
                    out_rank = True
                elif out_rank and o.rotation_hurdle > 0 and best_new and np.isfinite(mom[i, j]):
                    rt = 2 * (cfg.fee_taker + slip + half_spread(j, i))
                    gain = max(float(np.nan_to_num(mom[i, b], nan=-9)) for b in best_new) - float(mom[i, j])
                    if rank.get(j) is not None and gain < o.rotation_hurdle * rt:
                        continue  # encore admissible et remplacement non rentable : conservée
                if not out_rank:
                    continue
                if use_limit:
                    pending.append(new_order({"j": j, "side": "sell", "px": C[i, j] * (1 + cfg.limit_offset_bps / 1e4),
                                              "ttl": cfg.limit_ttl_bars, "reason": "sortie"}, i))
                elif ex in ("close_maker", "limit_ideal"):
                    px = C[i, j] * (1 + (cfg.limit_offset_bps / 1e4 if ex == "limit_ideal" else 0))
                    pending.append(new_order({"j": j, "side": "sell", "px": px, "market_at": i + 1, "reason": "sortie"}, i))
                else:
                    pending.append(new_order({"j": j, "side": "sell", "market_at": i + 1, "reason": "sortie"}, i))
            if e > 0:
                equity = cash + sum(p["q"] * mark[i, j] for j, p in pos.items())
                target = equity * (1 - cfg.reserve) * e / cfg.top_n
                for m in sc.index[: cfg.top_n]:
                    j = mk.index(m)
                    if j in pos or any(od["j"] == j and od["side"] == "buy" for od in pending) or sc[m] <= cfg.min_score:
                        continue
                    rt = 2 * (cfg.fee_taker + slip + half_spread(j, i))
                    if cfg.cost_hurdle_mult > 0 and not (float(np.nan_to_num(mom[i, j], nan=-9)) > cfg.cost_hurdle_mult * rt):
                        continue
                    w = size(j, i, equity, target)
                    committed = sum(od.get("eur", 0.0) for od in pending if od["side"] == "buy")
                    eur = min(w, (cash - committed) / (1 + cfg.fee_taker))
                    if eur < cfg.min_order_eur:
                        continue  # sous le minimum Bitvavo : on reste en euros
                    if use_limit:
                        pending.append(new_order({"j": j, "side": "buy", "px": C[i, j] * (1 - cfg.limit_offset_bps / 1e4),
                                                  "ttl": cfg.limit_ttl_bars, "eur": eur, "reason": "entree"}, i))
                    elif ex in ("close_maker", "limit_ideal"):
                        px = C[i, j] * (1 - (cfg.limit_offset_bps / 1e4 if ex == "limit_ideal" else 0))
                        pending.append(new_order({"j": j, "side": "buy", "px": px, "market_at": i + 1, "eur": eur,
                                                  "reason": "entree"}, i))
                    else:
                        pending.append(new_order({"j": j, "side": "buy", "market_at": i + 1, "eur": eur, "reason": "entree"}, i))
        if pos:
            exposed += 1
        eq[i - start] = cash + sum(p["q"] * mark[i, j] for j, p in pos.items())
    for j in list(pos):
        sell(j, n - 1, mark[n - 1, j] * (1 - slip - half_spread(j, n - 1)), False, "fin")
    eq[-1] = cash
    stats = {"limit_remplis": filled_limits, "limit_manques": missed, "repli_marche": fallback_market,
             "frais_economises_eur": float(fees_saved), "ordres": orders}
    return XSResult(pd.Series(eq, index=idx[start:n]), trades, fees, turnover, exposed / max(1, n - start), stats)


def order_effects(orders: list[dict[str, Any]], rt_cost: float = 0.0) -> dict[str, Any]:
    """Transactions évitées et sélection : rendement ultérieur (depuis la clôture de décision) des ENTRÉES
    remplies vs manquées. Un écart positif « manquées > remplies » = sélection adverse (on rate les hausses)."""
    ent = [x for x in orders if x["side"] == "buy" and x["statut"] in ("rempli", "manque", "repli_marche", "rempli_marche")]
    out: dict[str, Any] = {}
    for st in ("rempli", "manque", "repli_marche", "rempli_marche"):
        v = np.array([x["rendement_ulterieur"] for x in ent if x["statut"] == st], float)
        v = v[np.isfinite(v)]
        out[st] = {"n": int(len(v)), "rendement_ulterieur_moyen": float(v.mean()) if len(v) else None}
    f, m = out["rempli"]["rendement_ulterieur_moyen"], out["manque"]["rendement_ulterieur_moyen"]
    out["selection_adverse_manque_moins_rempli"] = (m - f) if f is not None and m is not None else None
    out["taux_remplissage_entrees"] = out["rempli"]["n"] / max(1, sum(out[s]["n"] for s in ("rempli", "manque", "repli_marche")))
    return out


def regime_exposures(P: dict[str, pd.DataFrame], cfg: XSConfig, btc: str = "BTC-EUR") -> dict[str, np.ndarray]:
    """Indicateurs de régime CAUSAUX (valeurs à t calculées sur <= t), binaires 0/1."""
    close = P["close"]
    lb = cfg.regime_ema
    ok = (P["age"] >= cfg.min_age_bars) & (P["liq"] >= cfg.min_liquidity_quote)
    ema = close.ewm(span=cfg.trend_slow, adjust=False, min_periods=cfg.trend_slow).mean()
    above = (close > ema).where(ok)
    breadth = above.mean(axis=1)
    r1 = np.log(close).diff().where(ok)
    idx_ret = r1.mean(axis=1).fillna(0)
    mkt = np.exp(idx_ret.cumsum())
    mkt_trend = mkt > mkt.ewm(span=lb, adjust=False, min_periods=lb).mean()
    gvol = P["rv"].where(ok).median(axis=1)
    gvol_hi = gvol > gvol.rolling(lb * 3, min_periods=lb).quantile(0.7)
    disp = P["mom"].where(ok).std(axis=1)
    disp_hi = disp > disp.rolling(lb * 3, min_periods=lb).median()
    win = max(10, cfg.vol_lookback)
    corr = _avg_corr(r1, win)
    corr_hi = corr > corr.rolling(lb * 3, min_periods=lb).quantile(0.7)
    liq = P["qvol"].where(ok).sum(axis=1).rolling(win, min_periods=win // 2).mean()
    liq_hi = liq > liq.rolling(lb * 3, min_periods=lb).median()
    out = {
        "btc": btc_regime(P, cfg, btc) if btc in close else np.ones(len(close)),
        "tendance_marche": mkt_trend.to_numpy(float),
        "proportion_haussiere": (breadth > 0.5).to_numpy(float),
        "volatilite_basse": (~gvol_hi).to_numpy(float),
        "dispersion_haute": disp_hi.to_numpy(float),
        "correlation_basse": (~corr_hi).to_numpy(float),
        "liquidite_haute": liq_hi.to_numpy(float),
    }
    return out


def _avg_corr(r: pd.DataFrame, win: int, max_assets: int = 30) -> pd.Series:
    """Corrélation moyenne approchée : variance de l'indice vs variances individuelles (formule de la
    corrélation moyenne implicite), sur les `max_assets` marchés les plus présents. Causale (fenêtre passée)."""
    cols = r.notna().sum().sort_values(ascending=False).index[:max_assets]
    x = r[cols]
    var_idx = x.mean(axis=1).rolling(win, min_periods=win // 2).var()
    var_ind = x.rolling(win, min_periods=win // 2).var().mean(axis=1)
    k = x.notna().sum(axis=1).clip(lower=2)
    rho = (var_idx * k * k / var_ind - k) / (k * (k - 1))
    return rho.clip(-1, 1)


def block_shuffle(x: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    """Permutation par blocs : même proportion ET même persistance que le régime réel, timing aléatoire."""
    n = len(x)
    starts = rng.integers(0, max(1, n - block), size=n // block + 1)
    return np.concatenate([x[s:s + block] for s in starts])[:n]
