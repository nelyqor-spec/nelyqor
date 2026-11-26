"""Moteur de simulation v4 (phase 5) — réécriture vérifiable, indépendante des moteurs figés v1/v2.

Principes (chacun couvert par un scénario déterministe calculable à la main, `tests/test_xsection_v4.py`) :

1. AGRÉGATION (`aggregate`) : une bougie agrégée existe dès qu'au moins une bougie horaire existe dans l'intervalle
   (une heure sans bougie chez Bitvavo = aucune transaction). La DERNIÈRE bougie agrégée est retirée si son
   intervalle n'est pas entièrement écoulé à la date des données (`asof_ms`) : jamais de bougie incomplète, jamais
   de « fausse radiation ». Horodatages = début d'intervalle, en millisecondes UTC, alignés sur l'époque.
2. ÉTATS PAR MARCHÉ ET PAR BOUGIE (`classify`), tous CAUSAUX (connus à la date t) :
   NON_COTE (avant la 1re bougie) · ACTIF (bougie présente) · SANS_TRANSACTION (absent depuis < `gap_days`, prix
   reporté, inexécutable) · RADIE (absent depuis ≥ `gap_days` ALORS QUE la majorité des autres marchés cotent) ·
   PANNE (bougie où moins de `outage_share` des marchés cotés ont une bougie : collecte défaillante, aucune décision,
   valorisation gelée).
3. VALORISATION : position valorisée au dernier prix connu ; une radiation (constatée causalement) vend au dernier
   prix connu × (1 − `delist_haircut`), paramètre explicite (défaut 0,10 [HYPOTHÈSE], sensibilité publiée) — une
   simple donnée manquante n'entraîne JAMAIS de décote.
4. EXÉCUTION : décision à la clôture de t, exécution à l'ouverture de t+1 (marché : ouverture ± demi-spread ±
   glissement, frais taker) ; ordre limité rempli seulement si une bougie postérieure TRAVERSE le prix limite d'au
   moins `through_bps` et dans la limite de `max_volume_share` du volume (achats ET ventes ; un reliquat partiel
   reste en attente) ; durée de validité `ttl_bars` ; un marché SANS_TRANSACTION n'exécute rien : un achat au
   marché est ANNULÉ (l'argent reste en euros, compté), une vente est reportée jusqu'à exécution ou radiation.
   Les ventes en attente sont exécutées avant les achats ; un ordre en attente n'est annulé que s'il est remplacé
   par une nouvelle cible sur le même marché (jamais une vente).
5. FRAIS ET ARRONDIS : frais sur le notionnel ; quantité arrondie VERS LE BAS au pas de quantité ; minimum d'ordre
   en euros (défaut 5 €) : en dessous, l'ordre n'est pas émis et l'argent reste en euros ; jamais de reliquat
   invendable (une vente laissant moins que le minimum vend tout).
6. COMPTABILITÉ : trésorerie + Σ quantité × dernier prix ; chaque transaction journalisée ; contrôle d'invariant
   (trésorerie ≥ 0, quantités ≥ 0, équité = trésorerie + valorisation) à chaque bougie.
7. DÉCISIONS COMPLÈTES : une décision est prise à chaque bougie y compris la dernière ; `next_orders()` renvoie les ordres qui seraient émis à la prochaine ouverture (marché,
   sens, montant, quantité, type, prix limite, durée de validité, conditions d'annulation) — utilisés par le
   prospectif v3 comme PRÉDICTIONS enregistrées avant observation.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

NON_COTE, ACTIF, SANS_TRANSACTION, RADIE, PANNE = 0, 1, 2, 3, 4
STATE_NAMES = {NON_COTE: "non_cote", ACTIF: "actif", SANS_TRANSACTION: "sans_transaction", RADIE: "radie", PANNE: "panne"}
RULE_MS = {"1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000}


# --------------------------------------------------------------------------------------------- agrégation
def aggregate(df: pd.DataFrame, interval: str, asof_ms: int | None = None) -> pd.DataFrame:
    """Agrège des bougies horaires (index = début, ms UTC) vers `interval`. Une bougie agrégée existe si au moins
    une bougie horaire existe dans l'intervalle ; la dernière est retirée si son intervalle n'est pas terminé à
    `asof_ms` (défaut : fin de la dernière bougie horaire). Colonne `n_sources` = nombre de bougies horaires."""
    step = RULE_MS[interval]
    if df.empty:
        return df.assign(n_sources=pd.Series(dtype=int))
    df = df[~df.index.duplicated(keep="last")].sort_index()  # doublons : dernière version ; ordre chronologique
    idx = np.asarray(df.index, dtype=np.int64)
    asof = int(asof_ms if asof_ms is not None else idx.max() + 3_600_000)
    bins = (idx // step) * step
    g = df.assign(_b=bins).groupby("_b", sort=True)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(), "n_sources": g["close"].count()})
    out.index = pd.Index(out.index.astype(np.int64), dtype=np.int64)
    return out[out.index + step <= asof]  # intervalle entièrement écoulé uniquement


def sanitize(df: pd.DataFrame) -> pd.DataFrame:
    """Une bougie n'est « présente » que si clôture > 0 et ouverture/haut/bas finis et > 0 ; sinon elle est
    traitée comme ABSENTE (NaN), jamais comme un prix nul. Volume manquant -> 0."""
    if df.empty:
        return df
    ok = np.ones(len(df), dtype=bool)
    for c in ("open", "high", "low", "close"):
        v = df[c].to_numpy(float)
        ok &= np.isfinite(v) & (v > 0)
    out = df.copy()
    out.loc[~ok, ["open", "high", "low", "close"]] = np.nan
    out["volume"] = out["volume"].astype(float).where(np.isfinite(out["volume"].astype(float)), 0.0)
    return out


def align(data: dict[str, pd.DataFrame], step_ms: int) -> tuple[np.ndarray, dict[str, pd.DataFrame]]:
    """Grille temporelle régulière (aucune bougie inventée : les absences restent NaN ; bougies invalides
    neutralisées par `sanitize`)."""
    data = {m: sanitize(d) for m, d in data.items()}
    if not data or not any(len(d) for d in data.values()):
        return np.array([], dtype=np.int64), {}
    lo = min(int(d.index.min()) for d in data.values() if len(d))
    hi = max(int(d.index.max()) for d in data.values() if len(d))
    grid = np.arange(lo, hi + step_ms, step_ms, dtype=np.int64)
    return grid, {m: d.reindex(grid) for m, d in data.items()}


# --------------------------------------------------------------------------------------------- états
@dataclass
class DataPolicy:
    gap_days: float = 7.0  # absence continue au-delà de laquelle un marché est réputé radié
    delist_haircut: float = 0.10  # [HYPOTHÈSE] perte à la vente forcée d'un marché radié
    outage_share: float = 0.10  # bougie de PANNE si moins de 10 % des marchés cotés ont une bougie
    min_others_share: float = 0.5  # radiation constatée seulement si ≥ 50 % des autres marchés cotent


def _share_outage(present: np.ndarray, gap_bars: int, pol: DataPolicy) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dénominateur = marchés RÉCEMMENT actifs (vus, absents depuis moins de `gap_bars` bougies brutes) : un marché
    radié ne compte plus (défaut D1 de la contre-expertise). PANNE si la part présente < `outage_share`, ou si aucun
    marché n'est présent alors qu'au moins un a déjà coté."""
    n = present.shape[0]
    ar = np.arange(n)[:, None]
    last = np.maximum.accumulate(np.where(present, ar, -1), axis=0)
    seen = last >= 0
    recent = seen & ((ar - last) < gap_bars)
    cnt = recent.sum(axis=1)
    npres = present.sum(axis=1)
    share = np.where(cnt > 0, npres / np.maximum(cnt, 1), 1.0)
    outage = (share < pol.outage_share) | ((npres == 0) & seen.any(axis=1))
    return seen, share, outage


