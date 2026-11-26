"""Phase 6 — plafonds et robustesse, en SIMULATION et sur le chemin RÉEL simulé (faux échanges uniquement).

1. Plafond par position, exposition cumulée, nombre d'ordres d'achat simultanés : le Guardian, l'exécuteur
   (contrôle atomique dans la transaction de soumission) et le moteur complet (cycle de décision).
   Défaut corrigé : un achat LIMITE en attente (non rempli) n'était compté ni dans l'exposition ni dans la position
   de son marché ; d'un cycle à l'autre, le décideur pouvait empiler des ordres en attente au-delà des plafonds.
   Défaut corrigé : aucun plafond sur le nombre d'ordres d'achat simultanés (`max_active_entry_orders`).
2. Redémarrage du moteur : état reconstruit depuis engine.db (positions, ordres en attente) et le magasin d'état
   (état du Guardian), plafonds toujours appliqués après redémarrage.
3. Interruptions réseau : échange qui lève des exceptions, données PÉRIMÉES. Défaut corrigé : une réponse reçue
   à l'instant mais horodatée d'il y a longtemps par l'échange était considérée comme fraîche.
"""
from __future__ import annotations

import dataclasses
import json
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest

from backend.core.clock import FakeClock
from backend.core.config import HardLimits
from backend.execution.engine import ExecutionEngine, OrderRequest
from backend.execution.simulator import SimulatedExchange
from backend.guardian.guardian import Guardian, IntentCheck, State
from backend.market_data.models import MarketInfo
from backend.market_data.universe import MarketState, check_eligibility, data_age_ms
from backend.portfolio.decision import Proposal
from backend.runner import Engine
from backend.storage.db import Database
from tests.conftest import BTC_RAW, STATE_KEY
from tests.fake_bitvavo import FakePublic
from tests.test_phase5_separation import make, real_buys, real_ready, settings

ETH_RAW = {**BTC_RAW, "market": "ETH-EUR", "base": "ETH"}
SOL_RAW = {**BTC_RAW, "market": "SOL-EUR", "base": "SOL"}
MARKETS = [MarketInfo.from_bitvavo(r) for r in (BTC_RAW, ETH_RAW, SOL_RAW)]
PX = {"BTC-EUR": D(50000), "ETH-EUR": D(3000), "SOL-EUR": D(150)}


# ---------------------------------------------------------------------------------------------- outils
def exchange(clock: FakeClock, eur: D = D(100), cls: type = SimulatedExchange) -> SimulatedExchange:
    ex = cls(MARKETS, {"EUR": eur}, now_ms=clock.now_ms)
    for mk, p in PX.items():
        ex.set_book(mk, [(p * D("0.999"), D(100))], [(p * D("1.001"), D(100))])
    return ex


def executor(tmp_path: Path, clock: FakeClock, mode: str = "simulation", limits: HardLimits | None = None,
             ex: SimulatedExchange | None = None) -> tuple[ExecutionEngine, Guardian, Database]:
    db = Database(tmp_path / f"{mode}.db")
    g = Guardian(db, limits or HardLimits.load(), real_server_flag=(mode == "real"), role="engine",
                 now_ms=clock.now_ms, state_key=STATE_KEY)
    g.init_state()
    if mode == "real":
        g._put("guardian_state", State.REAL.value)
    e = ExecutionEngine(db, ex or exchange(clock), g, {m.market: m for m in MARKETS}, mode, 42, clock.now_ms)
    return e, g, db


def entry(market: str, amount: D, mode: str, clock: FakeClock, equity: D = D(100), exposure_after: D | None = None,
          position_after: D | None = None) -> IntentCheck:
    """Contrôle d'entrée tel que le construit le moteur : position et exposition DÉTENUES (journal) + cet ordre."""
    chk = IntentCheck(market, "buy", "limit", amount, True, spread_bps=D(5), min_order_quote=D(5),
                      available_quote=equity, equity=equity,
                      exposure_after=amount if exposure_after is None else exposure_after,
                      position_after=amount if position_after is None else position_after, mode=mode)
    if mode == "real":
        chk.authenticated_equity, chk.authenticated_cash, chk.authenticated_ts = equity, equity, clock.now_ms()
    return chk


