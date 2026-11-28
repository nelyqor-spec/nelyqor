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
from backend.core.num import D
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


@pytest.mark.skipif(NODE is None, reason="node absent")
def test_pnl_coherence_never_silent():
    r = run_vue("""console.log(JSON.stringify([
      V.pnlCoherence({pnl_trading_eur: "0.78", pnl_realise_eur: "1.23", pnl_latent_eur: "-0.45"}),
      V.pnlCoherence({pnl_trading_eur: "0", pnl_realise_eur: "1.23", pnl_latent_eur: "-0.45"}),
      V.pnlCoherence({pnl_trading_eur: "0", pnl_realise_eur: "1.23", pnl_latent_eur: "-0.45", pnl_ecart_eur: "-0.78", pnl_ecart_raison: "x"}),
      V.pnlCoherence({pnl_trading_eur: "5", pnl_realise_eur: "5", pnl_latent_eur: null}),
      V.HELP.pnl[1]]));""")
    assert r[0]["ok"] is True
    assert r[1]["ok"] is False and r[1]["ecart"] == -0.78  # l'ancien défaut (0,00 € avec 1,23 / −0,45) est signalé
    assert r[2] == {"ok": False, "ecart": -0.78, "raison": "x"}
    assert r[3]["ok"] is True  # latent inconnu : rien à comparer, jamais estimé
    assert "réalisé" in r[4] and "DÉJÀ" in r[4]


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


def _publish(s, fills, snap, snap_before_fills=False):
    now = 1_770_000_000_000
    eng = Database(s.db_path)
    eng.kv_set("engine_heartbeat", {"ts": now, "mode": "simulation", "data_ok": True, "last_data_ms": now}, now)
    for i, (side, amt, px, fee) in enumerate(fills):
        eng.exec("INSERT INTO fills(id,intent_id,exchange_order_id,ts,market,side,amount,price,fee,fee_currency,fee_eur,"
                 "taker,mode) VALUES(?,?,?,?,'BTC-EUR',?,?,?,?,'EUR',?,1,'simulation')",
                 (f"f{i}", f"i{i}", f"o{i}", now - 1000 + i, side, amt, px, fee, fee))
    if snap:
        eng.exec("INSERT INTO equity_snapshots VALUES(?,?,?,?,?)", (now - 5000 if snap_before_fills else now, "simulation", *snap))
    DisplayPublisher(eng, s.display_file, lambda: now).publish(force=True)


def test_portfolio_realized_and_unrealized_split(client):
    assert client.post("/api/login", json={"password": PW}).status_code == 200
    s = client.settings
    # 100 € fictifs. Achat 0,001 BTC à 50 000 (frais 0,125) : trésorerie 49,875, coût 50,125.
    # Vente 0,0005 à 60 000 (frais 0,075) : +29,925 -> trésorerie 79,80 ; réalisé = 29,925 − 25,0625 = 4,8625.
    # Reste 0,0005 valorisé 27,50 : latent = 27,50 − 25,0625 = 2,4375 ; équité 107,30 ; total 7,30 = réalisé + latent.
    _publish(s, [("buy", "0.001", "50000", "0.125"), ("sell", "0.0005", "60000", "0.075")], ("107.30", "79.80", "27.50"))
    pf = client.get("/api/portfolio").json()
    assert pf["pnl_realise_eur"] == "4.86" and pf["pnl_latent_eur"] == "2.44"  # arrondis d'affichage
    assert D(pf["pnl_trading_eur"]) == D("7.30") and pf["pnl_ecart_eur"] is None and pf["pnl_ecart_raison"] is None
    assert D(pf["frais_eur"]) == D("0.2")  # frais DÉJÀ déduits du réalisé et du latent : jamais retirés deux fois
    assert pf["positions_detail"]["BTC-EUR"]["quantite"] == "0.0005" and "réalisé + latent" in pf["pnl_note"]


def test_portfolio_inconsistent_sources_publish_gap_never_wrong_total(client):
    client.post("/api/login", json={"password": PW})
    # trésorerie de la valorisation (77,50) ≠ trésorerie du journal (79,80) : total 5,00 contre 7,30 par les composantes
    _publish(client.settings, [("buy", "0.001", "50000", "0.125"), ("sell", "0.0005", "60000", "0.075")],
             ("105.00", "77.50", "27.50"))
    pf = client.get("/api/portfolio").json()
    assert D(pf["pnl_trading_eur"]) == D("5.00") and D(pf["pnl_ecart_eur"]) == D("-2.30")
    assert "divergent" in pf["pnl_ecart_raison"]


def test_portfolio_gap_when_valuation_older_than_last_fill(client):
    client.post("/api/login", json={"password": PW})
    _publish(client.settings, [("buy", "0.001", "50000", "0.125")], ("100.00", "100.00", "0"), snap_before_fills=True)
    pf = client.get("/api/portfolio").json()
    # total (valorisation) 0 ; composantes : réalisé 0 + latent (0 − 50,125) => écart publié, jamais un faux total
    assert D(pf["pnl_trading_eur"]) == 0 and D(pf["pnl_ecart_eur"]) == D("50.12")  # 50,125 arrondi bancaire
    assert "antérieure à la dernière exécution" in pf["pnl_ecart_raison"]


def test_portfolio_without_snapshot_never_estimates_latent(client):
    client.post("/api/login", json={"password": PW})
    _publish(client.settings, [("buy", "0.001", "50000", "0.1")], None)
    pf = client.get("/api/portfolio").json()
    assert pf["pnl_latent_eur"] is None and "non calculable" in pf["pnl_note"] and pf["pnl_realise_eur"] == "0.00"