def classify(close: np.ndarray, step_ms: int, pol: DataPolicy) -> tuple[np.ndarray, np.ndarray]:
    """Version VECTORISÉE de `classify_reference` (même résultat, vérifié par test) : (états [t, m], panne [t])."""
    n, k = close.shape
    if n == 0 or k == 0:
        return np.zeros((n, k), dtype=np.int8), np.zeros(n, dtype=bool)
    present = np.isfinite(close) & (close > 0)
    gap_bars = max(1, int(round(pol.gap_days * 86_400_000 / step_ms)))
    seen, share, outage = _share_outage(present, gap_bars, pol)
    inc = (~present) & (~outage[:, None]) & seen
    c = np.cumsum(inc, axis=0)
    base = np.maximum.accumulate(np.where(present, c, 0), axis=0)
    since = c - base
    trig = inc & (since >= gap_bars) & (share[:, None] >= pol.min_others_share)
    ct = np.cumsum(trig, axis=0)
    tbase = np.maximum.accumulate(np.where(present, ct, 0), axis=0)
    delisted = (ct - tbase) > 0
    state = np.full((n, k), NON_COTE, dtype=np.int8)
    state[seen & ~present] = SANS_TRANSACTION
    state[seen & ~present & delisted] = RADIE
    state[seen & ~present & outage[:, None]] = PANNE
    state[present] = ACTIF
    return state, outage


