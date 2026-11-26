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
    # Phase 5 — capital AUTHENTIFIÉ (soldes Bitvavo) pour dimensionner un achat réel : `max_order_eur` (500 €)
    # n'est pas une protection pour un capital de 100 € ; ces bornes sont relatives au capital authentifié.
    max_order_fraction_of_authenticated_equity: Decimal = Decimal("0.35")
    auth_balance_max_age_ms: int = 180_000
    auth_divergence_tolerance_eur: Decimal = Decimal("0.50")
    auth_divergence_tolerance_fraction: Decimal = Decimal("0.005")
    # Phase 6 — nombre maximal d'ordres d'ENTRÉE (achats) actifs simultanément par mode (ordres limites en
    # attente compris). Les ventes et protections ne sont jamais bornées par ce plafond.
    max_active_entry_orders: int = 3
    # Phase 6 (contre-expertise D1) : âge maximal des données pour une VENTE DE PROTECTION au marché (sortie,
    # liquidation). Plus large que `max_stale_ms` : ne jamais laisser une position sans stop ni sortie possible.
    max_stale_ms_protective_exit: int = 120_000
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
            max_order_fraction_of_authenticated_equity=D(d["max_order_fraction_of_authenticated_equity"]),
            auth_balance_max_age_ms=int(d["auth_balance_max_age_ms"]),
            auth_divergence_tolerance_eur=D(d["auth_divergence_tolerance_eur"]),
            auth_divergence_tolerance_fraction=D(d["auth_divergence_tolerance_fraction"]),
            max_active_entry_orders=int(d.get("max_active_entry_orders", 3)),
            max_stale_ms_protective_exit=int(d.get("max_stale_ms_protective_exit", 120_000)),
            sha256=file_sha256(p),
        )

    def __post_init__(self) -> None:
        # Garde-fou : ces interdits du cahier des charges ne peuvent pas être levés par fichier.
        if self.allow_margin or self.allow_short or self.allow_derivatives:
            raise ValueError("Marge, vente à découvert et dérivés sont interdits")
        if self.allow_withdrawals or self.allow_martingale:
            raise ValueError("Retraits automatiques et martingale sont interdits")
        if not (Decimal(0) < self.max_order_fraction_of_authenticated_equity <= Decimal(1)):
            raise ValueError("max_order_fraction_of_authenticated_equity doit être dans ]0, 1]")
        if not (self.max_stale_ms <= self.max_stale_ms_protective_exit <= 600_000):
            raise ValueError("max_stale_ms_protective_exit doit être dans [max_stale_ms, 10 min]")
        if not (1 <= self.max_active_entry_orders <= 20):
            raise ValueError("max_active_entry_orders doit être dans [1, 20]")
        if not (0 < self.auth_balance_max_age_ms <= 600_000):
            raise ValueError("auth_balance_max_age_ms doit être dans ]0, 10 min]")


@dataclass
class Settings:
    # Base du MOTEUR (autoritaire, volume engine_data monté dans le moteur SEUL ; phase 5).
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("NELYQOR_DB", ROOT / "data" / "engine.db")))
    # Canaux contrôlés (backend/storage/channels.py) ; par défaut à côté de db_path (développement, tests).
    commands_db_path: Path | None = None  # api écrit, moteur lit (:ro) — table control_commands seule
    display_db_path: Path | None = None   # moteur écrit, api lit (:ro) — copie d'affichage
    api_db_path: Path | None = None       # audit propre à l'API
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
    # Phase 6 (D8) — rétention de engine.db, en jours (purge quotidienne bornée, docs/DEPLOIEMENT.md § 11)
    retention_days: dict[str, int] = field(default_factory=lambda: {
        k: int(os.environ.get(f"NELYQOR_RETENTION_{k.upper()}_DAYS", v))
        for k, v in {"decisions": 90, "intents_vetoed": 90, "equity": 730, "candles": 400}.items()})

    def __post_init__(self) -> None:
        base = Path(self.db_path).parent
        if self.commands_db_path is None:
            self.commands_db_path = Path(os.environ.get("NELYQOR_COMMANDS_DB", base / "commandes.db"))
        if self.display_db_path is None:
            self.display_db_path = Path(os.environ.get("NELYQOR_DISPLAY_DB", base / "affichage.db"))
        if self.api_db_path is None:
            self.api_db_path = Path(os.environ.get("NELYQOR_API_DB", base / "api.db"))

    @property
    def commands_file(self) -> Path:
        assert self.commands_db_path is not None
        return Path(self.commands_db_path)

    @property
    def display_file(self) -> Path:
        assert self.display_db_path is not None
        return Path(self.display_db_path)

    @property
    def api_file(self) -> Path:
        assert self.api_db_path is not None
        return Path(self.api_db_path)

    def read_secret(self, name: str) -> str | None:
        """Lit un secret monté en fichier (Docker secrets). Jamais depuis le dépôt."""
        if self.role != "engine" and name.startswith(("bitvavo", "engine_", "telegram", "owner_command_key")):
            # Séparation des permissions : seul le moteur d'exécution peut lire les clés Bitvavo.
            raise PermissionError(f"Le rôle {self.role!r} n'a pas accès aux clés d'échange")
        p = self.secrets_dir / name
        if not p.exists():
            return None
        try:
            return p.read_text(encoding="utf-8").strip() or None
        except PermissionError as e:
            # phase 6 (déploiement réel) : un secret en 600 appartenant à root est illisible par l'utilisateur
            # des conteneurs ; échec EXPLICITE (jamais un démarrage silencieux sans clé d'intégrité)
            raise PermissionError(f"secret {name} illisible par l'uid {os.getuid()} : sur l'hôte, "
                                  f"`chown 10001:10001 secrets/{name} && chmod 600 secrets/{name}` "
                                  "(docs/DEPLOIEMENT.md)") from e


def load_strategy_params() -> dict[str, Any]:
    return _load_json(CONFIG_DIR / "strategies.json")
