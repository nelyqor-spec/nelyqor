"""Interface/API : accès non authentifié refusé, CSRF, limitation des tentatives, cookies sécurisés,
commandes auditées, ré-authentification pour le réel et la liquidation, aucune fuite de secret."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.auth import AuthManager, hash_password
from backend.core.config import Settings
from backend.storage.channels import DisplayPublisher
from backend.storage.db import Database

PW = "un-mot-de-passe-solide-42"


@pytest.fixture
def client(tmp_path):
    # phase 5 : l'API ne reçoit AUCUNE base du moteur ; elle lit l'affichage, écrit les commandes et son audit
    s = Settings(db_path=tmp_path / "engine.db", role="api", secrets_dir=tmp_path, cookie_secure=True)
    app = create_app(s, AuthManager(hash_password(PW)))
    c = TestClient(app, base_url="https://testserver")
    c.commands = app.state.commands  # type: ignore[attr-defined]
    c.api_db = app.state.api_db  # type: ignore[attr-defined]
    c.settings = s  # type: ignore[attr-defined]
    return c


def login(c):
    r = c.post("/api/login", json={"password": PW})
    assert r.status_code == 200
    return r.json()["csrf"]


def test_unauthenticated_refused(client):
    for path in ("/api/status", "/api/portfolio", "/api/markets", "/api/orders", "/api/export/operations.csv"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/control/emergency_stop").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_login_sets_secure_cookie(client):
    r = client.post("/api/login", json={"password": PW})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "secure" in sc and "samesite=strict" in sc


def test_csrf_required_for_commands(client):
    csrf = login(client)
    assert client.post("/api/control/pause").status_code == 403
    assert client.post("/api/control/pause", headers={"X-CSRF-Token": "faux"}).status_code == 403
    r = client.post("/api/control/pause", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    row = client.commands.one("SELECT command, status FROM control_commands")
    assert row["command"] == "pause" and row["status"] == "pending"
    assert client.api_db.one("SELECT COUNT(*) n FROM audit WHERE action='commande pause'")["n"] == 1


def test_bruteforce_lockout(client):
    for _ in range(5):
        assert client.post("/api/login", json={"password": "mauvais"}).status_code == 401
    assert client.post("/api/login", json={"password": PW}).status_code == 429


def test_real_activation_requires_reauth(client):
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    r = client.post("/api/control/activate_real", headers=h, json={"password": "faux", "confirmation": "x"})
    assert r.status_code == 401
    assert client.commands.one("SELECT COUNT(*) n FROM control_commands")["n"] == 0
    r = client.post("/api/control/activate_real", headers=h, json={"password": PW, "confirmation": "x"})
    assert r.status_code == 200
    payload = client.commands.one("SELECT payload FROM control_commands")["payload"]
    assert PW not in payload  # le mot de passe n'est jamais stocké


def test_status_exposes_no_secret(client, tmp_path):
    (tmp_path / "bitvavo_api_secret").write_text("S" * 64)
    csrf = login(client)
    body = client.get("/api/status").text
    assert "S" * 64 not in body
    assert client.get("/api/status").json()["etat"] == "SIMULATION"
    assert client.get("/api/session").json()["csrf"] == csrf


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]


def test_input_validation(client):
    csrf = login(client)
    r = client.post("/api/control/ack_flow", headers={"X-CSRF-Token": csrf}, json={"flow_id": "x" * 1000})
    assert r.status_code == 422


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Nelyqor" in r.text
    assert client.get("/manifest.webmanifest").status_code == 200


# ------------------------------------------------------------------ phase 5 : bases séparées
def _publish_display(s, now=1_770_000_000_000, **kv):
    """Le MOTEUR publie l'affichage depuis sa propre base (que l'API n'ouvre jamais)."""
    eng_db = Database(s.db_path)
    for k, v in kv.items():
        eng_db.kv_set(k, v, now)
    eng_db.exec("INSERT INTO fills(id,intent_id,exchange_order_id,ts,market,side,amount,price,fee,fee_currency,fee_eur,"
                "taker,mode) VALUES('f1','i1','o1',?,'BTC-EUR','buy','0.001','50000','0.1','EUR','0.1',1,'real')", (now,))
    eng_db.exec("INSERT INTO control_commands VALUES('c-done',?,'pause','{}','done',?,?)", (now, now, '{"ok": true}'))
    pub = DisplayPublisher(eng_db, s.display_file, lambda: now)
    pub.publish(force=True)
    return eng_db, pub


def test_api_never_opens_engine_db_and_reads_display_readonly(client):
    import sqlite3

    s = client.settings
    login(client)
    client.get("/api/status")
    client.get("/api/portfolio")
    assert not s.db_path.exists()  # l'API n'a créé ni ouvert la base du moteur
    eng_db, _ = _publish_display(s, guardian_state={"v": "PAUSE_NOUVELLES_ENTREES"})
    assert client.get("/api/status").json()["etat"] == "PAUSE_NOUVELLES_ENTREES"
    assert client.get("/api/orders").json()["fills"][0]["id"] == "f1"
    view = client.app.state.db
    assert not hasattr(view, "exec") and not hasattr(view, "tx")  # aucune méthode d'écriture exposée
    with pytest.raises(sqlite3.OperationalError):  # connexion en lecture seule (mode=ro + query_only)
        view._get().conn.execute("INSERT INTO fills(id, mode) VALUES('faux-depot', 'real')")
    assert eng_db.one("SELECT COUNT(*) n FROM fills")["n"] == 1


def test_command_channel_holds_only_control_commands(client):
    tables = {r["name"] for r in client.commands.q("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"control_commands"}  # aucune table du moteur dans le canal écrit par l'API
    assert client.commands.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # lisible sur un montage :ro


def test_command_status_from_engine_log_then_pending(client):
    csrf = login(client)
    cid = client.post("/api/control/pause", headers={"X-CSRF-Token": csrf}).json()["command_id"]
    assert client.get(f"/api/commands/{cid}").json()["status"] == "pending"
    _publish_display(client.settings)
    assert client.get("/api/commands/c-done").json()["result"] == {"ok": True}
    assert client.get("/api/commands/inconnue").status_code == 404


def test_ack_flow_forwards_owner_signature(client):
    import json

    csrf = login(client)
    r = client.post("/api/control/ack_flow", headers={"X-CSRF-Token": csrf},
                    json={"flow_id": "unk-EUR", "signature": "ab" * 32, "sig_ts": 1, "sig_nonce": "n",
                          "sig_fields": {"flow_id": "unk-EUR"}})
    assert r.status_code == 200
    payload = json.loads(client.commands.one("SELECT payload FROM control_commands")["payload"])
    assert payload["sig_fields"] == {"flow_id": "unk-EUR"} and payload["signature"] == "ab" * 32
