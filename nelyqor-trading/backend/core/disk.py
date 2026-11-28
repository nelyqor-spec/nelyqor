"""Surveillance de l'espace disque du moteur (phase 7) : seuils avec hystérésis.

- « ok »       : espace libre ≥ seuil d'alerte ;
- « alerte »   : espace libre < seuil d'alerte (NELYQOR_DISK_WARN_FREE_MB) : notification, fonctionnement normal ;
- « critique » : espace libre < seuil critique (NELYQOR_DISK_CRIT_FREE_MB) : notification et ARRÊT PROPRE des
  écritures NON critiques du moteur (décisions, nouvelles entrées, instantanés d'equity, bougies hors positions,
  purge). Restent permises les écritures critiques : suivi des ordres, ventes, protections, battement de cœur.
Sortie de « critique » seulement au-dessus du seuil d'ALERTE (hystérésis : pas d'oscillation au voisinage du seuil).
SQLite annule proprement une transaction qui ne peut pas s'écrire (SQLITE_FULL) : la base n'est pas corrompue ;
le seuil critique garde une réserve pour que les écritures critiques passent encore."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

OK, ALERTE, CRITIQUE = "ok", "alerte", "critique"
DEFAULT_WARN_FREE_MB = 2048
DEFAULT_CRIT_FREE_MB = 512


@dataclass(frozen=True)
class DiskThresholds:
    warn_free_mb: float = DEFAULT_WARN_FREE_MB
    crit_free_mb: float = DEFAULT_CRIT_FREE_MB

    def __post_init__(self) -> None:
        if not (0 < self.crit_free_mb < self.warn_free_mb):
            raise ValueError("seuils disque : 0 < critique < alerte exigé")

    @classmethod
    def from_env(cls) -> DiskThresholds:
        return cls(float(os.environ.get("NELYQOR_DISK_WARN_FREE_MB", DEFAULT_WARN_FREE_MB)),
                   float(os.environ.get("NELYQOR_DISK_CRIT_FREE_MB", DEFAULT_CRIT_FREE_MB)))


def free_mb(path: Path | str) -> tuple[float, float]:
    """(espace libre pour un utilisateur non root, taille totale) en Mo du système de fichiers de `path`."""
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e6, st.f_blocks * st.f_frsize / 1e6


def inodes_exhausted(path: Path | str, min_free: int = 1000, min_ratio: float = 0.01) -> bool:
    """Inodes presque épuisés (contre-expertise phase 7, D9) : l'écriture échoue (ENOSPC) alors que des blocs sont
    libres. Faux si le système de fichiers ne publie pas d'inodes (f_files = 0, ex. certains montages)."""
    st = os.statvfs(path)
    if st.f_files <= 0:
        return False
    return st.f_favail < max(min_free, int(st.f_files * min_ratio)) and st.f_favail < st.f_files


def next_level(previous: str, free: float, th: DiskThresholds) -> str:
    if free < th.crit_free_mb:
        return CRITIQUE
    if previous == CRITIQUE and free < th.warn_free_mb:
        return CRITIQUE  # hystérésis : on ne reprend les écritures qu'au-dessus du seuil d'alerte
    if free < th.warn_free_mb:
        return ALERTE
    return OK


def is_disk_full_error(e: BaseException) -> bool:
    """Erreur d'écriture due au disque plein (SQLite SQLITE_FULL, ou ENOSPC/EDQUOT du système)."""
    if isinstance(e, sqlite3.OperationalError):
        msg = str(e).lower()
        return "full" in msg or "no space" in msg
    return isinstance(e, OSError) and e.errno in (28, 122)  # ENOSPC, EDQUOT
