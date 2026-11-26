"""Canaux contrôlés entre l'API et le moteur (phase 5 : séparation des bases).

Avant : l'API et le moteur partageaient UNE base SQLite ; une API compromise pouvait écrire dans `fills`,
`external_flows`, `intents`, `kv`… et gonfler l'équité qui dimensionne les ordres réels.

Désormais quatre fichiers, chacun sur son propre volume :

    engine.db      (volume engine_data, moteur SEUL)   journal, intentions, flux, décisions, état d'affichage,
                                                        journal des commandes traitées. Autoritaire.
    commandes.db   (volume commands : api rw, moteur :ro)  table `control_commands` UNIQUEMENT. Entrée non
                                                        fiable : le moteur la lit, la valide, ne l'écrit jamais.
    affichage.db   (volume display : moteur rw, api :ro)   copie d'affichage publiée par le moteur. Le moteur ne
                                                        la relit JAMAIS (aucune valeur qui pilote un ordre n'en vient).
    api.db         (volume api_data, API seule)          journal d'audit propre à l'API (connexions, demandes).

Les bases d'échange (commandes, affichage) sont en journal DELETE : le lecteur les ouvre en lecture seule sur un
montage `:ro`, ce qu'un lecteur WAL ne peut pas faire (il doit écrire le fichier -shm).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..core.logging import get_logger
from .db import Database

log = get_logger("channels")

COMMANDS_MIGRATIONS: list[str] = [
    """
    CREATE TABLE control_commands (
        id TEXT PRIMARY KEY, ts INTEGER, command TEXT, payload TEXT, status TEXT, processed_ms INTEGER, result TEXT
    )
    """,
]

API_MIGRATIONS: list[str] = [
    """
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY, ts INTEGER, actor TEXT, action TEXT, ip TEXT, ok INTEGER, details TEXT
    )
    """,
]

MAX_COMMAND_ID = 64
MAX_PAYLOAD_BYTES = 16_384
MAX_COMMANDS_PER_READ = 500


def open_command_writer(path: Path | str) -> Database:
    """Côté API : seule base que l'API écrit pour piloter le moteur (une table, aucune donnée du moteur)."""
    return Database(path, migrations=COMMANDS_MIGRATIONS, journal_mode="DELETE")


def open_api_db(path: Path | str) -> Database:
    """Côté API : audit propre à l'API (jamais lu par le moteur)."""
    return Database(path, migrations=API_MIGRATIONS)


