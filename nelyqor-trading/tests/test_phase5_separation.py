"""Phase 5 (priorité 7) — bases séparées et capital authentifié.

1. Séparation : le moteur possède engine.db ; l'API écrit seulement commandes.db (table control_commands) et lit
   affichage.db en lecture seule ; le moteur ne relit jamais l'affichage.
2. Capital authentifié : un achat RÉEL est dimensionné sur min(journal, soldes Bitvavo) ; écart au-delà de la
   tolérance, soldes indisponibles ou périmés => aucun achat, ventes/protections permises ; borne relative au
   capital authentifié (le plafond absolu de 500 € ne protège pas 100 €).
3. Non-régression : commandes signées, anti-rejeu (nonces dans le magasin), révocation, comptabilité.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.auth import AuthManager, hash_password
from backend.core.clock import FakeClock
from backend.core.command_sig import sign
from backend.core.config import ROOT, Settings
from backend.execution.engine import OrderRequest
from backend.guardian.guardian import REAL_CONFIRMATION_PHRASE, IntentCheck, State
from backend.market_data.models import MarketInfo
from backend.portfolio.decision import Proposal
from backend.runner import Engine
from backend.storage.channels import ReadOnlyView, open_command_writer
from backend.storage.db import Database
from tests.conftest import BTC_RAW, STATE_KEY, post_command
from tests.fake_bitvavo import FakePublic
from tests.test_resilience import CMD_KEY, RealSim, signed_payload, valid_proof

PW = "un-mot-de-passe-solide-42"
FIELDS = {"confirmation": REAL_CONFIRMATION_PHRASE, "declare_retrait_desactive": True, "declare_ip_fixe": True,
          "declare_2fa": True, "accepte_risque_perte_totale": True}


def settings(tmp_path: Path, role: str = "engine") -> Settings:
    """Un dossier par volume, comme dans compose.yaml."""
    return Settings(db_path=tmp_path / "engine_data" / "engine.db", role=role, real_trading_server_flag=True,
                    secrets_dir=tmp_path, cookie_secure=True,
                    commands_db_path=tmp_path / "commands" / "commandes.db",
                    display_db_path=tmp_path / "display" / "affichage.db", api_db_path=tmp_path / "api_data" / "api.db")


def make(tmp_path: Path, clock: FakeClock) -> tuple[Engine, Database]:
    s = settings(tmp_path)
    db = Database(s.db_path)
    eng = Engine(s, db, FakePublic(clock), clock, state_key=STATE_KEY, command_key=CMD_KEY,
                 store_path=tmp_path / "state" / "etat_moteur.json")
    eng.start()
    return eng, db


def result(db: Database, cid: str) -> dict:
    return json.loads(db.one("SELECT result FROM control_commands WHERE id=?", (cid,))["result"])


async def real_ready(tmp_path: Path, eur: D = D(100)):
    """Moteur en REEL_AUTORISE sur un compte simulé de `eur` EUR (ouverture rapprochée, activation signée)."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    real = RealSim([MarketInfo.from_bitvavo(BTC_RAW)], {"EUR": eur}, now_ms=clock.now_ms)
    real.deposits, real.withdrawals = [], []
    eng.attach_real_exchange(real)
    await eng.step()
    assert await eng._reconcile_real()  # ouverture : `eur` affectés au robot
    db.kv_set("acceptance_proof", valid_proof(clock.now_ms()))
    post_command(eng, "a1", clock.now_ms(), "activate_real", signed_payload("activate_real", FIELDS, clock.now_ms(), "n1"))
    await eng.process_commands()
    assert result(db, "a1")["ok"], result(db, "a1")
    assert eng.guardian.state == State.REAL and eng.exec is eng.real_exec
    st = eng.states["BTC-EUR"]
    real.set_book("BTC-EUR", [(st.best_bid, D(10))], [(st.best_ask, D(10))])
    return eng, db, real, clock


