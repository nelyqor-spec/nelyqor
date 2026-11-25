"""Résilience : cycle complet du moteur, redémarrage avec positions ouvertes, perte réseau, réconciliation,
flux manuel non identifié, ordre inconnu, disque plein, arrêt d'urgence, verrou."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal as D

import httpx
import pytest

from backend.core.clock import FakeClock
from backend.core.config import Settings
from backend.execution.engine import OrderRequest
from backend.execution.simulator import SimulatedExchange
from backend.guardian.guardian import REAL_CONFIRMATION_PHRASE, State
from backend.market_data.models import MarketInfo
from backend.runner import Engine
from backend.storage.db import Database
from tests.conftest import BTC_RAW, STATE_KEY
from tests.fake_bitvavo import FakePublic


def make_engine(tmp_path, clock, flag=False):
    s = Settings(db_path=tmp_path / "e.db", role="engine", real_trading_server_flag=flag, secrets_dir=tmp_path)
    db = Database(s.db_path)
    eng = Engine(s, db, FakePublic(clock), clock, state_key=STATE_KEY)
    eng.start()
    return eng, db


async def test_full_cycle_simulation(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    await eng.step()
    assert eng.exec is not None and eng.exec.mode == "simulation"
    sc = db.kv_get("scanner")
    rows = {r["market"]: r for r in sc["rows"]}
    assert rows["BTC-EUR"]["eligible"] and not rows["DEAD-EUR"]["eligible"]
    assert db.kv_get("engine_heartbeat")["mode"] == "simulation"
    assert db.one("SELECT COUNT(*) n FROM equity_snapshots")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM candles")["n"] > 0
    # la bougie en cours n'est jamais stockée
    last = db.one("SELECT MAX(ts) t FROM candles")["t"]
    assert last + 3_600_000 <= clock.now_ms()


async def test_restart_with_open_position_rebuilds_from_journal(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    await eng.step()
    st = eng.states["BTC-EUR"]
    r = await eng.exec.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D(20),
                                           check=eng._check(st, "buy", D(20), True, D(100), D(100), D(20), "simulation")),
                              D(100))
    assert r.accepted
    qty = eng.paper.available["BTC"]
    db.close()
    # redémarrage : nouvelle instance, même base
    clock.advance(10_000)
    eng2, db2 = make_engine(tmp_path, clock)
    await eng2.step()
    total = eng2.paper.available.get("BTC", D(0)) + eng2.paper.in_order.get("BTC", D(0))
    assert total == qty
    assert eng2.held_positions("simulation")["BTC-EUR"].qty == qty
    # la position a été re-protégée par un stop natif CONFIRMÉ après redémarrage
    prot = db2.kv_get("protections_simulation")["BTC-EUR"]
    assert prot["protege"] and prot["etat"] == "CONFIRMED"
    assert eng2.paper.available["EUR"] < D(81)


async def test_restart_from_real_goes_through_reconciliation(tmp_path):
    clock = FakeClock()
    eng, db = make_engine(tmp_path, clock, flag=True)
    eng.guardian._put("guardian_state", "REEL_AUTORISE")
    db.close()
    eng2, db2 = make_engine(tmp_path, clock, flag=True)
    assert eng2.guardian.state == State.RECONCILIATION
    assert not eng2.guardian.check(_entry()).allowed


def _entry():
    from backend.guardian.guardian import IntentCheck
    return IntentCheck("BTC-EUR", "buy", "market", D(10), True, spread_bps=D(5), min_order_quote=D(5),
                       available_quote=D(100), equity=D(100))


async def test_network_loss_raises_and_data_goes_stale(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    await eng.step()
    eng.rest.fail = True
    with pytest.raises(httpx.ConnectError):
        await eng.step()
    clock.advance(eng.limits.max_stale_ms + 1000)
    pf = await eng.preflight(for_real=False)
    assert pf["donnees_marche_fraiches"] is False
    # la reprise est refusée tant que les données ne sont pas fraîches
    eng.guardian.emergency_stop("owner")
    assert not eng.guardian.resume("owner", pf).allowed


async def test_emergency_stop_via_command_keeps_positions(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    await eng.step()
    st = eng.states["BTC-EUR"]
    await eng.exec.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D(20),
                                       check=eng._check(st, "buy", D(20), True, D(100), D(100), D(20), "simulation")), D(100))
    db.exec("INSERT INTO control_commands(id,ts,command,payload,status) VALUES('c1',0,'emergency_stop','{}','pending')")
    await eng.process_commands()
    assert eng.guardian.state == State.PAUSE
    assert eng.paper.available.get("BTC", 0) > 0  # pause ≠ liquidation
    res = json.loads(db.one("SELECT result FROM control_commands WHERE id='c1'")["result"])
    assert res["ok"]


async def test_liquidation_requires_fresh_estimate_and_confirmation(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    await eng.step()
    st = eng.states["BTC-EUR"]
    await eng.exec.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D(20),
                                       check=eng._check(st, "buy", D(20), True, D(100), D(100), D(20), "simulation")), D(100))
    est = eng.liquidation_estimate()
    assert D(est["net_total_eur"]) > D(19)
    assert not (await eng.liquidate({"estimate_id": est["id"], "password_ok": True, "confirmation": "oui"}))["ok"]
    clock.advance(130_000)
    assert not (await eng.liquidate({"estimate_id": est["id"], "password_ok": True, "confirmation": "LIQUIDER"}))["ok"]
    est = eng.liquidation_estimate()
    r = await eng.liquidate({"estimate_id": est["id"], "password_ok": True, "confirmation": "LIQUIDER"})
    assert r["ok"] and r["resultats"][0]["status"] == "filled"
    assert eng.paper.available.get("BTC", D(0)) == 0


class RealSim(SimulatedExchange):
    """Échange simulé jouant le rôle du compte réel pour les tests de réconciliation."""

    has_credentials = True
    deposits: list = []
    withdrawals: list = []

    async def deposit_history(self, start=None):
        return self.deposits

    async def withdrawal_history(self, start=None):
        return self.withdrawals


async def test_unidentified_manual_flow_pauses_entries(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock, flag=True)
    real = RealSim([MarketInfo.from_bitvavo(BTC_RAW)], {"EUR": D(100), "ADA": D(50)}, now_ms=clock.now_ms)
    real.deposits = [{"symbol": "EUR", "amount": "100", "status": "completed", "txId": "t0", "timestamp": 1}]
    eng.attach_real_exchange(real)
    await eng.step()
    # 1er rapprochement : solde d'ouverture (100 EUR affectés), ADA déjà détenu EXCLU, dépôt antérieur non recompté
    assert await eng._reconcile_real()
    assert eng._ledger("real").s.cash["EUR"] == D(100)
    assert db.kv_get("real_excluded_symbols") == ["ADA"]
    assert await eng._reconcile_real()  # stable, pas de double comptage du dépôt t0
    # nouveau dépôt présent dans l'historique -> identifié et comptabilisé comme apport
    clock.advance(1000)
    real.external_deposit("EUR", D(50))
    real.deposits.append({"symbol": "EUR", "amount": "50", "status": "completed", "txId": "t1", "timestamp": clock.now_ms()})
    assert await eng._reconcile_real()
    assert eng._ledger("real").s.net_contributions_eur == D(150)
    # retrait avec frais : montant + frais sortent du compte, identifié
    real.available["EUR"] -= D(21)
    real.withdrawals = [{"symbol": "EUR", "amount": "20", "fee": "1", "status": "completed", "txId": "w1",
                         "timestamp": clock.now_ms()}]
    assert await eng._reconcile_real()
    # dépôt manuel non annoncé dans l'historique -> non identifié -> pause des entrées + notification
    real.external_deposit("EUR", D(7))
    assert not await eng._reconcile_real()
    assert not await eng._reconcile_real()  # répété : toujours UNE seule ligne d'écart
    assert len(db.q("SELECT * FROM external_flows WHERE identified=0")) == 1
    assert eng.guardian.state == State.PAUSE
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='flux_non_identifie'")["n"] == 1
    # rapprochement manuel : valorisé à l'acceptation (pas à 0 €), puis rapprochement propre
    assert eng.ack_flow("unk-EUR", "virement oublié")["ok"]
    assert db.one("SELECT value_eur FROM external_flows WHERE id='unk-EUR'")["value_eur"] == "7"
    assert await eng._reconcile_real()


async def test_activate_real_end_to_end_requires_everything(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock, flag=True)
    real = RealSim([MarketInfo.from_bitvavo(BTC_RAW)], {"EUR": D(0)}, now_ms=clock.now_ms)
    eng.attach_real_exchange(real)
    await eng.step()
    payload = {"password_ok": True, "confirmation": REAL_CONFIRMATION_PHRASE, "declare_retrait_desactive": True,
               "declare_ip_fixe": True, "declare_2fa": True, "accepte_risque_perte_totale": True}
    db.exec("INSERT INTO control_commands VALUES('a1',?,'activate_real',?,'pending',NULL,NULL)", (clock.now_ms(), json.dumps(payload)))
    await eng.process_commands()
    res = json.loads(db.one("SELECT result FROM control_commands WHERE id='a1'")["result"])
    assert not res["ok"] and any("tests_acceptation_ok" in r for r in res["reasons"])
    db.kv_set("acceptance_tests_ok", True)
    db.exec("INSERT INTO control_commands VALUES('a2',?,'activate_real',?,'pending',NULL,NULL)", (clock.now_ms(), json.dumps(payload)))
    await eng.process_commands()
    res = json.loads(db.one("SELECT result FROM control_commands WHERE id='a2'")["result"])
    assert res["ok"], res
    assert eng.exec.mode == "real" and eng.guardian.state == State.REAL


def test_disk_full_write_fails_cleanly(tmp_path):
    db = Database(tmp_path / "d.db")
    db.exec("INSERT INTO kv VALUES('a','1',0)")
    db.conn.execute("PRAGMA max_page_count=1")  # simule un disque plein pour SQLite
    with pytest.raises(sqlite3.OperationalError):
        with db.tx() as c:
            c.executemany("INSERT INTO audit(ts,actor,action,ok,details) VALUES(0,'x','y',1,?)",
                          [("z" * 5000,) for _ in range(200)])
    assert db.kv_get("a") == 1  # transaction annulée, base cohérente


def test_backup_restore(tmp_path):
    db = Database(tmp_path / "a.db")
    db.kv_set("x", {"v": 1})
    db.backup_to(tmp_path / "b.db")
    assert Database(tmp_path / "b.db").kv_get("x") == {"v": 1}


async def test_emergency_stop_processed_during_network_outage(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    await eng.step()
    eng.rest.fail = True
    db.exec("INSERT INTO control_commands(id,ts,command,payload,status) VALUES('s1',0,'emergency_stop','{}','pending')")
    clock.advance(700_000)
    with pytest.raises(httpx.ConnectError):
        await eng.step()
    assert eng.guardian.state == State.PAUSE  # traité malgré la panne réseau
    hb = db.kv_get("engine_heartbeat")
    assert hb["data_ok"] is False  # moteur vivant mais données périmées, signalé à l'interface


async def test_stale_command_expires_but_emergency_stop_never_expires(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    old = clock.now_ms() - 3_600_000
    db.exec("INSERT INTO control_commands VALUES('r1',?,'resume','{}','pending',NULL,NULL)", (old,))
    db.exec("INSERT INTO control_commands VALUES('e1',?,'emergency_stop','{}','pending',NULL,NULL)", (old,))
    await eng.process_commands()
    r1 = json.loads(db.one("SELECT result FROM control_commands WHERE id='r1'")["result"])
    assert not r1["ok"] and "expirée" in r1["reasons"][0]
    assert eng.guardian.state == State.PAUSE  # l'arrêt d'urgence, même ancien, est appliqué


async def test_command_interrupted_by_crash_is_not_replayed(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    db.exec("INSERT INTO control_commands VALUES('l1',?,'liquidation_confirm','{}','processing',NULL,NULL)",
            (clock.now_ms(),))
    db.close()
    eng2, db2 = make_engine(tmp_path, clock)  # redémarrage
    r = db2.one("SELECT status, result FROM control_commands WHERE id='l1'")
    assert r["status"] == "done" and "non rejouée" in r["result"]


async def test_liquidation_estimate_single_use(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make_engine(tmp_path, clock)
    await eng.step()
    est = eng.liquidation_estimate()
    assert (await eng.liquidate({"estimate_id": est["id"], "password_ok": True, "confirmation": "LIQUIDER"}))["ok"]
    again = await eng.liquidate({"estimate_id": est["id"], "password_ok": True, "confirmation": "LIQUIDER"})
    assert not again["ok"]
