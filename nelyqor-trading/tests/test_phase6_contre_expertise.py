"""Phase 6 — contre-expertise adversariale : un test de non-régression par défaut corrigé (D1 à D9).

D1 sortie qui laissait la position SANS stop (annulation du stop puis vente refusée « donnée périmée »).
D2 dérive d'horloge de l'hôte : mesurée sur /v2/time, alertée, soustraite dans l'âge des données, pré-requis réel.
D3 sel public illisible par Caddy sans capacités + repli silencieux de la PWA sur l'API.
D4 achat « non-entrée » qui échappait à tous les plafonds.
D5 position détenue sans prix valorisée 0 dans l'exposition.
D6 un ordre UNKNOWN bloquait aussi ventes et stops.
D7 sauvegarde/restauration (volumes, magasin d'état, api.db, propriétaire) ; ordres ouverts rapprochés au démarrage.
D8 purge bornée de engine.db.
D9 garde-fous de deploy_check.sh, point d'accès officiel en réel, horodatage invalide, X-Forwarded-For.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sqlite3
import subprocess
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

from backend.core.clock import FakeClock
from backend.core.config import HardLimits, Settings
from backend.execution.engine import OrderRequest
from backend.guardian.guardian import IntentCheck, State
from backend.market_data.models import MarketInfo
from backend.market_data.universe import MarketState, check_eligibility, data_age_ms, parse_exchange_ts
from backend.portfolio.decision import Proposal
from backend.runner import Engine
from backend.storage.db import Database
from tests.conftest import BTC_RAW, STATE_KEY
from tests.fake_bitvavo import FakePublic
from tests.test_phase5_separation import make, real_ready, settings
from tests.test_phase6_plafonds import entry, exchange, executor, pending_limit, propose

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------------------------- outils
async def protected_btc(tmp_path: Path):
    """Moteur papier avec une position BTC de 30 € protégée par un stop CONFIRMÉ."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    st = eng.states["BTC-EUR"]
    r = await eng.paper_exec.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D(30), purpose="entree",
                                                 check=eng._check(st, "buy", D(30), True, D(100), D(100), D(30),
                                                                  "simulation")), D(100))
    assert r.accepted
    eng._kv_set("position_meta_simulation", {"BTC-EUR": {"entry_ts": clock.now_ms(), "strategy": "t",
                                                          "stop": str(st.best_bid * D("0.9"))}}, "simulation")
    eng.decider.evaluate_exit = lambda *a, **k: None  # type: ignore[method-assign]
    await eng.manage_positions(eng.paper_exec)
    assert db.kv_get("protections_simulation")["BTC-EUR"]["protege"] is True
    return eng, db, clock


def lag_book(eng: Engine, clock: FakeClock, lag_ms: int) -> None:
    orig = eng.rest.ticker_book

    async def lagging():
        return [{**b, "timestamp": clock.now_ms() - lag_ms} for b in await orig()]
    eng.rest.ticker_book = lagging  # type: ignore[method-assign]


def want_exit(eng: Engine) -> None:
    eng.decider.evaluate_exit = lambda m, s, df, h: Proposal(  # type: ignore[method-assign]
        m.market, "vendre", "t", amount_base=h.qty, reasons=["sortie"])


def active_stops(db: Database) -> list:
    return db.q("SELECT id FROM intents WHERE mode='simulation' AND side='sell' AND order_type='stopLoss' "
                "AND status='awaitingTrigger'")


# ======================================================================== D1 / C
async def test_D1_exit_refused_before_cancel_keeps_stop(tmp_path):
    """Données trop vieilles même pour une sortie de protection (200 s) : la vente est évaluée AVANT l'annulation,
    le stop hébergé n'est jamais retiré et la position reste « protégée »."""
    eng, db, clock = await protected_btc(tmp_path)
    lag_book(eng, clock, 200_000)
    want_exit(eng)
    for _ in range(3):
        clock.advance(5_000)
        await eng.refresh_tickers()
        await eng.manage_positions(eng.paper_exec)
    assert "BTC-EUR" in eng.held_positions("simulation")
    assert len(active_stops(db)) == 1
    assert db.one("SELECT COUNT(*) n FROM intents WHERE side='sell' AND status='canceled'")["n"] == 0
    assert db.kv_get("protections_simulation")["BTC-EUR"]["protege"] is True
    d = db.one("SELECT reasons FROM decisions WHERE action='vendre' ORDER BY ts DESC")
    assert "AVANT annulation du stop" in d["reasons"]


