"""Phase 8 — carnet WebSocket public horodaté par l'échange, branché au moteur comme garde-fou.

Vérifie : fraîcheur et latence (horodatage échange en ns), interruptions et reconnexions, données manquantes (trous de
nonce, messages invalides), synchronisation d'horloge, et la distinction entre un MARCHÉ IMMOBILE et un FLUX DÉFAILLANT.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.core.clock import FakeClock
from backend.market_data import ws_feed as W
from tests.test_phase5_separation import make

NS = 1_000_000  # ms -> ns


class Snap:
    """Faux instantané REST /{market}/book (nonce et meilleurs prix pilotables)."""

    def __init__(self, nonce=100, bid="100", ask="100.1"):
        self.nonce, self.bid, self.ask, self.calls, self.fail = nonce, bid, ask, 0, False

    async def __call__(self, market, depth):
        self.calls += 1
        if self.fail:
            raise ConnectionError("Bitvavo injoignable (simulé)")
        return {"market": market, "nonce": self.nonce, "bids": [[self.bid, "1"]], "asks": [[self.ask, "1"]]}


def feed(clock, snap=None, **cfg):
    f = W.PublicBookFeed("wss://ws.bitvavo.com/v2/", snap or Snap(), clock.now_ms, W.FeedConfig(**cfg))
    f.set_markets(["BTC-EUR", "ETH-EUR"])
    f.connected = True
    return f


def upd(market, nonce, clock, lag_ms=90, bids=(("100", "2"),), asks=()):
    return {"event": "book", "market": market, "nonce": nonce, "bids": [list(b) for b in bids],
            "asks": [list(a) for a in asks], "timestamp": (clock.now_ms() - lag_ms) * NS}


def test_exchange_timestamp_units_to_ms():
    assert W.to_ms(1_764_000_000_123 * NS) == 1_764_000_000_123
    assert W.to_ms(1_764_000_000_123_456) == 1_764_000_000_123  # µs
    assert W.to_ms(1_764_000_000_123) == 1_764_000_000_123
    assert W.to_ms("1764000000123") == 1_764_000_000_123
    for bad in (None, True, -5, "abc", 0, {}):
        assert W.to_ms(bad) == 0


async def test_freshness_and_latency_from_exchange_timestamps():
    clock = FakeClock(1_770_000_000_000)
    f = feed(clock)
    await f.handle(upd("BTC-EUR", 101, clock))   # 1er message : instantané (nonce 100), puis 101 appliqué
    assert f.markets["BTC-EUR"].book.nonce == 101 and f.markets["BTC-EUR"].book.synced
    for n in range(102, 112):
        clock.advance(500)
        await f.handle(upd("BTC-EUR", n, clock, lag_ms=90))
    assert f.exchange_delay_ms() == 90                       # latence échange -> réception, en ms (pas en ns)
    assert f.exchange_age_ms("BTC-EUR") == 90
    assert f.status("BTC-EUR") == W.OK
    f.clock_skew_ms = 40  # l'horloge de l'échange avance de 40 ms sur l'hôte (mesuré sur /v2/time)
    clock.advance(500)
    await f.handle(upd("BTC-EUR", 112, clock, lag_ms=50))
    assert f.stats.latencies_ms[-1] == 90                    # 50 ms apparents + 40 ms de décalage corrigé


async def test_nonce_gap_triggers_resync_and_counts():
    clock = FakeClock(1_770_000_000_000)
    snap = Snap(nonce=100)
    f = feed(clock, snap)
    await f.handle(upd("BTC-EUR", 101, clock))
    snap.nonce = 150                                         # l'échange a avancé : instantané plus récent
    await f.handle(upd("BTC-EUR", 105, clock))               # 102 à 104 manquants
    mf = f.markets["BTC-EUR"]
    assert mf.gaps == 1 and f.stats.gaps == 1 and snap.calls == 2
    assert mf.book.nonce == 150 and not mf.resync_pending    # message 105 plus ancien que l'instantané : ignoré
    await f.handle(upd("BTC-EUR", 151, clock))
    assert mf.book.nonce == 151 and f.status("BTC-EUR") == W.OK


async def test_failed_snapshot_keeps_market_desynchronised_then_recovers():
    clock = FakeClock(1_770_000_000_000)
    snap = Snap()
    snap.fail = True
    f = feed(clock, snap)
    await f.handle(upd("BTC-EUR", 101, clock))
    assert f.status("BTC-EUR") == W.DESYNCHRO and f.stats.resync_failures == 1
    for _ in range(100):                                      # flux soutenu pendant que l'API REST échoue
        await f.handle(upd("BTC-EUR", 101, clock))
    assert snap.calls == 1                                    # aucun martèlement de l'API REST
    snap.fail = False
    clock.advance(5_001)
    await f.handle(upd("BTC-EUR", 101, clock))
    assert f.status("BTC-EUR") == W.OK and snap.calls == 2


async def test_reconnection_forces_resync_of_every_market():
    clock = FakeClock(1_770_000_000_000)
    snap = Snap()
    f = feed(clock, snap)
    for m in ("BTC-EUR", "ETH-EUR"):
        await f.handle(upd(m, 101, clock))
    f.on_disconnect()                                         # connexion perdue : continuité des nonces rompue
    assert all(mf.resync_pending for mf in f.markets.values())
    assert f.status("BTC-EUR") == W.FLUX_COUPE                # tant que la connexion n'est pas rétablie
    f.connected = True
    f.last_msg_ms = clock.now_ms()
    assert f.status("BTC-EUR") == W.DESYNCHRO                 # reconnecté, carnet pas encore réinitialisé
    await f.handle(upd("BTC-EUR", 101, clock))
    assert f.status("BTC-EUR") == W.OK and f.status("ETH-EUR") == W.DESYNCHRO


async def test_invalid_messages_never_crash_and_are_counted():
    clock = FakeClock(1_770_000_000_000)
    f = feed(clock)
    await f.handle(upd("BTC-EUR", 101, clock))
    for bad in ({"event": "book", "market": "BTC-EUR"}, {"event": "book", "market": "BTC-EUR", "nonce": "x"},
                {"event": "book", "market": "BTC-EUR", "nonce": 102, "bids": [["pas-un-prix", "1"]]},
                {"event": "book", "market": "INCONNU-EUR", "nonce": 1}, {"event": "subscribed"}):
        await f.handle(bad)
    assert f.stats.invalid_messages >= 3
    assert f.status("BTC-EUR") in (W.OK, W.DESYNCHRO)  # jamais un carnet faux tenu pour synchronisé


async def test_immobile_market_is_not_a_failure_but_dead_feed_is():
    """Un marché calme (aucune mise à jour) alors que le flux vit (d'autres marchés bougent) est IMMOBILE, pas en
    panne ; le flux entier silencieux est COUPÉ."""
    clock = FakeClock(1_770_000_000_000)
    f = feed(clock, feed_silence_ms=15_000, active_ms=10_000)
    await f.handle(upd("BTC-EUR", 101, clock))
    await f.handle(upd("ETH-EUR", 101, clock))
    for n in range(102, 142):                                 # ETH bouge pendant 40 s, BTC ne bouge pas
        clock.advance(1_000)
        await f.handle(upd("ETH-EUR", n, clock))
    assert f.status("ETH-EUR") == W.OK
    assert f.status("BTC-EUR", D("100"), D("100.1")) == W.IMMOBILE    # REST concorde : marché calme
    assert W.IMMOBILE not in W.BLOCKING
    clock.advance(16_000)                                     # plus AUCUN message : flux coupé
    assert f.status("ETH-EUR") == W.FLUX_COUPE and f.status("BTC-EUR") == W.FLUX_COUPE


async def test_rest_ws_disagreement_becomes_incoherent_after_delay():
    clock = FakeClock(1_770_000_000_000)
    f = feed(clock, disagree_bps=D(50), disagree_ms=30_000)
    await f.handle(upd("BTC-EUR", 101, clock))
    assert f.status("BTC-EUR", D("100"), D("100.1")) == W.OK
    assert f.status("BTC-EUR", D("110"), D("110.1")) in (W.OK, W.IMMOBILE)  # désaccord bref : toléré
    for n in range(102, 140):
        clock.advance(1_000)
        await f.handle(upd("BTC-EUR", n, clock, bids=()))   # le flux vit, mais le REST reste à 110
        st = f.status("BTC-EUR", D("110"), D("110.1"))
    assert st == W.INCOHERENT and W.INCOHERENT in W.BLOCKING
    assert f.status("BTC-EUR", D("100"), D("100.1")) in (W.OK, W.IMMOBILE)  # accord retrouvé


async def test_exchange_delay_beyond_threshold_is_blocking():
    clock = FakeClock(1_770_000_000_000)
    f = feed(clock, max_exchange_delay_ms=5_000)
    await f.handle(upd("BTC-EUR", 101, clock))
    for n in range(102, 160):
        clock.advance(200)
        await f.handle(upd("BTC-EUR", n, clock, lag_ms=8_000))  # l'échange envoie des données vieilles de 8 s
    assert f.exchange_delay_ms() == 8_000 and f.status("BTC-EUR") == W.RETARD and W.RETARD in W.BLOCKING


def test_market_set_is_bounded_and_resubscribes_only_on_change():
    clock = FakeClock(1_770_000_000_000)
    f = W.PublicBookFeed("wss://x", Snap(), clock.now_ms, W.FeedConfig(max_markets=3))
    assert f.set_markets(["A-EUR", "B-EUR", "C-EUR", "D-EUR"]) and list(f.markets) == ["A-EUR", "B-EUR", "C-EUR"]
    assert not f.set_markets(["C-EUR", "B-EUR", "A-EUR"])


# ======================================================================== intégration au moteur
async def test_engine_blocks_entries_on_blocking_ws_status_only(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    f = W.PublicBookFeed("wss://ws.bitvavo.com/v2/", Snap(), clock.now_ms)
    eng.ws_feed = f
    eng.update_ws_markets(force=True)
    assert "BTC-EUR" in f.markets
    f.connected, f.last_msg_ms = True, clock.now_ms()
    await eng.decide()
    row = {r["market"]: r for r in db.kv_get("scanner")["rows"]}["BTC-EUR"]
    assert any("flux WebSocket : desynchro" in x for x in row["reasons"]) and not row["eligible"]
    f.connected = False                                       # flux coupé : PAS bloquant (repli sur le REST)
    await eng.decide()
    row = {r["market"]: r for r in db.kv_get("scanner")["rows"]}["BTC-EUR"]
    assert not any("flux WebSocket" in x for x in row["reasons"])


async def test_engine_alerts_once_when_ws_feed_down_and_publishes_summary(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    f = W.PublicBookFeed("wss://ws.bitvavo.com/v2/", Snap(), clock.now_ms)
    f.set_markets(["BTC-EUR"])
    eng.ws_feed = f
    for _ in range(30):                                       # 3 min de flux coupé
        eng.check_ws_feed()
        clock.advance(6_000)
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='incident'")["n"] == 1
    eng.heartbeat(data_ok=True)
    s = db.kv_get("engine_heartbeat")["flux_ws"]
    assert s["marches"] == 1 and s["vivant"] is False and s["statuts"] == {"flux_coupe": 1}


async def test_clock_skew_is_propagated_to_ws_feed(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, _ = make(tmp_path, clock)
    eng.ws_feed = W.PublicBookFeed("wss://ws.bitvavo.com/v2/", Snap(), clock.now_ms)

    async def server_time():
        return clock.now_ms() + 1_234
    eng.rest.time = server_time  # type: ignore[attr-defined]
    await eng.measure_clock_skew()
    assert eng.ws_feed.clock_skew_ms == eng.clock_skew_ms == 1_234


def test_feed_enabled_only_against_official_endpoints():
    src = open("backend/runner.py").read()
    assert 'os.environ.get("NELYQOR_WS_FEED", "1") != "0" and eng.official_endpoints()' in src


@pytest.mark.parametrize("status", [W.OK, W.IMMOBILE, W.FLUX_COUPE])
def test_non_blocking_statuses(status):
    assert status not in W.BLOCKING