def force_buy(eng: Engine, amount: D) -> list[tuple[D, D]]:
    """Le décideur propose un achat BTC de `amount` € ; on capture l'equity et la trésorerie qu'il reçoit."""
    seen: list[tuple[D, D]] = []

    def fake_entry(m, st, df, equity, available, exposure, held, depth, held_returns=None):
        if m.market != "BTC-EUR":
            return []
        seen.append((equity, available))
        return [Proposal("BTC-EUR", "acheter", "test_phase5", amount_quote=amount)]
    eng.decider.evaluate_entry = fake_entry  # type: ignore[method-assign]
    return seen


def real_buys(db: Database) -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM intents WHERE mode='real' AND side='buy' ORDER BY ts")]


def fake_identified_deposit(db: Database, eur: str) -> None:
    db.exec("INSERT INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,identified) "
            "VALUES(?,?,?,?,?,?,?,?,1)", (f"faux-{eur}", 1, "real", "deposit", "EUR", eur, eur, "bitvavo_history"))


# ====================================================================== 1. séparation des bases
def test_compose_separates_engine_db_from_api():
    c = yaml.safe_load((ROOT / "compose.yaml").read_text())
    sv = c["services"]
    vols = {n: [str(v) for v in s.get("volumes", [])] for n, s in sv.items()}
    # base du moteur et magasin d'état : moteur SEUL
    for name, vs in vols.items():
        if name != "engine":
            assert not any(v.startswith(("engine_data:", "engine_state:")) for v in vs), name
    assert "engine_data:/engine" in vols["engine"] and "engine_state:/state" in vols["engine"]
    # l'API lit l'affichage en lecture seule, écrit le canal de commandes ; le moteur lit les commandes en :ro
    assert "display:/display:ro" in vols["api"] and "commands:/commands" in vols["api"]
    assert "commands:/commands:ro" in vols["engine"] and "display:/display" in vols["engine"]
    assert not any(v.startswith("data:") for vs in vols.values() for v in vs)  # plus de base partagée
    for name in ("lab", "collector"):
        assert not any(v.split(":")[0] in ("display", "commands", "api_data") for v in vols[name]), name
    env_e, env_a = sv["engine"]["environment"], sv["api"]["environment"]
    assert env_e["NELYQOR_DB"] == "/engine/engine.db" and "NELYQOR_DB" not in env_a
    assert env_a["NELYQOR_DISPLAY_DB"] == env_e["NELYQOR_DISPLAY_DB"]
    assert env_a["NELYQOR_COMMANDS_DB"] == env_e["NELYQOR_COMMANDS_DB"]
    assert "/engine/" in " ".join(sv["engine"]["healthcheck"]["test"])


async def test_engine_never_reads_display_db(tmp_path):
    """Une falsification de l'affichage (positions, exclusions, capital, journal) n'a aucun effet sur le moteur et
    est écrasée à la publication suivante."""
    eng, db, real, clock = await real_ready(tmp_path)
    disp = Database(eng.display.path, journal_mode="DELETE")  # écrivain hors moteur (hypothèse de menace)
    disp.kv_set("real_excluded_symbols", ["BTC"])
    disp.kv_set("position_meta_real", {"BTC-EUR": {"stop": "999999"}})
    disp.kv_set("capital_authentifie", {"ok": True, "capital_authentifie_eur": "100000"})
    disp.exec("INSERT INTO external_flows(id,ts,mode,kind,symbol,amount,value_eur,source,identified) "
              "VALUES('faux',1,'real','deposit','EUR','10000','10000','api',1)")
    disp.close()
    seen = force_buy(eng, D(20))
    await eng.decide()
    assert eng._ledger("real").s.net_contributions_eur == D(100)  # le faux dépôt de l'affichage est ignoré
    assert seen == [(D(100), D(100))]  # dimensionné sur le journal du moteur et les soldes Bitvavo seulement
    assert eng._kv_get("real_excluded_symbols", [], "real") == []
    eng.publish_display(force=True)
    view = ReadOnlyView(eng.display.path)
    assert view.one("SELECT 1 FROM external_flows WHERE id='faux'") is None  # falsification écrasée
    assert view.kv_get("real_excluded_symbols") == []