def classify_reference(close: np.ndarray, step_ms: int, pol: DataPolicy) -> tuple[np.ndarray, np.ndarray]:
    """Référence lisible (boucle) : renvoie (états [t, m], drapeau de panne [t]). Causal : l'état en t ne dépend
    que de t' ≤ t."""
    n, k = close.shape
    present = np.isfinite(close) & (close > 0)
    gap_bars = max(1, int(round(pol.gap_days * 86_400_000 / step_ms)))
    seen, share, outage = _share_outage(present, gap_bars, pol)
    state = np.full((n, k), NON_COTE, dtype=np.int8)
    since = np.zeros(k, dtype=np.int64)  # bougies consécutives d'absence (hors pannes)
    delisted = np.zeros(k, dtype=bool)
    for i in range(n):
        for j in range(k):
            if not seen[i, j]:
                continue
            if present[i, j]:
                since[j] = 0
                delisted[j] = False  # un marché qui recote redevient actif (l'éventuelle vente est déjà faite)
                state[i, j] = ACTIF
                continue
            if outage[i]:
                state[i, j] = PANNE
                continue
            since[j] += 1
            if not delisted[j] and since[j] >= gap_bars and share[i] >= pol.min_others_share:
                delisted[j] = True
            state[i, j] = RADIE if delisted[j] else SANS_TRANSACTION
    return state, outage


# --------------------------------------------------------------------------------------------- exécution
@dataclass
class ExecConfig:
    initial_eur: float = 100.0
    fee_taker: float = 0.0025
    fee_maker: float = 0.0015
    slippage_bps: float = 5.0
    min_order_eur: float = 5.0
    amount_step: float = 1e-8  # pas de quantité (arrondi vers le bas)
    mode: str = "market"  # market | limit | hybrid
    limit_offset_bps: float = 5.0
    ttl_bars: int = 1
    through_bps: float = 2.0
    max_volume_share: float = 0.05


@dataclass
class Order:
    market: str
    side: str  # buy | sell
    eur: float  # montant visé (achat) ; pour une vente : valeur estimée
    qty: float | None  # quantité (vente) ; None pour un achat en euros
    kind: str  # market | limit
    limit_px: float | None
    ttl_bars: int
    decided_i: int
    reason: str
    cancel: str = ""


@dataclass
class SimResult:
    equity: pd.Series
    trades: list[dict[str, Any]]
    fees_eur: float
    orders: list[dict[str, Any]]
    states: np.ndarray
    outage_bars: int
    stats: dict[str, Any] = field(default_factory=dict)


Strategy = Callable[[int, "Engine"], dict[str, float]]  # i -> poids cibles (fraction de l'équité) par marché


