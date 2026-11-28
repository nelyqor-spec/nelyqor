"""Phase 5 (priorités 3 et 4) — pouvoir prédictif des signaux ÉLÉMENTAIRES, mesuré APRÈS COÛTS, avant toute
stratégie. Rôle LAB uniquement : aucun ordre, aucune clé.

Signaux étudiés SÉPARÉMENT (paramètres pré-enregistrés dans `SignalConfig`, jamais ajustés aux résultats) :

  niveau MARCHÉ (une valeur par jour)      états
    btc_tendance        BTC > EMA longue                                      binaire (0 bas / 1 haut)
    largeur_binaire     part des admissibles au-dessus de leur EMA > 50 %     binaire
    largeur_terciles    idem, valeur continue                                 terciles EXPANSIFS (passé seul)
    dispersion          écart-type transversal des rendements 7 j             terciles expansifs
    volatilite_marche   volatilité 14 j de l'indice équipondéré               terciles expansifs
    liquidite_marche    volume total 30 j / sa médiane 180 j                  terciles expansifs
    persistance_marche  autocorrélation 30 j (retard 1) de l'indice           terciles expansifs
  niveau ACTIF (une valeur par jour et par marché)
    force_relative      rendement 7 j − médiane de l'univers admissible       quintiles transversaux
    momentum_absolu     rendement 30 j                                        signe (≤ 0 / > 0)
    volatilite_actif    volatilité 14 j                                       quintiles transversaux
    liquidite_actif     log du volume médian 30 j (EUR)                       quintiles transversaux
    persistance_autocorr autocorrélation 30 j (retard 1)                      quintiles transversaux
    persistance_runs    longueur signée de la série de jours de même signe    ≤−3 / −2..0 / 1..2 / ≥3

Cibles : rendement futur de la clôture t à la clôture t+h (h en jours), NET d'un coût aller-retour
2 × (commission taker + demi-spread estimé point-in-time). Cibles marché : BTC et indice équipondéré des marchés
admissibles à t. Cibles actif : moyenne équipondérée par état (quantile) du rendement futur net de chaque actif.

Anti-biais :
- toute caractéristique à t n'utilise que des bougies ≤ t (EMA `adjust=False`, fenêtres glissantes passées,
  quantiles EXPANSIFS, rangs transversaux du jour) — vérifié par test de troncature ;
- admissibilité point-in-time : bougie observée à t, ancienneté ≥ `min_age_days` jours observés, volume médian
  30 j passé ≥ `min_liq_eur_day` ;
- bougies agrégées depuis 1 h, deux façons comparées : « stricte » (`resample_ohlc` : un jour avec une heure
  manquante est NaN) et « tolérante » (un jour existe dès qu'une heure existe) ;
- bougie absente : JAMAIS remplacée par un prix inventé. Deux politiques comparées :
  « dernier_prix » (rendement calculé depuis le dernier prix réellement observé ; fenêtres exigeant
  `min_frac` des observations) et « exclure » (tout rendement dont la fenêtre contient un trou est NaN ; fenêtres
  complètes exigées). Les stratégies dérivées valorisent toujours au dernier prix observé (on ne peut pas
  sortir d'une position pendant un trou) ;
- bougies agrégées incomplètes (début et FIN d'échantillon) : retirées EXPLICITEMENT et comptées
  (`daily_panel` → méta `bougies_incompletes_*`), jamais silencieusement ;
- toutes les heures de rééquilibrage (phases 0–23 h) sont utilisées et POOLÉES : aucune heure n'est choisie.
  L'estimateur est le ratio des sommes sur (jour, phase) ; le bootstrap rééchantillonne des blocs de JOURS
  (toutes phases ensemble), ce qui conserve la dépendance entre phases et le chevauchement des horizons.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sstats

from ..data.resample import resample_ohlc
from .metrics import block_bootstrap_ci, deflated_sharpe
from .xsection import corwin_schultz_spread
from .xsection_v2 import block_shuffle

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
AGGREGATIONS = ("stricte", "tolerante")
GAP_POLICIES = ("dernier_prix", "exclure")
TARGETS = ("btc", "marche")
# nom -> type d'états ; (bas, haut) = états comparés
MARKET_SIGNALS: dict[str, str] = {
    "btc_tendance": "binaire", "largeur_binaire": "binaire", "largeur_terciles": "terciles",
    "dispersion": "terciles", "volatilite_marche": "terciles", "liquidite_marche": "terciles",
    "persistance_marche": "terciles",
}
ASSET_SIGNALS: dict[str, str] = {
    "force_relative": "quantiles", "momentum_absolu": "signe", "volatilite_actif": "quantiles",
    "liquidite_actif": "quantiles", "persistance_autocorr": "quantiles", "persistance_runs": "runs",
}
RUN_EDGES = (-2.5, 0.5, 2.5)  # états : ≤ −3 | −2..0 | 1..2 | ≥ 3


@dataclass(frozen=True)
class SignalConfig:
    fee_taker: float = 0.0025  # commission par côté (taker Bitvavo)
    spread_floor_bps: float = 5.0  # plancher du spread complet (le demi-spread vaut max(plancher, estimé)/2)
    half_spread_fixed: float | None = None  # si renseigné : demi-spread constant à la place de Corwin-Schultz
    horizons: tuple[int, ...] = (1, 3, 7, 14)
    min_age_days: int = 30
    liq_lookback_days: int = 30
    min_liq_eur_day: float = 50_000.0
    min_names: int = 5
    btc_ema_days: int = 50
    breadth_ema_days: int = 30
    rs_days: int = 7
    mom_days: int = 30
    vol_days: int = 14
    autocorr_days: int = 30
    dispersion_days: int = 7
    liq_norm_days: int = 180
    n_quantiles: int = 5
    state_burn_in_days: int = 180  # observations exigées avant de définir des terciles expansifs
    min_frac: float = 0.5  # politique dernier_prix : fraction des observations exigée dans une fenêtre
    n_boot: int = 1000
    n_random: int = 500  # tirages du minutage aléatoire (permutation par blocs)
    seed: int = 0
    btc: str = "BTC-EUR"
    top_n: int = 10
    capital_eur: float = 100.0
    min_order_eur: float = 5.0
    rebalance_days: int = 30
    min_state_days: int = 100  # en deçà, le signal de marché est déclaré non calculable (et non testé)
    n_prior_trials: int = 1200  # essais des phases 3–4b (ordre de grandeur documenté, script phase 4b)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def state_levels(kind: str, q: int) -> tuple[int, int, int]:
    """(état bas, état haut, nombre d'états)."""
    return {"binaire": (0, 1, 2), "signe": (0, 1, 2), "terciles": (0, 2, 3), "quantiles": (0, q - 1, q),
            "runs": (0, 3, 4)}[kind]


# ------------------------------------------------------------------------------------------- agrégation
def aggregate_daily(df: pd.DataFrame, phase_h: int, mode: str) -> pd.DataFrame:
    """Bougies journalières couvrant [J + phase h, J + 1 + phase h) ; index = OUVERTURE (ms).

    stricte   : `backend.data.resample.resample_ohlc` (jour NaN dès qu'une heure manque) ;
    tolerante : un jour existe dès qu'une bougie 1 h existe (Bitvavo n'émet pas de bougie sans transaction)."""
    off = phase_h * HOUR_MS
    d = df[["open", "high", "low", "close", "volume"]]
    if mode == "stricte":
        s = d.copy()
        s.index = pd.Index(d.index.to_numpy(np.int64) - off, dtype="int64")
        out = resample_ohlc(s, "1D")
        out.index = pd.Index(out.index.to_numpy(np.int64) + off, dtype="int64")
        return out
    if mode != "tolerante":
        raise ValueError(f"agrégation inconnue : {mode}")
    d = d[d["close"].notna()]
    key = (d.index.to_numpy(np.int64) - off) // DAY_MS
    g = d.groupby(key)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum()})
    out.index = pd.Index(out.index.to_numpy(np.int64) * DAY_MS + off, dtype="int64")
    return out


