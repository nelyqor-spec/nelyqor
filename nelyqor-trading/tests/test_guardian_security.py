"""Guardian et sécurité : états, mode réel explicite, LAB sans trading, aucune clé dans logs/frontend,
aucun retrait/marge possible, verrou d'instance."""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from backend.core.config import HardLimits, Settings
from backend.core.logging import JsonFormatter, register_secret
from backend.execution.engine import InstanceLock
from backend.guardian.guardian import REAL_CONFIRMATION_PHRASE, Guardian, IntentCheck, State
from backend.market_data.bitvavo_rest import BitvavoREST, ForbiddenOperation
from tests.conftest import STATE_KEY

ROOT = Path(__file__).resolve().parents[1]
PF_OK = {"tests": True, "reconciliation": True}


def entry(**kw):
    d = dict(market="BTC-EUR", side="buy", order_type="market", amount_quote=D(10), is_entry=True, spread_bps=D(5),
             min_order_quote=D(5), available_quote=D(100), equity=D(100), exposure_after=D(10), position_after=D(10))
    d.update(kw)
    return IntentCheck(**d)


def test_default_state_is_simulation(guardian):
    assert guardian.state == State.SIMULATION


def test_real_requires_server_flag(guardian):
    v = guardian.activate_real("owner", REAL_CONFIRMATION_PHRASE, True, PF_OK)
    assert not v.allowed and any("drapeau serveur" in r for r in v.reasons)
    assert guardian.state == State.SIMULATION


def test_real_requires_phrase_password_preflight(db, limits, clock):
    g = Guardian(db, limits, real_server_flag=True, now_ms=clock.now_ms, state_key=STATE_KEY)
    assert not g.activate_real("owner", "oui", True, PF_OK).allowed
    assert not g.activate_real("owner", REAL_CONFIRMATION_PHRASE, False, PF_OK).allowed
    assert not g.activate_real("owner", REAL_CONFIRMATION_PHRASE, True, {"tests": False}).allowed
    assert g.state == State.SIMULATION
    assert g.activate_real("owner", REAL_CONFIRMATION_PHRASE, True, PF_OK).allowed
    assert g.state == State.REAL
    audits = db.q("SELECT action, ok FROM audit WHERE action='activation_reel'")
    assert [a["ok"] for a in audits] == [0, 0, 0, 1]


def test_transition_cannot_bypass_real_activation(guardian):
    with pytest.raises(PermissionError):
        guardian.transition(State.REAL, "owner", "contournement")


def test_lab_cannot_activate_real_nor_trade(db, limits, clock):
    lab = Guardian(db, limits, real_server_flag=True, role="lab", now_ms=clock.now_ms, state_key=STATE_KEY)
    assert not lab.activate_real("lab", REAL_CONFIRMATION_PHRASE, True, PF_OK).allowed
    with pytest.raises(PermissionError):
        lab.transition(State.PAUSE, "lab", "x")
    assert not lab.check(entry()).allowed


def test_lab_cannot_read_exchange_keys(tmp_path):
    (tmp_path / "bitvavo_api_key").write_text("K" * 40)
    s = Settings(role="lab", secrets_dir=tmp_path)
    with pytest.raises(PermissionError):
        s.read_secret("bitvavo_api_key")
    s2 = Settings(role="api", secrets_dir=tmp_path)
    with pytest.raises(PermissionError):
        s2.read_secret("bitvavo_api_secret")
    assert Settings(role="engine", secrets_dir=tmp_path).read_secret("bitvavo_api_key") == "K" * 40


def test_emergency_stop_blocks_entries_not_exits(guardian):
    guardian.emergency_stop("owner")
    assert guardian.state == State.PAUSE
    assert not guardian.check(entry()).allowed
    assert guardian.check(entry(side="sell", is_entry=False, is_protective=True)).allowed


def test_resume_requires_checks(guardian):
    guardian.emergency_stop("owner")
    assert not guardian.resume("owner", {"donnees_fraiches": False}).allowed
    assert guardian.state == State.PAUSE
    assert guardian.resume("owner", {"donnees_fraiches": True}).allowed
    assert guardian.state == State.SIMULATION