async def test_compromised_api_cannot_reach_engine_data(tmp_path):
    """L'API (même compromise) ne détient que : une vue en lecture seule de l'affichage, le canal de commandes et
    son audit. Un « faux dépôt » écrit par elle n'atteint ni le journal ni la taille des achats."""
    import sqlite3

    eng, db, real, clock = await real_ready(tmp_path)
    eng.publish_display(force=True)
    app = create_app(settings(tmp_path, role="api"), AuthManager(hash_password(PW)))
    cmds, view, adb = app.state.commands, app.state.db, app.state.api_db
    for d in (cmds, adb):  # aucune des bases écrites par l'API n'est la base du moteur
        assert Path(d.path).resolve() != Path(db.path).resolve()
    assert Path(view.path).resolve() != Path(db.path).resolve()
    # l'attaquant crée des tables « moteur » dans le canal et y écrit un faux dépôt : jamais lues par le moteur
    cmds.exec("CREATE TABLE external_flows(id TEXT, ts INTEGER, mode TEXT, kind TEXT, symbol TEXT, amount TEXT, "
              "value_eur TEXT, source TEXT, identified INTEGER, note TEXT)")
    cmds.exec("INSERT INTO external_flows VALUES('faux',1,'real','deposit','EUR','10000','10000','x',1,'')")
    cmds.exec("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT, updated_ms INTEGER)")
    cmds.exec("INSERT INTO kv VALUES('real_excluded_symbols','[\"BTC\"]',0)")
    with pytest.raises(sqlite3.OperationalError):  # l'affichage est en lecture seule pour l'API
        view._get().conn.execute("INSERT INTO external_flows(id,mode,kind,symbol,amount,value_eur,identified) "
                                 "VALUES('faux','real','deposit','EUR','10000','10000',1)")
    seen = force_buy(eng, D(20))
    clock.advance(61_000)  # prochain cycle de décision
    await eng.step()
    assert eng._ledger("real").s.net_contributions_eur == D(100)
    assert seen and all(e == D(100) for e, _ in seen)  # aucun effet sur la taille des achats
    assert db.one("SELECT COUNT(*) n FROM external_flows WHERE id='faux'")["n"] == 0


