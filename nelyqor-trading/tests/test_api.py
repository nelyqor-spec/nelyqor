"""Interface/API : accès non authentifié refusé, CSRF, limitation des tentatives, cookies sécurisés,
commandes auditées, ré-authentification pour le réel et la liquidation, aucune fuite de secret."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.auth import AuthManager, hash_password
from backend.core.config import Settings
from backend.storage.db import Database

PW = "un-mot-de-passe-solide-42"


@pytest.fixture
def client(tmp_path):
    s = Settings(db_path=tmp_path / "a.db", role="api", secrets_dir=tmp_path, cookie_secure=True)
    db = Database(s.db_path)
    app = create_app(s, db, AuthManager(hash_password(PW)))
    c = TestClient(app, base_url="https://testserver")
    c.db = db  # type: ignore[attr-defined]
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
    row = client.db.one("SELECT command, status FROM control_commands")
    assert row["command"] == "pause" and row["status"] == "pending"
    assert client.db.one("SELECT COUNT(*) n FROM audit WHERE action='commande pause'")["n"] == 1


def test_bruteforce_lockout(client):
    for _ in range(5):
        assert client.post("/api/login", json={"password": "mauvais"}).status_code == 401
    assert client.post("/api/login", json={"password": PW}).status_code == 429


def test_real_activation_requires_reauth(client):
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    r = client.post("/api/control/activate_real", headers=h, json={"password": "faux", "confirmation": "x"})
    assert r.status_code == 401
    assert client.db.one("SELECT COUNT(*) n FROM control_commands")["n"] == 0
    r = client.post("/api/control/activate_real", headers=h, json={"password": PW, "confirmation": "x"})
    assert r.status_code == 200
    payload = client.db.one("SELECT payload FROM control_commands")["payload"]
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