def test_incident_allows_only_protective(guardian):
    guardian.incident("test")
    assert not guardian.check(entry(side="sell", is_entry=False, is_protective=False)).allowed
    assert guardian.check(entry(side="sell", is_entry=False, is_protective=True)).allowed


def test_margin_and_stale_and_anomaly_vetoed(guardian):
    assert not guardian.check(entry(margin=True)).allowed
    assert not guardian.check(entry(data_age_ms=60_000)).allowed
    assert not guardian.check(entry(anomaly=D("0.9"))).allowed
    assert not guardian.check(entry(spread_bps=None)).allowed
    assert not guardian.check(entry(mode="real")).allowed


def test_martingale_reinforcement_refused(guardian):
    assert not guardian.check(entry(reinforcement_of_loser=True)).allowed
    assert guardian.check(entry(reinforcement_of_loser=True, new_independent_thesis=True)).allowed


def test_no_global_loss_kill_switch(guardian):
    """Préférence du propriétaire : pas d'arrêt définitif fondé sur la seule perte cumulée."""
    assert guardian.check(entry(equity=D(20), available_quote=D(20), amount_quote=D(5), exposure_after=D(5),
                                position_after=D(5))).allowed


def test_hard_limits_forbid_margin(tmp_path):
    d = json.loads((ROOT / "config/hard_limits.json").read_text())
    d["allow_margin"] = True
    p = tmp_path / "h.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        HardLimits.load(p)
    d["allow_margin"], d["allow_withdrawals"] = False, True
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        HardLimits.load(p)


async def test_withdrawal_paths_blocked_in_client():
    c = BitvavoREST("http://127.0.0.1:9", "k" * 20, "s" * 20)
    for path in ("/withdrawal", "/transfer", "/deposit/address"):
        with pytest.raises(ForbiddenOperation):
            await c.request("POST", path, body={"symbol": "EUR"}, private=True)
    with pytest.raises(ForbiddenOperation):
        await c.request("POST", "/withdrawalHistory", private=True)
    assert not hasattr(c, "withdraw")
    await c.aclose()


def test_signature_matches_documented_scheme():
    import hashlib
    import hmac

    c = BitvavoREST(api_key="key", api_secret="secret")
    sig = c._sign(1700000000000, "POST", "/order", '{"a":1}')
    exp = hmac.new(b"secret", b'1700000000000POST/v2/order{"a":1}', hashlib.sha256).hexdigest()
    assert sig == exp


def test_secrets_redacted_in_logs():
    register_secret("SUPERSECRETVALUE123")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "clé=SUPERSECRETVALUE123", None, None)
    rec.data = {"api_secret": "abc", "nested": {"note": "SUPERSECRETVALUE123"}}
    out = JsonFormatter().format(rec)
    assert "SUPERSECRETVALUE123" not in out and "abc" not in out


def test_no_key_material_in_frontend_or_repo():
    pat = re.compile(r"(bitvavo-access-key|api[_-]?secret\s*[:=]\s*['\"][A-Za-z0-9]{16,}|BEGIN PRIVATE KEY)", re.I)
    for f in list((ROOT / "frontend").rglob("*")) + [ROOT / ".env.example"]:
        if f.is_file() and f.suffix in (".js", ".html", ".css", ".json", ".webmanifest", ".example"):
            assert not pat.search(f.read_text(errors="ignore")), f
    js = (ROOT / "frontend/app.js").read_text()
    assert "api.bitvavo.com" not in js  # l'iPhone ne parle jamais directement à l'échange


def test_instance_lock_exclusive(tmp_path):
    a, b = InstanceLock(tmp_path / "e.lock"), InstanceLock(tmp_path / "e.lock")
    a.acquire()
    with pytest.raises(RuntimeError):
        b.acquire()
    a.release()
    b.acquire()
    b.release()


def test_real_refused_without_integrity_key(db, limits, clock):
    g = Guardian(db, limits, real_server_flag=True, now_ms=clock.now_ms)
    v = g.activate_real("owner", REAL_CONFIRMATION_PHRASE, True, PF_OK)
    assert not v.allowed and any("intégrité" in r for r in v.reasons)


