"""Banc de performances Bitvavo (scripts/bench_bitvavo.py), HORS LIGNE : percentiles, limites d'API (arrêt sur 429,
pause sur la réserve, plafond propre), WebSocket (reconnexion forcée, ruptures de nonce, erreur 105), balayage du
moteur réel en simulation sur un faux Bitvavo, format de sortie."""
import asyncio
import json
import time

import httpx
import pytest

from backend.core.clock import Clock
from backend.market_data.models import MarketInfo
from backend.market_data.universe import MarketState
from scripts import bench_bitvavo as bb

from .conftest import BTC_RAW
from .fake_bitvavo import FakePublic


# ------------------------------------------------------------------ percentiles
def test_percentile_matches_numpy_linear():
    import numpy as np

    vals = [5.0, 1.0, 9.0, 3.0, 7.0, 2.0, 8.0, 4.0, 6.0, 10.0, 100.0]
    s = sorted(vals)
    for q in (0, 10, 50, 90, 99, 100):
        assert bb.percentile(s, q) == pytest.approx(float(np.percentile(vals, q)))


def test_summarize_edge_cases():
    assert bb.summarize([])["n"] == 0 and bb.summarize([])["mediane"] is None
    one = bb.summarize([42.0])
    assert one["n"] == 1 and one["mediane"] == one["p99"] == one["min"] == one["max"] == 42.0
    s = bb.summarize(list(range(1, 101)))
    assert s["mediane"] == 50.5 and s["p90"] == pytest.approx(90.1) and s["p99"] == pytest.approx(99.01)
    with pytest.raises(ValueError):
        bb.percentile([], 50)
    with pytest.raises(ValueError):
        bb.percentile([1.0], 101)


# ------------------------------------------------------------------ limites d'API
class FakeTime:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def mono(self):
        return self.t

    def wall_ms(self):
        return int(self.t * 1000)

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_guard_halts_on_429_and_refuses_further_requests():
    g = bb.RateGuard()
    g.observe(429, {"bitvavo-ratelimit-remaining": "0"}, {"errorCode": 110, "error": "x"})
    assert g.halted and "429" in g.halted
    with pytest.raises(bb.Halt):
        asyncio.run(g.acquire(1))


def test_guard_halts_on_ban_error_105():
    g = bb.RateGuard()
    g.observe(403, {}, {"errorCode": 105, "error": "banned until 123"})
    assert g.halted and "105" in g.halted


def test_guard_waits_until_resetat_when_reserve_reached():
    ft = FakeTime()
    g = bb.RateGuard(cap_per_min=1000, reserve=100, mono=ft.mono, wall_ms=ft.wall_ms, sleep=ft.sleep)
    g.observe(200, {"bitvavo-ratelimit-remaining": "120", "bitvavo-ratelimit-resetat": str(ft.wall_ms() + 7000),
                    "bitvavo-ratelimit-limit": "1000"}, [])
    asyncio.run(g.acquire(25))  # 120 - 25 < 100 : attendre la fin de la fenêtre de l'échange
    assert ft.slept and ft.slept[0] == pytest.approx(7.25)
    assert g.remaining is None and g.pauses == 1 and g.limit == 1000


def test_guard_own_cap_sliding_window():
    ft = FakeTime()
    g = bb.RateGuard(cap_per_min=50, reserve=0, mono=ft.mono, wall_ms=ft.wall_ms, sleep=ft.sleep)

    async def go():
        for _ in range(2):
            await g.acquire(25)
        assert not ft.slept
        await g.acquire(25)  # 75 > 50 : attendre que la première requête sorte de la fenêtre de 60 s

    asyncio.run(go())
    assert ft.slept[0] == pytest.approx(60.05)


def test_guard_deadline_raises_budget_exhausted():
    ft = FakeTime()
    g = bb.RateGuard(cap_per_min=10, reserve=0, mono=ft.mono, wall_ms=ft.wall_ms, sleep=ft.sleep)
    asyncio.run(g.acquire(10))
    with pytest.raises(bb.BudgetEpuise):
        asyncio.run(g.acquire(5, deadline=ft.t + 5))
    assert not ft.slept