async def test_D1_exchange_lag_20s_protective_exit_goes_through(tmp_path):
    """Carnet horodaté 20 s en retard (ou hôte en avance) : la sortie de protection passe (seuil 120 s)."""
    eng, db, clock = await protected_btc(tmp_path)
    lag_book(eng, clock, 20_000)
    want_exit(eng)
    clock.advance(5_000)
    await eng.refresh_tickers()
    await eng.manage_positions(eng.paper_exec)
    assert "BTC-EUR" not in eng.held_positions("simulation")
    assert db.one("SELECT status FROM intents WHERE side='sell' AND order_type='market'")["status"] == "filled"


async def test_D1_sell_rejected_after_cancel_restores_stop_and_alerts(tmp_path):
    eng, db, clock = await protected_btc(tmp_path)
    want_exit(eng)
    eng.paper.fail_next = "reject"  # la vente au marché (après annulation du stop) est rejetée par l'échange
    clock.advance(5_000)
    await eng.refresh_tickers()
    await eng.manage_positions(eng.paper_exec)
    assert "BTC-EUR" in eng.held_positions("simulation")
    assert len(active_stops(db)) == 1, "stop non reposé après l'échec de la sortie"
    assert db.kv_get("protections_simulation")["BTC-EUR"]["protege"] is True
    n = db.one("SELECT COUNT(*) n FROM notifications WHERE kind='arret_protection'")["n"]
    assert n >= 1
    d = db.one("SELECT reasons FROM decisions WHERE action='vendre' ORDER BY ts DESC")
    assert "stop reposé" in d["reasons"]