def test_direct_db_write_of_state_is_detected(db, limits, clock):
    """Un processus tiers (API compromise, LAB) qui écrit l'état dans la base ne peut pas passer en réel."""
    g = Guardian(db, limits, real_server_flag=True, now_ms=clock.now_ms, state_key=STATE_KEY)
    g.init_state()
    db.kv_set("guardian_state", {"v": "REEL_AUTORISE", "sig": "0" * 64})
    assert g.state == State.INCIDENT
    assert not g.verify_integrity()
    assert g.state == State.INCIDENT  # ré-signé en INCIDENT par le moteur
    assert not g.check(entry(mode="real")).allowed
    db.kv_set("guardian_state", "REEL_AUTORISE")  # format brut
    assert g.state == State.INCIDENT


def test_forged_resume_state_cannot_resume_to_real(db, limits, clock):
    g = Guardian(db, limits, real_server_flag=True, now_ms=clock.now_ms, state_key=STATE_KEY)
    g.init_state()
    g.emergency_stop("owner")
    db.kv_set("resume_state", {"v": "REEL_AUTORISE", "sig": "f" * 64})
    assert g.resume_state == State.SIMULATION  # valeur falsifiée ignorée
    assert g.resume("owner", {"ok": True}).allowed and g.state == State.SIMULATION


def test_resume_to_real_requires_prior_activation(db, limits, clock):
    g = Guardian(db, limits, real_server_flag=True, now_ms=clock.now_ms, state_key=STATE_KEY)
    g.init_state()
    g._put("resume_state", "REEL_AUTORISE")  # même signé, sans activation préalable
    g.emergency_stop("owner")
    g.resume("owner", {"ok": True})
    assert g.state == State.SIMULATION


def test_real_protective_sell_allowed_after_deactivation(db, limits, clock):
    """Après retour en simulation, une position réelle restante doit pouvoir être protégée/vendue."""
    g = Guardian(db, limits, real_server_flag=True, now_ms=clock.now_ms, state_key=STATE_KEY)
    g.init_state()
    assert g.activate_real("owner", REAL_CONFIRMATION_PHRASE, True, PF_OK).allowed
    g.deactivate_real("owner")
    assert not g.check(entry(mode="real")).allowed  # pas de nouvelle entrée réelle
    sell = entry(mode="real", side="sell", is_entry=False, is_protective=True, order_type="stopLoss",
                 sell_amount_base=D("0.001"), base_free_for_sale=D("0.001"))
    assert g.check(sell).allowed


def test_duplicate_sell_vetoed(guardian):
    v = guardian.check(entry(side="sell", is_entry=False, is_protective=True, sell_amount_base=D("0.002"),
                             base_free_for_sale=D("0.001")))
    assert not v.allowed and any("dupliquée" in r for r in v.reasons)


def test_conditional_buy_and_zero_equity_entry_vetoed(guardian):
    assert not guardian.check(entry(order_type="stopLoss")).allowed
    assert not guardian.check(entry(equity=D(0))).allowed


def test_api_and_lab_roles_cannot_read_engine_state_key(tmp_path):
    (tmp_path / "engine_state_key").write_text("x" * 40)
    for role in ("api", "lab"):
        with pytest.raises(PermissionError):
            Settings(role=role, secrets_dir=tmp_path).read_secret("engine_state_key")


def test_compose_isolates_lab_from_production_and_secrets():
    import yaml  # type: ignore[import-untyped]

    c = yaml.safe_load((ROOT / "compose.yaml").read_text())
    sv = c["services"]
    for name in ("lab", "collector"):
        assert "secrets" not in sv[name], name
        vols = " ".join(sv[name].get("volumes", []))
        assert "data:" not in vols and "docker.sock" not in vols, name  # pas de base de production
    assert any(v.startswith("market:/market:ro") for v in sv["lab"]["volumes"])
    assert set(sv["engine"]["secrets"]) >= {"bitvavo_api_key", "bitvavo_api_secret", "engine_state_key"}
    assert "secrets" in sv["api"] and set(sv["api"]["secrets"]) == {"owner_password_hash"}
    assert "ports" not in sv["engine"] and "ports" not in sv["api"]
