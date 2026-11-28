"""Simulateur DIRECTIONNEL LONG / SHORT / CASH (phase 6) — MODULE EXPÉRIMENTAL, SIMULATION UNIQUEMENT.

NON DÉPLOYABLE : aucun code du moteur réel (`backend/runner.py`, `backend/execution`, `backend/guardian`) ne doit
l'importer (vérifié par `tests/test_directional.py`). Il n'émet aucun ordre, n'utilise aucune clé, aucun réseau.
Il ne produit ni probabilité ni score de confiance : une règle renvoie seulement LONG, SHORT ou CASH.

Conventions reprises du moteur au comptant `xsection_v4` (importé en LECTURE pour `sanitize`/`align`, non modifié) :
bougie invalide = ABSENTE (jamais un prix nul) ; dans CE module, une bougie OHLC INCOHÉRENTE (il faut
L ≤ min(O, C) ≤ max(O, C) ≤ H) est aussi traitée comme absente ; décision à la CLÔTURE de t, exécution à l'OUVERTURE de t+1 ; frais
sur le notionnel ; quantité arrondie VERS LE BAS au pas ; minimum d'ordre 5 € (en dessous : rien, l'argent reste en
euros) ; une sortie TOTALE n'est jamais bloquée par le minimum (jamais de reliquat invendable) ; valorisation au dernier
prix connu ; journal de toutes les transactions ; invariant comptable contrôlé à chaque bougie.

ORDRE DES OPÉRATIONS À LA BOUGIE i (documenté, PRUDENT) :
 1. frais d'emprunt de la bougie i pour chaque short ouvert au début de i (quantité × dernier prix connu × taux
    annuel × durée). CHOIX PRUDENT : toute bougie touchée est facturée ENTIÈRE, y compris la bougie d'entrée
    (facturée à l'entrée) et la bougie de sortie (facturée même si le rachat a lieu à son ouverture) ;
 2. si la bougie i est absente : rien ne s'exécute (sorties reportées, entrées annulées), valorisation gelée ;
 3. DISCONTINUITÉS à l'ouverture, AVANT le rappel : short — ouverture ≥ prix de liquidation => liquidation À
    L'OUVERTURE (+ pénalité : une position déjà sous le seuil de santé ne peut pas échapper à la pénalité par un
    rappel) ; sinon ouverture au-delà du stop (long : O ≤ stop ; short : O ≥ stop) => sortie À L'OUVERTURE ;
 4. RAPPEL du prêteur (`borrowable[m][i]` faux alors qu'un short est ouvert) : rachat forcé à l'ouverture ;
 5. sorties décidées à la clôture précédente (changement de signal), au marché à l'ouverture ; puis RÉDUCTION des
    shorts si le levier effectif mesuré à la clôture précédente dépassait `max_effective_gross` ;
 6. entrées décidées à la clôture précédente, au marché à l'ouverture (contrôles levier/trésorerie/minimum refaits) ;
 7. INTRA-BOUGIE (positions anciennes ET nouvelles) : le STOP (ou la liquidation, selon le niveau atteint en premier
    sur un trajet défavorable) est TOUJOURS supposé touché AVANT tout objectif ; si stop et TP sont dans la même bougie,
    seul le stop est exécuté. Un TP n'est exécuté que si la bougie ne touche pas le stop ET le DÉPASSE strictement
    (long : H > TP ; short : L < TP) ; TP1 n'est marqué atteint que si la quantité a réellement diminué ;
 8. clôture : dernier prix connu, mise à jour CAUSALE des stops suiveurs / seuil de rentabilité (effectifs à i+1),
    contrôle d'invariant, puis décision.

PRIX D'EXÉCUTION : marché = ouverture × (1 ± (glissement + demi-écart)) côté défavorable, frais taker ; stop = niveau
du stop × (1 ∓ (glissement + demi-écart)), frais taker (non borné par le bas/haut de bougie : prudent) ; objectif (TP)
= niveau exact, frais maker, jamais mieux que le niveau même si l'ouverture l'a dépassé (prudent) ; liquidation =
niveau (ou ouverture si discontinuité) défavorisé du glissement, frais taker + pénalité.

DRAPEAUX : `EXPERIMENTAL = True`, `DEPLOYABLE = False`, `API_AVAILABLE = False` (le short Bitvavo n'est accessible
que dans l'application, pas par l'API), `LEVERAGE = 1`.

HYPOTHÈSES PAR DÉFAUT (docs/SHORT_BITVAVO.md) : emprunt 0,0023 %/h (Bitvavo), stress ×3 via `borrow_stress`
[HYPOTHÈSE] ; frais de liquidation 2 % (Bitvavo) ; ratio de santé d'alerte 120 % (Bitvavo), liquidation 110 %
[HYPOTHÈSE] ; frais 0,25 % par côté [HYPOTHÈSE] ; glissement de liquidation 0,5 % [HYPOTHÈSE] ; glissement ordinaire
5 pb [HYPOTHÈSE] ; vol cible 2 %/jour, plafond 50 % par position [HYPOTHÈSE].

SHORT (marge ISOLÉE par position, aucune compensation entre positions) :
 - ouverture de q à p : produit de la vente P = q·p BLOQUÉ, collatéral propre M = ratio·q·p (ratio ≥ 1) prélevé sur la
   trésorerie et BLOQUÉ ; frais payés sur la trésorerie. Contribution à l'équité = M + P − q·prix ;
 - ratio de santé = (M + P) / (q·x) ; liquidation s'il passe sous 1 + mm (110 % par défaut), soit au prix
   L = (M+P)/(q(1+mm)) ; alerte (comptée) sous 120 % ;
 - la perte n'est PAS bornée : une ouverture au-delà de 2p (ratio 1) coûte plus que M + P ; l'excédent est prélevé sur
   la trésorerie, qui peut devenir négative (dette envers la plateforme, compteur `deficits`) ;
 - disponibilité d'emprunt : série booléenne par marché ; indisponible => pas de short ; indisponible alors qu'un
   short est ouvert => RAPPEL (rachat forcé).

AUCUN LEVIER À L'ENTRÉE : à chaque entrée, Σ|notionnels| (aux prix d'ouverture) + nouveau ≤ `max_gross` × équité
APRÈS frais, avec `max_gross` ≤ 1 (refus de configuration sinon) ; un short consomme autant d'euros propres qu'un long.
LIMITE HONNÊTE : un short ouvert sans levier PEUT DÉPASSER 1× ENSUITE (si le prix monte, la dette q·x croît et
l'équité baisse : short 0,5 à 100 puis prix 170 => 85 / 64,875 ≈ 1,31×). Le levier EFFECTIF Σ|notionnels| / équité
est donc mesuré à chaque clôture (`levier_effectif_max`) ; au-delà de `max_effective_gross` (1,5 par défaut
[HYPOTHÈSE]) les shorts sont réduits à l'ouverture suivante pour revenir à `max_gross` ; entre deux clôtures (et
jusqu'à l'ouverture suivante), le levier effectif peut rester au-dessus du seuil. AUCUNE
MARTINGALE : pas d'ajout à une position existante ; après un trade perdant, le notionnel des entrées suivantes est
plafonné au notionnel initial du trade perdant (plafond levé après un trade gagnant).

TAILLE : poids = min(poids_max, vol_cible / vol_journalière causale) × facteur du propriétaire
(1 − (r − 45)/55)² si le score de risque r > 45 (r = vol journalière 1 % -> 0, 12 % -> 100, ou série fournie) ;
historique insuffisant (ATR ou vol non définis) => ABSTENTION (aucune entrée).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .xsection_v4 import align, sanitize

EXPERIMENTAL = True
DEPLOYABLE = False
# Le short Bitvavo (marge) n'est PAS accessible par l'API publique (seulement dans l'application) : ce module ne
# peut donc servir qu'à l'étude ; il ne doit jamais être relié au moteur réel (docs/SHORT_BITVAVO.md).
API_AVAILABLE = False
LEVERAGE = 1  # levier fixé à 1 (aucun effet de levier)

# Hypothèses par défaut (docs/SHORT_BITVAVO.md) :
BITVAVO_BORROW_HOURLY = 0.000023  # 0,0023 %/h (Bitvavo, extrait officiel)
BITVAVO_LIQUIDATION_FEE = 0.02  # 2 % de la valeur de la position (Bitvavo)
HEALTH_ALERT = 1.20  # ratio de santé d'alerte (Bitvavo)
HEALTH_LIQUIDATION = 1.10  # [HYPOTHÈSE] seuil de liquidation non publié

LONG, SHORT, CASH = "LONG", "SHORT", "CASH"
DIRECTION = {LONG: 1, SHORT: -1, CASH: 0}
YEAR_MS = 365 * 86_400_000
DAY_MS = 86_400_000
EPS = 1e-9


def owner_risk_factor(risk_score: float, cut: float = 45.0) -> float:
    """Règle du propriétaire (identique à `portfolio.sizing_math.risk_factor`, recopiée pour garder ce module
    autonome) : 1 si r ≤ 45 ; (1 − (r − 45)/55)² au-delà ; 0 à 100 ; score inconnu (NaN) -> 0."""
    if not math.isfinite(risk_score):
        return 0.0
    rs = min(100.0, max(0.0, risk_score))
    if rs <= cut:
        return 1.0
    return max(0.0, 1.0 - (rs - cut) / (100.0 - cut)) ** 2


def risk_score_from_vol(daily_vol: float, lo: float = 0.01, hi: float = 0.12) -> float:
    """Vol journalière 1 % -> 0 ; 12 % -> 100 (même échelle que `xsection.risk_score_vol`). Pas une probabilité."""
    if not math.isfinite(daily_vol):
        return float("nan")
    return float(np.clip((daily_vol - lo) / (hi - lo), 0.0, 1.0) * 100)


# --------------------------------------------------------------------------------------------- configuration
@dataclass
class DirConfig:
    initial_eur: float = 100.0
    fee_taker: float = 0.0025  # [HYPOTHÈSE] 0,25 % par côté
    fee_maker: float = 0.0025  # [HYPOTHÈSE] 0,25 % par côté (aucune remise maker supposée)
    slippage_bps: float = 5.0
    half_spread: float = 0.0  # demi-écart (fraction du prix), constant ; série par marché possible
    min_order_eur: float = 5.0
    amount_step: float = 1e-8
    allow_short: bool = True
    max_gross: float = 1.0  # Σ|notionnels| ≤ max_gross × équité à l'ENTRÉE ; ≤ 1 (aucun levier)
    max_effective_gross: float = 1.5  # [HYPOTHÈSE] levier effectif mesuré au-delà duquel les shorts sont réduits
    collateral_ratio: float = 1.0  # collatéral propre / notionnel du short ; ≥ 1
    # ratio de santé = (M + P) / (q·x) ; liquidation sous 1 + maintenance_margin (110 % [HYPOTHÈSE])
    maintenance_margin: float = HEALTH_LIQUIDATION - 1.0
    health_alert: float = HEALTH_ALERT  # alerte (comptée, sans action) sous 120 %
    liquidation_penalty: float = BITVAVO_LIQUIDATION_FEE  # frais de liquidation (fraction de la valeur rachetée)
    liquidation_slippage: float = 0.005  # [HYPOTHÈSE] glissement supplémentaire de 0,5 % sur liquidation
    borrow_rate_annual: float = BITVAVO_BORROW_HOURLY * 8760  # 0,0023 %/h proratisé au temps (≈ 20,1 %/an)
    borrow_stress: float = 1.0  # multiplicateur de stress du taux (scénario ×3 [HYPOTHÈSE])
    max_weight: float = 0.5  # plafond par position (fraction de l'équité)
    target_vol_daily: float = 0.02  # [HYPOTHÈSE] volatilité cible journalière par position
    vol_window: int = 20
    atr_window: int = 14
    risk_cut: float = 45.0
    stop_atr: float = 2.0
    tp1_atr: float | None = 2.0
    tp1_fraction: float = 0.5
    tp2_atr: float | None = 4.0
    trail_atr: float | None = 3.0
    breakeven_after_tp1: bool = True
    rearm_after_exit: bool = True  # après une sortie protectrice, ré-entrée seulement après un changement de signal
    anti_martingale: bool = True

    def validate(self) -> None:
        if not 0 < self.max_gross <= 1.0:
            raise ValueError("max_gross doit être dans ]0, 1] : aucun effet de levier")
        if self.collateral_ratio < 1.0:
            raise ValueError("collateral_ratio doit être ≥ 1 : collatéral ≥ 100 % du notionnel")
        if not 0 < self.max_weight <= 1.0:
            raise ValueError("max_weight doit être dans ]0, 1]")
        if not 0 < self.tp1_fraction <= 1.0:
            raise ValueError("tp1_fraction doit être dans ]0, 1]")
        if self.stop_atr <= 0:
            raise ValueError("stop_atr doit être > 0")
        if self.max_effective_gross < self.max_gross:
            raise ValueError("max_effective_gross doit être ≥ max_gross")
        if self.maintenance_margin < 0 or self.borrow_stress < 0:
            raise ValueError("maintenance_margin et borrow_stress doivent être ≥ 0")


@dataclass
class Position:
    side: int  # +1 long, −1 short
    qty: float
    entry_px: float
    entry_i: int
    notional0: float
    stop: float
    tp1: float | None
    tp2: float | None
    extreme: float  # plus haut (long) / plus bas (short) depuis l'entrée, pour le stop suiveur
    stop0: float = 0.0  # stop initial (un stop déplacé est un stop SUIVEUR)
    cost: float = 0.0  # long : coût restant (notionnel + frais) ; short : frais d'entrée restants
    collat: float = 0.0  # short : collatéral propre bloqué M
    proceeds: float = 0.0  # short : produit de la vente bloqué P
    tp1_done: bool = False
    realized: float = 0.0
    borrow_paid: float = 0.0


@dataclass
class PendingEntry:
    market: str
    side: int
    eur: float
    atr: float
    decided_i: int


@dataclass
class DirResult:
    equity: pd.Series
    journal: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    stats: dict[str, Any] = field(default_factory=dict)


class MarketView:
    """Vue CAUSALE remise à la règle à la clôture de i. Elle ne reçoit QUE des COPIES tronquées aux bougies 0..i
    (aucune référence au moteur ni aux tableaux complets : rien du futur n'est atteignable depuis la vue)."""

    def __init__(self, i: int, markets: list[str], o_: np.ndarray, h_: np.ndarray, l_: np.ndarray, c_: np.ndarray,
                 atr_row: np.ndarray, positions: Mapping[str, int]) -> None:
        self.i = i
        self.markets = list(markets)
        self._j = {m: k for k, m in enumerate(self.markets)}
        n = i + 1
        self._O, self._H, self._L, self._C = (np.array(a[:n], copy=True) for a in (o_, h_, l_, c_))
        self._atr = np.array(atr_row, copy=True)
        self._pos = dict(positions)

    def opens(self, m: str) -> np.ndarray:
        return self._O[:, self._j[m]].copy()

    def highs(self, m: str) -> np.ndarray:
        return self._H[:, self._j[m]].copy()

    def lows(self, m: str) -> np.ndarray:
        return self._L[:, self._j[m]].copy()

    def closes(self, m: str) -> np.ndarray:
        return self._C[:, self._j[m]].copy()

    def atr(self, m: str) -> float:
        return float(self._atr[self._j[m]])

    def position(self, m: str) -> int:
        return int(self._pos.get(m, 0))


Rule = Callable[[int, MarketView], Mapping[str, str]]


def long_only(rule: Rule) -> Rule:
    """Même règle privée du côté SHORT (SHORT -> CASH) : isole l'apport du short."""
    def wrapped(i: int, view: MarketView) -> dict[str, str]:
        return {m: (CASH if s == SHORT else s) for m, s in rule(i, view).items()}
    return wrapped


def always_cash(i: int, view: MarketView) -> dict[str, str]:
    return {}


# --------------------------------------------------------------------------------------------- indicateurs causaux
def causal_atr(H: np.ndarray, L: np.ndarray, C: np.ndarray, n: int) -> np.ndarray:
    """ATR simple : moyenne des n derniers vrais intervalles TR_t = max(H−L, |H−C_prec|, |L−C_prec|), où C_prec est
    la DERNIÈRE CLÔTURE CONNUE avant t (l'écart accumulé pendant une bougie absente est donc compté) ; TR = H−L s'il
    n'existe aucune clôture antérieure. Défini à partir de t = n−1 ; bougie absente dans la fenêtre => NaN."""
    prev = np.full_like(C, np.nan)
    last = np.full(C.shape[1] if C.ndim == 2 else 0, np.nan)
    for t in range(len(C)):
        prev[t] = last
        ok = np.isfinite(C[t])
        last = np.where(ok, C[t], last)
    hl = H - L
    tr = np.where(np.isfinite(prev), np.fmax(hl, np.fmax(np.abs(H - prev), np.abs(L - prev))), hl)
    tr[np.isnan(H) | np.isnan(L)] = np.nan
    out = np.full_like(tr, np.nan)
    for t in range(n - 1, len(tr)):
        out[t] = tr[t - n + 1: t + 1].mean(axis=0)  # NaN propagé
    return out


def causal_daily_vol(C: np.ndarray, n: int, step_ms: int) -> np.ndarray:
    """Écart-type (ddof=0) des n derniers log-rendements de clôture, × sqrt(bougies par jour). NaN si incomplet."""
    lr = np.full_like(C, np.nan)
    if len(C) > 1:
        lr[1:] = np.log(C[1:] / C[:-1])
    out = np.full_like(C, np.nan)
    k = math.sqrt(DAY_MS / step_ms)
    for t in range(n, len(C)):
        out[t] = lr[t - n + 1: t + 1].std(axis=0) * k
    return out


# --------------------------------------------------------------------------------------------- moteur
class DirEngine:
    def __init__(self, grid: np.ndarray, data: dict[str, pd.DataFrame], cfg: DirConfig | None = None,
                 borrowable: Mapping[str, np.ndarray] | None = None,
                 borrow_rate: Mapping[str, np.ndarray] | None = None,
                 risk_score: Mapping[str, np.ndarray] | None = None,
                 half_spread: Mapping[str, np.ndarray] | None = None) -> None:
        self.cfg = cfg or DirConfig()
        self.cfg.validate()
        self.grid = grid
        self.markets = sorted(data)
        self.j = {m: k for k, m in enumerate(self.markets)}
        data = {m: sanitize(d) for m, d in data.items()}
        n, k = len(grid), len(self.markets)

        def col(c: str) -> np.ndarray:
            if not k:
                return np.empty((n, 0))
            return np.column_stack([data[m][c].to_numpy(float) for m in self.markets])

        self.O, self.H, self.L, self.C = col("open"), col("high"), col("low"), col("close")
        fin = np.isfinite(self.O) & np.isfinite(self.H) & np.isfinite(self.L) & np.isfinite(self.C)
        with np.errstate(invalid="ignore"):
            coherent = (self.L <= np.minimum(self.O, self.C)) & (np.maximum(self.O, self.C) <= self.H)
        self.incoherent = fin & ~coherent
        self.present = fin & coherent  # bougie OHLC incohérente => ABSENTE dans ce module
        for arr in (self.O, self.H, self.L, self.C):
            arr[~self.present] = np.nan
        self.step = int(np.median(np.diff(grid))) if n > 1 else 3_600_000
        self.dt_year = self.step / YEAR_MS
        self.ATR = causal_atr(self.H, self.L, self.C, self.cfg.atr_window)
        self.VOL = causal_daily_vol(self.C, self.cfg.vol_window, self.step)
        self.borrowable = borrowable  # None => tout est empruntable ; marché absent du dict => NON empruntable
        self.borrow_rate = borrow_rate or {}
        self.risk_override = risk_score or {}
        self.hs = half_spread or {}
        self.cash = self.cfg.initial_eur
        self.pos: dict[str, Position] = {}
        self.last_px = np.full(k, np.nan)
        self.pending_exit: set[str] = set()
        self.pending_entry: list[PendingEntry] = []
        self.blocked: dict[str, int] = {}  # sens bloqué jusqu'au réarmement
        self.loss_cap = math.inf
        self.realized_total = 0.0
        self.journal: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.entry_gross: list[float] = []  # (Σ|notionnels| / équité) juste après chaque entrée
        self.effective_gross: list[float] = []  # levier effectif mesuré à chaque clôture
        self.reduce_shorts = False  # réduction des shorts demandée pour l'ouverture suivante
        self.counts = {"fees": 0.0, "borrow_fees": 0.0, "penalties": 0.0, "liquidations": 0, "rappels": 0,
                       "stops": 0, "trailing": 0, "tp1": 0, "tp2": 0, "deficits": 0, "entrees_annulees": 0,
                       "sous_minimum": 0, "short_refuse_emprunt": 0, "gaps": 0, "alertes_sante": 0,
                       "reductions_levier": 0, "bougies_incoherentes": int(self.incoherent.sum())}
        self.i = -1

    # ---- outils
    def _slip(self, j: int, i: int) -> float:
        arr = self.hs.get(self.markets[j])
        hs = float(arr[i]) if arr is not None and i < len(arr) and np.isfinite(arr[i]) else self.cfg.half_spread
        return self.cfg.slippage_bps / 1e4 + hs

    def _floor(self, q: float) -> float:
        s = self.cfg.amount_step
        return math.floor(q / s + 1e-9) * s if s > 0 else q

    def can_borrow(self, m: str, i: int) -> bool:
        if self.borrowable is None:
            return True
        arr = self.borrowable.get(m)
        return bool(arr is not None and i < len(arr) and arr[i])

    def _rate(self, m: str, i: int) -> float:
        arr = self.borrow_rate.get(m)
        base = float(arr[i]) if arr is not None and i < len(arr) and np.isfinite(arr[i]) else self.cfg.borrow_rate_annual
        return base * self.cfg.borrow_stress

    def _px(self, m: str, mark: np.ndarray | None = None) -> float:
        v = (mark if mark is not None else self.last_px)[self.j[m]]
        return float(v) if np.isfinite(v) else float(self.last_px[self.j[m]])

    def equity(self, mark: np.ndarray | None = None) -> float:
        """équité = trésorerie + collatéral bloqué (M + P) + valeur longue − dette courte."""
        e = self.cash
        for m, p in self.pos.items():
            px = self._px(m, mark)
            e += p.qty * px if p.side > 0 else p.collat + p.proceeds - p.qty * px
        return float(e)

    def gross(self, mark: np.ndarray | None = None, exclude: set[str] | None = None) -> float:
        return float(sum(p.qty * self._px(m, mark) for m, p in self.pos.items() if not exclude or m not in exclude))

    def collateral(self) -> float:
        return float(sum(p.collat + p.proceeds for p in self.pos.values() if p.side < 0))

    def liq_price(self, p: Position) -> float:
        return (p.collat + p.proceeds) / (p.qty * (1 + self.cfg.maintenance_margin)) if p.qty > 0 else math.inf

    def _log(self, i: int, m: str, action: str, side: str, qty: float, px: float, fee: float, extra: float = 0.0,
             pnl: float = 0.0) -> None:
        self.journal.append({"i": i, "ts": int(self.grid[i]), "market": m, "action": action, "side": side,
                             "qty": qty, "px": px, "notional": qty * px, "fee": fee, "penalty": extra,
                             "pnl": pnl, "cash_after": self.cash})

    # ---- entrées / sorties
    def _open(self, pe: PendingEntry, i: int) -> None:
        c, m, j = self.cfg, pe.market, self.j[pe.market]
        slip = self._slip(j, i)
        px = self.O[i, j] * (1 + slip if pe.side > 0 else 1 - slip)
        mark = np.where(self.present[i], self.O[i], self.last_px)
        e_open, g_open = self.equity(mark), self.gross(mark)
        # aucun levier, frictions comprises (k = ouverture / prix d'exécution, b = emprunt de la bougie d'entrée) :
        # g + n k ≤ G (E − f n − |k − 1| n − b n)  =>  n ≤ (G E − g) / (k + G (f + |k − 1| + b))
        k_px = self.O[i, j] / px
        b_rate = self._rate(m, i) * self.dt_year if pe.side < 0 else 0.0
        room = (c.max_gross * e_open - g_open) / (k_px + c.max_gross * (c.fee_taker + abs(k_px - 1) + b_rate))
        need = 1 + c.fee_taker if pe.side > 0 else c.collateral_ratio + c.fee_taker
        eur = min(pe.eur, room, max(0.0, self.cash) / need)
        q = self._floor(max(0.0, eur) / px)
        n = q * px
        if q <= 0 or n < c.min_order_eur:
            self.counts["sous_minimum"] += 1
            return
        fee = n * c.fee_taker
        stop = px - pe.side * c.stop_atr * pe.atr
        tp1 = px + pe.side * c.tp1_atr * pe.atr if c.tp1_atr is not None else None
        tp2 = px + pe.side * c.tp2_atr * pe.atr if c.tp2_atr is not None else None
        p = Position(pe.side, q, px, i, n, stop, tp1, tp2, px, stop)
        if pe.side > 0:
            self.cash -= n + fee
            p.cost = n + fee
        else:
            p.collat, p.proceeds, p.cost = c.collateral_ratio * n, n, fee
            self.cash -= p.collat + fee
        self.counts["fees"] += fee
        self.pos[m] = p
        self._log(i, m, "entree_long" if pe.side > 0 else "entree_short", "buy" if pe.side > 0 else "sell", q, px, fee)
        if pe.side < 0:
            self._borrow_fee(m, i, px)  # la bougie d'entrée est facturée entière (prudent)
        e_after = self.equity(mark)
        ratio = self.gross(mark) / e_after if e_after > 0 else math.inf
        self.entry_gross.append(ratio)
        if ratio > c.max_gross + 1e-9:
            raise AssertionError(f"levier détecté à la bougie {i} : {ratio}")

    def _close(self, m: str, i: int, frac: float, px: float, maker: bool, action: str, penalty_rate: float = 0.0) -> bool:
        """Ferme la fraction `frac` (1 = tout) de la position au prix d'exécution `px` (déjà défavorisé).
        Renvoie True si une quantité a réellement été échangée."""
        c, p = self.cfg, self.pos[m]
        if frac >= 1.0:
            q = p.qty
        else:
            q = self._floor(p.qty * frac)
            if (p.qty - q) * px < c.min_order_eur:
                q = p.qty  # jamais de reliquat sous le minimum
            elif q * px < c.min_order_eur:
                self.counts["sous_minimum"] += 1
                return False  # sortie partielle sous le minimum : impossible, la position reste entière
        share = q / p.qty
        n = q * px
        fee = n * (c.fee_maker if maker else c.fee_taker)
        pen = n * penalty_rate
        if p.side > 0:
            cost_part = p.cost * share
            pnl = n - fee - cost_part
            self.cash += n - fee
            p.cost -= cost_part
        else:
            m_part, p_part, f_part = p.collat * share, p.proceeds * share, p.cost * share
            pnl = p_part - n - fee - pen - f_part
            self.cash += m_part + p_part - n - fee - pen
            p.collat -= m_part
            p.proceeds -= p_part
            p.cost -= f_part
        self.counts["fees"] += fee
        self.counts["penalties"] += pen
        if action.startswith("trailing"):
            self.counts["trailing"] += 1
        self.realized_total += pnl
        p.realized += pnl
        p.qty -= q
        if self.cash < -EPS:
            self.counts["deficits"] += 1  # perte au-delà du collatéral et de la trésorerie : dette
        self._log(i, m, action, "sell" if p.side > 0 else "buy", q, px, fee, pen, pnl)
        if p.qty <= 1e-15 or share >= 1.0 - 1e-12:
            self._finish(m, i, action)
        return q > 0

    def _finish(self, m: str, i: int, why: str) -> None:
        p = self.pos.pop(m)
        total = p.realized - p.borrow_paid
        self.trades.append({"market": m, "side": LONG if p.side > 0 else SHORT, "entry_i": p.entry_i, "exit_i": i,
                            "entry_px": p.entry_px, "notional0": p.notional0, "pnl": total, "reason": why})
        if self.cfg.anti_martingale:
            self.loss_cap = min(self.loss_cap, p.notional0) if total < 0 else math.inf
        if self.cfg.rearm_after_exit and why != "signal":
            self.blocked[m] = p.side
        self.pending_exit.discard(m)

    def _borrow_fee(self, m: str, i: int, ref_px: float) -> None:
        p = self.pos[m]
        b = p.qty * ref_px * self._rate(m, i) * self.dt_year
        if b <= 0:
            return
        from_cash = min(b, max(0.0, self.cash))
        self.cash -= from_cash
        p.collat -= b - from_cash  # trésorerie insuffisante : prélevé sur le collatéral propre
        p.borrow_paid += b
        self.realized_total -= b
        self.counts["borrow_fees"] += b

    # ---- protections
    def _gap_and_intrabar(self, m: str, i: int, at_open: bool) -> None:
        c, p, j = self.cfg, self.pos[m], self.j[m]
        o, h, lo = self.O[i, j], self.H[i, j], self.L[i, j]
        slip = self._slip(j, i)
        moved = p.stop > p.stop0 + EPS if p.side > 0 else p.stop < p.stop0 - EPS
        stop_action = "trailing" if moved else "stop"
        if p.side > 0:
            if at_open:
                if o <= p.stop:
                    self.counts["gaps"] += 1
                    self.counts["stops"] += 1
                    self._close(m, i, 1.0, o * (1 - slip), False, stop_action + "_gap")
                return
            if lo <= p.stop:  # stop supposé touché AVANT tout objectif
                self.counts["stops"] += 1
                self._close(m, i, 1.0, p.stop * (1 - slip), False, stop_action)
                return
            if p.tp1 is not None and not p.tp1_done and h > p.tp1:
                if self._close(m, i, c.tp1_fraction, p.tp1, True, "tp1"):
                    p.tp1_done = True
                    self.counts["tp1"] += 1
                if m not in self.pos:
                    return
            if p.tp2 is not None and h > p.tp2:
                self.counts["tp2"] += 1
                self._close(m, i, 1.0, p.tp2, True, "tp2")
            return
        # short
        liq = self.liq_price(p)
        if at_open:
            if o >= liq:
                self.counts["gaps"] += 1
                self._liquidate(m, i, o * (1 + slip + c.liquidation_slippage))
            elif o >= p.stop:
                self.counts["gaps"] += 1
                self.counts["stops"] += 1
                self._close(m, i, 1.0, o * (1 + slip), False, stop_action + "_gap")
            return
        if h >= min(p.stop, liq):  # trajet défavorable : le premier niveau atteint s'exécute
            if p.stop < liq:
                self.counts["stops"] += 1
                self._close(m, i, 1.0, p.stop * (1 + slip), False, stop_action)
            else:
                self._liquidate(m, i, liq * (1 + slip + c.liquidation_slippage))
            return
        if p.tp1 is not None and not p.tp1_done and lo < p.tp1:
            if self._close(m, i, c.tp1_fraction, p.tp1, True, "tp1"):
                p.tp1_done = True
                self.counts["tp1"] += 1
            if m not in self.pos:
                return
        if p.tp2 is not None and lo < p.tp2:
            self.counts["tp2"] += 1
            self._close(m, i, 1.0, p.tp2, True, "tp2")

    def _liquidate(self, m: str, i: int, px: float) -> None:
        self.counts["liquidations"] += 1
        self._close(m, i, 1.0, px, False, "liquidation", self.cfg.liquidation_penalty)

    def _update_stops(self, i: int) -> None:
        """À la clôture de i (CAUSAL, effectif à i+1) : stop suiveur et seuil de rentabilité ; un stop ne recule jamais."""
        c = self.cfg
        for m, p in self.pos.items():
            j = self.j[m]
            if not self.present[i, j]:
                continue
            atr = self.ATR[i, j]
            if p.side > 0:
                p.extreme = max(p.extreme, self.H[i, j])
                cand = [p.stop]
                if c.trail_atr is not None and np.isfinite(atr):
                    cand.append(p.extreme - c.trail_atr * atr)
                if c.breakeven_after_tp1 and p.tp1_done:
                    cand.append(p.entry_px)
                p.stop = max(cand)
            else:
                p.extreme = min(p.extreme, self.L[i, j])
                cand = [p.stop]
                if c.trail_atr is not None and np.isfinite(atr):
                    cand.append(p.extreme + c.trail_atr * atr)
                if c.breakeven_after_tp1 and p.tp1_done:
                    cand.append(p.entry_px)
                p.stop = min(cand)

    # ---- boucle
    def step_bar(self, i: int, rule: Rule) -> None:
        self.i = i
        # 1) frais d'emprunt de la bougie i
        for m in [m for m, p in self.pos.items() if p.side < 0]:
            self._borrow_fee(m, i, float(self.last_px[self.j[m]]))
        live = self.present[i]
        # 2) discontinuités à l'ouverture (liquidation AVANT rappel : la pénalité ne peut pas être évitée)
        for m in list(self.pos):
            if live[self.j[m]]:
                self._gap_and_intrabar(m, i, at_open=True)
        # 3) rappel du prêteur
        for m in [m for m, p in self.pos.items() if p.side < 0]:
            j = self.j[m]
            if not self.can_borrow(m, i) and live[j]:
                self.counts["rappels"] += 1
                self._close(m, i, 1.0, self.O[i, j] * (1 + self._slip(j, i)), False, "rappel")
        # 4) sorties sur signal
        for m in sorted(self.pending_exit):
            j = self.j[m]
            if m in self.pos and live[j]:
                p = self.pos[m]
                slip = self._slip(j, i)
                self._close(m, i, 1.0, self.O[i, j] * (1 - slip if p.side > 0 else 1 + slip), False, "signal")
        self.pending_exit = {m for m in self.pending_exit if m in self.pos}  # sortie reportée si pas de bougie
        if self.reduce_shorts:
            self._reduce_shorts(i)
        # 5) entrées
        for pe in self.pending_entry:
            j = self.j[pe.market]
            ok = live[j] and pe.market not in self.pos and (pe.side > 0 or self.can_borrow(pe.market, i))
            if not ok:
                self.counts["entrees_annulees"] += 1
                continue
            self._open(pe, i)
        self.pending_entry = []
        # 6) intra-bougie
        for m in list(self.pos):
            if live[self.j[m]]:
                self._gap_and_intrabar(m, i, at_open=False)
        # 7) clôture
        self.last_px[live] = self.C[i, live]
        for m, p in self.pos.items():
            if p.side < 0 and p.qty > 0 and (p.collat + p.proceeds) / (p.qty * self._px(m)) < self.cfg.health_alert:
                self.counts["alertes_sante"] += 1
        self._update_stops(i)
        self._check(i)
        self._measure_leverage()
        self._decide(i, rule)

    def _measure_leverage(self) -> None:
        """Levier EFFECTIF à la clôture = Σ|notionnels| / équité (∞ si équité ≤ 0 avec positions ouvertes).
        Au-delà de `max_effective_gross`, les shorts seront réduits à l'ouverture suivante."""
        if not self.pos:
            self.effective_gross.append(0.0)
            self.reduce_shorts = False
            return
        e, g = self.equity(), self.gross()
        ratio = g / e if e > 0 else math.inf
        self.effective_gross.append(ratio)
        self.reduce_shorts = ratio > self.cfg.max_effective_gross + 1e-12 and any(p.side < 0 for p in self.pos.values())

    def _reduce_shorts(self, i: int) -> None:
        """Rachète la même fraction f de chaque short ouvert, à l'ouverture de i, pour ramener Σ|notionnels| ≤
        max_gross × équité : g − f S ≤ G (E − f S φ) => f = (g − G E) / (S (1 − G φ)), φ = frais + glissement ;
        f borné à [0, 1] (équité ≤ 0 => rachat total)."""
        self.reduce_shorts = False
        c = self.cfg
        shorts = [m for m, p in self.pos.items() if p.side < 0 and self.present[i, self.j[m]]]
        if not shorts:
            return
        mark = np.where(self.present[i], self.O[i], self.last_px)
        e, g = self.equity(mark), self.gross(mark)
        if e > 0 and g <= c.max_gross * e:
            return
        s_tot = sum(self.pos[m].qty * self._px(m, mark) for m in shorts)
        phi = c.fee_taker + max(self._slip(self.j[m], i) for m in shorts)
        f = 1.0 if e <= 0 else min(1.0, max(0.0, (g - c.max_gross * e) / (s_tot * (1 - c.max_gross * phi))))
        if f <= 0:
            return
        self.counts["reductions_levier"] += 1
        for m in shorts:
            j = self.j[m]
            self._close(m, i, f, self.O[i, j] * (1 + self._slip(j, i)), False, "reduction_levier")

    def _decide(self, i: int, rule: Rule) -> None:
        c = self.cfg
        view = MarketView(i, self.markets, self.O, self.H, self.L, self.C, self.ATR[i],
                          {m: p.side for m, p in self.pos.items()})
        sig = dict(rule(i, view))
        e = self.equity()
        wants: dict[str, int] = {}
        for m in self.markets:
            s = sig.get(m, CASH)
            if s not in DIRECTION:
                raise ValueError(f"signal inconnu {s!r} (attendu LONG, SHORT ou CASH)")
            d = DIRECTION[s]
            if d < 0 and not c.allow_short:
                d = 0
            if self.blocked.get(m) is not None and self.blocked[m] != d:
                del self.blocked[m]  # réarmement : la règle a changé d'avis
            wants[m] = d
            p = self.pos.get(m)
            if p and p.side != d:
                self.pending_exit.add(m)
            elif p:
                self.pending_exit.discard(m)  # la décision la plus récente prévaut : sortie reportée annulée
        if e <= 0:
            return
        committed = self.gross(exclude=self.pending_exit)
        for m in self.markets:
            d, j = wants[m], self.j[m]
            p = self.pos.get(m)
            if d == 0 or (p and p.side == d) or self.blocked.get(m) == d or not self.present[i, j]:
                continue
            if d < 0 and not self.can_borrow(m, i):
                self.counts["short_refuse_emprunt"] += 1
                continue
            atr, vol = float(self.ATR[i, j]), float(self.VOL[i, j])
            if not (np.isfinite(atr) and atr > 0 and np.isfinite(vol)):
                continue  # preuves insuffisantes : abstention
            w = c.max_weight if vol <= 0 else min(c.max_weight, c.target_vol_daily / vol)
            ro = self.risk_override.get(m)
            rs = float(ro[i]) if ro is not None and i < len(ro) else risk_score_from_vol(vol)
            w *= owner_risk_factor(rs, c.risk_cut)
            eur = min(w * e, self.loss_cap, c.max_gross * e - committed)
            if eur < c.min_order_eur:
                if w > 0:
                    self.counts["sous_minimum"] += 1
                continue
            committed += eur
            self.pending_entry.append(PendingEntry(m, d, eur, atr, i))

    def _check(self, i: int) -> None:
        e = self.equity()
        unreal = 0.0
        for m, p in self.pos.items():
            px = self._px(m)
            if p.qty < -1e-12 or p.collat < -1e-9 or p.proceeds < -1e-9:
                raise AssertionError(f"quantité ou collatéral négatif à la bougie {i}")
            unreal += p.qty * px - p.cost if p.side > 0 else p.proceeds - p.qty * px - p.cost
        recon = self.cfg.initial_eur + self.realized_total + unreal
        if abs(e - recon) > 1e-6:
            raise AssertionError(f"invariant comptable violé à la bougie {i} : {e} != {recon}")
        if self.cash < -EPS and self.counts["deficits"] == 0:
            raise AssertionError(f"trésorerie négative sans déficit constaté à la bougie {i}")

    def close_all(self, i: int) -> None:
        for m in list(self.pos):
            j, p = self.j[m], self.pos[m]
            slip = self._slip(j, i)
            px = float(self.last_px[j])
            self._close(m, i, 1.0, px * (1 - slip if p.side > 0 else 1 + slip), False, "fin")


def max_drawdown(eq: np.ndarray) -> float:
    if not len(eq):
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / peak)) if np.all(peak > 0) else 1.0