def daily_panel(raw: dict[str, pd.DataFrame], phase_h: int, mode: str, base_ms: int = HOUR_MS
                ) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Panneau journalier (index = clé de jour (ouverture − phase) // 1 j, colonnes = marchés).

    Bougies INCOMPLÈTES traitées explicitement : une bougie journalière dont la période commence avant la première
    bougie 1 h de l'univers ou finit après la clôture de la dernière bougie 1 h de l'univers est RETIRÉE et
    comptée (la dernière bougie de l'échantillon est presque toujours incomplète pour une phase ≠ 0)."""
    raw = {m: d for m, d in raw.items() if len(d) >= 48}
    if not raw:
        raise ValueError("univers vide")
    start = min(int(d.index[0]) for d in raw.values())
    end = max(int(d.index[-1]) for d in raw.values()) + base_ms  # clôture de la dernière bougie 1 h
    off = phase_h * HOUR_MS
    cols: dict[str, pd.DataFrame] = {}
    meta = {"bougies_incompletes_debut": 0, "bougies_incompletes_fin": 0}
    for m, df in raw.items():
        a = aggregate_daily(df, phase_h, mode)
        ix = a.index.to_numpy(np.int64)
        head = ix < start
        tail = ix + DAY_MS > end
        meta["bougies_incompletes_debut"] += int(head.sum())
        meta["bougies_incompletes_fin"] += int(tail.sum())
        a = a[~(head | tail)]
        a.index = pd.Index((a.index.to_numpy(np.int64) - off) // DAY_MS, dtype="int64")
        cols[m] = a
    lo = min(int(a.index[0]) for a in cols.values() if len(a))
    hi = max(int(a.index[-1]) for a in cols.values() if len(a))
    keys = pd.Index(np.arange(lo, hi + 1), dtype="int64", name="jour")
    mk = sorted(cols)
    panel = {f: pd.DataFrame({m: cols[m][f].reindex(keys) for m in mk}, index=keys)
             for f in ("open", "high", "low", "close", "volume")}
    meta["jours"] = len(keys)
    meta["marches"] = len(mk)
    return panel, meta


# ------------------------------------------------------------------------------------------- caractéristiques
def expanding_terciles(x: pd.Series, burn_in: int) -> pd.Series:
    """États 0/1/2 par rapport aux terciles de la distribution PASSÉE (≤ t) ; NaN avant `burn_in` observations."""
    q1 = x.expanding(min_periods=burn_in).quantile(1 / 3)
    q2 = x.expanding(min_periods=burn_in).quantile(2 / 3)
    st = pd.Series(np.where(x <= q1, 0.0, np.where(x > q2, 2.0, 1.0)), index=x.index)
    return st.where(q1.notna() & x.notna())


def cross_quantile_bins(x: pd.DataFrame, q: int, min_names: int) -> pd.DataFrame:
    """Quantile transversal du jour (0 = plus bas) ; NaN si moins de `min_names` valeurs ce jour-là."""
    pct = x.rank(axis=1, pct=True, method="average")
    b = (np.ceil(pct * q) - 1).clip(0, q - 1)
    n = x.notna().sum(axis=1)
    return b.where(pd.DataFrame(np.repeat((n >= min_names).to_numpy()[:, None], x.shape[1], axis=1),
                                index=x.index, columns=x.columns))


def signed_runs(r1: pd.DataFrame, exclude_gaps: bool) -> pd.DataFrame:
    """Longueur signée de la série courante de rendements de même signe (+3 = 3 hausses consécutives).
    dernier_prix : un jour absent ne change pas la série (le rendement suivant part du dernier prix connu) ;
    exclure : un jour absent remet la série à zéro."""
    R = r1.to_numpy(float)
    out = np.full_like(R, np.nan)
    run = np.zeros(R.shape[1])
    for t in range(R.shape[0]):
        x = R[t]
        obs = np.isfinite(x)
        s = np.sign(np.nan_to_num(x))
        cont = obs & (s != 0) & (np.sign(run) == s)
        new = np.where(cont, run + s, s)
        run = np.where(obs, new, 0.0 if exclude_gaps else run)
        out[t] = np.where(obs, run, np.nan)
    return pd.DataFrame(out, index=r1.index, columns=r1.columns)


def half_spread(panel: dict[str, pd.DataFrame], cfg: SignalConfig) -> pd.DataFrame:
    """Demi-spread point-in-time : médiane glissante 30 j (passée) de Corwin-Schultz sur bougies journalières,
    plancher `spread_floor_bps` ; jour sans estimation -> médiane transversale du jour, puis plancher."""
    close = panel["close"]
    if cfg.half_spread_fixed is not None:
        return pd.DataFrame(cfg.half_spread_fixed, index=close.index, columns=close.columns)
    hi, lo = panel["high"], panel["low"]
    valid = hi.notna() & lo.notna() & hi.shift(1).notna() & lo.shift(1).notna()
    cs = pd.DataFrame({m: corwin_schultz_spread(hi[m].to_numpy(float), lo[m].to_numpy(float)) for m in close},
                      index=close.index).where(valid)
    s = cs.rolling(30, min_periods=5).median()
    floor = cfg.spread_floor_bps / 1e4
    med = s.median(axis=1).to_numpy(float)
    a = s.to_numpy(float)
    a = np.where(np.isnan(a), med[:, None], a)
    return pd.DataFrame(np.nan_to_num(np.maximum(a, floor), nan=floor) / 2, index=close.index, columns=close.columns)


def compute_features(panel: dict[str, pd.DataFrame], cfg: SignalConfig, gap: str) -> dict[str, Any]:
    if gap not in GAP_POLICIES:
        raise ValueError(f"politique de trous inconnue : {gap}")
    close, vol = panel["close"], panel["volume"]
    obs = close.notna()
    cf = close.ffill()  # dernier prix RÉELLEMENT observé (jamais interpolé)
    lc, lf = np.log(close), np.log(cf)
    excl = gap == "exclure"
    obs_f = obs.astype(float)

    def mp(w: int) -> int:
        return w if excl else max(3, int(math.ceil(w * cfg.min_frac)))

    def past(k: int) -> pd.DataFrame:
        if excl:
            full = obs_f.rolling(k + 1, min_periods=k + 1).sum() >= k + 1
            return (lc - lc.shift(k)).where(full)
        return (lf - lf.shift(k)).where(obs)

    r1 = past(1)
    age = obs_f.cumsum()
    qv = (close * vol).where(obs)
    L = cfg.liq_lookback_days
    liq = qv.rolling(L, min_periods=max(3, L // 2)).median()
    ok = obs & (age >= cfg.min_age_days) & (liq >= cfg.min_liq_eur_day)
    n_ok = ok.sum(axis=1)
    enough = n_ok >= cfg.min_names
    hs = half_spread(panel, cfg)
    cost = cfg.fee_taker + hs  # coût par côté
    fwd_gross: dict[int, pd.DataFrame] = {}
    for h in cfg.horizons:
        if excl:
            cnt = obs_f.rolling(h + 1, min_periods=h + 1).sum().shift(-h)
            g = (lc.shift(-h) - lc).where(cnt >= h + 1)
        else:
            g = (lf.shift(-h) - lc).where(obs)
        fwd_gross[h] = np.expm1(g)
    fwd_net = {h: g - 2 * cost for h, g in fwd_gross.items()}
    R1 = cf.shift(-1) / cf - 1  # rendement 1 j des positions (dernier prix observé)

    mstate: dict[str, pd.Series] = {}
    burn = cfg.state_burn_in_days
    if cfg.btc in close:
        bc = cf[cfg.btc]
        ema = bc.ewm(span=cfg.btc_ema_days, adjust=False, min_periods=cfg.btc_ema_days).mean()
        mstate["btc_tendance"] = (bc > ema).astype(float).where(obs[cfg.btc] & ema.notna())
    ema_all = cf.ewm(span=cfg.breadth_ema_days, adjust=False, min_periods=cfg.breadth_ema_days).mean()
    above = (close > ema_all).astype(float).where(ok & ema_all.notna())
    breadth = above.mean(axis=1).where(above.notna().sum(axis=1) >= cfg.min_names)
    mstate["largeur_binaire"] = (breadth > 0.5).astype(float).where(breadth.notna())
    mstate["largeur_terciles"] = expanding_terciles(breadth, burn)
    retd = past(cfg.dispersion_days).where(ok)
    disp = retd.std(axis=1).where(retd.notna().sum(axis=1) >= cfg.min_names)
    mstate["dispersion"] = expanding_terciles(disp, burn)
    r_ok = r1.where(ok)
    r_idx = r_ok.mean(axis=1).where(r_ok.notna().sum(axis=1) >= cfg.min_names)
    volm = r_idx.rolling(cfg.vol_days, min_periods=mp(cfg.vol_days)).std()
    mstate["volatilite_marche"] = expanding_terciles(volm, burn)
    liqm = qv.where(ok).sum(axis=1).where(enough).rolling(L, min_periods=mp(L)).mean()
    liq_ratio = liqm / liqm.rolling(cfg.liq_norm_days, min_periods=cfg.liq_norm_days // 2).median()
    mstate["liquidite_marche"] = expanding_terciles(liq_ratio, burn)
    persm = r_idx.rolling(cfg.autocorr_days, min_periods=mp(cfg.autocorr_days)).corr(r_idx.shift(1))
    mstate["persistance_marche"] = expanding_terciles(persm, burn)

    q = cfg.n_quantiles
    ret_rs = past(cfg.rs_days).where(ok)
    asig = {
        "force_relative": ret_rs.sub(ret_rs.median(axis=1), axis=0),
        "momentum_absolu": past(cfg.mom_days).where(ok),
        "volatilite_actif": r1.rolling(cfg.vol_days, min_periods=mp(cfg.vol_days)).std().where(ok),
        "liquidite_actif": np.log(liq.where(ok & (liq > 0))),
        "persistance_autocorr": r1.rolling(cfg.autocorr_days, min_periods=mp(cfg.autocorr_days)).corr(
            r1.shift(1)).where(ok),
        "persistance_runs": signed_runs(r1, excl).where(ok),
    }
    astate: dict[str, pd.DataFrame] = {}
    for name, kind in ASSET_SIGNALS.items():
        x = asig[name].replace([np.inf, -np.inf], np.nan)
        if kind == "quantiles":
            astate[name] = cross_quantile_bins(x, q, 2 * q)
        elif kind == "signe":
            astate[name] = (x > 0).astype(float).where(x.notna())
        else:
            astate[name] = pd.DataFrame(np.digitize(x.to_numpy(float), RUN_EDGES).astype(float),
                                        index=x.index, columns=x.columns).where(x.notna())
    return {"close": close, "cf": cf, "obs": obs, "ok": ok, "n_ok": n_ok, "liq": liq, "hs": hs, "cost": cost,
            "fwd_gross": fwd_gross, "fwd_net": fwd_net, "R1": R1, "market_states": mstate,
            "asset_signals": asig, "asset_states": astate, "breadth": breadth}


def bin_means(val: pd.DataFrame, states: pd.DataFrame, n_states: int) -> pd.DataFrame:
    """Moyenne équipondérée du jour de `val` par état (colonnes 0..n_states−1)."""
    V, S = val.to_numpy(float), states.to_numpy(float)
    out = np.full((len(V), n_states), np.nan)
    for k in range(n_states):
        m = (S == k) & np.isfinite(V)
        c = m.sum(1)
        s = np.where(m, V, 0.0).sum(1)
        out[:, k] = np.where(c > 0, s / np.maximum(c, 1), np.nan)
    return pd.DataFrame(out, index=val.index)


# ------------------------------------------------------------------------------------------- portefeuilles
def portfolio_net(W: np.ndarray, R1: np.ndarray, C: np.ndarray) -> np.ndarray:
    """Rendement net de t à t+1 d'un portefeuille de poids W (décidés à la clôture t, reste en euros).
    Coût = Σ |W_t − W̃_t| × coût par côté, W̃_t = poids de la veille dérivés par les rendements (exact)."""
    W = np.nan_to_num(W)
    R = np.nan_to_num(R1)
    C = np.nan_to_num(C)
    g = (W * R).sum(1)
    Wt = np.zeros_like(W)
    Wt[1:] = W[:-1] * (1 + R[:-1]) / (1 + g[:-1])[:, None]
    return g - (np.abs(W - Wt) * C).sum(1)


def timing_returns(e: np.ndarray, b: np.ndarray, kappa: np.ndarray, cbar: np.ndarray) -> np.ndarray:
    """Exposition e_t ∈ [0, 1] à une cible de rendement b (t -> t+1). Coût d'ajustement |e_t − ẽ_t| × c̄_t
    (ẽ = exposition de la veille dérivée) + coût interne de rééquilibrage de la cible min(e, ẽ) × κ_t.
    Exact pour un actif unique (κ = 0) ; pour le panier équipondéré, approximation au jour de transition."""
    e = np.nan_to_num(e)
    b = np.nan_to_num(b)
    prev = np.zeros_like(e)
    prev[1:] = e[:-1]
    bp = np.zeros_like(b)
    bp[1:] = b[:-1]
    et = prev * (1 + bp) / (1 + prev * bp)
    return e * b - np.abs(e - et) * cbar - np.minimum(e, et) * kappa


def simulate_top_liquid(cf: np.ndarray, obs: np.ndarray, liq: np.ndarray, ok: np.ndarray, hs: np.ndarray,
                        cfg: SignalConfig) -> np.ndarray:
    """Portefeuille équipondéré des `top_n` marchés les plus liquides (point-in-time), capital `capital_eur`,
    ordre minimal `min_order_eur` (un ordre plus petit n'est pas passé : reliquats conservés), rebalancement tous
    les `rebalance_days` jours, commission taker + demi-spread. Échange seulement sur un jour OBSERVÉ.
    Retourne la valeur à chaque clôture (NaN avant le premier jour avec `top_n` admissibles)."""
    n, M = cf.shape
    eq = np.full(n, np.nan)
    units = np.zeros(M)
    cash = cfg.capital_eur
    started = -1
    fee = cfg.fee_taker
    for t in range(n):
        px = np.nan_to_num(cf[t])
        value = cash + float((units * px).sum())
        if started < 0 and ok[t].sum() >= cfg.top_n:
            started = t
        if started >= 0 and (t - started) % cfg.rebalance_days == 0:
            cand = np.flatnonzero(ok[t])
            sel = cand[np.argsort(-liq[t, cand], kind="stable")[: cfg.top_n]]
            tgt = np.zeros(M)
            tgt[sel] = value / cfg.top_n
            cur = units * px
            for j in np.flatnonzero(units > 0):
                d = tgt[j] - cur[j]
                if not obs[t, j] or -d < cfg.min_order_eur:
                    continue
                frac = 1.0 if tgt[j] == 0 else min(1.0, -d / cur[j])
                cash += units[j] * frac * px[j] * (1 - hs[t, j]) * (1 - fee)
                units[j] *= 1 - frac
            for j in sel:
                d = min(tgt[j] - units[j] * px[j], cash)
                if not obs[t, j] or d < cfg.min_order_eur:
                    continue
                units[j] += d * (1 - fee) / (px[j] * (1 + hs[t, j]))
                cash -= d
            value = cash + float((units * px).sum())
        if started >= 0:
            eq[t] = value
    return eq


# ------------------------------------------------------------------------------------------- résumé par phase
def summarize_phase(panel: dict[str, pd.DataFrame], cfg: SignalConfig, gap: str) -> dict[str, Any]:
    """Réduit une phase à des séries journalières compactes (clé de jour) : états, cibles, portefeuilles."""
    F = compute_features(panel, cfg, gap)
    ok, cost, R1 = F["ok"], F["cost"], F["R1"]
    out: dict[str, Any] = {"etat_m": F["market_states"], "cible": {}, "cible_brute": {}, "bins": {},
                           "timing_in": {}, "ptf": {}, "n_ok": F["n_ok"]}
    enough = F["n_ok"] >= cfg.min_names
    for h in cfg.horizons:
        fn, fg = F["fwd_net"][h], F["fwd_gross"][h]
        if cfg.btc in fn:
            out["cible"][("btc", h)] = fn[cfg.btc]
            out["cible_brute"][("btc", h)] = fg[cfg.btc]
        fo = fn.where(ok)
        out["cible"][("marche", h)] = fo.mean(axis=1).where(enough & (fo.notna().sum(axis=1) >= cfg.min_names))
        out["cible_brute"][("marche", h)] = fg.where(ok).mean(axis=1).where(enough)
        for name, kind in ASSET_SIGNALS.items():
            out["bins"][(name, h)] = bin_means(fn.where(ok), F["asset_states"][name],
                                               state_levels(kind, cfg.n_quantiles)[2])
    okf = ok.astype(float)
    B = okf.div(F["n_ok"].where(enough), axis=0).fillna(0.0)
    Rz = R1.fillna(0.0)
    b = (B * Rz).sum(axis=1)
    Bt = (B.shift(1) * (1 + Rz.shift(1))).div(1 + b.shift(1), axis=0).fillna(0.0)
    kappa = ((B - Bt).abs() * cost).sum(axis=1)
    cbar = (B * cost).sum(axis=1).where(enough, cfg.fee_taker)
    out["timing_in"]["marche"] = (b.where(enough, 0.0), kappa.where(enough, 0.0), cbar)
    if cfg.btc in R1:
        out["timing_in"]["btc"] = (R1[cfg.btc].fillna(0.0), pd.Series(0.0, index=R1.index), cost[cfg.btc])
    Ra, Ca = R1.to_numpy(float), cost.to_numpy(float)
    for name, kind in ASSET_SIGNALS.items():
        lo, hi, _ = state_levels(kind, cfg.n_quantiles)
        S = F["asset_states"][name].to_numpy(float)
        for side, lvl in (("haut", hi), ("bas", lo)):
            m = (S == lvl) & ok.to_numpy()
            c = m.sum(1, keepdims=True)
            W = np.where(m, 1.0 / np.maximum(c, 1), 0.0)
            out["ptf"][(name, side)] = pd.Series(portfolio_net(W, Ra, Ca), index=R1.index)
    eq = simulate_top_liquid(F["cf"].to_numpy(float), F["obs"].to_numpy(), np.nan_to_num(F["liq"].to_numpy(float)),
                             ok.to_numpy(), F["hs"].to_numpy(float), cfg)
    eqs = pd.Series(eq, index=R1.index)
    out["top_liquides"] = (eqs.shift(-1) / eqs - 1)
    return out


# ------------------------------------------------------------------------------------------- statistiques
def holm(p_in: Sequence[float] | np.ndarray) -> np.ndarray:
    """p-valeurs ajustées de Holm–Bonferroni (contrôle du FWER)."""
    p = np.asarray(p_in, float)
    m = len(p)
    if m == 0:
        return p
    o = np.argsort(p, kind="stable")
    adj = np.maximum.accumulate((m - np.arange(m)) * p[o]).clip(max=1.0)
    out = np.empty(m)
    out[o] = adj
    return out


def benjamini_hochberg(p_in: Sequence[float] | np.ndarray) -> np.ndarray:
    """p-valeurs ajustées de Benjamini–Hochberg (contrôle du FDR)."""
    p = np.asarray(p_in, float)
    m = len(p)
    if m == 0:
        return p
    o = np.argsort(p, kind="stable")
    adj = p[o] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1].clip(max=1.0)
    out = np.empty(m)
    out[o] = adj
    return out


def boot_indices(n: int, block: int, n_boot: int, seed: int) -> np.ndarray:
    """Indices de rééchantillonnage par blocs mobiles, même schéma que `metrics.block_bootstrap_ci`."""
    rng = np.random.default_rng(seed + 7919)
    b = max(1, min(block, n // 2))
    nb = int(np.ceil(n / b))
    starts = rng.integers(0, n - b + 1, (n_boot, nb))
    return (starts[:, :, None] + np.arange(b)).reshape(n_boot, nb * b)[:, :n]


def _ratio(nH: np.ndarray, dH: np.ndarray, nL: np.ndarray | None, dL: np.ndarray | None) -> Any:
    with np.errstate(invalid="ignore", divide="ignore"):
        a = nH.sum(-1) / dH.sum(-1)
        if nL is None or dL is None:
            return a
        return a - nL.sum(-1) / dL.sum(-1)


def ratio_test(nH: np.ndarray, dH: np.ndarray, nL: np.ndarray | None, dL: np.ndarray | None, keys: np.ndarray,
               block: int, cfg: SignalConfig, cache: dict[tuple[int, int], np.ndarray]) -> dict[str, Any]:
    """Statistique poolée sur (jour, phase) : Σ numH / Σ denH (− Σ numL / Σ denL). Tableaux (jours, phases).
    IC95 : `metrics.block_bootstrap_ci` sur les jours ; p-valeur bilatérale : bootstrap par blocs centré."""
    n = nH.shape[0]
    h1, d1 = nH.sum(1), dH.sum(1)
    l1 = nL.sum(1) if nL is not None else None
    e1 = dL.sum(1) if dL is not None else None
    est = float(_ratio(h1, d1, l1, e1))
    res: dict[str, Any] = {"estimation": est, "n_jours": int(n), "n_obs_haut": int(d1.sum()),
                           "n_obs_bas": int(e1.sum()) if e1 is not None else None, "bloc_jours": block}
    if not np.isfinite(est) or d1.sum() < 10 or (e1 is not None and e1.sum() < 10):
        res.update({"ic95": [None, None], "p": 1.0, "donnees_insuffisantes": True})
        return res

    def stat(s: np.ndarray) -> float:
        ix = s.astype(int)
        return float(_ratio(h1[ix], d1[ix], None if l1 is None else l1[ix], None if e1 is None else e1[ix]))

    lo, hi = block_bootstrap_ci(np.arange(n, dtype=float), stat, block=block, n=cfg.n_boot, seed=cfg.seed)
    k = (n, block)
    if k not in cache:
        cache[k] = boot_indices(n, block, cfg.n_boot, cfg.seed)
    ix = cache[k]
    dist = _ratio(h1[ix], d1[ix], None if l1 is None else l1[ix], None if e1 is None else e1[ix])
    dist = dist[np.isfinite(dist)]
    p_emp = float((1 + np.sum(np.abs(dist - est) >= abs(est))) / (1 + len(dist)))
    se = float(dist.std(ddof=1)) if len(dist) > 2 else float("nan")
    # p-valeur principale : approximation normale avec l'erreur-type bootstrap (résolution continue, nécessaire
    # pour Holm sur ~10^3 tests ; la p-valeur empirique est bornée par 1/(B+1)).
    p = float(2 * sstats.norm.sf(abs(est) / se)) if se > 0 else 1.0
    per = np.asarray(_ratio(nH.T, dH.T, None if nL is None else nL.T, None if dL is None else dL.T), float)
    per = per[np.isfinite(per)]
    years = pd.to_datetime(keys * DAY_MS, unit="ms", utc=True).year.to_numpy()
    sub: dict[str, float | None] = {}
    for lab, m in [("moitie_1", np.arange(n) < n // 2), ("moitie_2", np.arange(n) >= n // 2)] + [
            (str(y), years == y) for y in np.unique(years)]:
        v = _ratio(h1[m], d1[m], None if l1 is None else l1[m], None if e1 is None else e1[m]) if m.sum() >= 30 else np.nan
        sub[lab] = float(v) if np.isfinite(v) else None
    sv = [v for v in sub.values() if v is not None]
    res.update({"ic95": [lo, hi], "p": p, "p_bootstrap_empirique": p_emp, "erreur_type_bootstrap": se,
                "par_phase": {"n": int(len(per)), "mediane": float(np.median(per)) if len(per) else None,
                              "min": float(per.min()) if len(per) else None,
                              "max": float(per.max()) if len(per) else None,
                              "part_meme_signe": float(np.mean(np.sign(per) == np.sign(est))) if len(per) else None},
                "sous_periodes": sub,
                "part_sous_periodes_meme_signe": float(np.mean(np.sign(sv) == np.sign(est))) if sv else None})
    return res


def _stack(series: list[pd.Series], keys: np.ndarray) -> np.ndarray:
    return np.column_stack([s.reindex(keys).to_numpy(float) for s in series])


def perf(x: np.ndarray) -> dict[str, float]:
    x = np.nan_to_num(np.asarray(x, float))
    eq = np.cumprod(1 + x)
    sd = float(x.std(ddof=1)) if len(x) > 1 else 0.0
    return {"moyenne_annualisee": float(x.mean() * 365), "sharpe": float(x.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
            "rendement_cumule": float(eq[-1] - 1) if len(eq) else 0.0,
            "max_drawdown": float((eq / np.maximum.accumulate(eq) - 1).min()) if len(eq) else 0.0}


def mean_spell(e: np.ndarray) -> float:
    """Durée moyenne (jours) des périodes d'état constant."""
    e = np.asarray(e, float).reshape(len(e), -1)
    spells = (np.diff(e, axis=0) != 0).sum() + e.shape[1]
    return float(e.size / max(1, spells))


# ------------------------------------------------------------------------------------------- analyse
def analyze_treatment(summaries: list[dict[str, Any]], cfg: SignalConfig, label: str) -> dict[str, Any]:
    """Tous les tests d'un traitement (agrégation × politique de trous), toutes phases poolées."""
    P = len(summaries)
    def fv(s: pd.Series) -> int:
        v = s.first_valid_index()
        return int(s.index[-1]) if v is None else int(v)

    # signal de marché calculable : au moins `min_state_days` jours d'état défini dans CHAQUE phase
    usable = [nm for nm in MARKET_SIGNALS if all(nm in sm["etat_m"] and sm["etat_m"][nm].notna().sum()
                                                 >= cfg.min_state_days for sm in summaries)]
    skipped = [nm for nm in MARKET_SIGNALS if nm not in usable]
    first = [max([fv(sm["etat_m"][nm]) for nm in usable] or [int(sm["n_ok"].index[0])]) for sm in summaries]
    last = [int(sm["n_ok"].index[-1]) for sm in summaries]
    k0, k1 = max(first), min(last) - 1
    keys = np.arange(k0, k1 + 1)
    n = len(keys)
    cache: dict[tuple[int, int], np.ndarray] = {}
    tests: list[dict[str, Any]] = []
    q = cfg.n_quantiles

    def block_for(h: int) -> int:
        return max(10, 2 * h)

    targets = [t for t in TARGETS if all((t, cfg.horizons[0]) in sm["cible"] for sm in summaries)]
    ST = {name: _stack([sm["etat_m"][name] for sm in summaries], keys)
          for name in usable}
    for tgt in targets:
        for h in cfg.horizons:
            V = _stack([sm["cible"][(tgt, h)] for sm in summaries], keys)
            G = _stack([sm["cible_brute"][(tgt, h)] for sm in summaries], keys)
            val = np.isfinite(V)
            Vz = np.nan_to_num(V)
            for name, S in ST.items():
                lo, hi, ns = state_levels(MARKET_SIGNALS[name], q)
                IH, IL = (S == hi) & val, (S == lo) & val
                base = {"traitement": label, "famille": "conditionnel_marche", "signal": name, "cible": tgt,
                        "horizon_j": h}
                means = {}
                for k in range(ns):
                    Ik = (S == k) & val
                    means[str(k)] = {"net": float(Vz[Ik].mean()) if Ik.any() else None,
                                     "brut": float(np.nan_to_num(G)[Ik & np.isfinite(G)].mean()) if Ik.any() else None,
                                     "n": int(Ik.sum())}
                r = ratio_test(Vz * IH, IH.astype(float), Vz * IL, IL.astype(float), keys, block_for(h), cfg, cache)
                tests.append({**base, "type": "diff_haut_moins_bas", **r, "moyennes_par_etat": means})
                r = ratio_test(Vz * IH, IH.astype(float), None, None, keys, block_for(h), cfg, cache)
                tests.append({**base, "type": "net_etat_haut", **r})
            if "btc_tendance" in ST and "largeur_binaire" in ST:
                S, Bt = ST["largeur_binaire"], ST["btc_tendance"]
                for bs in (1, 0):
                    IH, IL = (S == 1) & (Bt == bs) & val, (S == 0) & (Bt == bs) & val
                    r = ratio_test(Vz * IH, IH.astype(float), Vz * IL, IL.astype(float), keys, block_for(h), cfg, cache)
                    tests.append({"traitement": label, "famille": "largeur_sachant_btc", "signal": "largeur_binaire",
                                  "condition": f"btc_tendance={bs}", "cible": tgt, "horizon_j": h,
                                  "type": "diff_haut_moins_bas", **r})
    for name, kind in ASSET_SIGNALS.items():
        lo, hi, ns = state_levels(kind, q)
        for h in cfg.horizons:
            cols = [np.column_stack([sm["bins"][(name, h)][k].reindex(keys).to_numpy(float) for sm in summaries])
                    for k in range(ns)]
            VH, VL = cols[hi], cols[lo]
            IH, IL = np.isfinite(VH), np.isfinite(VL)
            base = {"traitement": label, "famille": "conditionnel_actif", "signal": name, "cible": "actif",
                    "horizon_j": h}
            means = {str(k): {"net": float(np.nanmean(c)) if np.isfinite(c).any() else None,
                              "n": int(np.isfinite(c).sum())} for k, c in enumerate(cols)}
            r = ratio_test(np.nan_to_num(VH), IH.astype(float), np.nan_to_num(VL), IL.astype(float), keys,
                           block_for(h), cfg, cache)
            tests.append({**base, "type": "diff_haut_moins_bas", **r, "moyennes_par_etat": means})
            r = ratio_test(np.nan_to_num(VH), IH.astype(float), None, None, keys, block_for(h), cfg, cache)
            tests.append({**base, "type": "net_etat_haut", **r})

    # ---- stratégies dérivées, références, décomposition exposition / minutage
    rng = np.random.default_rng(cfg.seed + 1)
    strategies: dict[str, np.ndarray] = {}
    decomposition: dict[str, Any] = {}
    refs: dict[str, Any] = {"euros": perf(np.zeros(n))}
    tin = {t: tuple(_stack([sm["timing_in"][t][i] for sm in summaries], keys) for i in range(3))
           for t in targets if all(t in sm["timing_in"] for sm in summaries)}

    def pooled(x: np.ndarray) -> np.ndarray:
        return x.mean(axis=1)

    for tgt, (b, kap, cb) in tin.items():
        passive = pooled(timing_returns(np.ones_like(b), b, kap, cb))
        refs[f"{tgt}_passif"] = perf(passive)
        expos: dict[str, np.ndarray] = {}
        for name, S in ST.items():
            lo, hi, _ = state_levels(MARKET_SIGNALS[name], q)
            expos[f"{name}=haut"] = (S == hi).astype(float)
            if MARKET_SIGNALS[name] == "terciles":
                expos[f"{name}=bas"] = (S == lo).astype(float)
        if "btc_tendance" in ST and "largeur_binaire" in ST:
            expos["btc_tendance_et_largeur_binaire"] = ((ST["btc_tendance"] == 1) & (ST["largeur_binaire"] == 1)).astype(float)
        for ename, e in expos.items():
            s_mat = timing_returns(e, b, kap, cb)
            s = pooled(s_mat)
            strategies[f"minutage|{ename}|{tgt}"] = s
            cbar_e = float(e.mean())
            c_mat = timing_returns(np.full_like(e, cbar_e), b, kap, cb)
            c = pooled(c_mat)
            # minutage aléatoire de même proportion et de même persistance : permutation par blocs de jours
            # (blocs ≥ 5 durées moyennes d'état : les coupures ajoutent peu de changements d'exposition)
            L = int(max(20, round(5 * mean_spell(e))))
            rnd = np.empty(cfg.n_random)
            rnd_g = np.empty(cfg.n_random)
            zero = np.zeros_like(b)
            for i in range(cfg.n_random):
                ix = block_shuffle(np.arange(n), L, rng).astype(int)
                rnd[i] = pooled(timing_returns(e[ix], b, kap, cb)).mean()
                rnd_g[i] = pooled(timing_returns(e[ix], b, zero, zero)).mean()
            m_s, m_c, m_p, m_r = s.mean(), c.mean(), passive.mean(), float(rnd.mean())
            m_sg, m_rg = float(pooled(timing_returns(e, b, zero, zero)).mean()), float(rnd_g.mean())
            p_rand_emp = float((1 + np.sum(np.abs(rnd - m_r) >= abs(m_s - m_r))) / (1 + cfg.n_random))
            sd_r, sd_g = float(rnd.std(ddof=1)), float(rnd_g.std(ddof=1))
            p_rand = float(2 * sstats.norm.sf(abs(m_s - m_r) / sd_r)) if sd_r > 0 else 1.0
            p_rand_g = float(2 * sstats.norm.sf(abs(m_sg - m_rg) / sd_g)) if sd_g > 0 else 1.0
            diff = s - c
            ci = block_bootstrap_ci(diff, np.mean, block=20, n=cfg.n_boot, seed=cfg.seed)
            per_phase = (s_mat - c_mat).mean(axis=0) * 365
            down, up = b < 0, b > 0
            row = {
                "exposition_moyenne": cbar_e, "bloc_permutation_jours": L,
                "strategie": perf(s), "exposition_constante_equivalente": perf(c), "passif": perf(passive),
                "minutage_aleatoire_moyenne_annualisee": m_r * 365,
                "minutage_aleatoire_q05_q95": [float(np.quantile(rnd, 0.05) * 365), float(np.quantile(rnd, 0.95) * 365)],
                "effet_total_vs_passif": float((m_s - m_p) * 365),
                "effet_exposition": float((m_c - m_p) * 365),
                "effet_minutage": float((m_s - m_c) * 365),
                "effet_minutage_ic95": [ci[0] * 365, ci[1] * 365],
                "effet_minutage_vs_aleatoire": float((m_s - m_r) * 365),
                "p_vs_aleatoire": p_rand, "p_vs_aleatoire_empirique": p_rand_emp,
                "effet_minutage_brut_vs_aleatoire": float((m_sg - m_rg) * 365), "p_brut_vs_aleatoire": p_rand_g,
                "effet_minutage_par_phase": {"mediane": float(np.median(per_phase)), "min": float(per_phase.min()),
                                             "max": float(per_phase.max()), "part_positive": float((per_phase > 0).mean())},
                "exposition_jours_baissiers": float(e[down].mean()) if down.any() else None,
                "exposition_jours_haussiers": float(e[up].mean()) if up.any() else None,
                "effet_minutage_moities": [float(diff[: n // 2].mean() * 365), float(diff[n // 2:].mean() * 365)],
            }
            decomposition[f"{ename}|{tgt}"] = row
            tests.append({"traitement": label, "famille": "minutage_vs_aleatoire", "signal": ename, "cible": tgt,
                          "horizon_j": 1, "type": "minutage_vs_aleatoire_permutation_blocs",
                          "estimation": row["effet_minutage_vs_aleatoire"], "ic95": row["effet_minutage_ic95"],
                          "p": p_rand, "n_jours": n})
            tests.append({"traitement": label, "famille": "minutage_vs_aleatoire", "signal": ename, "cible": tgt,
                          "horizon_j": 1, "type": "minutage_brut_vs_aleatoire_permutation_blocs",
                          "estimation": row["effet_minutage_brut_vs_aleatoire"], "ic95": [None, None],
                          "p": p_rand_g, "n_jours": n})
    if "btc" in tin and "btc_tendance" in ST:
        refs["btc_filtre_tendance"] = perf(strategies["minutage|btc_tendance=haut|btc"])
    for (name, side) in [(nm, sd) for nm in ASSET_SIGNALS for sd in ("haut", "bas")]:
        strategies[f"portefeuille|{name}={side}"] = pooled(_stack([sm["ptf"][(name, side)] for sm in summaries], keys))
    top = _stack([sm["top_liquides"] for sm in summaries], keys)
    top_ok = np.isfinite(top).all(axis=0)
    if top_ok.any():
        tp = top[:, top_ok]
        cum = np.prod(1 + tp, axis=0) - 1
        refs[f"equipondere_top{cfg.top_n}_liquides_{cfg.capital_eur:.0f}eur_min{cfg.min_order_eur:.0f}eur"] = {
            **perf(pooled(tp)), "phases": int(top_ok.sum()),
            "cumule_par_phase": {"mediane": float(np.median(cum)), "min": float(cum.min()), "max": float(cum.max())}}
    refs["_strategies_derivees"] = {sk: perf(sv) for sk, sv in strategies.items()}
    return {"tests": tests, "decomposition": decomposition, "references": refs, "strategies": strategies,
            "fenetre": {"debut": str(pd.to_datetime(k0 * DAY_MS, unit="ms", utc=True).date()),
                        "fin": str(pd.to_datetime(k1 * DAY_MS, unit="ms", utc=True).date()), "jours": n, "phases": P},
            "signaux_marche_non_calculables": skipped}


def dsr_table(strategies: dict[str, np.ndarray], n_prior: int) -> dict[str, Any]:
    """DSR de chaque stratégie dérivée ; essais = toutes les stratégies dérivées (tous traitements) + antérieurs."""
    sr = {k: (float(v.mean() / v.std(ddof=1)) if v.std(ddof=1) > 0 else 0.0) for k, v in strategies.items()}
    n_tr = len(sr) + n_prior
    var = float(np.var(list(sr.values()))) if len(sr) > 1 else 0.0
    out = {}
    for k, v in strategies.items():
        out[k] = {"sharpe_annualise": sr[k] * math.sqrt(365),
                  "dsr": deflated_sharpe(sr[k], len(v), n_tr, var, float(sstats.skew(v)),
                                         float(sstats.kurtosis(v, fisher=False)))}
    best = max(out, key=lambda x: out[x]["sharpe_annualise"]) if out else None
    return {"n_essais": n_tr, "n_strategies_derivees": len(sr), "n_essais_anterieurs": n_prior,
            "variance_sharpe_par_obs": var, "par_strategie": out, "meilleure": best,
            "meilleure_dsr": out[best]["dsr"] if best else None}


def run_study(raw: dict[str, pd.DataFrame], cfg: SignalConfig, phases: Sequence[int],
              aggregations: Sequence[str] = AGGREGATIONS, gaps: Sequence[str] = GAP_POLICIES,
              principal: str = "stricte|dernier_prix", log: Callable[[str], None] = print) -> dict[str, Any]:
    summaries: dict[str, list[dict[str, Any]]] = {f"{a}|{g}": [] for a in aggregations for g in gaps}
    meta_agg: dict[str, Any] = {}
    for a in aggregations:
        tot = {"bougies_incompletes_debut": 0, "bougies_incompletes_fin": 0}
        for ph_h in phases:
            panel, meta = daily_panel(raw, int(ph_h), a)
            for k in tot:
                tot[k] += meta[k]
            for g in gaps:
                summaries[f"{a}|{g}"].append(summarize_phase(panel, cfg, g))
            log(f"signaux : agrégation {a} phase {int(ph_h):02d} h ({meta['jours']} jours, {meta['marches']} marchés, "
                f"incomplètes retirées {meta['bougies_incompletes_debut']}+{meta['bougies_incompletes_fin']})")
        meta_agg[a] = tot
    results: dict[str, Any] = {}
    all_strats: dict[str, np.ndarray] = {}
    tests: list[dict[str, Any]] = []
    for lab, sm in summaries.items():
        r = analyze_treatment(sm, cfg, lab)
        tests.extend(r["tests"])
        all_strats.update({f"{lab}|{k}": v for k, v in r["strategies"].items()})
        results[lab] = {"fenetre": r["fenetre"], "signaux_marche_non_calculables": r["signaux_marche_non_calculables"], "decomposition": r["decomposition"], "references": r["references"]}
        log(f"signaux : traitement {lab} analysé ({len(r['tests'])} tests)")
    p = np.array([t["p"] for t in tests], float)
    ph, pb = holm(p), benjamini_hochberg(p)
    pr = [i for i, t in enumerate(tests) if t["traitement"] == principal]
    php, pbp = holm(p[pr]), benjamini_hochberg(p[pr])
    for i, t in enumerate(tests):
        t["p_holm_global"], t["p_bh_global"] = float(ph[i]), float(pb[i])
    for j, i in enumerate(pr):
        tests[i]["p_holm_principal"], tests[i]["p_bh_principal"] = float(php[j]), float(pbp[j])
    fam: dict[str, int] = {}
    for t in tests:
        fam[t["famille"]] = fam.get(t["famille"], 0) + 1
    n_sub = sum(len(t.get("sous_periodes", {})) for t in tests)
    counting = {"n_tests_pvaleurs_global": len(tests), "n_tests_pvaleurs_traitement_principal": len(pr),
                "par_famille": fam, "traitements": list(summaries), "phases": list(map(int, phases)),
                "n_estimations_sous_periodes_descriptives": n_sub,
                "n_estimations_par_phase_descriptives": sum(t.get("par_phase", {}).get("n", 0) or 0 for t in tests),
                "significatifs_holm_global_5pc": int((ph < 0.05).sum()), "significatifs_bh_global_5pc": int((pb < 0.05).sum()),
                "significatifs_holm_principal_5pc": int((php < 0.05).sum()),
                "significatifs_bh_principal_5pc": int((pbp < 0.05).sum()),
                "attendus_par_hasard_5pc_sans_correction": round(0.05 * len(tests), 1),
                "significatifs_bruts_5pc": int((p < 0.05).sum())}
    return {"config": cfg.as_dict(), "agregation_meta": meta_agg, "comptage": counting, "tests": tests,
            "traitements": results, "comparaison_traitements": compare_treatments(tests, principal),
            "dsr": dsr_table(all_strats, cfg.n_prior_trials),
            "question_largeur": breadth_verdict(results, tests, principal)}


def _tid(t: dict[str, Any]) -> str:
    return "|".join(str(t.get(k)) for k in ("famille", "signal", "condition", "cible", "horizon_j", "type"))


def compare_treatments(tests: list[dict[str, Any]], principal: str) -> dict[str, Any]:
    """Stabilité des estimations entre agrégation stricte/tolérante et politiques de trous."""
    by: dict[str, dict[str, float]] = {}
    for t in tests:
        if np.isfinite(t["estimation"]):
            by.setdefault(t["traitement"], {})[_tid(t)] = t["estimation"]
    base = by.get(principal, {})
    out = {}
    for lab, d in by.items():
        if lab == principal:
            continue
        common = [k for k in base if k in d]
        if len(common) < 3:
            continue
        a, b = np.array([base[k] for k in common]), np.array([d[k] for k in common])
        out[f"{principal} vs {lab}"] = {"n": len(common), "correlation": float(np.corrcoef(a, b)[0, 1]),
                                        "accord_de_signe": float(np.mean(np.sign(a) == np.sign(b))),
                                        "ecart_absolu_median": float(np.median(np.abs(a - b)))}
    return out


def breadth_verdict(results: dict[str, Any], tests: list[dict[str, Any]], principal: str) -> dict[str, Any]:
    """Question centrale : la largeur apporte-t-elle un avantage PROPRE ou seulement une baisse d'exposition ?"""
    dec = results.get(principal, {}).get("decomposition", {})
    out: dict[str, Any] = {}
    for tgt in TARGETS:
        k = f"largeur_binaire=haut|{tgt}"
        if k not in dec:
            continue
        d = dec[k]
        trow = next((t for t in tests if t["traitement"] == principal and t["famille"] == "minutage_vs_aleatoire"
                     and t["signal"] == "largeur_binaire=haut" and t["cible"] == tgt
                     and t["type"] == "minutage_vs_aleatoire_permutation_blocs"), None)
        trow_g = next((t for t in tests if t["traitement"] == principal and t["famille"] == "minutage_vs_aleatoire"
                       and t["signal"] == "largeur_binaire=haut" and t["cible"] == tgt
                       and t["type"] == "minutage_brut_vs_aleatoire_permutation_blocs"), None)
        cond = [t for t in tests if t["traitement"] == principal and t["famille"] == "largeur_sachant_btc"
                and t["cible"] == tgt]
        btc = dec.get(f"btc_tendance=haut|{tgt}")
        comb = dec.get(f"btc_tendance_et_largeur_binaire|{tgt}")
        ci = d["effet_minutage_ic95"]
        propre = bool(trow and trow["p_holm_global"] < 0.05 and ci[0] is not None and ci[0] > 0)
        out[tgt] = {
            "i_signal": d["strategie"], "ii_exposition_constante": d["exposition_constante_equivalente"],
            "iii_minutage_aleatoire_moyenne_annualisee": d["minutage_aleatoire_moyenne_annualisee"],
            "iv_btc_seul": btc["strategie"] if btc else None,
            "btc_et_largeur": comb["strategie"] if comb else None,
            "effet_exposition": d["effet_exposition"], "effet_minutage": d["effet_minutage"],
            "effet_minutage_ic95": ci, "p_vs_aleatoire": d["p_vs_aleatoire"],
            "p_vs_aleatoire_holm_global": trow["p_holm_global"] if trow else None,
            "effet_minutage_brut_vs_aleatoire": d["effet_minutage_brut_vs_aleatoire"],
            "p_brut_vs_aleatoire_holm_global": trow_g["p_holm_global"] if trow_g else None,
            "largeur_sachant_btc": {t["condition"] + f"|h={t['horizon_j']}": {
                "diff": t["estimation"], "ic95": t["ic95"], "p_holm_global": t["p_holm_global"]} for t in cond},
            "exposition_jours_baissiers_vs_haussiers": [d["exposition_jours_baissiers"], d["exposition_jours_haussiers"]],
            "verdict": ("avantage de minutage PROPRE significatif après correction" if propre else
                        "aucun avantage propre démontré : le gain éventuel s'explique par la réduction d'exposition "
                        "(effet d'exposition) ou n'est pas distinguable d'un minutage aléatoire de même persistance"),
        }
    return out
