"""Validation des bougies : doublons, trous, alignement, horodatages futurs/bougie en cours, cohérence OHLC,
anomalies de rendement et de volume. Ne corrige jamais une valeur : signale, déduplique les doublons STRICTEMENT
identiques, et met en quarantaine les lignes incohérentes (exclues de la recherche, conservées pour audit)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

COLS = ["ts", "open", "high", "low", "close", "volume"]


@dataclass
class ValidationReport:
    rows_in: int
    rows_out: int
    duplicates_identical: int = 0
    duplicates_conflicting: int = 0
    misaligned: int = 0
    future_or_open: int = 0
    ohlc_invalid: int = 0
    nonpositive: int = 0
    missing_intervals: int = 0
    gaps: list[tuple[int, int]] = field(default_factory=list)  # (début du trou, nb de bougies manquantes)
    zero_volume: int = 0
    extreme_returns: int = 0
    extreme_return_ts: list[int] = field(default_factory=list)
    first_ts: int | None = None
    last_ts: int | None = None

    @property
    def ok(self) -> bool:
        return self.duplicates_conflicting == 0 and self.ohlc_invalid == 0 and self.nonpositive == 0 \
            and self.misaligned == 0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gaps"] = self.gaps[:50]
        d["extreme_return_ts"] = self.extreme_return_ts[:50]
        d["ok"] = self.ok
        return d


def validate_candles(df: pd.DataFrame, interval_ms: int, now_ms: int, extreme_k: float = 12.0
                     ) -> tuple[pd.DataFrame, pd.DataFrame, ValidationReport]:
    """Renvoie (propre, quarantaine, rapport). `df` : colonnes COLS, prix en float."""
    rep = ValidationReport(rows_in=len(df), rows_out=0)
    if df.empty:
        return df.copy(), df.copy(), rep
    d = df[COLS].copy()
    d["ts"] = d["ts"].astype("int64")
    # doublons : identiques -> un seul conservé ; contradictoires -> quarantaine (aucune version n'est choisie)
    dup_mask = d.duplicated("ts", keep=False)
    quarantine = []
    if dup_mask.any():
        grp = d[dup_mask].groupby("ts")
        for _ts, g in grp:
            if len(g.drop_duplicates()) == 1:
                rep.duplicates_identical += len(g) - 1
            else:
                rep.duplicates_conflicting += 1
                quarantine.append(g.assign(reason="doublon_contradictoire"))
        conflicting_ts = {int(t) for t, g in grp if len(g.drop_duplicates()) > 1}
        d = d[~d["ts"].isin(conflicting_ts)].drop_duplicates("ts")
    d = d.sort_values("ts").reset_index(drop=True)
    bad_align = d["ts"] % interval_ms != 0
    future = d["ts"] + interval_ms > now_ms  # bougie en cours ou future : jamais stockée comme clôturée
    p = d[["open", "high", "low", "close"]]
    nonpos = (p <= 0).any(axis=1) | p.isna().any(axis=1) | (d["volume"] < 0) | d["volume"].isna()
    ohlc_bad = (d["high"] < p[["open", "close", "low"]].max(axis=1)) | (d["low"] > p[["open", "close", "high"]].min(axis=1))
    for mask, reason in ((bad_align, "horodatage_non_aligne"), (future, "bougie_en_cours_ou_future"),
                         (nonpos, "valeur_nulle_negative_ou_absente"), (ohlc_bad, "ohlc_incoherent")):
        if mask.any():
            quarantine.append(d[mask].assign(reason=reason))
    rep.misaligned = int(bad_align.sum())
    rep.future_or_open = int(future.sum())
    rep.nonpositive = int(nonpos.sum())
    rep.ohlc_invalid = int((ohlc_bad & ~nonpos).sum())
    d = d[~(bad_align | future | nonpos | ohlc_bad)].reset_index(drop=True)
    if len(d) >= 2:
        steps = np.diff(d["ts"].to_numpy()) // interval_ms
        for i in np.flatnonzero(steps > 1):
            rep.gaps.append((int(d["ts"].iloc[i] + interval_ms), int(steps[i] - 1)))
        rep.missing_intervals = int(sum(n for _, n in rep.gaps))
        r = np.log(d["close"].to_numpy(dtype=float))
        lr = np.diff(r)
        med = np.median(lr)
        mad = np.median(np.abs(lr - med)) * 1.4826 or 1e-12
        ext = np.flatnonzero(np.abs(lr - med) > extreme_k * mad) + 1
        rep.extreme_returns = int(len(ext))
        rep.extreme_return_ts = [int(d["ts"].iloc[i]) for i in ext]  # SIGNALÉS, pas supprimés (krachs réels)
    rep.zero_volume = int((d["volume"] == 0).sum())
    rep.rows_out = len(d)
    rep.first_ts = int(d["ts"].iloc[0]) if len(d) else None
    rep.last_ts = int(d["ts"].iloc[-1]) if len(d) else None
    q = pd.concat(quarantine, ignore_index=True) if quarantine else pd.DataFrame(columns=[*COLS, "reason"])
    return d, q, rep