def simulate(grid: np.ndarray, data: dict[str, pd.DataFrame], rule: Rule, cfg: DirConfig | None = None,
             close_at_end: bool = True, **exo: Any) -> tuple[DirResult, DirEngine]:
    """Simule `rule` sur la grille. `exo` : borrowable, borrow_rate, risk_score, half_spread (séries par marché)."""
    eng = DirEngine(grid, data, cfg, **exo)
    eq = np.full(len(grid), np.nan)
    for i in range(len(grid)):
        eng.step_bar(i, rule)
        eq[i] = eng.equity()
    if close_at_end and len(grid):
        eng.close_all(len(grid) - 1)
        eq[-1] = eng.equity()
        eng._check(len(grid) - 1)
    stats = {**eng.counts, "equity_finale": float(eq[-1]) if len(eq) else eng.cfg.initial_eur,
             "max_drawdown": max_drawdown(eq), "n_trades": len(eng.trades),
             "trades_perdants": sum(1 for t in eng.trades if t["pnl"] < 0),
             "levier_max_entree": max(eng.entry_gross, default=0.0),
             "levier_effectif_max": max(eng.effective_gross, default=0.0)}
    return DirResult(pd.Series(eq, index=grid), eng.journal, eng.trades, stats), eng


def simulate_frames(data: dict[str, pd.DataFrame], rule: Rule, cfg: DirConfig | None = None, step_ms: int = 3_600_000,
                    **kw: Any) -> tuple[DirResult, DirEngine]:
    grid, al = align(data, step_ms)
    return simulate(grid, al, rule, cfg, **kw)


def compare(grid: np.ndarray, data: dict[str, pd.DataFrame], rule: Rule, cfg: DirConfig | None = None,
            **kw: Any) -> dict[str, Any]:
    """Même règle avec SHORT (LONG/SHORT/CASH), sans SHORT (LONG/CASH) et référence CASH ; même configuration.
    `apport_short` = équité finale (avec short) − équité finale (sans short). Aucune probabilité n'est calculée."""
    out: dict[str, Any] = {}
    for name, r in (("long_short", rule), ("long_cash", long_only(rule)), ("cash", always_cash)):
        res, _ = simulate(grid, data, r, cfg, **kw)
        out[name] = res.stats
    out["apport_short"] = out["long_short"]["equity_finale"] - out["long_cash"]["equity_finale"]
    out["apport_long_cash_vs_cash"] = out["long_cash"]["equity_finale"] - out["cash"]["equity_finale"]
    return out