def pending_limit(market: str, amount: D, chk: IntentCheck) -> OrderRequest:
    """Achat LIMITE post-only sous le meilleur bid : reste EN ATTENTE (non rempli)."""
    price = PX[market] * D("0.99")
    return OrderRequest(market, "buy", "limit", amount=amount / price, price=price, post_only=True,
                        strategy="test_phase6", check=chk, purpose="entree")


def veto(db: Database, intent_id: str) -> list[str]:
    return json.loads(db.one("SELECT details FROM intents WHERE id=?", (intent_id,))["details"])["veto"]


# ======================================================================== 1. Guardian : bornes immuables
@pytest.mark.parametrize("mode", ["simulation", "real"])
def test_guardian_caps_position_exposure_and_simultaneous_orders(tmp_path, mode):
    clock = FakeClock(1_770_000_000_000)
    _, g, _ = executor(tmp_path, clock, mode)
    L = g.limits
    assert L.max_position_fraction == D("0.35") and L.max_total_exposure_fraction == D("0.90")
    assert L.max_active_entry_orders == 3
    assert g.check(entry("BTC-EUR", D(30), mode, clock, position_after=D(35))).allowed  # exactement au plafond
    v = g.check(entry("BTC-EUR", D(30), mode, clock, position_after=D("35.01")))
    assert not v.allowed and "dépasse la limite par position" in v.reasons
    v = g.check(entry("BTC-EUR", D(30), mode, clock, exposure_after=D("90.01")))
    assert not v.allowed and "dépasse l'exposition totale" in v.reasons
    if mode == "real":
        assert "dépasse l'exposition totale (capital authentifié)" in v.reasons
    chk = entry("BTC-EUR", D(10), mode, clock)
    chk.active_entry_orders = 3
    v = g.check(chk)
    assert not v.allowed and any("ordres d'achat simultanés" in r for r in v.reasons)
    chk.active_entry_orders = 2
    assert g.check(chk).allowed
    # le plafond d'ordres simultanés ne bloque JAMAIS une vente de protection
    sell = IntentCheck("BTC-EUR", "sell", "stopLoss", D(30), False, True, spread_bps=D(5), min_order_quote=D(5),
                       mode=mode, sell_amount_base=D("0.001"), base_free_for_sale=D("0.001"), active_entry_orders=99)
    assert g.check(sell).allowed


def test_hard_limit_on_simultaneous_orders_is_bounded():
    L = HardLimits.load()
    with pytest.raises(ValueError):
        dataclasses.replace(L, max_active_entry_orders=0)
    with pytest.raises(ValueError):
        dataclasses.replace(L, max_active_entry_orders=21)
    raw = json.loads((Path(__file__).resolve().parents[1] / "config" / "hard_limits.json").read_text())
    assert raw["max_active_entry_orders"] == 3


# ======================================================================== 2. exécuteur : ordres EN ATTENTE
@pytest.mark.parametrize("mode", ["simulation", "real"])
async def test_pending_buy_counts_in_position_cap(tmp_path, mode):
    """Non-régression : un 2e achat limite sur le même marché, alors que le 1er est encore en attente, dépassait
    le plafond par position (la position détenue au journal était nulle)."""
    clock = FakeClock(1_770_000_000_000)
    e, _, db = executor(tmp_path, clock, mode)
    r1 = await e.submit(pending_limit("BTC-EUR", D(20), entry("BTC-EUR", D(20), mode, clock)), D(100))
    assert r1.accepted and r1.status == "new"  # en attente, aucun fill
    assert e.pending_buy_quote() > D(20)
    r2 = await e.submit(pending_limit("BTC-EUR", D(20), entry("BTC-EUR", D(20), mode, clock)), D(100))
    assert not r2.accepted and "dépasse la limite par position" in veto(db, r2.intent_id)
    # un autre marché reste possible (position distincte)
    r3 = await e.submit(pending_limit("ETH-EUR", D(20), entry("ETH-EUR", D(20), mode, clock)), D(100))
    assert r3.accepted, r3.reasons


