"""Phase 4 — sécurité : preuve de recette signée et liée au code, révocation des autorisations, reprise vers le
réel signée, prise atomique des commandes (concurrence), reprise après panne."""
from __future__ import annotations

import json
import shutil

from backend.core.attest import make_proof, parse_pytest, verify_proof
from backend.core.clock import FakeClock
from backend.core.config import ROOT
from backend.guardian.guardian import REAL_CONFIRMATION_PHRASE, State
from backend.market_data.models import MarketInfo
from tests.conftest import BTC_RAW, STATE_KEY
from tests.test_resilience import CMD_KEY, RealSim, make_engine, signed_payload, valid_proof

FIELDS = {"confirmation": REAL_CONFIRMATION_PHRASE, "declare_retrait_desactive": True, "declare_ip_fixe": True,
          "declare_2fa": True, "accepte_risque_perte_totale": True}


def submit(db, cid, ts, command, payload):
    db.exec("INSERT INTO control_commands VALUES(?,?,?,?,'pending',NULL,NULL)", (cid, ts, command, json.dumps(payload)))


def result(db, cid):
    return json.loads(db.one("SELECT result FROM control_commands WHERE id=?", (cid,))["result"])


async def real_engine(tmp_path, clock):
    eng, db = make_engine(tmp_path, clock, flag=True, command_key=CMD_KEY)
    eng.attach_real_exchange(RealSim([MarketInfo.from_bitvavo(BTC_RAW)], {"EUR": D0()}, now_ms=clock.now_ms))
    await eng.step()
    db.kv_set("acceptance_proof", valid_proof(clock.now_ms()))
    return eng, db


def D0():
    from decimal import Decimal
    return Decimal(0)


