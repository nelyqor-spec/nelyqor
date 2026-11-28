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


MAX_CHANNEL_BYTES = 8 * 1024 * 1024  # fichier de commandes au-delà : ignoré (aucune lecture)
MAX_NEW_PER_CYCLE = 5  # quota de commandes NOUVELLES prises par cycle (anti-remplissage du disque du moteur)
SQL_STEP_BUDGET = 2_000_000  # instructions de la VM SQLite par lecture (anti-boucle : vue récursive, etc.)
_EXPECTED_TABLE = " ".join(COMMANDS_MIGRATIONS[0].split()).replace("( ", "(").replace(" )", ")")


def _norm(sql: str | None) -> str:
    return " ".join((sql or "").split()).replace("( ", "(").replace(" )", ")")


def read_channel_bytes(path: Path) -> bytes | None:
    """Lecture BORNÉE et non bloquante du fichier de commandes : fichier régulier uniquement (ni lien symbolique,
    ni FIFO, ni périphérique), taille ≤ MAX_CHANNEL_BYTES. Aucune ouverture SQLite du fichier partagé : aucun verrou
    de l'API ne peut bloquer le moteur (contre-expertise sécurité phase 5, failles 1, 6, 7)."""
    import os
    import stat
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_CHANNEL_BYTES:
        log.error("canal de commandes refusé (type ou taille)", extra={"data": {"mode": st.st_mode, "taille": st.st_size}})
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st2 = os.fstat(fd)
        if not stat.S_ISREG(st2.st_mode) or st2.st_size > MAX_CHANNEL_BYTES:
            return None
        chunks, total = [], 0
        while total <= MAX_CHANNEL_BYTES:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
            total += len(b)
        return None if total > MAX_CHANNEL_BYTES else b"".join(chunks)
    finally:
        os.close(fd)


def channel_writable(path: Path) -> bool:
    """Le moteur peut-il écrire le canal de commandes ? En production, le volume est monté `:ro` dans le moteur
    (drapeau ST_RDONLY du système de fichiers) : sinon la séparation n'est pas effective."""
    import os
    d = path.parent if path.parent.exists() else path.parent.parent
    try:
        return not (os.statvfs(d).f_flag & os.ST_RDONLY)
    except OSError:
        return True


class CommandInbox:
    """Côté moteur : lecture des commandes déposées par l'API. Tout le contenu est NON FIABLE.

    Le fichier est lu en octets (borné), puis chargé dans une base SQLite EN MÉMOIRE privée (`deserialize`) :
    schéma exigé à l'identique (une seule table, ni vue, ni déclencheur), `trusted_schema=OFF`, budget
    d'instructions, filtres de taille dans le SQL. L'arrêt d'urgence est lu en PRIORITÉ ; au plus
    `MAX_NEW_PER_CYCLE` commandes nouvelles par cycle (les identifiants déjà pris sont exclus)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @property
    def available(self) -> bool:
        return self.path.exists()

    def _load(self) -> sqlite3.Connection | None:
        data = read_channel_bytes(self.path)
        if not data:
            return None
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.deserialize(data)
        c.execute("PRAGMA trusted_schema=OFF")
        steps = {"n": 0}

        def guard() -> int:
            steps["n"] += 1000
            return 1 if steps["n"] > SQL_STEP_BUDGET else 0
        c.set_progress_handler(guard, 1000)
        objs = c.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        tables = [o for o in objs if o["type"] == "table"]
        others = [o for o in objs if o["type"] != "table" and not (o["type"] == "index" and o["sql"] is None)]
        if others or len(tables) != 1 or tables[0]["name"] != "control_commands" or _norm(tables[0]["sql"]) != _EXPECTED_TABLE:
            log.error("canal de commandes refusé : schéma inattendu", extra={"data": {"objets": [o["name"] for o in objs][:10]}})
            c.close()
            return None
        return c

    def candidates(self, since_ts: int, always_valid: set[str], taken: set[str] | None = None,
                   always_valid_since: int | None = None) -> list[dict[str, Any]]:
        try:
            c = self._load()
        except Exception as e:  # noqa: BLE001 — base piégée/corrompue, mémoire : jamais bloquant pour le moteur
            log.error("lecture des commandes impossible", extra={"data": {"err": repr(e)[:200]}})
            return []
        if c is None:
            return []
        try:
            c.execute("CREATE TEMP TABLE pris(id TEXT PRIMARY KEY)")
            c.executemany("INSERT OR IGNORE INTO temp.pris VALUES(?)", [(x,) for x in (taken or set())])
            av = sorted(always_valid)
            marks = ",".join("?" for _ in av) or "''"
            ok = ("typeof(id)='text' AND length(id) BETWEEN 1 AND ? AND typeof(ts)='integer' AND typeof(command)='text' "
                  "AND length(command) <= 64 AND id NOT IN (SELECT id FROM temp.pris)")
            base = (MAX_PAYLOAD_BYTES, MAX_COMMAND_ID)
            # charge utile invalide ou trop grande : remplacée DANS LE SQL (jamais copiée en Python) -> refus journalisé
            cols = ("id, ts, command, CASE WHEN payload IS NULL THEN NULL WHEN typeof(payload)='text' AND "
                    "length(CAST(payload AS BLOB)) <= ? THEN payload ELSE '__invalide__' END AS payload")
            av_since = since_ts if always_valid_since is None else always_valid_since
            sql_urgent = (f"SELECT {cols} FROM control_commands WHERE {ok} AND command='emergency_stop' "  # noqa: S608
                          "ORDER BY ts DESC LIMIT ?")
            sql_other = (f"SELECT {cols} FROM control_commands WHERE {ok} AND command != 'emergency_stop' AND "  # noqa: S608
                         f"(ts >= ? OR (command IN ({marks}) AND ts >= ?)) ORDER BY ts DESC LIMIT ?")
            rows = c.execute(sql_urgent, (*base, 2)).fetchall()  # arrêt d'urgence : toute date, prioritaire
            rows += c.execute(sql_other, (*base, since_ts, *av, av_since, MAX_NEW_PER_CYCLE)).fetchall()
            out = [{"id": r["id"], "ts": int(r["ts"]), "command": r["command"], "payload": r["payload"]} for r in rows]
        except Exception as e:  # noqa: BLE001
            log.error("lecture des commandes impossible", extra={"data": {"err": repr(e)[:200]}})
            return []
        finally:
            c.close()
        out.sort(key=lambda x: (x["command"] != "emergency_stop", x["ts"]))  # urgence d'abord, puis chronologique
        return out[:MAX_NEW_PER_CYCLE]


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