async def test_api_and_engine_end_to_end_through_channels(tmp_path):
    """Chemin complet : commande HTTP -> commandes.db -> moteur -> journal du moteur -> affichage -> HTTP."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    app = create_app(settings(tmp_path, role="api"), AuthManager(hash_password(PW)))
    c = TestClient(app, base_url="https://testserver")
    csrf = c.post("/api/login", json={"password": PW}).json()["csrf"]
    assert c.get("/api/status").json()["etat"] == "SIMULATION"
    cid = c.post("/api/control/emergency_stop", headers={"X-CSRF-Token": csrf}).json()["command_id"]
    assert c.get(f"/api/commands/{cid}").json()["status"] == "pending"
    await eng.process_commands()  # l'arrêt d'urgence n'expire jamais (horodatage = horloge réelle de l'API)
    assert eng.guardian.state == State.PAUSE
    got = c.get(f"/api/commands/{cid}").json()
    assert got["status"] == "done" and got["result"]["ok"]
    assert c.get("/api/status").json()["etat"] == "PAUSE_NOUVELLES_ENTREES"
    audit = c.get("/api/incidents").json()["audit"]
    assert {a["source"] for a in audit} == {"api", "moteur"}  # audit de l'API + audit du moteur (affichage)


async def test_malformed_or_future_commands_refused(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    post_command(eng, "x1", clock.now_ms(), "withdraw", {})
    post_command(eng, "x2", clock.now_ms(), "resume", "{pas du json")
    post_command(eng, "x3", clock.now_ms() + 3_600_000, "deactivate_real", {})
    post_command(eng, "x4", clock.now_ms(), "pause", "x" * 20_000)
    await eng.process_commands()
    assert "inconnue" in result(db, "x1")["reasons"][0]
    assert "invalide" in result(db, "x2")["reasons"][0]
    assert "futur" in result(db, "x3")["reasons"][0]
    assert "invalide" in result(db, "x4")["reasons"][0]
    assert eng.guardian.state == State.SIMULATION


async def test_engine_survives_missing_or_corrupt_command_channel(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.process_commands()  # canal pas encore créé par l'API : aucune erreur
    eng.inbox.path.parent.mkdir(parents=True, exist_ok=True)
    eng.inbox.path.write_bytes(b"ceci n'est pas une base SQLite" * 100)
    eng.inbox.view.close()
    await eng.step()  # canal corrompu : le moteur continue (canal SSH /state toujours disponible)
    (tmp_path / "state" / "ARRET_URGENCE").touch()
    await eng.step()
    assert eng.guardian.state == State.PAUSE


async def test_real_activation_requires_separated_bases(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    s = Settings(db_path=tmp_path / "x.db", role="engine", real_trading_server_flag=True, secrets_dir=tmp_path,
                 commands_db_path=tmp_path / "x.db", display_db_path=tmp_path / "aff.db")
    eng = Engine(s, Database(s.db_path), FakePublic(clock), clock, state_key=STATE_KEY, command_key=CMD_KEY,
                 store_path=tmp_path / "state" / "e.json")
    eng.start()
    assert eng.bases_separated() is False
    assert (await eng.preflight(for_real=True))["bases_separees"] is False


# ====================================================================== 2. capital authentifié
async def test_buy_sized_on_min_of_ledger_and_authenticated_balances(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    auth = await eng.authenticated_capital()
    assert auth.ok and auth.equity_eur == D(100) and auth.eur_available == D(100)
    # faux dépôt SOUS la tolérance (0,40 €) : journal 100,40 €, soldes 100 € -> dimensionnement sur 100 €
    fake_identified_deposit(db, "0.40")
    clock.advance(31_000)
    await eng.refresh_tickers()
    seen = force_buy(eng, D(20))
    assert eng._ledger("real").s.cash["EUR"] == D("100.40")
    await eng.decide()
    assert seen == [(D(100), D(100))]  # le journal gonflé n'augmente pas la taille
    buys = real_buys(db)
    assert len(buys) == 1 and buys[0]["status"] == "filled"
    # comptabilité cohérente après un vrai remplissage : journal et soldes concordent (frais compris)
    clock.advance(31_000)
    after = await eng.authenticated_capital()
    assert after.ok, after.reasons
    assert after.divergence_eur <= D("0.40") + D("0.01")


async def test_fake_deposit_in_journal_blocks_buys_but_not_sells(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    seen = force_buy(eng, D(20))
    await eng.decide()  # achat légitime de 20 € (position à protéger ensuite)
    assert [b["status"] for b in real_buys(db)] == ["filled"]
    fake_identified_deposit(db, "10000")  # journal falsifié (défense en profondeur : l'API n'y a plus accès)
    clock.advance(61_000)
    await eng.refresh_tickers()
    seen.clear()
    await eng.decide()
    assert seen == [(D(0), D(0))]  # rien à dimensionner
    last = real_buys(db)[-1]
    assert len(real_buys(db)) == 2 and last["status"] == "VETOED"
    assert any("capital authentifié indisponible" in r for r in json.loads(last["details"])["veto"])
    assert any("écart journal/soldes" in r for r in eng._auth.reasons)
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='securite' AND message LIKE 'Achats réels suspendus%'")["n"] == 1
    eng.publish_display(force=True)
    assert ReadOnlyView(eng.display.path).kv_get("capital_authentifie")["ok"] is False  # signalé à la PWA
    # vente / protection : toujours permises
    st = eng.states["BTC-EUR"]
    h = eng.held_positions("real")["BTC-EUR"]
    qty = st.info.round_amount(h.qty)
    r = await eng.real_exec.submit(OrderRequest("BTC-EUR", "sell", "market", amount=qty, is_entry=False,
                                                is_protective=True, position_qty=h.qty, purpose="sortie",
                                                ref_price=st.best_bid,
                                                check=eng._check(st, "sell", qty * st.best_bid, False, D(0), D(0), D(0),
                                                                 "real")), D(0))
    assert r.accepted, r.reasons


async def test_unexplained_balance_drop_blocks_buys(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    real.available["EUR"] -= D(30)  # retrait/vol non annoncé : soldes < journal
    clock.advance(6_000)  # cycle suivant : soldes relus chez Bitvavo
    await eng.refresh_tickers()
    force_buy(eng, D(20))
    await eng.decide()
    b = real_buys(db)
    assert len(b) == 1 and b[0]["status"] == "VETOED"
    assert real.available["EUR"] == D(70)  # aucun ordre n'est parti chez l'échange


async def test_unavailable_balances_block_buys(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)

    async def down():
        raise ConnectionError("réseau coupé")
    real.balance = down  # type: ignore[method-assign]
    clock.advance(31_000)  # au-delà du cache de 30 s : relecture obligatoire
    await eng.refresh_tickers()
    seen = force_buy(eng, D(20))
    await eng.decide()
    assert seen == [(D(0), D(0))]
    b = real_buys(db)
    assert len(b) == 1 and b[0]["status"] == "VETOED"
    assert any("non vérifiables" in r for r in json.loads(b[0]["details"])["veto"])


def _real_entry(**kw) -> IntentCheck:
    base = dict(market="BTC-EUR", side="buy", order_type="market", amount_quote=D(300), is_entry=True,
                spread_bps=D(5), min_order_quote=D(5), available_quote=D(10_000), equity=D(10_000),
                exposure_after=D(300), position_after=D(300), mode="real")
    base.update(kw)
    return IntentCheck(**base)


def test_guardian_relative_bounds_on_authenticated_capital(tmp_path):
    """300 € < max_order_eur (500 €) et < 35 % d'une equity GONFLÉE de 10 000 € : seule la borne relative au capital
    authentifié (100 €) arrête l'ordre."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    g = eng.guardian
    g._set_state(State.REAL, "test")
    assert g.state == State.REAL
    sim = g.check(_real_entry(mode="simulation"))
    assert not any("authentifi" in r for r in sim.reasons)  # la simulation n'en dépend pas
    v = g.check(_real_entry())
    assert not v.allowed and any("capital authentifié indisponible" in r for r in v.reasons)
    now = clock.now_ms()
    v = g.check(_real_entry(authenticated_equity=D(100), authenticated_cash=D(100), authenticated_ts=now))
    assert not v.allowed
    assert {"dépasse l'EUR disponible authentifié chez Bitvavo", "dépasse la part autorisée du capital authentifié par ordre",
            "dépasse la limite par position (capital authentifié)"} <= set(v.reasons)
    ok = g.check(_real_entry(amount_quote=D(30), exposure_after=D(30), position_after=D(30), authenticated_equity=D(100),
                             authenticated_cash=D(100), authenticated_ts=now))
    assert ok.allowed, ok.reasons
    v = g.check(_real_entry(amount_quote=D(30), exposure_after=D(30), position_after=D(30), authenticated_equity=D(100),
                            authenticated_cash=D(20), authenticated_ts=now))
    assert "dépasse l'EUR disponible authentifié chez Bitvavo" in v.reasons
    stale = g.check(_real_entry(amount_quote=D(30), exposure_after=D(30), position_after=D(30),
                                authenticated_equity=D(100), authenticated_cash=D(100),
                                authenticated_ts=now - eng.limits.auth_balance_max_age_ms - 1))
    assert not stale.allowed and any("périmés" in r for r in stale.reasons)
    sell = IntentCheck("BTC-EUR", "sell", "market", D(30), False, True, spread_bps=D(5), min_order_quote=D(5),
                       mode="real", sell_amount_base=D("0.001"), base_free_for_sale=D("0.001"))
    assert g.check(sell).allowed  # vente protectrice : aucune exigence de capital authentifié


