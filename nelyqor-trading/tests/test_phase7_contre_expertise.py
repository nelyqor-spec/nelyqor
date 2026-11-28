"""Phase 7 — défauts trouvés par la contre-expertise indépendante (D1 à D10) : un test de non-régression par défaut.

D1 PWA : bandeau « SIMULATION, argent FICTIF » alors que des positions RÉELLES restaient ouvertes et gérées
   (arrêt d'urgence pendant le réel : les nouvelles entrées passent par la simulation).
D2 garde disque : ENOSPC alors que statvfs voit de la place (inodes, quota, tmpfs) => oscillation critique/ok,
   clés de déduplication horodatées => rafale ; le quota du jour épuisé bloquait ensuite « ordre inconnu ».
D3 la vigie gardait le battement frais pendant une boucle FIGÉE : santé Docker et API « vivant », aucune alerte.
D4 installation --sans-domaine <nom> : Caddy publié sur 0.0.0.0 (Docker contourne UFW).
D5 restore.sh : état courant supprimé même si la copie de sécurité échouait (tar || true).
D6 restore.sh : refus si le moteur tourne, mais pas si l'API tourne (api.db écrasée sous ses pieds).
D7 install --ecoute non validé (écrit tel quel dans .env).
D8 angles morts du test de sécurité des workflows : voir tests/test_workflows_securite.py.
D9 garde disque du moteur : inodes ignorés.
D10 disque critique : copie d'affichage complète réécrite toutes les 10 s.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from backend.core import disk
from backend.core.clock import FakeClock
from tests.test_phase5_separation import make
from tests.test_phase7_exploitation import ROOT, _env, _os_release, _sh, _volumes

FE = ROOT / "frontend"
NODE = shutil.which("node")


def _compose_healthcheck(db_path: str) -> list[str]:
    hc = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["engine"]["healthcheck"]["test"]
    assert hc[:2] == ["CMD", "python"]
    return [sys.executable, *[a.replace("/engine/engine.db", db_path) for a in hc[2:]]]


# ======================================================================== D3 boucle figée
async def test_D3_vigil_does_not_hide_a_frozen_loop(tmp_path):
    clock = FakeClock(int(time.time() * 1000))  # horloge réelle : le contrôle de santé compare à time.time()
    eng, db = make(tmp_path, clock)
    await eng.step()
    hang = asyncio.Event()

    async def blocked():
        await hang.wait()
        return []
    eng.rest.ticker_book = blocked  # type: ignore[method-assign]
    task = asyncio.create_task(eng.step())
    await asyncio.sleep(0)
    for _ in range(30):  # 2,5 min : pas encore figée
        clock.advance(5_000)
        eng.vigil_once()
    assert eng.db.kv_get("engine_heartbeat")["boucle_figee"] is False
    for _ in range(120):  # 10 min de plus
        clock.advance(5_000)
        eng.vigil_once()
    hb = eng.db.kv_get("engine_heartbeat")
    assert hb["boucle_figee"] is True and hb["last_cycle_ms"] > 0
    # même battement « frais », le contrôle de santé Docker échoue : ramener le battement au temps réel
    hb["ts"] = int(time.time() * 1000)
    eng.db.kv_set("engine_heartbeat", hb)
    assert subprocess.run(_compose_healthcheck(eng.db.path)).returncode != 0
    kinds = [r["kind"] for r in db.q("SELECT kind FROM notifications")]
    assert kinds.count("panne_prolongee") == 1  # une seule alerte, pas une par passage de vigie
    hang.set()
    await task
    assert eng.loop_frozen() is False  # cycle terminé : plus figée
    eng.heartbeat(data_ok=True)
    hb = eng.db.kv_get("engine_heartbeat")
    hb["ts"] = int(time.time() * 1000)
    eng.db.kv_set("engine_heartbeat", hb)
    assert subprocess.run(_compose_healthcheck(eng.db.path)).returncode == 0


def test_D3_api_health_and_status_report_frozen_loop():
    src = (ROOT / "backend/api/app.py").read_text()
    i = src.index("def health()")
    assert "boucle_figee" in src[i:i + 400]
    assert '"moteur_vivant": hb is not None and now - hb["ts"] <= 120_000 and not hb.get("boucle_figee")' in src


# ======================================================================== D2 oscillation et quota d'alertes
async def test_D2_enospc_with_free_blocks_does_not_flap(tmp_path, monkeypatch):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    eng.disk_th = disk.DiskThresholds(warn_free_mb=100, crit_free_mb=50)
    monkeypatch.setattr("backend.runner.free_mb", lambda p: (10_000.0, 20_000.0))
    monkeypatch.setattr("backend.runner.inodes_exhausted", lambda p: False)
    levels = []
    for _ in range(5):
        eng.on_disk_full(sqlite3.OperationalError("database or disk is full"))
        clock.advance(5_000)
        levels.append(eng.check_disk())
    n = db.one("SELECT COUNT(*) n FROM notifications WHERE kind='disque'")["n"]
    assert levels == ["critique"] * 5 and n == 1, (levels, n)
    clock.advance(eng.enospc_hold_ms)  # plus aucun refus d'écriture pendant 10 min : reprise
    assert eng.check_disk() == "ok"


async def test_D2_disk_alerts_never_consume_security_quota(tmp_path, monkeypatch):
    clock = FakeClock(1_770_000_000_000)
    eng, _ = make(tmp_path, clock)
    sent: list[str] = []
    eng.notifier.ntfy_url = "http://x"
    monkeypatch.setattr(eng.notifier, "_ntfy", lambda kind, msg: (sent.append(kind), True)[1])
    for i in range(40):  # 40 alertes disque DISTINCTES (pire cas)
        eng.notifier.notify("disque", f"disque {i}", dedup_key=f"d{i}")
    assert sent.count("disque") == eng.notifier.max_per_day  # leur propre quota
    eng.notifier.notify("ordre_inconnu", "ordre réel à l'état inconnu")
    assert "ordre_inconnu" in sent


def test_D2_emergency_flag_rename_failure_alerts_once(tmp_path, monkeypatch):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    if eng.store is None:
        pytest.skip("magasin d'état requis")
    flag = eng.store.path.parent / "ARRET_URGENCE"
    flag.write_text("")
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(OSError("lecture seule")))
    for _ in range(10):
        eng._check_emergency_file()
        clock.advance(5_000)
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='incident'")["n"] == 1


# ======================================================================== D9 inodes
def test_D9_inodes_exhausted_detected(monkeypatch):
    fake = SimpleNamespace(f_files=100_000, f_favail=500, f_bavail=10**9, f_frsize=4096, f_blocks=10**9)
    monkeypatch.setattr(disk.os, "statvfs", lambda p: fake)
    assert disk.inodes_exhausted("/x") is True
    fake.f_favail = 50_000
    assert disk.inodes_exhausted("/x") is False
    fake.f_files = 0  # système de fichiers sans compteur d'inodes
    assert disk.inodes_exhausted("/x") is False


async def test_D9_engine_goes_critical_on_inode_exhaustion(tmp_path, monkeypatch):
    eng, _ = make(tmp_path, FakeClock(1_770_000_000_000))
    monkeypatch.setattr("backend.runner.free_mb", lambda p: (10_000.0, 20_000.0))
    monkeypatch.setattr("backend.runner.inodes_exhausted", lambda p: True)
    assert eng.check_disk() == "critique" and eng.writes_suspended


# ======================================================================== D10 affichage en disque critique
def test_D10_display_throttled_when_disk_critical(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, _ = make(tmp_path, clock)
    calls: list[bool] = []
    eng.display = SimpleNamespace(publish=lambda force=False: calls.append(force))
    eng.disk_level = disk.CRITIQUE
    for _ in range(30):  # 5 min de battements toutes les 10 s
        eng.publish_display()
        clock.advance(10_000)
    assert len(calls) <= 2, len(calls)
    eng.publish_display(force=True)  # résultat de commande : toujours publié
    assert calls[-1] is True
    eng.disk_level = disk.OK
    n = len(calls)
    eng.publish_display()
    assert len(calls) == n + 1


# ======================================================================== D1 mode affiché
@pytest.mark.skipif(NODE is None, reason="node absent")
def test_D1_real_positions_open_never_shown_as_fictitious():
    js = f'''import * as V from "{(FE / "vue.js").as_posix()}";
const base = {{ mode: "simulation", etat: "PAUSE_NOUVELLES_ENTREES", moteur_vivant: true }};
console.log(JSON.stringify([V.modeInfo({{...base, positions_reelles_ouvertes: 2}}),
  V.modeInfo({{...base, positions_reelles_ouvertes: -1}}), V.modeInfo({{...base, positions_reelles_ouvertes: 0}}),
  V.alertsFrom({{...base, boucle_figee: true, moteur_vivant: false}}, {{}}).map(a => a.cle)]));'''
    out = subprocess.run([NODE, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)  # type: ignore[list-item]
    assert out.returncode == 0, out.stderr
    two, unknown, none, alerts = json.loads(out.stdout)
    assert two["kind"] == "real" and "FICTIF" not in two["detail"] and "2 position" in two["detail"]
    assert unknown["kind"] == "real"  # inconnu : prudence
    assert none["kind"] == "sim"
    assert alerts[0] == "boucle_figee" and "robot_silencieux" not in alerts


async def test_D1_heartbeat_and_status_publish_real_exposure(tmp_path):
    eng, _ = make(tmp_path, FakeClock(1_770_000_000_000))
    eng.heartbeat(data_ok=True)
    hb = eng.db.kv_get("engine_heartbeat")
    assert hb["positions_reelles_ouvertes"] == (0 if eng.real_exec is None else len(eng.held_positions("real")))
    assert '"positions_reelles_ouvertes": int(' in (ROOT / "backend/api/app.py").read_text()


# ======================================================================== D4 / D7 installation
def _install(tmp_path, *args):
    env = _env(NELYQOR_OS_RELEASE=_os_release(tmp_path, "debian", "12", "bookworm"), NELYQOR_ARCH="amd64")
    return _sh("install_serveur.sh", "--dry-run", "--oui", *args, "--sans-ntfy", env=env)


def test_D4_sans_domaine_hostname_requires_private_listen_ip(tmp_path):
    r = _install(tmp_path, "--sans-domaine", "nelyqor.tailnet-x.ts.net")
    assert r.returncode != 0 and "--ecoute" in r.stderr
    r = _install(tmp_path, "--sans-domaine", "nelyqor.tailnet-x.ts.net", "--ecoute", "0.0.0.0")
    assert r.returncode != 0
    r = _install(tmp_path, "--sans-domaine", "nelyqor.tailnet-x.ts.net", "--ecoute", "100.64.1.2")
    assert r.returncode == 0, r.stderr
    assert "NELYQOR_BIND_IP=100.64.1.2" in r.stdout and "NELYQOR_BIND_IP=0.0.0.0" not in r.stdout
    r = _install(tmp_path, "--sans-domaine", "100.64.1.3")
    assert r.returncode == 0 and "NELYQOR_BIND_IP=100.64.1.3" in r.stdout


@pytest.mark.parametrize("bad", ["1.2.3.4|x;y", "300.1.1.1", "abc", "1.2.3.4\nX=1", ""])
def test_D7_ecoute_must_be_ipv4(tmp_path, bad):
    r = _install(tmp_path, "--domaine", "a.fr", "--ecoute", bad)
    assert r.returncode != 0


# ======================================================================== D5 / D6 restauration
def _fake_docker(tmp_path: Path, service: str) -> str:
    b = tmp_path / "bin"
    b.mkdir()
    d = b / "docker"
    d.write_text(f"#!/bin/sh\ncase \"$*\" in *service={service}*) echo abc123 ;; esac\nexit 0\n")
    d.chmod(0o755)
    return f"{b}:{os.environ['PATH']}"


def test_D6_restore_refuses_when_api_running(tmp_path):
    env = _env(**_volumes(tmp_path), NELYQOR_RESTORE_OWNER="-")
    from backend.storage.db import Database
    api = tmp_path / "volumes/nelyqor_api_data/_data/api.db"
    Database(api).kv_set("a", 1)
    dest = tmp_path / "bk"
    assert _sh("backup.sh", str(dest), env=env).returncode == 0
    Database(api).kv_set("a", 2)  # état courant de l'API, plus récent que la sauvegarde
    env["PATH"] = _fake_docker(tmp_path, "api")
    r = _sh("restore.sh", str(next(dest.glob("engine-*.db.enc"))), env=env)
    assert r.returncode != 0 and "api" in r.stderr
    assert Database(api).kv_get("a") == 2  # rien d'écrasé


def test_D5_restore_never_deletes_state_without_safety_copy(tmp_path):
    env = _env(**_volumes(tmp_path), NELYQOR_RESTORE_OWNER="-")
    dest = tmp_path / "bk"
    assert _sh("backup.sh", str(dest), env=env).returncode == 0
    st = tmp_path / "volumes/nelyqor_engine_state/_data"
    (st / "etat_moteur.json").write_text('{"le":"plus récent"}')
    eng_db = tmp_path / "volumes/nelyqor_engine_data/_data/engine.db"
    before = eng_db.read_bytes()
    now = int(time.time())
    for t in range(now, now + 60):  # copie de sécurité impossible : le nom de destination est un dossier
        (st.parent / f"etat-avant-restauration.{t}.tar").mkdir()
    r = _sh("restore.sh", str(next(dest.glob("engine-*.db.enc"))), env=env)
    assert r.returncode != 0 and "copie de sécurité" in r.stderr
    assert json.loads((st / "etat_moteur.json").read_text()) == {"le": "plus récent"}  # état intact
    assert eng_db.read_bytes() == before  # base du moteur intacte : refus AVANT toute modification


def test_D5_restore_normal_path_still_works(tmp_path):
    env = _env(**_volumes(tmp_path), NELYQOR_RESTORE_OWNER="-")
    dest = tmp_path / "bk"
    assert _sh("backup.sh", str(dest), env=env).returncode == 0
    r = _sh("restore.sh", str(next(dest.glob("engine-*.db.enc"))), env=env)
    assert r.returncode == 0, r.stderr
    assert list((tmp_path / "volumes/nelyqor_engine_state").glob("etat-avant-restauration.*.tar"))


# ======================================================================== divers (lecture)
def test_alert_title_without_hostname_and_build_log_not_fixed_path():
    assert "Nelyqor ($HOTE)" not in (ROOT / "scripts/alerte.sh").read_text()
    assert "> /tmp/nelyqor-build.log" not in (ROOT / "scripts/install_serveur.sh").read_text()