class Engine:
    """Simulateur de portefeuille au comptant. `strategy(i, engine)` renvoie les POIDS cibles décidés à la clôture
    de i (0 pour sortir) ; l'exécution a lieu à partir de i+1."""

    def __init__(self, grid: np.ndarray, data: dict[str, pd.DataFrame], cfg: ExecConfig | None = None,
                 pol: DataPolicy | None = None, half_spread: dict[str, np.ndarray] | None = None,
                 states: tuple[np.ndarray, np.ndarray] | None = None) -> None:
        self.cfg, self.pol = cfg or ExecConfig(), pol or DataPolicy()
        self.grid = grid
        self.markets = sorted(data)
        self.j = {m: k for k, m in enumerate(self.markets)}
        col = lambda c: np.column_stack([data[m][c].to_numpy(float) for m in self.markets]) if self.markets else np.empty((len(grid), 0))  # noqa: E731
        self.O, self.H, self.L, self.C, self.V = col("open"), col("high"), col("low"), col("close"), col("volume")
        bad = ~(np.isfinite(self.O) & np.isfinite(self.H) & np.isfinite(self.L) & np.isfinite(self.C)
                & (self.O > 0) & (self.H > 0) & (self.L > 0) & (self.C > 0))
        for arr in (self.O, self.H, self.L, self.C):
            arr[bad] = np.nan
        self.V = np.where(np.isfinite(self.V), self.V, 0.0)
        self.step = int(np.median(np.diff(grid))) if len(grid) > 1 else 3_600_000
        self.state, self.outage = states if states is not None else classify(self.C, self.step, self.pol)
        self.last_px = np.full(len(self.markets), np.nan)
        self.hs = half_spread or {}
        self.cash = self.cfg.initial_eur
        self.qty = np.zeros(len(self.markets))
        self.cost = np.zeros(len(self.markets))
        self.entry_i = np.full(len(self.markets), -1)
        self.pending: list[Order] = []
        self.trades: list[dict[str, Any]] = []
        self.orders_log: list[dict[str, Any]] = []
        self.fees = 0.0
        self.i = -1
        self.counts = {"limite_remplis": 0, "limite_partiels": 0, "limite_manques": 0, "repli_marche": 0,
                       "reportes_sans_transaction": 0, "achats_annules_sans_transaction": 0}

    # ---- outils
    def equity(self) -> float:
        px = np.nan_to_num(self.last_px, nan=0.0)
        return float(self.cash + (self.qty * px).sum())

    def weight(self, m: str) -> float:
        e = self.equity()
        return float(self.qty[self.j[m]] * np.nan_to_num(self.last_px[self.j[m]]) / e) if e > 0 else 0.0

    def held(self) -> list[str]:
        return [m for m in self.markets if self.qty[self.j[m]] > 0]

    def _half_spread(self, j: int, i: int) -> float:
        arr = self.hs.get(self.markets[j])
        return float(arr[i]) if arr is not None and i < len(arr) and np.isfinite(arr[i]) else 0.0

    def _floor_qty(self, q: float) -> float:
        s = self.cfg.amount_step
        return math.floor(q / s + 1e-9) * s if s > 0 else q

    def _fill(self, j: int, i: int, side: str, px: float, eur: float | None, qty: float | None, maker: bool, why: str) -> float:
        """Exécute ; renvoie la quantité échangée (0 si rien)."""
        c = self.cfg
        if not (np.isfinite(px) and px > 0):
            return 0.0  # prix invalide : refus (D7)
        fee_r = c.fee_maker if maker else c.fee_taker
        if side == "buy":
            assert eur is not None
            eur = min(eur, self.cash / (1 + fee_r))
            q = self._floor_qty(eur / px)
            notional = q * px
            if notional < c.min_order_eur or q <= 0:
                return 0.0
            fee = notional * fee_r
            self.cash -= notional + fee
            self.qty[j] += q
            self.cost[j] += notional + fee
            if self.entry_i[j] < 0:
                self.entry_i[j] = i
        else:
            q = min(self.qty[j], qty if qty is not None else self.qty[j])
            if (self.qty[j] - q) * px < c.min_order_eur:
                q = self.qty[j]  # jamais de reliquat invendable (la position entière est déjà au pas)
            else:
                q = self._floor_qty(q)  # vente partielle : quantité arrondie vers le bas au pas (D8)
            if q <= 0:
                return 0.0
            notional = q * px
            fee = notional * fee_r
            share = q / self.qty[j]
            cost_part = self.cost[j] * share
            self.cash += notional - fee
            self.qty[j] -= q
            self.cost[j] -= cost_part
            self.trades.append({"market": self.markets[j], "entry_i": int(self.entry_i[j]), "exit_i": i,
                                "pnl": notional - fee - cost_part, "reason": why})
            if self.qty[j] <= 1e-15:
                self.qty[j], self.cost[j], self.entry_i[j] = 0.0, 0.0, -1
        self.fees += fee
        self.orders_log.append({"i": i, "market": self.markets[j], "side": side, "px": px, "notional": notional,
                                "fee": fee, "maker": maker, "reason": why})
        return float(q)

    # ---- boucle
    def step_bar(self, i: int, strategy: Strategy, start: int) -> None:
        self.i = i
        c = self.cfg
        if self.outage[i]:
            return  # panne de collecte : ni exécution, ni décision, valorisation gelée
        st = self.state[i]
        # 1) exécution des ordres décidés auparavant (ouverture de i)
        still: list[Order] = []
        for od in sorted(self.pending, key=lambda o: o.side != "sell"):  # ventes d'abord (libèrent la trésorerie) (D3)
            j = self.j[od.market]
            if st[j] != ACTIF:
                od.ttl_bars -= 1
                if od.side == "sell" and st[j] == SANS_TRANSACTION:
                    self.counts["reportes_sans_transaction"] += 1
                    still.append(od)  # une sortie est reportée tant que le marché n'est pas radié
                elif od.ttl_bars > 0:
                    self.counts["reportes_sans_transaction"] += 1
                    still.append(od)
                elif od.side == "buy":
                    self.counts["achats_annules_sans_transaction"] += 1  # coût d'opportunité : l'argent reste en euros (D5)
                continue
            slip = c.slippage_bps / 1e4 + self._half_spread(j, i - 1)
            if od.kind == "market":
                px = self.O[i, j] * (1 + slip if od.side == "buy" else 1 - slip)
                self._fill(j, i, od.side, px, od.eur, od.qty, False, od.reason)
                continue
            lp = float(od.limit_px or 0.0)
            through = (self.L[i, j] < lp * (1 - c.through_bps / 1e4)) if od.side == "buy" else (self.H[i, j] > lp * (1 + c.through_bps / 1e4))
            cap = self.V[i, j] * lp * c.max_volume_share  # plafond de participation, achats ET ventes (D4)
            if through and cap >= c.min_order_eur:
                if od.side == "buy":
                    eur = min(od.eur, cap)
                    q = self._fill(j, i, "buy", lp, eur, None, True, od.reason)
                    rest_eur = od.eur - q * lp
                    partial = q > 0 and rest_eur >= c.min_order_eur
                    rest = Order(od.market, "buy", rest_eur, None, "limit", lp, od.ttl_bars - 1, od.decided_i, od.reason, od.cancel)
                else:
                    want = od.qty if od.qty is not None else self.qty[j]
                    q = self._fill(j, i, "sell", lp, None, min(want, cap / lp), True, od.reason)
                    rest_q = min(want - q, self.qty[j])
                    partial = rest_q > 0 and self.qty[j] > 0
                    rest = Order(od.market, "sell", rest_q * lp, rest_q, "limit", lp, od.ttl_bars - 1, od.decided_i, od.reason, od.cancel)
                self.counts["limite_remplis"] += 1
                if not partial:
                    continue
                self.counts["limite_partiels"] += 1  # reliquat conservé (D4)
                od = rest
                if od.ttl_bars > 0:
                    still.append(od)
                elif c.mode == "hybrid" or od.side == "sell":
                    self.counts["repli_marche"] += 1
                    still.append(Order(od.market, od.side, od.eur, od.qty, "market", None, 1, i, od.reason + "_repli"))
                continue
            od.ttl_bars -= 1
            if od.ttl_bars > 0:
                still.append(od)
            elif c.mode == "hybrid" or od.side == "sell":  # une sortie n'est jamais abandonnée : repli au marché
                self.counts["repli_marche"] += 1
                still.append(Order(od.market, od.side, od.eur, od.qty, "market", None, 1, i, od.reason + "_repli"))
            else:
                self.counts["limite_manques"] += 1
        self.pending = still
        # 2) prix connus et radiations constatées
        ok = np.isfinite(self.C[i])
        self.last_px[ok] = self.C[i, ok]
        for jr in np.flatnonzero((st == RADIE) & (self.qty > 0)):
            self._fill(int(jr), i, "sell", float(self.last_px[jr]) * (1 - self.pol.delist_haircut), None, None, False, "radiation")
        self._check(i)
        # 3) décision à la clôture de i
        # D6 : une décision est prise aussi à la DERNIÈRE bougie (non exécutée dans l'échantillon) — c'est elle que
        # le prospectif enregistre comme prédiction via `next_orders()`.
        if i < start:
            return
        targets = strategy(i, self)
        # D2 : un ordre en attente n'est annulé que s'il est REMPLACÉ par une nouvelle cible sur le même marché, et
        # jamais s'il s'agit d'une vente (une sortie n'est jamais abandonnée).
        self.pending = [od for od in self.pending if od.side == "sell" or od.market not in targets]
        self.pending.extend(self._orders_for(targets, i))

    def _orders_for(self, targets: dict[str, float], i: int) -> list[Order]:
        c = self.cfg
        e = self.equity()
        out: list[Order] = []
        busy = {od.market for od in self.pending}

        def delta_of(item: tuple[str, float]) -> float:
            jj = self.j[item[0]]
            return item[1] * e - self.qty[jj] * float(np.nan_to_num(self.last_px[jj]))

        for m, w in sorted(targets.items(), key=delta_of):  # ventes (delta négatif) d'abord, puis achats
            j = self.j[m]
            px = self.last_px[j]
            if not np.isfinite(px) or m in busy or self.state[i, j] in (RADIE, NON_COTE):
                continue
            cur = self.qty[j] * px
            delta = w * e - cur
            if abs(delta) < c.min_order_eur:
                continue
            side = "buy" if delta > 0 else "sell"
            kind = "market" if c.mode == "market" else "limit"
            lp = px * (1 - c.limit_offset_bps / 1e4) if side == "buy" else px * (1 + c.limit_offset_bps / 1e4)
            qty = None if side == "buy" else min(self.qty[j], -delta / px) if w > 0 else self.qty[j]
            out.append(Order(m, side, max(0.0, delta) if side == "buy" else -delta, qty, kind,
                             lp if kind == "limit" else None, c.ttl_bars if kind == "limit" else 1, i,
                             "entree" if side == "buy" else "sortie",
                             cancel=("expire après la durée de validité ; repli au marché pour une vente" if kind == "limit"
                                     else "exécuté à l'ouverture suivante ; achat annulé si aucune transaction (euros conservés), vente reportée jusqu'à exécution ou radiation")))
        return out

    def _check(self, i: int) -> None:
        if self.cash < -1e-9 or (self.qty < -1e-12).any():
            raise AssertionError(f"invariant comptable violé à la bougie {i}")

    def next_orders(self) -> list[dict[str, Any]]:
        return [{"marche": o.market, "sens": o.side, "montant_eur": round(o.eur, 4),
                 "quantite": None if o.qty is None else round(o.qty, 10), "type": o.kind,
                 "prix_limite": None if o.limit_px is None else float(o.limit_px), "duree_validite_bougies": o.ttl_bars,
                 "decide_a": int(self.grid[o.decided_i]), "motif": o.reason, "annulation": o.cancel} for o in self.pending]


