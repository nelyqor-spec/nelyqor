"""Non-régression des failles de la contre-expertise de sécurité adversariale phase 5 (canal de commandes écrit
par une API supposée COMPROMISE). Chaque attaque a été exécutée contre le commit 1917023 et réussissait."""
from __future__ import annotations

import os
import sqlite3
import time

from backend.core.clock import FakeClock
from backend.guardian.guardian import State
from backend.storage.channels import MAX_NEW_PER_CYCLE, CommandInbox
from tests.conftest import post_command
from tests.test_phase5_separation import make, result

DDL = ("CREATE TABLE control_commands (id TEXT PRIMARY KEY, ts INTEGER, command TEXT, payload TEXT, status TEXT, "
       "processed_ms INTEGER, result TEXT)")


def raw(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, isolation_level=None)
    c.execute("PRAGMA journal_mode=DELETE")
    return c


def test_recursive_view_refused_instantly(tmp_path):
    """Faille 1 : une VUE récursive infinie à la place de la table gelait le moteur. Désormais : schéma refusé."""
    p = tmp_path / "c" / "commandes.db"
    c = raw(p)
    c.execute("CREATE VIEW control_commands AS WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM r WHERE x>0) "
              "SELECT 'a'||x AS id, 0 AS ts, 'pause' AS command, '{}' AS payload FROM r")
    c.close()
    t = time.time()
    assert CommandInbox(p).candidates(0, {"pause"}) == [] and time.time() - t < 1


def test_fifo_and_symlink_and_oversize_refused(tmp_path):
    """Failles 1 et 7 : FIFO (lecture bloquante), lien symbolique, fichier géant : jamais ouverts."""
    d = tmp_path / "c"
    d.mkdir()
    os.mkfifo(d / "fifo.db")
    t = time.time()
    assert CommandInbox(d / "fifo.db").candidates(0, {"pause"}) == [] and time.time() - t < 1
    c = raw(d / "vrai.db")
    c.execute(DDL)
    c.execute("INSERT INTO control_commands VALUES('a',1,'pause','{}','pending',NULL,NULL)")
    c.close()
    os.symlink(d / "vrai.db", d / "lien.db")
    assert CommandInbox(d / "lien.db").candidates(0, {"pause"}) == []
    with open(d / "geant.db", "wb") as f:
        f.truncate(64 * 1024 * 1024)
    assert CommandInbox(d / "geant.db").candidates(0, {"pause"}) == []


def test_trigger_or_extra_table_refused(tmp_path):
    p = tmp_path / "c" / "commandes.db"
    c = raw(p)
    c.execute(DDL)
    c.execute("CREATE TRIGGER t AFTER INSERT ON control_commands BEGIN SELECT 1; END")
    c.execute("INSERT INTO control_commands VALUES('a',1,'pause','{}','pending',NULL,NULL)")
    c.close()
    assert CommandInbox(p).candidates(0, {"pause"}) == []


def test_exclusive_lock_does_not_block(tmp_path):
    """Faille 6 : un verrou exclusif de l'API bloquait chaque lecture 10 s. Lecture en octets : aucun verrou."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    post_command(eng, "p", clock.now_ms(), "pause", {})
    w = sqlite3.connect(eng.inbox.path, isolation_level=None)
    w.execute("BEGIN EXCLUSIVE")
    t = time.time()
    rows = eng.inbox.candidates(0, {"pause"})
    w.execute("ROLLBACK")
    assert time.time() - t < 1 and [r["id"] for r in rows] == ["p"]


async def test_flood_cannot_starve_emergency_stop_nor_fill_disk(tmp_path):
    """Failles 3 et 4 : 400 « pause » de 16 Ko (6,4 Mo, sous la borne de 8 Mo du canal) puis un arrêt d'urgence récent : l'arrêt est pris au 1er cycle,
    au plus MAX_NEW_PER_CYCLE commandes par cycle, et le journal du moteur ne garde qu'une empreinte."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    for k in range(400):
        post_command(eng, f"f{k}", clock.now_ms() - k, "pause", {"reason": "x" * 16_000})
    post_command(eng, "urgence", clock.now_ms(), "emergency_stop", {})
    size0 = os.path.getsize(db.path)
    await eng.process_commands()
    assert result(db, "urgence")["ok"] and eng.guardian.state == State.PAUSE
    n = db.one("SELECT COUNT(*) n FROM control_commands")["n"]
    assert n <= MAX_NEW_PER_CYCLE
    assert all(r["payload"].startswith("sha256:") for r in db.q("SELECT payload FROM control_commands"))
    for _ in range(4):
        await eng.process_commands()
    assert os.path.getsize(db.path) - size0 < 2_000_000  # avant correction : +36,6 Mo en 4 cycles


async def test_oversized_payload_refused_without_materializing(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    post_command(eng, "g", clock.now_ms(), "pause", "x" * 200_000)
    await eng.process_commands()
    assert "invalide" in result(db, "g")["reasons"][0] and eng.guardian.state == State.SIMULATION


async def test_channel_failure_never_skips_position_management(tmp_path, monkeypatch):
    """Faille 2 : une exception du canal (ex. MemoryError) sortait de step() avant la gestion des positions."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)

    def boom(*a, **k):
        raise MemoryError("canal piégé")
    monkeypatch.setattr(eng.inbox, "candidates", boom)
    called = {"n": 0}
    orig = eng.manage_positions

    async def spy(ex):
        called["n"] += 1
        return await orig(ex)
    monkeypatch.setattr(eng, "manage_positions", spy)
    await eng.step()
    assert called["n"] >= 1


async def test_writable_channel_is_not_separated(tmp_path, monkeypatch):
    """Faille 5 : trois fichiers dans un répertoire inscriptible par le moteur ne sont PAS une séparation."""
    import backend.runner as runner
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    monkeypatch.setattr(runner, "channel_writable", lambda p: True)  # montage non :ro (réalité des tests)
    assert eng.bases_separated() is False
    assert (await eng.preflight(for_real=True))["bases_separees"] is False


async def test_oversized_channel_is_ignored_and_ssh_stop_still_works(tmp_path):
    """Canal saturé au-delà de 8 Mo : aucune commande lue (une API compromise peut de toute façon supprimer des
    commandes — risque accepté) ; l'arrêt d'urgence par le répertoire d'état du moteur (SSH) reste effectif."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    for k in range(600):
        post_command(eng, f"f{k}", clock.now_ms(), "pause", {"reason": "x" * 16_000})
    assert eng.inbox.candidates(0, {"pause"}) == []
    (tmp_path / "state" / "ARRET_URGENCE").touch()
    await eng.step()
    assert eng.guardian.state == State.PAUSE
