"""PWA phase 7 (point 6) : installation iPhone, service worker sans données sensibles, réalisé/latent exacts côté API,
logique d'affichage (SIMULATION/RÉEL jamais ambigu, position NON protégée = alerte distincte d'un stop actif)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.auth import AuthManager, hash_password
from backend.core.config import ROOT, Settings
from backend.storage.channels import DisplayPublisher
from backend.storage.db import Database

FE = ROOT / "frontend"
PW = "un-mot-de-passe-solide-42"


# ------------------------------------------------------------------ installation et service worker
def test_install_meta_and_manifest():
    html = (FE / "index.html").read_text()
    assert 'rel="apple-touch-icon" href="icons/icon-180.png"' in html and (FE / "icons/icon-180.png").is_file()
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
    assert "viewport-fit=cover" in html and 'rel="manifest"' in html
    m = json.loads((FE / "manifest.webmanifest").read_text())
    assert m["display"] == "standalone" and m["lang"] == "fr" and m["start_url"] == m["scope"] == "./"
    for ic in m["icons"]:
        assert (FE / ic["src"]).is_file()
    css = (FE / "app.css").read_text()
    for side in ("top", "bottom", "left", "right"):
        assert f"env(safe-area-inset-{side})" in css  # encoche et barre d'accueil
    assert "prefers-color-scheme: light" in css and '[data-theme="light"]' in css


def test_service_worker_never_caches_sensitive_data():
    sw = (FE / "sw.js").read_text()
    assert re.search(r'NEVER = \["/api/", "/meta/"\]', sw)
    assert 'e.request.method !== "GET"' in sw and "url.origin !== self.location.origin" in sw
    assert "cache.put" not in sw and ".put(" not in sw  # aucune réponse réseau réécrite en cache
    shell = re.search(r"SHELL = \[(.*?)\];", sw, re.S).group(1)
    assert "api" not in shell and "meta" not in shell
    for f in re.findall(r'"([^"]+)"', shell):
        assert f == "./" or (FE / f).is_file(), f


def test_emergency_stop_one_gesture_with_confirmation():
    html = (FE / "index.html").read_text()
    assert 'id="fab-stop"' in html and html.index('id="fab-stop"') > html.index('id="tabs"')  # dans la barre du bas
    js = (FE / "app.js").read_text()
    i = js.index("function askStop")
    body = js[i:js.index("\n}", i)]
    assert "stop-confirm" in body and "emergency_stop" in body and "Annuler" in body
    assert 'on("btn-stop"' not in js  # plus aucun envoi direct sans confirmation


# ------------------------------------------------------------------ logique d'affichage (node)
NODE = shutil.which("node")


def run_vue(snippet: str) -> dict:
    js = f'import * as V from "{(FE / "vue.js").as_posix()}";\n{snippet}'
    out = subprocess.run([NODE, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)  # type: ignore[list-item]
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node absent")
def test_unprotected_position_is_a_distinct_red_alert():
    pf = {"mode": "simulation",
          "positions": {"BTC-EUR": {"stop": "1"}, "ETH-EUR": {"stop": "2"}, "SOL-EUR": {"stop": "3"}},
          "protections": {"BTC-EUR": {"protege": True, "etat": "ACTIF", "declencheur": "1"},
                          "ETH-EUR": {"protege": False, "etat": "REFUSE", "declencheur": "2", "raison": "refus"},
                          "ADA-EUR": {"protege": "true", "etat": "?"}},  # valeur non booléenne : jamais « protégée »
          "protections_reelles": {"XRP-EUR": {"protege": False, "etat": "ERREUR", "raison": "x"}}}
    r = run_vue(f"const pf = {json.dumps(pf)}; console.log(JSON.stringify({{rows: V.positionRows(pf), al: V.alertsFrom(null, pf)}}));")
    st = {(x["marche"], x["mode"]): x["statut"] for x in r["rows"]}
    assert st == {("BTC-EUR", "simulation"): "actif", ("ETH-EUR", "simulation"): "non_protegee",
                  ("SOL-EUR", "simulation"): "inconnue", ("ADA-EUR", "simulation"): "non_protegee",
                  ("XRP-EUR", "real"): "non_protegee"}
    eth = next(x for x in r["rows"] if x["marche"] == "ETH-EUR")
    assert eth["declencheur"] is None  # un stop refusé n'est jamais présenté comme un déclencheur actif
    red = {a["marche"] for a in r["al"] if a["niveau"] == "rouge" and a["cle"] == "non_protegee"}
    assert red == {"ETH-EUR", "SOL-EUR", "ADA-EUR", "XRP-EUR"}
    assert any("ARGENT RÉEL" in a["titre"] for a in r["al"] if a["marche"] == "XRP-EUR")


@pytest.mark.skipif(NODE is None, reason="node absent")
def test_mode_never_ambiguous_and_risk_rule():
    r = run_vue("""console.log(JSON.stringify({
      a: V.modeInfo({mode: "simulation", etat: "SIMULATION"}), b: V.modeInfo({mode: "real", etat: "PAUSE_NOUVELLES_ENTREES"}),
      c: V.modeInfo({mode: "simulation", etat: "REEL_AUTORISE"}), d: V.modeInfo(null),
      r1: V.riskSummary({rows: [{market: "A", propositions: [{risk: 30, strategy: "s"}, {risk: 60, strategy: "t"}]}]}),
      r2: V.riskSummary({rows: []}), cut: V.RISK_CUT, help: V.HELP.risque[1],
      al: V.alertsFrom({moteur_vivant: false, ordres_inconnus: 2, etat: "INCIDENT"}, {})}));""")
    assert r["a"]["kind"] == "sim" and "FICTIF" in r["a"]["detail"]
    assert r["b"]["kind"] == "real" and r["c"]["kind"] == "real" and r["b"]["titre"] == "ARGENT RÉEL"
    assert r["d"]["kind"] == "inconnu"
    assert r["r1"]["note"] == 60 and r["r1"]["reduit"] is True and r["r2"]["note"] is None and r["cut"] == 45
    assert "45/100" in r["help"] and "RÉDUIT" in r["help"]
    assert {a["cle"] for a in r["al"]} == {"robot_silencieux", "ordres_inconnus", "incident"}


def test_help_texts_are_plain_french():
    js = (FE / "vue.js").read_text()
    for k in ("mode", "capital", "realise", "latent", "frais", "risque", "robot", "arret", "labo", "short", "protection"):
        assert re.search(rf"\n  {k}: \[", js), k


# ------------------------------------------------------------------ API : réalisé / latent
@pytest.fixture
def client(tmp_path):
    s = Settings(db_path=tmp_path / "engine.db", role="api", secrets_dir=tmp_path, cookie_secure=True)
    c = TestClient(create_app(s, AuthManager(hash_password(PW))), base_url="https://testserver")
    c.settings = s  # type: ignore[attr-defined]
    return c


def _publish(s, fills, snap):
    now = 1_770_000_000_000
    eng = Database(s.db_path)
    eng.kv_set("engine_heartbeat", {"ts": now, "mode": "simulation", "data_ok": True, "last_data_ms": now}, now)
    for i, (side, amt, px, fee) in enumerate(fills):
        eng.exec("INSERT INTO fills(id,intent_id,exchange_order_id,ts,market,side,amount,price,fee,fee_currency,fee_eur,"
                 "taker,mode) VALUES(?,?,?,?,'BTC-EUR',?,?,?,?,'EUR',?,1,'simulation')",
                 (f"f{i}", f"i{i}", f"o{i}", now - 1000 + i, side, amt, px, fee, fee))
    if snap:
        eng.exec("INSERT INTO equity_snapshots VALUES(?,?,?,?,?)", (now, "simulation", *snap))
    DisplayPublisher(eng, s.display_file, lambda: now).publish(force=True)


def test_portfolio_realized_and_unrealized_split(client):
    assert client.post("/api/login", json={"password": PW}).status_code == 200
    s = client.settings
    # achat 0,002 BTC à 50 000 (frais 0,25) puis vente 0,001 à 60 000 (frais 0,15) ; reste 0,001 valorisé 55 €
    _publish(s, [("buy", "0.002", "50000", "0.25"), ("sell", "0.001", "60000", "0.15")], ("104.75", "49.75", "55"))
    pf = client.get("/api/portfolio").json()
    # réalisé = 60 - 0,15 - (100,25 / 2) = 9,725 -> 9,72 (arrondi bancaire) ; coût restant 50,125
    assert pf["pnl_realise_eur"] == "9.72" and pf["pnl_latent_eur"] == "4.88"
    assert pf["positions_detail"]["BTC-EUR"]["quantite"] == "0.001"
    assert pf["equity_eur"] == "104.75" and pf["pnl_trading_eur"].startswith("4.75")
    assert "latent" in pf["pnl_note"] and pf["positions_eur"] == "55"


def test_portfolio_without_snapshot_never_estimates_latent(client):
    client.post("/api/login", json={"password": PW})
    _publish(client.settings, [("buy", "0.001", "50000", "0.1")], None)
    pf = client.get("/api/portfolio").json()
    assert pf["pnl_latent_eur"] is None and "non calculable" in pf["pnl_note"] and pf["pnl_realise_eur"] == "0.00"