def simulate(grid: np.ndarray, data: dict[str, pd.DataFrame], strategy: Strategy, cfg: ExecConfig | None = None,
             pol: DataPolicy | None = None, start: int = 0, half_spread: dict[str, np.ndarray] | None = None,
             liquidate_at_end: bool = True, states: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[SimResult, Engine]:
    eng = Engine(grid, data, cfg, pol, half_spread, states)
    eq = np.full(len(grid), np.nan)
    inv = np.zeros(len(grid))
    for i in range(len(grid)):
        eng.step_bar(i, strategy, start)
        eq[i] = eng.equity()
        inv[i] = 1 - eng.cash / eq[i] if eq[i] > 0 else 0.0
    if liquidate_at_end:  # fin d'échantillon : vente au dernier prix connu, frais taker, AUCUNE décote
        n = len(grid) - 1
        for j in np.flatnonzero(eng.qty > 0):
            px = float(eng.last_px[j]) * (1 - eng.cfg.slippage_bps / 1e4)
            eng._fill(int(j), n, "sell", px, None, None, False, "fin")
        eq[-1] = eng.equity()
    res = SimResult(pd.Series(eq, index=grid), eng.trades, eng.fees, eng.orders_log, eng.state, int(eng.outage.sum()),
                    {"etats": {STATE_NAMES[s]: int((eng.state == s).sum()) for s in STATE_NAMES}, **eng.counts,
                     "exposition_reelle_moy": float(inv[start:].mean()) if len(inv) > start else 0.0})
    return res, eng