# ------------------------------------------------------------------ preuve de recette
def test_proof_valid_and_bound_to_code(tmp_path):
    rec = make_proof(ROOT, STATE_KEY, "== 10 passed in 1s ==", "abc", 1_000)
    assert verify_proof(rec, STATE_KEY, ROOT, 2_000) == []
    # signature falsifiée / mauvaise clé
    bad = json.loads(json.dumps(rec))
    bad["proof"]["tests_failed"] = 0
    bad["proof"]["tests_passed"] = 999
    assert "invalide" in verify_proof(bad, STATE_KEY, ROOT, 2_000)[0]
    assert verify_proof(rec, b"autre-cle", ROOT, 2_000)
    # recette trop ancienne
    assert any("ancienne" in r for r in verify_proof(rec, STATE_KEY, ROOT, 1_000 + 31 * 86_400_000))
    # code modifié après la recette : copie du dépôt avec un seul fichier changé
    root2 = tmp_path / "copie"
    shutil.copytree(ROOT / "backend", root2 / "backend", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", root2 / "config")
    rec2 = make_proof(root2, STATE_KEY, "== 10 passed in 1s ==", "abc", 1_000)
    assert verify_proof(rec2, STATE_KEY, root2, 2_000) == []
    (root2 / "backend" / "runner.py").write_text((root2 / "backend" / "runner.py").read_text() + "\n# modif\n")
    assert any("code exécuté diffère" in r for r in verify_proof(rec2, STATE_KEY, root2, 2_000))
    (root2 / "config" / "x.json").write_text("{}")
    assert any("configuration diffère" in r for r in verify_proof(rec2, STATE_KEY, root2, 2_000))


def test_proof_rejects_failed_tests_and_parses_pytest():
    assert parse_pytest("=== 3 failed, 180 passed, 1 error in 9s ===") == (180, 4)
    rec = make_proof(ROOT, STATE_KEY, "=== 1 failed, 5 passed in 1s ===", "abc", 1_000)
    assert any("échoués" in r for r in verify_proof(rec, STATE_KEY, ROOT, 1_000))


async def test_unsigned_acceptance_flag_no_longer_sufficient(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock, flag=True, command_key=CMD_KEY)
    db.kv_set("acceptance_tests_ok", True)  # ancien booléen non signé
    pf = await eng.preflight(for_real=True)
    assert pf["tests_acceptation_ok"] is False


# ------------------------------------------------------------------ révocation
async def test_signature_from_before_revocation_is_refused(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = await real_engine(tmp_path, clock)
    t_sign = clock.now_ms()
    submit(db, "a1", t_sign, "activate_real", signed_payload("activate_real", FIELDS, t_sign, "n1"))
    # une seconde autorisation signée au même moment, conservée par un attaquant (nonce différent)
    stolen = signed_payload("activate_real", FIELDS, t_sign, "n2")
    await eng.process_commands()
    assert result(db, "a1")["ok"] and eng.guardian.state == State.REAL
    clock.advance(1_000)
    submit(db, "d1", clock.now_ms(), "deactivate_real", {})
    await eng.process_commands()
    assert eng.guardian.state == State.SIMULATION and eng.guardian.revoked_ms == clock.now_ms() + 30_000
    clock.advance(1_000)  # toujours dans la fenêtre de 5 min de la signature volée
    submit(db, "x1", clock.now_ms(), "activate_real", stolen)
    await eng.process_commands()
    res = result(db, "x1")
    assert not res["ok"] and any("révocation" in r for r in res["reasons"])
    assert eng.guardian.state == State.SIMULATION


async def test_emergency_stop_then_forged_resume_cannot_rearm_real(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = await real_engine(tmp_path, clock)
    submit(db, "a1", clock.now_ms(), "activate_real", signed_payload("activate_real", FIELDS, clock.now_ms(), "n1"))
    await eng.process_commands()
    assert eng.guardian.state == State.REAL
    clock.advance(1_000)
    submit(db, "s1", clock.now_ms(), "emergency_stop", {})  # arrêt d'urgence : NON signé, toujours possible
    await eng.process_commands()
    assert eng.guardian.state == State.PAUSE
    clock.advance(30_000)  # tolérance d'horloge après une révocation
    await eng.step()  # données de marché rafraîchies (sinon la reprise est refusée pour données périmées)
    # API compromise : « reprise » non signée -> refusée, le réel n'est pas réarmé
    clock.advance(1_000)
    submit(db, "r1", clock.now_ms(), "resume", {"password_ok": True})
    await eng.process_commands()
    res = result(db, "r1")
    assert not res["ok"] and res["needs_signature"] == "resume_real" and eng.guardian.state == State.PAUSE
    # signature d'une AUTRE commande réutilisée pour la reprise -> refusée
    clock.advance(1_000)
    submit(db, "r2", clock.now_ms(), "resume", signed_payload("activate_real", FIELDS, clock.now_ms(), "n3"))
    await eng.process_commands()
    assert not result(db, "r2")["ok"] and eng.guardian.state == State.PAUSE
    # reprise signée par le propriétaire après l'arrêt -> acceptée
    clock.advance(1_000)
    submit(db, "r3", clock.now_ms(), "resume", signed_payload("resume_real", {"reprise": "reel"}, clock.now_ms(), "n4"))
    await eng.process_commands()
    assert result(db, "r3")["ok"], result(db, "r3")
    assert eng.guardian.state == State.REAL


async def test_resume_to_simulation_stays_unsigned(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock, command_key=CMD_KEY)
    await eng.step()
    submit(db, "s1", clock.now_ms(), "emergency_stop", {})
    await eng.process_commands()
    submit(db, "r1", clock.now_ms(), "resume", {})
    await eng.process_commands()
    assert result(db, "r1")["ok"] and eng.guardian.state == State.SIMULATION


def test_revocation_timestamp_tamper_detected(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    eng.guardian.emergency_stop("owner")
    raw = db.kv_get("auth_revoked_ms")
    raw["v"] = 0  # écriture directe en base pour réhabiliter une ancienne signature
    db.kv_set("auth_revoked_ms", raw)
    assert eng.guardian.verify_integrity() is False and eng.guardian.state == State.INCIDENT


# ------------------------------------------------------------------ concurrence et reprise après panne
async def test_command_taken_once_by_concurrent_loops(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng_a, db = make_engine(tmp_path, clock)
    submit(db, "p1", clock.now_ms(), "emergency_stop", {})
    stale = db.q("SELECT * FROM control_commands WHERE status='pending' ORDER BY ts")  # lu par B avant A
    await eng_a.process_commands()
    eng_b, db_b = make_engine(tmp_path, clock)  # seconde boucle sur la même base
    db_b.q = lambda sql, args=(): stale if "status='pending'" in sql else type(db_b).q(db_b, sql, args)  # type: ignore[method-assign]
    await eng_b.process_commands()
    n = db.one("SELECT COUNT(*) n FROM audit WHERE action='arret_urgence'")["n"]
    assert n == 1


async def test_interrupted_command_not_replayed_after_crash(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    db.exec("INSERT INTO control_commands VALUES('c1',?,'emergency_stop','{}','processing',NULL,NULL)", (clock.now_ms(),))
    db.close()
    eng2, db2 = make_engine(tmp_path, clock)  # redémarrage
    r = json.loads(db2.one("SELECT status, result FROM control_commands WHERE id='c1'")["result"])
    assert not r["ok"] and "non rejouée" in r["reasons"][0]
    await eng2.process_commands()
    assert db2.one("SELECT COUNT(*) n FROM audit WHERE action='arret_urgence'")["n"] == 0
    assert eng2.guardian.state == State.SIMULATION