class ReadOnlyView:
    """Base ouverte en LECTURE SEULE, paresseusement : le fichier peut ne pas encore exister (l'autre service
    n'a pas démarré) ; on renvoie alors des résultats vides au lieu d'échouer."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._db: Database | None = None

    def _get(self) -> Database | None:
        if self._db is None and self.path.exists():
            try:
                self._db = Database(self.path, readonly=True)
            except sqlite3.Error as e:
                log.warning("base en lecture seule indisponible", extra={"data": {"path": str(self.path), "err": repr(e)}})
                return None
        return self._db

    @property
    def available(self) -> bool:
        return self._get() is not None

    def q(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        db = self._get()
        if db is None:
            return []
        try:
            return db.q(sql, args)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return []  # base créée, schéma pas encore écrit
            raise

    def one(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None


class CommandInbox:
    """Côté moteur : lecture SEULE des commandes déposées par l'API. Tout le contenu est non fiable ; la prise
    (anti double exécution, anti-rejeu par remise à « pending ») se fait dans le journal de la base du MOTEUR."""

    def __init__(self, path: Path | str) -> None:
        self.view = ReadOnlyView(path)

    @property
    def path(self) -> Path:
        return self.view.path

    def candidates(self, since_ts: int, always_valid: set[str]) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in always_valid) or "''"
        try:
            rows = self.view.q(
                f"SELECT id, ts, command, payload FROM control_commands WHERE ts >= ? OR command IN ({marks}) "  # noqa: S608
                f"ORDER BY ts, rowid LIMIT {MAX_COMMANDS_PER_READ}", (since_ts, *sorted(always_valid)))
        except sqlite3.Error as e:  # base de commandes corrompue/verrouillée : jamais bloquant pour le moteur
            log.error("lecture des commandes impossible", extra={"data": {"err": repr(e)}})
            return []
        out = []
        for r in rows:
            cid, ts, cmd, payload = r["id"], r["ts"], r["command"], r["payload"]
            if not isinstance(cid, str) or not cid or len(cid) > MAX_COMMAND_ID:
                continue
            if not isinstance(ts, int) or not isinstance(cmd, str) or len(cmd) > 64:
                continue
            if payload is not None and (not isinstance(payload, str) or len(payload.encode()) > MAX_PAYLOAD_BYTES):
                payload = "__invalide__"
            out.append({"id": cid, "ts": ts, "command": cmd, "payload": payload})
        return out


# tables publiées vers l'affichage : (requête de sélection dans la base du moteur)
_ACTIVE = "('PENDING','SUBMITTING','UNKNOWN','new','partiallyFilled','awaitingTrigger')"
DISPLAY_TABLES: dict[str, str] = {
    "fills": "SELECT * FROM fills",
    "external_flows": "SELECT * FROM external_flows",
    "intents": f"SELECT * FROM intents WHERE status IN {_ACTIVE} OR id IN "  # noqa: S608
               "(SELECT id FROM intents ORDER BY ts DESC LIMIT 500)",
    "decisions": "SELECT * FROM decisions ORDER BY ts DESC LIMIT 500",
    "notifications": "SELECT * FROM notifications ORDER BY ts DESC LIMIT 200",
    "audit": "SELECT * FROM audit ORDER BY id DESC LIMIT 300",
    "control_commands": "SELECT * FROM control_commands ORDER BY ts DESC LIMIT 300",
    "model_registry": "SELECT * FROM model_registry",
    "experiments": "SELECT * FROM experiments ORDER BY ts DESC LIMIT 200",
}
TABLES_EVERY_MS = 10_000


class DisplayPublisher:
    """Côté moteur : ÉCRIT la base d'affichage. Aucune méthode de lecture de cette base n'est exposée au moteur :
    l'état incrémental (dernier instantané publié) est tenu en mémoire."""

    def __init__(self, src: Database, path: Path | str, now_ms: Any) -> None:
        self.src = src
        self.path = Path(path)
        self._now = now_ms
        self._dst: Database | None = None
        self._eq_ts: int | None = None  # dernier instantané d'equity publié (mémoire)
        self._tables_ms = -TABLES_EVERY_MS

    def _db(self) -> Database:
        if self._dst is None:
            self._dst = Database(self.path, journal_mode="DELETE")
        return self._dst

    @staticmethod
    def _copy(c: sqlite3.Connection, table: str, rows: list[sqlite3.Row]) -> None:
        if not rows:
            return
        cols = rows[0].keys()
        sql = f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})"  # noqa: S608
        c.executemany(sql, [tuple(r) for r in rows])

    def publish(self, force: bool = False) -> None:
        now = self._now()
        dst = self._db()
        kv = self.src.q("SELECT k, v, updated_ms FROM kv")
        if self._eq_ts is None:
            eq = self.src.q("SELECT * FROM equity_snapshots")
        else:
            eq = self.src.q("SELECT * FROM equity_snapshots WHERE ts >= ?", (self._eq_ts,))
        tables = force or now - self._tables_ms >= TABLES_EVERY_MS
        data = {t: self.src.q(sql) for t, sql in DISPLAY_TABLES.items()} if tables else {}
        with dst.tx() as c:
            c.execute("DELETE FROM kv")
            self._copy(c, "kv", kv)
            if self._eq_ts is None:
                c.execute("DELETE FROM equity_snapshots")
            self._copy(c, "equity_snapshots", eq)
            for t, rows in data.items():
                c.execute(f"DELETE FROM {t}")  # noqa: S608
                self._copy(c, t, rows)
        if eq:
            self._eq_ts = max(int(r["ts"]) for r in eq)
        elif self._eq_ts is None:
            self._eq_ts = 0
        if tables:
            self._tables_ms = now

    def close(self) -> None:
        if self._dst is not None:
            self._dst.close()
            self._dst = None