def test_probe_stops_on_first_429_without_retry():
    calls = []

    def h(req):
        calls.append(req.url.path)
        if len(calls) == 2:
            return httpx.Response(429, json={"errorCode": 110, "error": "rate"})
        return httpx.Response(200, json={"time": 1}, headers={"bitvavo-ratelimit-remaining": str(1000 - len(calls)),
                                                              "bitvavo-ratelimit-resetat": "9999999999999"})

    async def go():
        g = bb.RateGuard()
        p = bb.RestProbe(g, "https://x/v2", httpx.MockTransport(h))
        pub = bb.GuardedPublic(p)
        await pub.time()
        with pytest.raises(bb.Halt):
            await pub.time()
        with pytest.raises(bb.Halt):
            await pub.time()  # plus aucune requête envoyée
        rep = p.report()
        await p.aclose()
        return rep, g

    rep, g = asyncio.run(go())
    assert len(calls) == 2
    assert rep["points"]["/time"]["statuts"] == {"200": 1, "429": 1}
    assert rep["points"]["/time"]["latence_ms"]["n"] == 1  # seule la réponse 200 est une latence valide
    assert "bitvavo-ratelimit-remaining" in rep["entetes_limite_vus"]
    assert g.report()["arret"]


def test_probe_measures_weight_from_headers():
    rem = iter([975, 950, 949])

    def h(req):
        return httpx.Response(200, json=[], headers={"bitvavo-ratelimit-remaining": str(next(rem)),
                                                     "bitvavo-ratelimit-resetat": "9999999999999"})

    async def go():
        p = bb.RestProbe(bb.RateGuard(reserve=0), "https://x/v2", httpx.MockTransport(h))
        pub = bb.GuardedPublic(p)
        await pub.ticker_24h()
        await pub.ticker_24h()
        await pub.markets()
        rep = p.report()
        await p.aclose()
        return rep

    rep = asyncio.run(go())
    assert rep["points"]["/ticker/24h"]["poids_observe"]["mediane"] == 25.0  # 975 -> 950
    assert rep["points"]["/markets"]["poids_observe"]["mediane"] == 1.0


# ------------------------------------------------------------------ WebSocket
class FakeWS:
    """Socket scripté : un accusé de souscription puis des mises à jour de carnet (nonce +1, avec trous)."""

    def __init__(self, server, conn_no):
        self.server, self.conn_no = server, conn_no
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.closed = True

    async def send(self, m):
        self.sent.append(json.loads(m))
        self.server.queue.append({"event": "subscribed", "subscriptions": {}})

    async def close(self):
        self.closed = True
        self.server.nonce += 5  # 5 mises à jour publiées par l'échange pendant la coupure

    async def recv(self):
        await asyncio.sleep(0.005)
        if self.server.queue:
            return json.dumps(self.server.queue.pop(0))
        if self.server.fail_at and self.server.sent_msgs == self.server.fail_at:
            self.server.fail_at = None
            raise ConnectionError("coupure réseau simulée")
        self.server.sent_msgs += 1
        now = int(time.time() * 1000)
        if self.server.sent_msgs % 4 == 0:
            return json.dumps({"event": "trade", "market": "BTC-EUR", "timestamp": now - 30, "price": "1"})
        self.server.nonce += 1
        if self.server.sent_msgs == 10:
            self.server.nonce += 3  # rupture en continu : 3 mises à jour manquantes
        return json.dumps({"event": "book", "market": "BTC-EUR", "nonce": self.server.nonce, "bids": [], "asks": []})


class FakeServer:
    def __init__(self, fail_at=None):
        self.queue, self.nonce, self.sent_msgs, self.conns, self.fail_at = [], 100, 0, 0, fail_at

    def connect(self, url):
        assert url.startswith("wss://ws.bitvavo.com/")
        self.conns += 1
        return FakeWS(self, self.conns)