async def test_order_above_authenticated_fraction_vetoed_end_to_end(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    force_buy(eng, D(40))  # 40 % de 100 € authentifiés
    await eng.decide()
    b = real_buys(db)
    assert len(b) == 1 and b[0]["status"] == "VETOED"
    assert "dépasse la part autorisée du capital authentifié par ordre" in json.loads(b[0]["details"])["veto"]


# ====================================================================== 3. non-régression sécurité
async def test_signed_commands_nonces_and_revocation_after_separation(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    # nonce de l'activation : dans le MAGASIN du moteur, jamais dans une base lisible/écrivable par l'API
    assert "n1" in eng.store.get("used_command_nonces")
    eng.publish_display(force=True)
    assert db.kv_get("used_command_nonces") is None
    assert ReadOnlyView(eng.display.path).kv_get("used_command_nonces") is None
    # rejeu de la même signature (nouvel identifiant de commande) : refusé
    post_command(eng, "a2", clock.now_ms(), "activate_real", signed_payload("activate_real", FIELDS, clock.now_ms(), "n1"))
    await eng.process_commands()
    assert not result(db, "a2")["ok"] and any("rejeu" in r for r in result(db, "a2")["reasons"])
    # révocation : arrêt d'urgence via le canal, puis signature antérieure refusée
    stolen = signed_payload("resume_real", {"reprise": "reel"}, clock.now_ms(), "r-vol")
    clock.advance(1_000)
    post_command(eng, "s1", clock.now_ms(), "emergency_stop", {})
    await eng.process_commands()
    assert eng.guardian.state == State.PAUSE
    clock.advance(1_000)
    post_command(eng, "r1", clock.now_ms(), "resume", stolen)
    await eng.process_commands()
    assert not result(db, "r1")["ok"] and eng.guardian.state == State.PAUSE
    # l'API ne peut pas effacer la révocation : elle vit dans le magasin du moteur
    w = open_command_writer(eng.inbox.path)
    w.exec("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT, updated_ms INTEGER)")
    w.exec("INSERT INTO kv VALUES('auth_revoked_ms','{\"v\": 0}',0)")
    w.close()
    assert eng.guardian.revoked_ms >= clock.now_ms() - 1_000


async def test_ack_of_real_flow_requires_owner_signature(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    clock.advance(1_000)
    real.external_deposit("EUR", D(7))  # virement non annoncé
    assert not await eng._reconcile_real()
    assert eng.guardian.state == State.PAUSE
    post_command(eng, "k1", clock.now_ms(), "ack_flow", {"flow_id": "unk-EUR", "note": "x"})
    await eng.process_commands()
    r = result(db, "k1")
    assert not r["ok"] and r["needs_signature"] == "ack_flow"
    assert db.one("SELECT identified FROM external_flows WHERE id='unk-EUR'")["identified"] == 0
    early = signed_payload("ack_flow", {"flow_id": "unk-EUR"}, clock.now_ms(), "k-0")
    post_command(eng, "k0", clock.now_ms(), "ack_flow", {"flow_id": "unk-EUR", **early})
    await eng.process_commands()
    assert any("révocation" in x for x in result(db, "k0")["reasons"])  # la pause de réconciliation a révoqué
    clock.advance(31_000)
    wrong = signed_payload("ack_flow", {"flow_id": "autre"}, clock.now_ms(), "k-a")
    post_command(eng, "k2", clock.now_ms(), "ack_flow", {"flow_id": "unk-EUR", **wrong})
    await eng.process_commands()
    assert not result(db, "k2")["ok"]
    good = signed_payload("ack_flow", {"flow_id": "unk-EUR"}, clock.now_ms(), "k-b")
    post_command(eng, "k3", clock.now_ms(), "ack_flow", {"flow_id": "unk-EUR", **good})
    await eng.process_commands()
    assert result(db, "k3")["ok"], result(db, "k3")
    assert db.one("SELECT value_eur FROM external_flows WHERE id='unk-EUR'")["value_eur"] == "7"
    post_command(eng, "k4", clock.now_ms(), "ack_flow", {"flow_id": "unk-EUR", **good})  # rejeu
    await eng.process_commands()
    assert any("rejeu" in x for x in result(db, "k4")["reasons"])
    # comptabilité : le flux rapproché est un apport ; journal et soldes concordent de nouveau
    assert eng._ledger("real").s.net_contributions_eur == D(107)
    assert await eng._reconcile_real()
    clock.advance(31_000)
    assert (await eng.authenticated_capital()).ok


def test_sign_helper_matches_payload_format():
    p = signed_payload("ack_flow", {"flow_id": "f"}, 5, "n")
    assert p["signature"] == sign(CMD_KEY, "ack_flow", 5, "n", {"flow_id": "f"})