def test_C_protective_threshold_wider_than_entry_threshold(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    _, g, _ = executor(tmp_path, clock)
    L = g.limits
    assert L.max_stale_ms_protective_exit == 120_000 > L.max_stale_ms

    def sell(age: int) -> IntentCheck:
        return IntentCheck("BTC-EUR", "sell", "market", D(30), False, True, spread_bps=D(5), min_order_quote=D(5),
                           data_age_ms=age, sell_amount_base=D("0.3"), base_free_for_sale=D("0.3"))
    assert g.check(sell(20_000)).allowed
    assert not g.check(sell(121_000)).allowed
    buy = entry("BTC-EUR", D(10), "simulation", clock)
    buy.data_age_ms = 20_000
    assert any("périmée" in r for r in g.check(buy).reasons)
    with pytest.raises(ValueError):
        dataclasses.replace(L, max_stale_ms_protective_exit=L.max_stale_ms - 1)


# ======================================================================== D2
class SkewedPublic(FakePublic):
    """Hôte en AVANCE de 20 s sur Bitvavo : l'échange horodate (et donne l'heure) 20 s « dans le passé »."""

    skew_ms = -20_000

    async def ticker_book(self):
        return [{**b, "timestamp": self.clock.now_ms() + self.skew_ms} for b in await super().ticker_book()]


async def test_D2_clock_skew_measured_alerted_and_subtracted(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    s = settings(tmp_path)
    db = Database(s.db_path)
    eng = Engine(s, db, SkewedPublic(clock), clock, state_key=STATE_KEY,
                 store_path=tmp_path / "state" / "etat_moteur.json")
    eng.start()
    await eng.step()
    assert eng.clock_skew_known and eng.clock_skew_ms == -20_000
    assert db.kv_get("decalage_horloge")["alerte"] is True
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='incident' AND dedup_key='decalage_horloge'")["n"] == 1
    st = eng.states["BTC-EUR"]
    assert data_age_ms(st, clock.now_ms(), eng.clock_skew_ms) < 1_000  # sans correction : 20 s => périmé
    assert data_age_ms(st, clock.now_ms()) >= 20_000
    assert check_eligibility(st, eng.limits, clock.now_ms(), clock_skew_ms=eng.clock_skew_ms).eligible
    pf = await eng.preflight(for_real=True)
    assert pf["horloge_synchronisee"] is False and pf["donnees_marche_fraiches"] is True
    eng.rest.skew_ms = 1_000
    clock.advance(600_000)
    await eng.step()
    assert (await eng.preflight(for_real=True))["horloge_synchronisee"] is True


# ======================================================================== D3
def test_D3_salt_public_readable_by_caddy_and_no_silent_fallback():
    c = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["caddy"]
    assert c["cap_drop"] == ["ALL"] and "DAC_OVERRIDE" not in c["cap_add"]
    dep = (ROOT / "docs" / "DEPLOIEMENT.md").read_text()
    assert "chmod 644 secrets/owner_command_salt" in dep
    js = (ROOT / "frontend" / "app.js").read_text()
    assert "ALERTE SÉCURITÉ" in js
    # le repli sur l'API n'est atteignable qu'en développement local http://localhost
    i = js.index('if (!salt) {')
    block = js[i:js.index("}", js.index("console.warn", i))]
    assert "throw new Error" in block and block.index("throw new Error") < block.index('api("/api/command_salt")')
    sh = (ROOT / "scripts" / "deploy_check.sh").read_text()
    assert "/meta/command_salt" in sh and "chmod 644" in sh


# ======================================================================== D4
async def test_D4_buy_not_flagged_entry_is_vetoed(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    e, g, db = executor(tmp_path, clock)
    req = OrderRequest("ETH-EUR", "buy", "limit", amount=D(1000) / D(2970), price=D(2970), post_only=True,
                       is_entry=False, strategy="audit")
    r = await e.submit(req, D(100))
    row = db.one("SELECT status, details FROM intents WHERE id=?", (r.intent_id,))
    assert row["status"] == "VETOED"
    assert any("achat non marqué comme entrée" in x for x in json.loads(row["details"])["veto"])


# ======================================================================== D5
async def test_D5_held_position_without_price_valued_at_last_price(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    eng.limits = dataclasses.replace(eng.limits, max_total_exposure_fraction=D("0.50"), max_position_fraction=D("0.50"))
    eng.guardian.limits = eng.limits
    st = eng.states["BTC-EUR"]
    r = await eng.paper_exec.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D(40), purpose="entree",
                                                 check=eng._check(st, "buy", D(40), True, D(100), D(100), D(40),
                                                                  "simulation")), D(100))
    assert r.accepted
    eng.prices_eur()
    st.best_bid = st.best_ask = st.last_price = None  # marché suspendu : plus aucun prix
    px = eng.prices_eur()
    assert "BTC" not in px
    assert eng._exposure("simulation", px) > D(39)  # dernier prix connu, jamais 0
    eng._last_px.clear()
    assert eng._exposure("simulation", px) > D(39)  # sinon coût moyen
    propose(eng, D(30), order="market")
    clock.advance(1000)
    await eng.decide()
    assert all(x["status"] != "filled" for x in db.q("SELECT status FROM intents WHERE market='ETH-EUR' AND side='buy'"))


# ======================================================================== D6
async def test_D6_unknown_order_blocks_entries_only(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    ex = exchange(clock)
    ex.available["BTC"] = D("0.001")
    e, _, db = executor(tmp_path, clock, ex=ex)
    r = await e.submit(pending_limit("ETH-EUR", D(20), entry("ETH-EUR", D(20), "simulation", clock)), D(100))
    e._set(r.intent_id, status="UNKNOWN")
    stop = OrderRequest("BTC-EUR", "sell", "stopLoss", amount=D("0.0006"), trigger_amount=D(45000),
                        trigger_reference="bestBid", is_entry=False, is_protective=True,
                        position_qty=D("0.001"), purpose="stop")
    res = await e.submit(stop, D(0))
    assert res.accepted, res.reasons
    buy = await e.submit(pending_limit("SOL-EUR", D(10), entry("SOL-EUR", D(10), "simulation", clock)), D(100))
    assert not buy.accepted and "inconnu" in buy.reasons[0]


# ======================================================================== D7
def _kv(path: Path, v: int) -> None:
    d = Database(path)  # fermée : la restauration remplace le fichier (comme moteur ARRÊTÉ)
    d.kv_set("x", {"v": v})
    d.close()


def _sh(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(ROOT / "scripts" / script), *args], env={**os.environ, **env},
                          capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl requis")
def test_D7_backup_restore_volumes_state_api_and_owner(tmp_path):
    vol = tmp_path / "volumes"
    eng_d, st_d, api_d = (vol / f"nelyqor_{v}" / "_data" for v in ("engine_data", "engine_state", "api_data"))
    for d in (eng_d, st_d, api_d):
        d.mkdir(parents=True)
    pw = tmp_path / "pass"
    pw.write_text("phrase-de-test-factice")
    env = {"NELYQOR_VOLUMES_DIR": str(vol), "NELYQOR_BACKUP_PASS_FILE": str(pw), "NELYQOR_RESTORE_OWNER": "-"}
    dest = tmp_path / "bk"
    r = _sh("backup.sh", env, str(dest))
    assert r.returncode != 0 and "REFUS : base du moteur absente" in r.stderr  # cible absente refusée
    _kv(eng_d / "engine.db", 1)
    r = _sh("backup.sh", env, str(dest))
    assert r.returncode != 0 and "magasin d'état absent" in r.stderr
    (st_d / "etat_moteur.json").write_text('{"etat": "original"}')
    a = Database(api_d / "api.db")
    a.exec("CREATE TABLE IF NOT EXISTS t(v)")
    a.close()
    r = _sh("backup.sh", env, str(dest))
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in dest.iterdir())
    assert any(n.startswith("engine-") for n in names) and any(n.startswith("api-") for n in names)
    assert any(n.startswith("etat-moteur-") and n.endswith(".tar.enc") for n in names)
    # dégâts puis restauration : base, magasin d'état et api.db reviennent
    _kv(eng_d / "engine.db", 2)
    (st_d / "etat_moteur.json").write_text('{"etat": "abime"}')
    src = next(dest.glob("engine-*.db.enc"))
    owner = "-" if os.getuid() != 0 else "10001:10001"
    r = _sh("restore.sh", {**env, "NELYQOR_RESTORE_OWNER": owner}, str(src))
    assert r.returncode == 0, r.stderr
    assert json.loads(sqlite3.connect(eng_d / "engine.db").execute("SELECT v FROM kv WHERE k='x'").fetchone()[0]) == {"v": 1}
    assert json.loads((st_d / "etat_moteur.json").read_text()) == {"etat": "original"}
    if os.getuid() == 0:
        assert (eng_d / "engine.db").stat().st_uid == 10001 and (st_d / "etat_moteur.json").stat().st_uid == 10001
    # cible absente : refus
    shutil.rmtree(st_d)
    r = _sh("restore.sh", env, str(src))
    assert r.returncode != 0 and "REFUS" in r.stderr


async def test_D7_open_orders_unknown_to_journal_are_counted_and_block_real(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    rep = await eng.check_open_orders(eng.real_exec)
    assert rep["inconnus"] == [] and (await eng.preflight(for_real=True))["aucun_ordre_ouvert_inconnu"] is True
    # ordre posé hors du moteur (ou journal restauré trop ancien)
    st = eng.states["BTC-EUR"]
    await real.create_order({"market": "BTC-EUR", "side": "buy", "orderType": "limit", "operatorId": 1,
                             "amount": "0.0002", "price": str(st.best_bid * D("0.9")), "clientOrderId": "hors-moteur"})
    rep = await eng.check_open_orders(eng.real_exec)
    assert rep["ouverts_echange"] == 1 and len(rep["inconnus"]) == 1
    assert eng.guardian.state == State.PAUSE
    assert (await eng.preflight(for_real=True))["aucun_ordre_ouvert_inconnu"] is False
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='ordre_inconnu'")["n"] == 1


async def test_D7_open_orders_checked_once_at_startup_in_real(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    assert eng._open_orders_checked is True
    assert db.kv_get("ordres_ouverts_rapprochement")["mode"] == "real"


# ======================================================================== D8
def test_D8_bounded_purge_keeps_journal_and_daily_equity(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    now, day = clock.now_ms(), 86_400_000
    old = now - 800 * day
    with db.tx() as c:
        c.executemany("INSERT INTO decisions(id,ts,mode,market,strategy,action,accepted,reasons,details) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", [(f"d{i}", old + i, "simulation", "BTC-EUR", "s", "a", 0, "[]", "{}")
                                                    for i in range(10)] + [("dn", now, "simulation", "BTC-EUR", "s", "a", 0, "[]", "{}")])
        c.executemany("INSERT INTO intents(id,ts,mode,market,side,order_type,status,client_order_id) VALUES(?,?,?,?,?,?,?,?)",
                      [("v-old", old, "simulation", "BTC-EUR", "buy", "market", "VETOED", "c1"),
                       ("f-old", old, "simulation", "BTC-EUR", "buy", "market", "filled", "c2")])
        c.executemany("INSERT INTO equity_snapshots VALUES(?,?,?,?,?)",
                      [(old + i * 60_000, "real", "1", "1", "0") for i in range(5)])
        c.execute("INSERT INTO candles VALUES('BTC-EUR','1h',?,'1','1','1','1','1',0,'t')", (old,))
        c.execute("INSERT INTO fills(id,intent_id,ts,market,side,amount,price,fee,fee_currency,mode) "
                  "VALUES('fx','f-old',?,'BTC-EUR','buy','1','1','0','EUR','simulation')", (old,))
    out = eng.purge_history()
    assert out["decisions"] == 10 and out["intents_vetoed"] == 1 and out["candles"] == 1 and out["equity"] == 4
    assert db.one("SELECT COUNT(*) n FROM decisions")["n"] == 1
    assert db.one("SELECT status FROM intents WHERE id='f-old'")["status"] == "filled"  # jamais purgé
    assert db.one("SELECT COUNT(*) n FROM fills WHERE id='fx'")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM equity_snapshots WHERE mode='real'")["n"] == 1  # un par jour conservé
    assert db.kv_get("purge_historique")["retention_jours"]["decisions"] == 90


def test_D8_retention_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("NELYQOR_RETENTION_DECISIONS_DAYS", "7")
    assert Settings(db_path=tmp_path / "e.db").retention_days["decisions"] == 7


# ======================================================================== D9
def test_D9_deploy_check_refuses_production_project_and_image_tag():
    sh = ROOT / "scripts" / "deploy_check.sh"
    r = subprocess.run(["bash", str(sh)], env={**os.environ, "DEPLOY_CHECK_PROJECT": "nelyqor"},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "REFUS" in r.stderr
    r = subprocess.run(["bash", str(sh)], env={**os.environ, "DEPLOY_CHECK_IMAGE": "nelyqor-trading:local"},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "tag de l'image de production" in r.stderr
    txt = sh.read_text()
    assert "label=com.docker.compose.project=nelyqor" in txt  # pile de production active => refus
    di = (ROOT / ".dockerignore").read_text()
    assert "scripts/deploy_fake_bitvavo.py" in di and "scripts/deploy_probe.py" in di


async def test_D9_real_requires_official_endpoint(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, _ = make(tmp_path, clock)
    assert eng.official_endpoints() is True
    eng.s.bitvavo_rest_url = "http://fakebitvavo:8080/v2"
    assert eng.official_endpoints() is False
    assert (await eng.preflight(for_real=True))["point_acces_bitvavo_officiel"] is False


@pytest.mark.parametrize("raw,expected", [(None, 0), ("abc", 0), (1.5, 0), (True, 0), (-5, 0), ("123", 123), (456, 456)])
def test_D9_parse_exchange_ts(raw, expected):
    assert parse_exchange_ts(raw) == expected


async def test_D9_invalid_book_timestamp_does_not_break_other_markets(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    orig = eng.rest.ticker_book

    async def odd():
        rows = await orig()
        return [{**b, "timestamp": None if b["market"] == "BTC-EUR" else "pas-un-entier"} for b in rows]
    eng.rest.ticker_book = odd  # type: ignore[method-assign]
    await eng.step()
    assert eng.states["BTC-EUR"].best_bid is not None and eng.states["ETH-EUR"].best_bid is not None
    assert check_eligibility(eng.states["ETH-EUR"], eng.limits, clock.now_ms()).eligible


def test_D9_forwarded_for_only_from_caddy():
    c = yaml.safe_load((ROOT / "compose.yaml").read_text())
    api_cmd = c["services"]["api"]["command"]
    ip = c["services"]["caddy"]["networks"]["default"]["ipv4_address"]
    assert api_cmd[api_cmd.index("--forwarded-allow-ips") + 1] == ip != "*"
    assert c["networks"]["default"]["ipam"]["config"][0]["subnet"] == "172.30.57.0/24"


def test_data_age_unknown_exchange_ts_uses_reception():
    st = MarketState(MarketInfo.from_bitvavo(BTC_RAW), ts_received_ms=1_000, ts_market_ms=0)
    assert data_age_ms(st, 2_000, clock_skew_ms=-50_000) == 1_000
    assert HardLimits.load().max_stale_ms_protective_exit == 120_000
