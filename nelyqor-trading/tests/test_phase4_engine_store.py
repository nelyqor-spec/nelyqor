"""Contre-expertise sécurité phase 4 : chaque attaque vérifiée par l'auditeur indépendant est rejouée ici
contre le moteur corrigé (magasin d'état réservé au moteur, phrase de signature distincte, réallocation en
mémoire, révocation sur pause, cible de reprise figée, signature du futur refusée)."""
from __future__ import annotations

import json
import shutil
from decimal import Decimal as D

from backend.core.clock import FakeClock
from backend.core.command_sig import derive_key, sign
from backend.core.config import Settings
from backend.core.engine_store import EngineStore
from backend.guardian.guardian import REAL_CONFIRMATION_PHRASE, IntentCheck, State
from backend.market_data.models import MarketInfo
from backend.runner import Engine
from backend.storage.db import Database
from tests.conftest import BTC_RAW, STATE_KEY
from tests.fake_bitvavo import FakePublic
from tests.test_resilience import CMD_KEY, RealSim, signed_payload, valid_proof

FIELDS = {"confirmation": REAL_CONFIRMATION_PHRASE, "declare_retrait_desactive": True, "declare_ip_fixe": True,
          "declare_2fa": True, "accepte_risque_perte_totale": True}


def engine(tmp_path, clock, flag=True):
    s = Settings(db_path=tmp_path / "e.db", role="engine", real_trading_server_flag=flag, secrets_dir=tmp_path)
    db = Database(s.db_path)
    eng = Engine(s, db, FakePublic(clock), clock, state_key=STATE_KEY, command_key=CMD_KEY,
                 store_path=tmp_path / "state" / "etat_moteur.json")
    eng.start()
    return eng, db


async def to_real(tmp_path, clock):
    eng, db = engine(tmp_path, clock)
    eng.attach_real_exchange(RealSim([MarketInfo.from_bitvavo(BTC_RAW)], {"EUR": D(0)}, now_ms=clock.now_ms))
    await eng.step()
    db.kv_set("acceptance_proof", valid_proof(clock.now_ms()))
    submit(db, "a1", clock.now_ms(), "activate_real", signed_payload("activate_real", FIELDS, clock.now_ms(), "n1"))
    await eng.process_commands()
    assert eng.guardian.state == State.REAL
    return eng, db


def submit(db, cid, ts, command, payload):
    db.exec("INSERT INTO control_commands VALUES(?,?,?,?,'pending',NULL,NULL)", (cid, ts, command, json.dumps(payload)))


def result(db, cid):
    return json.loads(db.one("SELECT result FROM control_commands WHERE id=?", (cid,))["result"])