def test_ws_forced_reconnections_and_missing_messages():
    srv = FakeServer()
    ws = bb.WsProbe(bb.WS_URL, ["BTC-EUR"], connect=srv.connect)
    asyncio.run(ws.run(duration_s=1.0, reconnect_every_s=0.25, max_forced=2))
    rep = ws.report()
    assert rep["reconnexions_forcees"] == 2 and rep["connexions"] == 3 and rep["coupures_non_planifiees"] == 0
    full = [r for r in rep["reprises"] if not r.get("incomplete")]
    assert len(full) == 2
    for r in full:
        assert r["connexion_ms"] is not None and r["souscription_ms"] is not None
        assert r["premiere_donnee_ms"] >= r["souscription_ms"] >= r["connexion_ms"] >= 0
    assert rep["reprise_premiere_donnee_ms"]["n"] == 2
    assert rep["carnet"]["mises_a_jour_manquantes_pendant_reconnexions"] == 10  # 2 coupures x 5
    assert rep["carnet"]["ruptures_nonce_en_continu"] == 1
    assert rep["carnet"]["mises_a_jour_manquantes_en_continu"] == 3
    d = rep["delai_echange_reception_ms"]["trade"]
    assert d["n"] > 0 and 20 <= d["mediane"] < 1000  # horodatage de l'échange 30 ms avant l'envoi
    assert rep["messages_sans_horodatage"]["book"] > 0
    assert rep["debit_msg_par_s"]["book"] > 0


def test_ws_unplanned_disconnect_is_counted_and_resumed():
    srv = FakeServer(fail_at=5)
    ws = bb.WsProbe(bb.WS_URL, ["BTC-EUR"], connect=srv.connect, backoff_s=0.05)
    asyncio.run(ws.run(duration_s=0.8, reconnect_every_s=0, max_forced=0))
    rep = ws.report()
    assert rep["coupures_non_planifiees"] == 1 and rep["connexions"] == 2
    assert rep["reprises"] and rep["reprises"][0].get("non_planifiee")
    assert rep["reprises"][0]["premiere_donnee_ms"] is not None


def test_ws_error_105_halts():
    class BanWS(FakeWS):
        async def recv(self):
            await asyncio.sleep(0.005)
            return json.dumps({"action": "subscribe", "errorCode": 105, "error": "banned"})

    def connect(url):
        return BanWS(FakeServer(), 1)

    ws = bb.WsProbe(bb.WS_URL, ["BTC-EUR"], connect=connect)
    t0 = time.monotonic()
    asyncio.run(ws.run(duration_s=5.0, reconnect_every_s=1.0))
    assert time.monotonic() - t0 < 2 and ws.halted and ws.connections == 1


def test_ws_stops_when_rest_guard_halts():
    g = bb.RateGuard()
    g.halted = "HTTP 429"
    ws = bb.WsProbe(bb.WS_URL, ["BTC-EUR"], connect=FakeServer().connect, guard=g)
    asyncio.run(ws.run(duration_s=5.0, reconnect_every_s=1.0))
    assert ws.connections == 0


def test_subscribe_message_public_channels_only():
    ws = bb.WsProbe(bb.WS_URL, ["BTC-EUR", "ETH-EUR"])
    msg = ws.subscribe_msg()
    assert msg["action"] == "subscribe"
    assert {c["name"] for c in msg["channels"]} == {"ticker", "book", "trades"}  # jamais « account »


# ------------------------------------------------------------------ données périmées
def test_staleness_uses_engine_rule():
    now = 10_000_000
    info = MarketInfo.from_bitvavo(BTC_RAW)
    fresh = MarketState(info, ts_received_ms=now - 1000, ts_market_ms=now - 2000)
    old_exchange = MarketState(info, ts_received_ms=now - 100, ts_market_ms=now - 60_000)  # reçu frais, daté ancien
    never = MarketState(info)
    other = MarketState(MarketInfo.from_bitvavo({**BTC_RAW, "market": "BTC-USDC", "quote": "USDC"}),
                        ts_received_ms=now - 999_999)
    r = bb.staleness({"a": fresh, "b": old_exchange, "c": never, "d": other}, now, 0, 15_000)
    assert r["marches"] == 3 and r["sans_donnee"] == 1 and r["perimes"] == 1 and r["part_perimes"] == 0.5
    assert r["age_ms"]["max"] == 60_000