@pytest.mark.parametrize("mode", ["simulation", "real"])
async def test_pending_buys_count_in_total_exposure(tmp_path, mode):
    clock = FakeClock(1_770_000_000_000)
    e, _, db = executor(tmp_path, clock, mode, ex=exchange(clock, D(200)))
    for mk in ("BTC-EUR", "ETH-EUR"):
        r = await e.submit(pending_limit(mk, D(34), entry(mk, D(34), mode, clock)), D(100))
        assert r.accepted, r.reasons
    # 34 + 34 (+ frais réservés) en attente + 30 > 90 % de 100 €
    r = await e.submit(pending_limit("SOL-EUR", D(30), entry("SOL-EUR", D(30), mode, clock)), D(100))
    assert not r.accepted
    reasons = veto(db, r.intent_id)
    assert "dépasse l'exposition totale" in reasons
    if mode == "real":
        assert "dépasse l'exposition totale (capital authentifié)" in reasons
    # une fois l'ordre en attente annulé, sa réservation ne compte plus
    first = db.one("SELECT id FROM intents WHERE market='BTC-EUR' AND status='new'")["id"]
    await e.cancel(first)
    r = await e.submit(pending_limit("SOL-EUR", D(30), entry("SOL-EUR", D(30), mode, clock)), D(100))
    assert r.accepted, r.reasons


@pytest.mark.parametrize("mode", ["simulation", "real"])
async def test_simultaneous_entry_orders_capped(tmp_path, mode):
    clock = FakeClock(1_770_000_000_000)
    limits = dataclasses.replace(HardLimits.load(), max_active_entry_orders=2)
    e, _, db = executor(tmp_path, clock, mode, limits)
    for mk in ("BTC-EUR", "ETH-EUR"):
        assert (await e.submit(pending_limit(mk, D(10), entry(mk, D(10), mode, clock)), D(100))).accepted
    r = await e.submit(pending_limit("SOL-EUR", D(10), entry("SOL-EUR", D(10), mode, clock)), D(100))
    assert not r.accepted and any("ordres d'achat simultanés (2 actifs, max 2)" in x for x in veto(db, r.intent_id))
    # un ordre au marché REMPLI ne reste pas actif : il ne consomme pas le quota
    await e.cancel(db.one("SELECT id FROM intents WHERE market='ETH-EUR'")["id"])
    chk = entry("SOL-EUR", D(10), mode, clock)
    chk.order_type = "market"
    m = await e.submit(OrderRequest("SOL-EUR", "buy", "market", amount_quote=D(10), check=chk, purpose="entree"), D(100))
    assert m.accepted and m.status == "filled"
    r = await e.submit(pending_limit("ETH-EUR", D(10), entry("ETH-EUR", D(10), mode, clock)), D(100))
    assert r.accepted, r.reasons


# ======================================================================== 3. moteur complet (cycle de décision)
def propose(eng: Engine, amount: D, order: str = "limit") -> None:
    """Le décideur propose un achat sur chaque marché éligible (limite post-only sous le bid : en attente)."""
    def fake_entry(m, st, df, equity, available, exposure, held, depth, held_returns=None):
        if order == "limit":
            px = (st.best_bid or D(1)) * D("0.99")
            return [Proposal(m.market, "acheter", "test_phase6", amount_quote=amount, order_type="limit",
                             limit_price=m.round_price(px), post_only=True)]
        return [Proposal(m.market, "acheter", "test_phase6", amount_quote=amount)]
    eng.decider.evaluate_entry = fake_entry  # type: ignore[method-assign]