async def test_C2_replaying_old_signed_db_rows_has_no_effect(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = await to_real(tmp_path, clock)
    old = {r["k"]: r["v"] for r in db.q("SELECT k, v FROM kv WHERE k IN ('guardian_state','resume_state','real_activated_ms')")}
    clock.advance(1_000)
    submit(db, "d1", clock.now_ms(), "deactivate_real", {})
    await eng.process_commands()
    for k, v in old.items():  # API compromise : réécrit les anciennes lignes « REEL_AUTORISE »
        db.exec("UPDATE kv SET v=? WHERE k=?", (v, k))
    assert eng.guardian.verify_integrity() and eng.guardian.state == State.SIMULATION
    assert eng.exec is eng.paper_exec


async def test_E1_deleting_revocation_and_nonces_in_db_has_no_effect(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = engine(tmp_path, clock)
    eng.attach_real_exchange(RealSim([MarketInfo.from_bitvavo(BTC_RAW)], {"EUR": D(0)}, now_ms=clock.now_ms))
    await eng.step()
    db.kv_set("acceptance_proof", valid_proof(clock.now_ms()))
    payload = signed_payload("activate_real", FIELDS, clock.now_ms(), "n1")
    submit(db, "a1", clock.now_ms(), "activate_real", payload)
    await eng.process_commands()
    clock.advance(30_000)
    submit(db, "s1", clock.now_ms(), "emergency_stop", {})
    await eng.process_commands()
    db.exec("DELETE FROM kv WHERE k IN ('auth_revoked_ms','used_command_nonces')")
    submit(db, "a2", clock.now_ms(), "activate_real", payload)  # même signature, encore dans les 5 min
    await eng.process_commands()
    assert not result(db, "a2")["ok"] and eng.guardian.state == State.PAUSE


async def test_store_tampering_or_replay_of_old_file_is_detected(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = await to_real(tmp_path, clock)
    f = tmp_path / "state" / "etat_moteur.json"
    snapshot = tmp_path / "ancien.json"
    shutil.copy(f, snapshot)  # fichier signé du temps où le réel était actif
    eng.guardian.deactivate_real("owner")
    shutil.copy(snapshot, f)  # rejeu de l'ancien fichier (même signé)
    assert eng.guardian.verify_integrity() is False and eng.guardian.state == State.INCIDENT
    # modification directe : signature invalide
    raw = json.loads(f.read_text())
    raw["data"]["guardian_state"] = "REEL_AUTORISE"
    f.write_text(json.dumps(raw))
    assert eng.guardian.verify_integrity() is False and eng.guardian.state == State.INCIDENT


def test_store_deleted_after_initialisation_is_detected(tmp_path):
    p = tmp_path / "s" / "etat.json"
    st = EngineStore(p, STATE_KEY)
    st.set("guardian_state", "SIMULATION")
    p.unlink()
    assert EngineStore(p, STATE_KEY).tampered


async def test_E2_injected_reallocation_request_cannot_sell(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = engine(tmp_path, clock)
    await eng.step()
    db.kv_set("realloc_real", {"BTC-EUR": {"ts": clock.now_ms(), "au_profit_de": "X"}})
    db.kv_set("realloc_simulation", {"BTC-EUR": {"ts": clock.now_ms(), "au_profit_de": "X"}})
    assert eng._realloc == {"simulation": {}, "real": {}}  # seule la mémoire du moteur fait foi
    await eng.step()
    assert db.one("SELECT COUNT(*) n FROM intents WHERE side='sell'")["n"] == 0


async def test_M1_pause_revokes_previous_resume_signature(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = await to_real(tmp_path, clock)
    clock.advance(1_000)
    resume_sig = signed_payload("resume_real", {"reprise": "reel"}, clock.now_ms(), "r1")
    stolen = signed_payload("resume_real", {"reprise": "reel"}, clock.now_ms(), "r2")
    submit(db, "p0", clock.now_ms(), "pause", {})
    await eng.process_commands()
    clock.advance(1_000)
    resume_sig = signed_payload("resume_real", {"reprise": "reel"}, clock.now_ms(), "r1")
    submit(db, "r1", clock.now_ms(), "resume", resume_sig)
    await eng.process_commands()
    assert result(db, "r1")["ok"] and eng.guardian.state == State.REAL
    clock.advance(1_000)
    submit(db, "p1", clock.now_ms(), "pause", {})
    await eng.process_commands()
    clock.advance(1_000)
    submit(db, "r2", clock.now_ms(), "resume", stolen)  # signature antérieure à la pause
    await eng.process_commands()
    assert not result(db, "r2")["ok"] and eng.guardian.state == State.PAUSE


async def test_F1_future_dated_signature_refused(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = engine(tmp_path, clock)
    await eng.step()
    future = clock.now_ms() + 4 * 60_000
    submit(db, "a1", clock.now_ms(), "activate_real", signed_payload("activate_real", FIELDS, future, "f1"))
    await eng.process_commands()
    assert any("futur" in r for r in result(db, "a1")["reasons"])


def test_C1_signing_key_not_derivable_from_login_password(tmp_path, monkeypatch):
    import runpy
    import sys

    answers = iter(["Mot-de-passe-2026!", "Mot-de-passe-2026!", "phrase de signature longue", "phrase de signature longue"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(sys, "argv", ["set_password.py", str(tmp_path / "owner_password_hash")])
    runpy.run_path("scripts/set_password.py", run_name="__main__")
    key = bytes.fromhex((tmp_path / "owner_command_key").read_text().strip())
    salt = (tmp_path / "owner_command_salt").read_text().strip()
    assert key != derive_key("Mot-de-passe-2026!", salt)  # l'API connaît le mot de passe : il ne signe plus
    assert key == derive_key("phrase de signature longue", salt)
    assert sign(key, "x", 1, "n", {}) != sign(derive_key("Mot-de-passe-2026!", salt), "x", 1, "n", {})


def test_real_entry_vetoed_when_server_flag_absent(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = engine(tmp_path, clock, flag=False)
    it = IntentCheck("BTC-EUR", "buy", "market", D(10), True, mode="real", equity=D(100), available_quote=D(100))
    v = eng.guardian.check(it)
    assert not v.allowed and any("drapeau serveur" in r for r in v.reasons)
