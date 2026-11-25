"""SQLite en WAL, un seul écrivain sérialisé par verrou, migrations versionnées."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MIGRATIONS: list[str] = [
    # v1 — schéma initial
    """
    CREATE TABLE markets (
        market TEXT PRIMARY KEY, base TEXT, quote TEXT, status TEXT, meta TEXT NOT NULL,
        first_seen_ms INTEGER, last_seen_ms INTEGER, delisted_ms INTEGER
    );
    CREATE TABLE candles (
        market TEXT, interval TEXT, ts INTEGER, open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT,
        received_ms INTEGER, source TEXT, PRIMARY KEY (market, interval, ts)
    );
    CREATE TABLE book_snapshots (
        id INTEGER PRIMARY KEY, market TEXT, nonce INTEGER, ts_market INTEGER, ts_received INTEGER,
        best_bid TEXT, best_ask TEXT, depth TEXT
    );
    CREATE TABLE predictions (
        id TEXT PRIMARY KEY, ts INTEGER, market TEXT, horizon TEXT, model TEXT, model_version TEXT,
        value TEXT NOT NULL, calibrated INTEGER NOT NULL, realized TEXT, realized_ts INTEGER
    );
    CREATE TABLE decisions (
        id TEXT PRIMARY KEY, ts INTEGER, mode TEXT, market TEXT, strategy TEXT, action TEXT,
        accepted INTEGER, reasons TEXT NOT NULL, details TEXT NOT NULL, hypothetical INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE intents (
        id TEXT PRIMARY KEY, ts INTEGER, mode TEXT, market TEXT, side TEXT, order_type TEXT,
        amount TEXT, amount_quote TEXT, price TEXT, post_only INTEGER, strategy TEXT,
        reserved_quote TEXT, reserved_base TEXT, status TEXT NOT NULL, client_order_id TEXT UNIQUE,
        exchange_order_id TEXT, last_error TEXT, updated_ms INTEGER, details TEXT
    );
    CREATE TABLE fills (
        id TEXT PRIMARY KEY, intent_id TEXT, exchange_order_id TEXT, ts INTEGER, market TEXT, side TEXT,
        amount TEXT, price TEXT, fee TEXT, fee_currency TEXT, fee_eur TEXT, taker INTEGER, mode TEXT
    );
    CREATE TABLE balances (
        ts INTEGER, mode TEXT, symbol TEXT, available TEXT, in_order TEXT, PRIMARY KEY (ts, mode, symbol)
    );
    CREATE TABLE external_flows (
        id TEXT PRIMARY KEY, ts INTEGER, mode TEXT, kind TEXT, symbol TEXT, amount TEXT, value_eur TEXT,
        source TEXT, identified INTEGER NOT NULL DEFAULT 1, note TEXT
    );
    CREATE TABLE equity_snapshots (
        ts INTEGER, mode TEXT, equity_eur TEXT, cash_eur TEXT, positions_eur TEXT, PRIMARY KEY (ts, mode)
    );
    CREATE TABLE experiments (
        id TEXT PRIMARY KEY, ts INTEGER, kind TEXT, strategy TEXT, params TEXT, seed INTEGER,
        code_version TEXT, data_window TEXT, data_source TEXT, metrics TEXT, passed INTEGER, notes TEXT
    );
    CREATE TABLE model_registry (
        name TEXT, version TEXT, status TEXT, sha256 TEXT, params TEXT, created_ms INTEGER,
        updated_ms INTEGER, evidence TEXT, PRIMARY KEY (name, version)
    );
    CREATE TABLE incidents (
        id TEXT PRIMARY KEY, ts INTEGER, severity TEXT, kind TEXT, message TEXT, resolved_ms INTEGER, data TEXT
    );
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY, ts INTEGER, actor TEXT, action TEXT, ip TEXT, ok INTEGER, details TEXT
    );
    CREATE TABLE control_commands (
        id TEXT PRIMARY KEY, ts INTEGER, command TEXT, payload TEXT, status TEXT, processed_ms INTEGER, result TEXT
    );
    CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_ms INTEGER);
    CREATE TABLE notifications (
        id TEXT PRIMARY KEY, ts INTEGER, severity TEXT, kind TEXT, dedup_key TEXT, message TEXT, sent INTEGER
    );
    CREATE INDEX idx_intents_status ON intents(status);
    CREATE INDEX idx_fills_ts ON fills(ts);
    CREATE INDEX idx_decisions_ts ON decisions(ts);
    """,
    # v2 — phase 2 : consultations du holdout, informations externes, index de vente
    """
    CREATE TABLE holdout_views (
        id TEXT PRIMARY KEY, ts INTEGER, data_fingerprint TEXT, window TEXT, strategy TEXT, params_hash TEXT
    );
    CREATE TABLE external_items (
        id TEXT PRIMARY KEY, source TEXT, kind TEXT, published_ms INTEGER, available_ms INTEGER, fetched_ms INTEGER,
        content_hash TEXT UNIQUE, value TEXT, credibility REAL, flags TEXT
    );
    CREATE INDEX idx_intents_market_side ON intents(mode, market, side, status);
    CREATE INDEX idx_equity_mode_ts ON equity_snapshots(mode, ts);
    """,
]


class Database:
    def __init__(self, path: Path | str, readonly: bool = False) -> None:
        self.path = str(path)
        self.readonly = readonly
        self._lock = threading.RLock()
        if self.path != ":memory:" and not readonly:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        uri = f"file:{self.path}?mode=ro" if readonly else self.path
        self.conn = sqlite3.connect(uri, uri=readonly, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if not readonly:
            self.conn.execute("PRAGMA busy_timeout=10000")  # sérialise les écrivains inter-processus
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.migrate()

    def migrate(self) -> int:
        with self._lock:
            cur = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(MIGRATIONS[cur:], start=cur + 1):
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in [s for s in sql.split(";") if s.strip()]:
                        self.conn.execute(stmt)
                    self.conn.execute(f"PRAGMA user_version={i}")
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
            return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transaction sérialisée (un seul écrivain)."""
        if self.readonly:
            raise PermissionError("Base ouverte en lecture seule")
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, args).fetchall())

    def one(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def exec(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        with self.tx() as c:
            c.execute(sql, args)

    # --- clé/valeur (état Guardian, mode, etc.)
    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v: Any, now_ms: int = 0) -> None:
        self.exec(
            "INSERT INTO kv(k,v,updated_ms) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_ms=excluded.updated_ms",
            (k, json.dumps(v, default=str), now_ms),
        )

    def backup_to(self, dest: Path | str) -> None:
        with self._lock:
            target = sqlite3.connect(str(dest))
            self.conn.backup(target)
            target.close()

    def close(self) -> None:
        self.conn.close()