async def test_engine_does_not_stack_pending_entries_across_cycles_simulation(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    propose(eng, D(20))
    await eng.decide()
    first = [dict(r) for r in db.q("SELECT market, status FROM intents WHERE mode='simulation' AND side='buy'")]
    assert sorted(r["market"] for r in first if r["status"] == "new") == ["BTC-EUR", "ETH-EUR"]
    for _ in range(3):  # cycles suivants : les ordres sont toujours en attente
        clock.advance(61_000)
        await eng.refresh_tickers()
        await eng.decide()
    rows = [dict(r) for r in db.q("SELECT market, status, details FROM intents WHERE mode='simulation' AND side='buy'")]
    active = [r for r in rows if r["status"] == "new"]
    assert len(active) == 2  # aucun empilement au-delà du plafond par position
    vetoed = [r for r in rows if r["status"] == "VETOED"]
    assert vetoed and all("dépasse la limite par position" in json.loads(r["details"])["veto"] for r in vetoed)
    committed = eng._exposure("simulation", eng.prices_eur()) + eng.paper_exec.pending_buy_quote()
    assert committed <= D(100) * eng.limits.max_total_exposure_fraction


async def test_engine_real_path_caps_pending_entries(tmp_path):
    eng, db, real, clock = await real_ready(tmp_path)
    propose(eng, D(25))
    await eng.decide()
    clock.advance(61_000)
    await eng.refresh_tickers()
    await eng.decide()
    b = [x for x in real_buys(db) if x["market"] == "BTC-EUR"]  # compte réel simulé : BTC-EUR seul
    assert [x["status"] for x in b] == ["new", "VETOED"]
    reasons = json.loads(b[1]["details"])["veto"]
    assert "dépasse la limite par position (capital authentifié)" in reasons


async def test_engine_market_buys_within_one_cycle_respect_exposure(tmp_path):
    """Achats au marché REMPLIS dans le même cycle : l'exposition est recalculée depuis le journal."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    eng.limits = dataclasses.replace(eng.limits, max_total_exposure_fraction=D("0.50"))
    eng.guardian.limits = eng.limits
    propose(eng, D(30), order="market")
    await eng.decide()
    rows = [dict(r) for r in db.q("SELECT market, status, details FROM intents WHERE mode='simulation' AND side='buy' "
                                  "ORDER BY ts, rowid")]
    assert [r["status"] for r in rows] == ["filled", "VETOED"]
    assert "dépasse l'exposition totale" in json.loads(rows[1]["details"])["veto"]


# ======================================================================== 4. redémarrages du moteur
async def test_restart_rebuilds_positions_pending_orders_and_guardian_state(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    propose(eng, D(20))
    await eng.decide()
    assert eng.paper_exec.pending_buy_quote() > 0
    eng.guardian.pause("owner", "test redémarrage")
    db.close()
    # redémarrage : nouvelle instance, même engine.db et même magasin d'état /state
    clock.advance(10_000)
    s = settings(tmp_path)
    db2 = Database(s.db_path)
    eng2 = Engine(s, db2, FakePublic(clock), clock, state_key=STATE_KEY,
                  store_path=tmp_path / "state" / "etat_moteur.json")
    eng2.start()
    assert eng2.guardian.state == State.PAUSE and eng2.guardian.verify_integrity()
    await eng2.step()
    # simulation : les ordres papier ne survivent pas au redémarrage du simulateur ; le journal est COHÉRENT
    # (annulés, réservations libérées), jamais des réservations fantômes ni des ordres doublés
    assert eng2.paper_exec.pending_buy_quote() == 0
    rows = db2.q("SELECT status, reserved_quote, last_error FROM intents WHERE mode='simulation' AND side='buy'")
    assert len(rows) == 2 and all(r["status"] == "canceled" and r["reserved_quote"] == "0" for r in rows)
    assert eng2.paper.available["EUR"] == D(100)  # soldes papier rejoués depuis le journal
    assert eng2.guardian.state == State.PAUSE  # toujours en pause après le premier cycle
    # après reprise, les plafonds s'appliquent de nouveau aux nouveaux ordres en attente
    eng2.guardian.resume("owner", await eng2.preflight(for_real=False))
    assert eng2.guardian.state == State.SIMULATION
    propose(eng2, D(20))
    for _ in range(2):
        clock.advance(61_000)
        await eng2.refresh_tickers()
        await eng2.decide()
    active = db2.q("SELECT id FROM intents WHERE mode='simulation' AND side='buy' AND status='new'")
    assert len(active) == 2


async def test_restart_without_state_store_goes_to_incident_not_simulation(tmp_path):
    """Magasin d'état perdu (volume /state absent) alors que la base dit PAUSE : jamais un retour silencieux."""
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    eng.guardian.emergency_stop("ssh")
    db.close()
    (tmp_path / "state" / "etat_moteur.json").unlink()
    s = settings(tmp_path)
    eng2 = Engine(s, Database(s.db_path), FakePublic(clock), clock, state_key=STATE_KEY,
                  store_path=tmp_path / "state" / "etat_moteur.json")
    eng2.start()
    assert eng2.guardian.state != State.SIMULATION
    assert not eng2.guardian.check(IntentCheck("BTC-EUR", "buy", "market", D(10), True, spread_bps=D(5),
                                               min_order_quote=D(5), available_quote=D(100),
                                               equity=D(100))).allowed


# ======================================================================== 5. interruptions réseau
async def test_exchange_exceptions_never_lead_to_buys_and_engine_recovers(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    propose(eng, D(20), order="market")
    eng.rest.fail = True
    for _ in range(3):
        clock.advance(61_000)
        with pytest.raises(httpx.ConnectError):
            await eng.step()
    assert db.one("SELECT COUNT(*) n FROM intents WHERE side='buy'")["n"] == 0
    hb = db.kv_get("engine_heartbeat")
    assert hb["data_ok"] is False
    # décision forcée pendant la coupure : données périmées => veto, aucun ordre envoyé
    await eng.decide()
    rows = db.q("SELECT status, details FROM intents WHERE side='buy'")
    assert all(r["status"] == "VETOED" or r["status"] is None for r in rows)
    assert all(any("périmée" in x for x in json.loads(r["details"])["veto"]) for r in rows)
    sc = {r["market"]: r for r in db.kv_get("scanner")["rows"]}
    assert "donnée périmée" in sc["BTC-EUR"]["reasons"]
    # retour du réseau : reprise propre
    eng.rest.fail = False
    clock.advance(61_000)
    await eng.step()
    assert db.kv_get("engine_heartbeat")["data_ok"] is True
    assert db.one("SELECT COUNT(*) n FROM intents WHERE side='buy' AND status='filled'")["n"] >= 1


class StalePublic(FakePublic):
    """Échange qui répond, mais avec un carnet horodaté d'il y a `lag_ms` (cache figé, flux bloqué)."""

    lag_ms = 3_600_000

    async def ticker_book(self):
        rows = await super().ticker_book()
        return [{**r, "timestamp": self.clock.now_ms() - self.lag_ms} for r in rows]


def test_data_age_uses_oldest_of_reception_and_exchange_timestamp():
    st = MarketState(MarketInfo.from_bitvavo(BTC_RAW), best_bid=D(100), best_ask=D(100.1),
                     quote_volume_24h=D(10**7), ts_received_ms=1_000_000, ts_market_ms=1_000_000 - 3_600_000)
    assert data_age_ms(st, 1_000_000) == 3_600_000
    el = check_eligibility(st, HardLimits.load(), 1_000_000)
    assert not el.eligible and "donnée périmée" in el.reasons
    st.ts_market_ms = 0  # échange sans horodatage : heure de réception seule
    assert data_age_ms(st, 1_000_000) == 0 and check_eligibility(st, HardLimits.load(), 1_000_000).eligible


async def test_stale_exchange_timestamps_block_buys(tmp_path):
    """Non-régression : réponse reçue à l'instant mais horodatée d'il y a 1 h => aucun achat (avant : achat)."""
    clock = FakeClock(1_770_000_000_000)
    s = settings(tmp_path)
    db = Database(s.db_path)
    eng = Engine(s, db, StalePublic(clock), clock, state_key=STATE_KEY,
                 store_path=tmp_path / "state" / "etat_moteur.json")
    eng.start()
    await eng.step()
    propose(eng, D(20), order="market")
    await eng.decide()
    assert db.one("SELECT COUNT(*) n FROM intents WHERE side='buy'")["n"] == 0  # marchés inéligibles
    sc = {r["market"]: r for r in db.kv_get("scanner")["rows"]}
    assert "donnée périmée" in sc["BTC-EUR"]["reasons"]
    assert (await eng.preflight(for_real=False))["donnees_marche_fraiches"] is False
    # contrôle du Guardian sur un achat forcé : veto « donnée périmée »
    st = eng.states["BTC-EUR"]
    r = await eng.paper_exec.submit(OrderRequest("BTC-EUR", "buy", "market", amount_quote=D(20),
                                                 check=eng._check(st, "buy", D(20), True, D(100), D(100), D(20),
                                                                  "simulation")), D(100))
    assert not r.accepted and any("donnée périmée" in x for x in r.reasons)
    # flux rétabli : achats de nouveau possibles
    eng.rest.lag_ms = 0
    clock.advance(61_000)
    await eng.step()  # bougies chargées dès que les marchés redeviennent éligibles
    assert db.one("SELECT COUNT(*) n FROM intents WHERE side='buy' AND status='filled'")["n"] >= 1


async def test_real_path_exchange_failures_block_buys_but_engine_keeps_running(tmp_path):
    """Chemin réel simulé : soldes illisibles (exception) ou ticker en panne => aucun achat réel ; sorties et
    protections toujours possibles ; reprise quand l'échange répond de nouveau."""
    eng, db, real, clock = await real_ready(tmp_path)
    propose(eng, D(20), order="market")

    async def broken_balance():
        raise httpx.ReadTimeout("soldes : délai dépassé (simulé)")
    ok_balance = real.balance
    real.balance = broken_balance  # type: ignore[method-assign]
    clock.advance(200_000)  # cache des soldes expiré
    await eng.refresh_tickers()
    await eng.decide()
    assert all(b["status"] == "VETOED" for b in real_buys(db))
    eng.rest.fail = True
    clock.advance(61_000)
    with pytest.raises(httpx.ConnectError):
        await eng.step()
    assert eng.guardian.state == State.REAL  # pas de plantage ni d'incident : seules les entrées sont bloquées
    real.balance = ok_balance  # type: ignore[method-assign]
    eng.rest.fail = False
    clock.advance(61_000)
    await eng.step()
    assert any(b["status"] == "filled" for b in real_buys(db))


async def test_real_restart_keeps_pending_orders_and_caps(tmp_path):
    """Chemin réel simulé : l'ordre réel en attente survit au redémarrage (il est chez l'échange) ; le moteur
    repasse par la réconciliation, puis le plafond par position compte toujours cet ordre."""
    from tests.test_phase5_separation import CMD_KEY

    eng, db, real, clock = await real_ready(tmp_path)
    propose(eng, D(25))
    await eng.decide()
    assert [b["status"] for b in real_buys(db) if b["market"] == "BTC-EUR"] == ["new"]
    pending = eng.real_exec.pending_buy_quote()
    db.close()
    clock.advance(10_000)
    s = settings(tmp_path)
    db2 = Database(s.db_path)
    eng2 = Engine(s, db2, FakePublic(clock), clock, state_key=STATE_KEY, command_key=CMD_KEY,
                  store_path=tmp_path / "state" / "etat_moteur.json")
    eng2.attach_real_exchange(real)
    eng2.start()
    assert eng2.guardian.state == State.RECONCILIATION  # jamais d'entrée réelle directe après redémarrage
    await eng2.step()
    assert eng2.real_exec is not None and eng2.real_exec.pending_buy_quote() == pending
    assert len(await real.open_orders()) == 1  # aucun ordre perdu ni doublé chez l'échange
    # réconciliation réussie (journal = soldes) : retour au réel, et le plafond compte l'ordre repris
    assert eng2.guardian.state == State.REAL, eng2.guardian.state
    propose(eng2, D(25))
    clock.advance(61_000)
    await eng2.refresh_tickers()
    await eng2.decide()
    b = [x for x in real_buys(db2) if x["market"] == "BTC-EUR"]
    assert [x["status"] for x in b] == ["new", "VETOED"]
    assert "dépasse la limite par position (capital authentifié)" in json.loads(b[1]["details"])["veto"]


# ======================================================================== 6. déploiement réel (scripts/deploy_check.sh)
def _compose() -> dict:
    import yaml

    return yaml.safe_load((Path(__file__).resolve().parents[1] / "compose.yaml").read_text())


def test_unreadable_secret_fails_with_explicit_message(tmp_path, monkeypatch):
    """Défaut trouvé en conditions réelles : secrets en 600 appartenant à root => moteur et API en boucle de
    redémarrage sur une PermissionError brute. Désormais : message explicite avec la correction (chown 10001)."""
    from backend.core.config import Settings

    (tmp_path / "engine_state_key").write_text("x")
    (tmp_path / "owner_password_hash").write_text("x")

    def denied(self, *a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "read_text", denied)
    for role, name in (("engine", "engine_state_key"), ("api", "owner_password_hash")):
        with pytest.raises(PermissionError, match=rf"secret {name} illisible.*chown 10001:10001"):
            Settings(db_path=tmp_path / "e.db", role=role, secrets_dir=tmp_path).read_secret(name)


def test_api_reads_password_hash_through_read_secret(tmp_path):
    from backend.api.app import create_app
    from backend.core.config import Settings

    s = Settings(db_path=tmp_path / "e.db", role="api", secrets_dir=tmp_path)
    assert create_app(s).state.db is not None  # aucun hash : démarre (connexion impossible), pas d'exception
    (tmp_path / "owner_password_hash").write_text("")
    create_app(Settings(db_path=tmp_path / "e2.db", role="api", secrets_dir=tmp_path))


def test_compose_phase6_hardening_and_health():
    c = _compose()["services"]
    hc = c["api"]["healthcheck"]["test"]
    assert "/api/health" in " ".join(hc)
    cad = c["caddy"]
    assert cad["read_only"] is True and cad["cap_drop"] == ["ALL"] and cad["cap_add"] == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in cad["security_opt"]
    assert cad["depends_on"] == {"api": {"condition": "service_healthy"}}
    docs = (Path(__file__).resolve().parents[1] / "docs" / "DEPLOIEMENT.md").read_text()
    assert "chown 10001:10001" in docs


def test_deploy_check_script_restores_service_aliases_and_is_simulation_only():
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "deploy_check.sh").read_text()
    # sans l'alias, le nom de service « api » n'est plus résolu par Caddy après reconnexion (faux échec)
    assert 'network connect --alias api' in sh and 'network connect --alias engine' in sh
    assert "export NELYQOR_REAL_TRADING_ALLOWED=false" in sh
    assert ': > "$WORK/secrets/bitvavo_api_key"' in sh  # aucune clé Bitvavo
    assert 'PROJ=${DEPLOY_CHECK_PROJECT:-nelyqor-check}' in sh  # jamais les volumes de production
