"""Configuration : fichier JSON versionné (paramètres, bornes) + variables d'environnement (secrets montés).

Les bornes immuables (`hard_limits`) sont lues depuis config/hard_limits.json, hashées, et ne
peuvent pas être modifiées depuis l'API ni par le laboratoire.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .num import D

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("NELYQOR_CONFIG_DIR", ROOT / "config"))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class HardLimits:
    """Interdits et bornes non négociables. Aucune API ne les modifie."""

    allow_margin: bool
    allow_short: bool
    allow_derivatives: bool
    allow_withdrawals: bool
    allow_martingale: bool
    max_position_fraction: Decimal  # part max de l'equity par position
    max_total_exposure_fraction: Decimal
    min_reserve_fraction: Decimal
    max_order_eur: Decimal
    max_stale_ms: int
    max_spread_bps: Decimal
    min_quote_volume_24h_eur: Decimal
    anomaly_refuse_threshold: Decimal
    sha256: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> HardLimits:
        p = path or CONFIG_DIR / "hard_limits.json"
        d = _load_json(p)
        return cls(
            allow_margin=bool(d["allow_margin"]),
            allow_short=bool(d["allow_short"]),
            allow_derivatives=bool(d["allow_derivatives"]),
            allow_withdrawals=bool(d["allow_withdrawals"]),
            allow_martingale=bool(d["allow_martingale"]),
            max_position_fraction=D(d["max_position_fraction"]),
            max_total_exposure_fraction=D(d["max_total_exposure_fraction"]),
            min_reserve_fraction=D(d["min_reserve_fraction"]),
            max_order_eur=D(d["max_order_eur"]),
            max_stale_ms=int(d["max_stale_ms"]),
            max_spread_bps=D(d["max_spread_bps"]),
            min_quote_volume_24h_eur=D(d["min_quote_volume_24h_eur"]),
            anomaly_refuse_threshold=D(d["anomaly_refuse_threshold"]),
            sha256=file_sha256(p),
        )

    def __post_init__(self) -> None:
        # Garde-fou : ces interdits du cahier des charges ne peuvent pas être levés par fichier.
        if self.allow_margin or self.allow_short or self.allow_derivatives:
            raise ValueError("Marge, vente à découvert et dérivés sont interdits")
        if self.allow_withdrawals or self.allow_martingale:
            raise ValueError("Retraits automatiques et martingale sont interdits")


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("NELYQOR_DB", ROOT / "data" / "nelyqor.db")))
    role: str = field(default_factory=lambda: os.environ.get("NELYQOR_ROLE", "engine"))  # engine|api|lab
    # Le mode réel exige CETTE variable côté serveur ET une activation authentifiée par le propriétaire.
    real_trading_server_flag: bool = field(
        default_factory=lambda: os.environ.get("NELYQOR_REAL_TRADING_ALLOWED", "false").lower() == "true"
    )
    bitvavo_rest_url: str = field(default_factory=lambda: os.environ.get("BITVAVO_REST_URL", "https://api.bitvavo.com/v2"))
    bitvavo_ws_url: str = field(default_factory=lambda: os.environ.get("BITVAVO_WS_URL", "wss://ws.bitvavo.com/v2/"))
    bitvavo_operator_id: int = field(default_factory=lambda: int(os.environ.get("BITVAVO_OPERATOR_ID", "1")))
    secrets_dir: Path = field(default_factory=lambda: Path(os.environ.get("NELYQOR_SECRETS_DIR", "/run/secrets")))
    quote_currency: str = "EUR"
    initial_capital_eur: Decimal = D("100")
    monthly_hosting_eur: Decimal = field(default_factory=lambda: D(os.environ.get("NELYQOR_HOSTING_EUR", "7.19")))
    telegram_enabled: bool = field(default_factory=lambda: bool(os.environ.get("TELEGRAM_CHAT_ID")))
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("NELYQOR_COOKIE_SECURE", "true") == "true")

    def read_secret(self, name: str) -> str | None:
        """Lit un secret monté en fichier (Docker secrets). Jamais depuis le dépôt."""
        if self.role != "engine" and name.startswith(("bitvavo", "engine_", "telegram", "owner_command_key")):
            # Séparation des permissions : seul le moteur d'exécution peut lire les clés Bitvavo.
            raise PermissionError(f"Le rôle {self.role!r} n'a pas accès aux clés d'échange")
        p = self.secrets_dir / name
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8").strip() or None


def load_strategy_params() -> dict[str, Any]:
    return _load_json(CONFIG_DIR / "strategies.json")