# ------------------------------------------------------------------ moteur réel (simulation) sur faux Bitvavo
def _fake_transport(remaining):
    pub = FakePublic(Clock())

    async def h(req):
        path = req.url.path.removeprefix("/v2")
        remaining[0] -= 1
        hdr = {"bitvavo-ratelimit-remaining": str(remaining[0]), "bitvavo-ratelimit-resetat": "9999999999999",
               "bitvavo-ratelimit-limit": "1000"}
        if path == "/time":
            data = {"time": await pub.time()}
        elif path == "/markets":
            data = await pub.markets()
        elif path == "/ticker/book":
            data = await pub.ticker_book()
        elif path == "/ticker/24h":
            data = await pub.ticker_24h()
        elif path.endswith("/candles"):
            data = await pub.candles(path.split("/")[1], req.url.params["interval"], int(req.url.params["limit"]))
        else:
            return httpx.Response(404, json={"errorCode": 110, "error": "inconnu"}, headers=hdr)
        return httpx.Response(200, json=data, headers=hdr)

    return httpx.MockTransport(h)


def test_bench_engine_scans_universe_in_simulation(tmp_path):
    async def go():
        p = bb.RestProbe(bb.RateGuard(reserve=0), "https://x/v2", _fake_transport([1000]))
        res = await bb.bench_engine(bb.GuardedPublic(p), tmpdir=tmp_path)
        await p.aclose()
        return res, p.report()

    res, rep = asyncio.run(go())
    assert res["mode"] == "simulation"
    assert res["univers"]["marches"] == 3 and res["univers"]["eligibles"] == 2
    assert res["refresh_candles_univers"]["marches"] == 2 and res["bougies_perimees"] == 0
    assert res["decision_univers"]["lignes_scanner"] == 3
    assert res["perimes_avant_decision"]["perimes"] == 0
    assert res["decision_tracemalloc"]["pic_alloue_mo"] >= 0 and res["memoire_apres"]["ru_maxrss_mo"] > 0
    assert rep["points"]["/{market}/candles"]["latence_ms"]["n"] == 2


def test_run_bench_end_to_end_offline_and_output_markers(tmp_path):
    args = bb.parse_args(["--minutes", "0.05", "--pause", "0.01", "--reconnexion-s", "0.3",
                          "--out", str(tmp_path / "b.json")])
    srv = FakeServer()
    res = asyncio.run(bb.run_bench(args, transport=_fake_transport([1000]), connect=srv.connect))
    assert res["code_sortie"] == 0 and res["aucune_cle"] and res["aucun_ordre"]
    assert res["moteur"]["mode"] == "simulation"
    assert res["websocket"]["connexions"] >= 1
    assert res["rest"]["points"]["/time"]["latence_ms"]["n"] >= 3
    txt = bb.emit(res, args.out)
    lines = txt.splitlines()
    assert lines[0] == "BENCH_JSON_DEBUT" and lines[-1] == "BENCH_JSON_FIN"
    parsed = json.loads("\n".join(lines[1:-1]))
    assert parsed["ce_que_cela_ne_prouve_pas"] and json.loads((tmp_path / "b.json").read_text())["version"] == 1
    assert "secret" not in txt.lower() or "aucun" in txt.lower()


def test_run_bench_429_exits_with_code_3():
    n = [0]

    def h(req):
        n[0] += 1
        return httpx.Response(429, json={"errorCode": 110, "error": "rate"})

    args = bb.parse_args(["--minutes", "0.05", "--sans-moteur"])
    res = asyncio.run(bb.run_bench(args, transport=httpx.MockTransport(h), connect=FakeServer().connect))
    assert res["code_sortie"] == 3 and "429" in res["arret"] and n[0] == 1


def test_cli_bounds_and_real_mode_refusal(monkeypatch):
    with pytest.raises(SystemExit):
        bb.parse_args(["--minutes", "20"])
    with pytest.raises(SystemExit):
        bb.parse_args(["--poids-par-minute", "1500"])
    monkeypatch.setenv("NELYQOR_REAL_TRADING_ALLOWED", "true")
    assert bb.main(["--minutes", "1"]) == 2


def test_network_failure_gives_partial_json_and_code_4():
    def h(req):
        raise httpx.ConnectError("bloqué", request=req)

    class DownWS:
        def __init__(self, url):
            raise OSError("bloqué")

    args = bb.parse_args(["--minutes", "0.03", "--pause", "0.05"])
    res = asyncio.run(bb.run_bench(args, transport=httpx.MockTransport(h), connect=DownWS))
    assert res["code_sortie"] == 4 and res["aucune_mesure"]
    assert "erreur" in res["decalage_horloge"] and res["moteur"]["incomplet"]
