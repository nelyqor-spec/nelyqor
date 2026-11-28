"""Phase 6 — INVARIANT DE PROTECTION vérifié par injection de fautes (simulation ET chemin réel simulé).

Invariant, contrôlé après CHAQUE cycle du moteur (`Engine.step`), qu'il se termine normalement ou par une exception :
toute position détenue
  - soit a un stop ACTIF chez l'échange (ordre stop de vente `awaitingTrigger` couvrant la position, lu directement
    dans le faux échange — papier ou compte réel simulé —, jamais dans le journal du moteur) ;
  - soit est EXPLICITEMENT marquée « non protégée » (`protections_<mode>[marché].protege is False`) ET une
    notification `arret_protection` nommant ce marché a été émise.
Une position marquée « protégée » sans stop vivant chez l'échange est une violation (protection silencieusement fausse).

Faux échanges uniquement : aucune clé, aucune requête vers Bitvavo. Les fautes réseau reproduisent la sémantique du
client REST réel (`BitvavoREST.request`) : requête non idempotente (POST/PUT/DELETE) en échec réseau => `OrderStatusUnknown`,
lecture (GET) en échec réseau après relances => exception `httpx` brute.

Défauts révélés (corrigés dans backend/runner.py et backend/execution/protection.py) :
  P1 vente de protection en échec RÉSEAU après l'annulation du stop (relecture GET impossible pendant la recherche de
     l'ordre au statut inconnu) : l'exception traversait `_protective_exit` et `manage_positions` ; la position restait
     SANS stop alors que `protections_<mode>` affirmait encore « protégé » (cycle précédent) et aucune notification.
  P2 annulation du stop dont la relecture échoue (réseau) : même fuite d'exception, stop dans un état inconnu
     (peut-être annulé chez l'échange) toujours affiché « protégé ».
  P3 simulation : position non protégée (stop refusé, impossible à reposer) marquée mais JAMAIS notifiée hors
     « SORTIE_REFUSEE » ; libellé « réelles » et clé de déduplication commune aux deux modes.
  P4 redémarrage : les stops papier sont annulés par la reconstruction du simulateur mais `protections_simulation`
     restait « protégé » ; si le premier cycle après redémarrage échoue (réseau), positions nues non signalées.
  P5 position détenue sans état de marché, ou sans niveau de stop calculable (méta absente, bougies indisponibles) :
     ignorée silencieusement par `manage_positions` (ni stop, ni marquage, ni notification).
"""
from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.core.clock import FakeClock
from backend.execution.engine import OrderRequest
from backend.guardian.guardian import IntentCheck, State
from backend.market_data.bitvavo_rest import BitvavoError, OrderStatusUnknown
from backend.portfolio.decision import Proposal
from backend.runner import Engine
from backend.storage.db import Database
from tests.conftest import STATE_KEY
from tests.test_phase5_separation import force_buy, make, real_ready, settings
from tests.test_phase6_plafonds import entry
from tests.test_resilience import CMD_KEY

DEDUP_WINDOW_MS = 6 * 3_600_000
GET_METHODS = {"get_order", "trades", "open_orders", "balance", "deposit_history", "withdrawal_history", "account",
               "market_fees", "get_markets"}
WRITE_METHODS = {"create_order", "cancel_order", "update_order"}


class Crash(BaseException):
    """Arrêt brutal du processus (kill -9 simulé) : n'est intercepté par aucun `except Exception` du moteur."""


# ============================================================================== injection de fautes
Action = Callable[..., Awaitable[Any]]


class Faults:
    """Enveloppe les méthodes d'un faux échange (papier ou compte réel simulé). `down` : coupure réseau totale
    (aucune requête n'atteint l'échange). `when(...)` : faute ponctuelle sur les appels qui satisfont un prédicat."""

    def __init__(self, ex: Any) -> None:
        self.ex = ex
        self.down = False
        self.hooks: list[list[Any]] = []  # [méthode, prédicat, action, restant]
        self.calls: list[tuple[str, tuple, dict]] = []
        for name in sorted(GET_METHODS | WRITE_METHODS):
            orig = getattr(ex, name, None)
            if orig is not None:
                setattr(ex, name, self._wrap(name, orig))

    @staticmethod
    def net_error(name: str) -> BaseException:
        if name in WRITE_METHODS:
            return OrderStatusUnknown(f"{name}: ConnectError (simulé)")
        return httpx.ConnectError(f"{name}: réseau coupé (simulé)")

    def _wrap(self, name: str, orig: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def f(*a: Any, **k: Any) -> Any:
            self.calls.append((name, a, k))
            if self.down:
                raise self.net_error(name)
            for h in self.hooks:
                if h[0] == name and h[3] != 0 and h[1](*a, **k):
                    h[3] -= 1
                    return await h[2](orig, *a, **k)
            return await orig(*a, **k)
        return f

    def when(self, name: str, pred: Callable[..., bool], action: Action, times: int = 1) -> None:
        self.hooks.append([name, pred, action, times])


def is_market_sell(body: dict[str, Any]) -> bool:
    return body.get("side") == "sell" and body.get("orderType") == "market"


def any_call(*a: Any, **k: Any) -> bool:
    return True


def raising(exc: BaseException) -> Action:
    async def act(orig: Any, *a: Any, **k: Any) -> Any:
        raise exc
    return act


def after_then_raise(exc: BaseException) -> Action:
    """La requête atteint l'échange (effet appliqué), la réponse est perdue."""
    async def act(orig: Any, *a: Any, **k: Any) -> Any:
        await orig(*a, **k)
        raise exc
    return act


# ============================================================================== l'invariant
def live_stop(ex: Any, eng: Engine, mk: str, qty: D) -> bool:
    """Stop de vente VIVANT chez l'échange (lu dans le faux échange, pas dans le journal du moteur)."""
    if ex is None:
        return False
    m = eng.markets[mk]
    need = m.round_amount(qty) - m.min_order_base
    for o in ex.orders.values():
        if (o.market == mk and o.side == "sell" and o.order_type.startswith("stopLoss")
                and o.status == "awaitingTrigger" and o.amount is not None and o.amount - o.filled_amount >= need):
            return True
    return False


def assert_protection_invariant(eng: Engine, where: str = "") -> dict[str, str]:
    """INVARIANT, pour chaque mode actif : position détenue => stop vivant chez l'échange, OU marquée non protégée
    ET notifiée (`arret_protection` nommant le marché, dans la fenêtre de déduplication). Renvoie l'état par position
    (« stop » ou « signalee ») pour les assertions propres à chaque scénario."""
    out: dict[str, str] = {}
    now = eng.clock.now_ms()
    notes = eng.db.q("SELECT message, ts FROM notifications WHERE kind='arret_protection'")
    for mode, ex in (("simulation", eng.paper), ("real", eng.real_exchange)):
        if mode == "real" and eng.real_exec is None:
            continue
        statuses = eng.db.kv_get(f"protections_{mode}", {}) or {}
        for mk, h in eng.held_positions(mode).items():
            key = f"{mode}:{mk}"
            if live_stop(ex, eng, mk, h.qty):
                out[key] = "stop"
                continue
            s = statuses.get(mk)
            assert s is not None and s.get("protege") is False, (
                f"[{where}] {key} : position SANS stop vivant chez l'échange et non marquée non protégée ({s})")
            assert any(mk in n["message"] and now - int(n["ts"]) < DEDUP_WINDOW_MS for n in notes), (
                f"[{where}] {key} : marquée non protégée mais AUCUNE notification arret_protection")
            out[key] = "signalee"
    return out


async def cycle(eng: Engine, clock: FakeClock, dt: int = 61_000, where: str = "",
                expect: tuple[type[BaseException], ...] = ()) -> dict[str, str]:
    """Un cycle du moteur, PUIS l'invariant (y compris quand le cycle se termine par une exception attendue)."""
    clock.advance(dt)
    try:
        await eng.step()
    except expect:
        pass
    return assert_protection_invariant(eng, where)


# ============================================================================== montages
def quiet(eng: Engine) -> None:
    """Aucune entrée ni sortie spontanée : seuls les scénarios décident."""
    eng.decider.evaluate_entry = lambda *a, **k: []  # type: ignore[method-assign]
    eng.decider.evaluate_exit = lambda *a, **k: None  # type: ignore[method-assign]


def want_exit(eng: Engine, on: bool = True) -> None:
    if not on:
        eng.decider.evaluate_exit = lambda *a, **k: None  # type: ignore[method-assign]
        return
    eng.decider.evaluate_exit = lambda m, s, df, h: Proposal(  # type: ignore[method-assign]
        m.market, "vendre", "t", amount_base=h.qty, reasons=["sortie (test)"])


def exchange_of(eng: Engine, mode: str) -> Any:
    return eng.paper if mode == "simulation" else eng.real_exchange


def exec_of(eng: Engine, mode: str) -> Any:
    return eng.paper_exec if mode == "simulation" else eng.real_exec


async def setup(tmp_path: Path, mode: str) -> tuple[Engine, Database, FakeClock, Faults]:
    """Moteur avec une position BTC de ~30 € protégée par un stop CONFIRMÉ chez l'échange (papier ou réel simulé)."""
    if mode == "simulation":
        clock = FakeClock(1_770_000_000_000)
        eng, db = make(tmp_path, clock)
        quiet(eng)
        await eng.step()
        st = eng.states["BTC-EUR"]
        r = await eng.paper_exec.submit(OrderRequest(
            "BTC-EUR", "buy", "market", amount_quote=D(30), purpose="entree",
            check=eng._check(st, "buy", D(30), True, D(100), D(100), D(30), "simulation")), D(100))
        assert r.accepted
    else:
        eng, db, _real, clock = await real_ready(tmp_path)
        force_buy(eng, D(30))
        await eng.decide()
        quiet(eng)
        assert "BTC-EUR" in eng.held_positions("real")
    st = eng.states["BTC-EUR"]
    eng._kv_set(f"position_meta_{mode}", {"BTC-EUR": {"entry_ts": clock.now_ms(), "strategy": "t",
                                                       "stop": str(st.best_bid * D("0.9"))}}, mode)
    await eng.manage_positions(exec_of(eng, mode))
    assert db.kv_get(f"protections_{mode}")["BTC-EUR"]["protege"] is True
    f = Faults(exchange_of(eng, mode))
    assert assert_protection_invariant(eng, "montage") == {f"{mode}:BTC-EUR": "stop"}
    return eng, db, clock, f


def restart(tmp_path: Path, eng: Engine, clock: FakeClock) -> Engine:
    """Nouveau moteur sur la MÊME engine.db et le MÊME magasin d'état ; même échange (le compte réel survit)."""
    real = eng.real_exchange
    eng.db.close()
    s = settings(tmp_path)
    eng2 = Engine(s, Database(s.db_path), eng.rest, clock, state_key=STATE_KEY, command_key=CMD_KEY,
                  store_path=tmp_path / "state" / "etat_moteur.json")
    if real is not None:
        eng2.attach_real_exchange(real)
    eng2.start()
    quiet(eng2)
    return eng2


def notified(db: Database, mk: str = "BTC-EUR") -> int:
    return sum(mk in r["message"] for r in db.q("SELECT message FROM notifications WHERE kind='arret_protection'"))


def stop_restored_or_sold(eng: Engine, mode: str) -> str:
    """Fin de scénario, réseau revenu : la position est soit vendue, soit sous stop vivant (jamais « signalée »)."""
    if "BTC-EUR" not in eng.held_positions(mode):
        return "vendue"
    assert live_stop(exchange_of(eng, mode), eng, "BTC-EUR", eng.held_positions(mode)["BTC-EUR"].qty)
    assert eng.db.kv_get(f"protections_{mode}")["BTC-EUR"]["protege"] is True
    return "stop"


MODES = pytest.mark.parametrize("mode", ["simulation", "real"])


# ============================================================================== 1. vente refusée par le Guardian
@MODES
@pytest.mark.parametrize("stop_also_refused", [False, True])
async def test_F1_cancel_ok_then_sell_vetoed_by_guardian(tmp_path, mode, stop_also_refused):
    """Le Guardian accepte la vente AVANT l'annulation (pré-contrôle), puis la REFUSE à la soumission (état changé
    entre-temps) ; variante : il refuse aussi la repose du stop."""
    eng, db, clock, f = await setup(tmp_path, mode)
    orig = eng.guardian.check
    calls = {"n": 0}

    def check(it: IntentCheck):
        v = orig(it)
        if it.side == "sell" and it.order_type == "market":
            calls["n"] += 1
            if calls["n"] >= 2:  # 1er appel : pré-contrôle (autorisé) ; ensuite : soumission (refusée)
                v.allowed, v.reasons = False, ["refus injecté (Guardian)"]
        if stop_also_refused and it.order_type == "stopLoss" and calls["n"] >= 2:
            v.allowed, v.reasons = False, ["repose du stop refusée (injecté)"]
        return v
    eng.guardian.check = check  # type: ignore[method-assign]
    want_exit(eng)
    state = await cycle(eng, clock, where="F1")
    assert "BTC-EUR" in eng.held_positions(mode)
    if stop_also_refused:
        assert state == {f"{mode}:BTC-EUR": "signalee"} and notified(db) >= 1
    else:
        assert state == {f"{mode}:BTC-EUR": "stop"}
        assert notified(db) >= 1  # la sortie refusée APRÈS annulation est toujours signalée
    eng.guardian.check = orig  # type: ignore[method-assign]
    want_exit(eng, False)
    await cycle(eng, clock, where="F1 retour")
    assert stop_restored_or_sold(eng, mode) == "stop"


# ============================================================================== 2. vente : réseau / UNKNOWN / rejet
@MODES
@pytest.mark.parametrize("fault", ["reseau", "unknown_non_place", "unknown_place", "rejet"])
async def test_F2_cancel_ok_then_sell_fails(tmp_path, mode, fault):
    """Annulation du stop confirmée, puis la vente au marché : échec réseau (POST perdu ET relectures impossibles,
    sémantique du client réel), statut inconnu (requête perdue / exécutée mais réponse perdue), ou rejet."""
    eng, db, clock, f = await setup(tmp_path, mode)
    if fault == "reseau":
        async def net_after_post(orig: Any, *a: Any, **k: Any) -> Any:
            f.down = True  # le réseau tombe au moment de l'envoi de la vente : relectures GET impossibles
            raise Faults.net_error("create_order")
        f.when("create_order", is_market_sell, net_after_post)
    elif fault == "unknown_non_place":
        f.when("create_order", is_market_sell, raising(OrderStatusUnknown("timeout (simulé)")))
    elif fault == "unknown_place":
        f.when("create_order", is_market_sell, after_then_raise(OrderStatusUnknown("réponse perdue (simulé)")))
    else:
        f.when("create_order", is_market_sell, raising(BitvavoError(400, 216, "Insufficient balance (simulé)")))
    want_exit(eng)
    state = await cycle(eng, clock, where=f"F2 {fault}")
    if fault == "unknown_place":
        assert "BTC-EUR" not in eng.held_positions(mode)  # retrouvée par clientOrderId : vendue
        return
    assert "BTC-EUR" in eng.held_positions(mode)
    if fault == "rejet":
        assert state == {f"{mode}:BTC-EUR": "stop"} and notified(db) >= 1
    else:
        assert state == {f"{mode}:BTC-EUR": "signalee"}
    # retour à la normale : sortie abandonnée, la vente perdue est résolue (introuvable après la fenêtre d'accès)
    f.down = False
    want_exit(eng, False)
    for i in range(3):
        await cycle(eng, clock, where=f"F2 {fault} retour {i}")
    assert stop_restored_or_sold(eng, mode) == "stop"


# ============================================================================== 3. annulation du stop en échec
@MODES
@pytest.mark.parametrize("fault", ["rejetee", "sans_reponse", "sans_reponse_puis_relecture_impossible",
                                   "appliquee_reponse_perdue"])
async def test_F3_stop_cancel_fails_or_times_out(tmp_path, mode, fault):
    eng, db, clock, f = await setup(tmp_path, mode)
    if fault == "rejetee":
        f.when("cancel_order", any_call, raising(BitvavoError(400, 240, "Order cannot be canceled (simulé)")))
    elif fault == "sans_reponse":
        f.when("cancel_order", any_call, raising(OrderStatusUnknown("DELETE /order: ReadTimeout (simulé)")))
    elif fault == "sans_reponse_puis_relecture_impossible":
        async def cancel_lost(orig: Any, *a: Any, **k: Any) -> Any:
            await orig(*a, **k)  # l'annulation A ÉTÉ appliquée chez l'échange, mais on ne peut plus le savoir
            f.down = True
            raise OrderStatusUnknown("DELETE /order: ReadTimeout (simulé)")
        f.when("cancel_order", any_call, cancel_lost)
    else:
        f.when("cancel_order", any_call, after_then_raise(OrderStatusUnknown("réponse perdue (simulé)")))
    want_exit(eng)
    state = await cycle(eng, clock, where=f"F3 {fault}", expect=(httpx.HTTPError,))
    if fault == "appliquee_reponse_perdue":
        assert "BTC-EUR" not in eng.held_positions(mode)  # relecture : annulé => vente normale
        return
    assert "BTC-EUR" in eng.held_positions(mode)
    if fault in ("rejetee", "sans_reponse"):
        assert state == {f"{mode}:BTC-EUR": "stop"}  # sortie reportée, stop intact
    else:
        assert state == {f"{mode}:BTC-EUR": "signalee"}
        await cycle(eng, clock, where="F3 coupure", expect=(httpx.HTTPError,))
    f.down = False
    want_exit(eng, False)
    for i in range(2):
        await cycle(eng, clock, where=f"F3 {fault} retour {i}")
    assert stop_restored_or_sold(eng, mode) == "stop"


# ============================================================================== 4. décalage d'horloge
@MODES
@pytest.mark.parametrize("skew_s", [20, -20, 200, -200])
@pytest.mark.parametrize("measured", [True, False])
async def test_F4_clock_skew_during_exit(tmp_path, mode, skew_s, measured):
    """Horloge de l'échange décalée de `skew_s` par rapport à l'hôte (carnet ET /v2/time). Mesurée (D2) : l'âge des
    données est corrigé, la sortie passe. Non mesurée (dérive apparue entre deux synchronisations) : ±20 s et +200 s
    (horodatages « dans le futur » : l'âge retenu est celui de la réception) => la sortie passe ; −200 s => âge 200 s
    au-delà du seuil de 120 s => sortie refusée AVANT l'annulation, le stop reste en place."""
    eng, db, clock, f = await setup(tmp_path, mode)
    eng.rest.skew_ms = skew_s * 1000
    orig = type(eng.rest).ticker_book

    async def skewed() -> list[dict[str, Any]]:
        return [{**b, "timestamp": clock.now_ms() + eng.rest.skew_ms} for b in await orig(eng.rest)]
    eng.rest.ticker_book = skewed  # type: ignore[method-assign]
    if measured:
        eng.last_clock_sync_ms = -10**12  # resynchronisation au prochain cycle
    else:
        eng.last_clock_sync_ms = clock.now_ms() + 10**9  # pas de nouvelle mesure pendant le scénario
    want_exit(eng)
    states = [await cycle(eng, clock, where=f"F4 {skew_s}s mesuré={measured} {i}") for i in range(3)]
    if measured:
        assert eng.clock_skew_ms == skew_s * 1000
    if measured or skew_s != -200:
        assert "BTC-EUR" not in eng.held_positions(mode)
    else:
        assert "BTC-EUR" in eng.held_positions(mode)
        assert states == [{f"{mode}:BTC-EUR": "stop"}] * 3
        assert not any(n == "cancel_order" for n, _, _ in f.calls)


# ============================================================================== 5. coupure réseau au milieu d'une sortie
@MODES
@pytest.mark.parametrize("n_cut", [1, 3])
async def test_F5_total_network_cut_mid_exit(tmp_path, mode, n_cut):
    """Le stop vient d'être annulé (annulation confirmée) quand TOUT le réseau tombe (public et privé) : la vente,
    les relectures et la repose du stop échouent ; puis `n_cut` cycles entiers sans réseau ; puis retour."""
    eng, db, clock, f = await setup(tmp_path, mode)

    async def cut_after_cancel(orig: Any, *a: Any, **k: Any) -> Any:
        r = await orig(*a, **k)
        f.down = True
        eng.rest.fail = True
        return r
    # l'annulation passe, sa relecture (GET) aussi : le réseau tombe juste après la relecture
    f.when("get_order", lambda *a, **k: any(c[0] == "cancel_order" for c in f.calls), cut_after_cancel)
    want_exit(eng)
    state = await cycle(eng, clock, where="F5 coupure", expect=(httpx.HTTPError,))
    assert state == {f"{mode}:BTC-EUR": "signalee"}
    for i in range(n_cut):
        await cycle(eng, clock, where=f"F5 coupure {i}", expect=(httpx.HTTPError,))
        await cycle(eng, clock, dt=5_000, where=f"F5 coupure {i} (5 s)", expect=(httpx.HTTPError,))
    f.down = False
    eng.rest.fail = False
    for i in range(2):
        await cycle(eng, clock, where=f"F5 retour {i}")
    assert stop_restored_or_sold(eng, mode) in ("vendue", "stop")


# ============================================================================== 6. redémarrage du moteur
@MODES
@pytest.mark.parametrize("when", ["apres_annulation_avant_vente", "apres_vente_unknown"])
@pytest.mark.parametrize("net_down_at_restart", [False, True])
async def test_F6_restart_mid_exit(tmp_path, mode, when, net_down_at_restart):
    eng, db, clock, f = await setup(tmp_path, mode)
    want_exit(eng)
    if when == "apres_annulation_avant_vente":
        f.when("create_order", is_market_sell, raising(Crash()))
        with pytest.raises(Crash):
            clock.advance(61_000)
            await eng.step()
    else:
        f.when("create_order", is_market_sell, raising(OrderStatusUnknown("timeout (simulé)")))
        await cycle(eng, clock, where="F6 vente UNKNOWN")
        assert db.one("SELECT COUNT(*) n FROM intents WHERE side='sell' AND order_type='market' "
                      "AND status='UNKNOWN'")["n"] == 1
    eng2 = restart(tmp_path, eng, clock)
    f2 = Faults(eng2.real_exchange) if mode == "real" else None
    if net_down_at_restart:
        eng2.rest.fail = True
        if f2 is not None:
            f2.down = True
        with pytest.raises(httpx.HTTPError):
            clock.advance(5_000)
            await eng2.step()
        if mode == "simulation":
            # aucun cycle n'a pu reconstruire le simulateur : l'invariant porte sur le journal et l'état publié
            assert eng2.paper is None
            assert eng2.db.kv_get("protections_simulation")["BTC-EUR"]["protege"] is False
            assert notified(eng2.db) >= 1
        else:
            assert_protection_invariant(eng2, "F6 redémarrage sans réseau")
        eng2.rest.fail = False
        if f2 is not None:
            f2.down = False
    for i in range(3):
        await cycle(eng2, clock, where=f"F6 {when} après redémarrage {i}")
    assert stop_restored_or_sold(eng2, mode) == "stop"


# ============================================================================== 7. carnet sans prix / périmé
@MODES
@pytest.mark.parametrize("fault", ["sans_prix", "perime", "perime_pendant_annulation"])
async def test_F7_book_without_price_or_stale_during_exit(tmp_path, mode, fault):
    eng, db, clock, f = await setup(tmp_path, mode)
    orig = type(eng.rest).ticker_book
    if fault == "sans_prix":
        async def book() -> list[dict[str, Any]]:
            return [{**b, "bid": None, "ask": ""} for b in await orig(eng.rest)]
        eng.rest.ticker_book = book  # type: ignore[method-assign]
    elif fault == "perime":
        async def book() -> list[dict[str, Any]]:
            return [{**b, "timestamp": clock.now_ms() - 3_600_000} for b in await orig(eng.rest)]
        eng.rest.ticker_book = book  # type: ignore[method-assign]
        eng.last_clock_sync_ms = clock.now_ms() + 10**9
    else:
        async def slow_cancel(o: Any, *a: Any, **k: Any) -> Any:
            r = await o(*a, **k)
            clock.advance(130_000)  # annulation lente : les données deviennent périmées avant la vente
            return r
        f.when("cancel_order", any_call, slow_cancel)
    want_exit(eng)
    state = await cycle(eng, clock, where=f"F7 {fault}")
    assert "BTC-EUR" in eng.held_positions(mode)
    assert state == {f"{mode}:BTC-EUR": "stop"}
    if fault == "perime_pendant_annulation":
        assert notified(db) >= 1  # sortie refusée APRÈS annulation : stop reposé et signalé
    else:
        assert not any(n == "cancel_order" for n, _, _ in f.calls)
    for i in range(2):
        await cycle(eng, clock, where=f"F7 {fault} suite {i}")


# ============================================================================== 8. UNKNOWN sur un autre marché
@MODES
@pytest.mark.parametrize("why", ["stop_disparu", "sortie_rejetee"])
async def test_F8_unknown_order_elsewhere_while_stop_must_be_reposed(tmp_path, mode, why):
    eng, db, clock, f = await setup(tmp_path, mode)
    ex = exec_of(eng, mode)
    # un achat ETH au statut durablement inconnu (requête perdue, relectures en timeout)
    f.when("create_order", lambda b: b.get("market") == "ETH-EUR", raising(OrderStatusUnknown("timeout (simulé)")))
    f.when("get_order", lambda m, **k: m == "ETH-EUR", raising(OrderStatusUnknown("timeout (simulé)")), times=-1)
    f.when("trades", lambda m, *a, **k: m == "ETH-EUR", raising(OrderStatusUnknown("timeout (simulé)")), times=-1)
    eth = eng.states["ETH-EUR"]
    chk = entry("ETH-EUR", D(10), mode, clock)
    r = await ex.submit(OrderRequest("ETH-EUR", "buy", "market", amount_quote=D(10), purpose="entree",
                                     ref_price=eth.best_ask, check=chk), D(100))
    assert r.status == "UNKNOWN"
    if why == "stop_disparu":
        real_ex = exchange_of(eng, mode)
        oid = next(o.order_id for o in real_ex.orders.values() if o.order_type == "stopLoss")
        await type(real_ex).cancel_order(real_ex, "BTC-EUR", oid, 1)  # annulé côté échange (hors moteur)
    else:
        f.when("create_order", is_market_sell, raising(BitvavoError(400, 216, "Insufficient balance (simulé)")))
        want_exit(eng)
    state = await cycle(eng, clock, where=f"F8 {why}")
    assert eng.guardian.state not in (State.SIMULATION, State.REAL)  # l'ordre inconnu a mis les entrées en pause
    assert db.one("SELECT status FROM intents WHERE id=?", (r.intent_id,))["status"] == "UNKNOWN"
    assert state == {f"{mode}:BTC-EUR": "stop"}  # le stop est reposé malgré l'ordre inconnu ailleurs
    want_exit(eng, False)
    await cycle(eng, clock, dt=5_000, where=f"F8 {why} suite")


# ============================================================================== 5'. défauts annexes (P5)
@MODES
async def test_P5_position_without_market_state_or_stop_level_is_flagged(tmp_path, mode):
    eng, db, clock, f = await setup(tmp_path, mode)
    # méta perdue et bougies indisponibles : aucun niveau de stop calculable ; le stop existant disparaît
    eng._kv_set(f"position_meta_{mode}", {}, mode)
    eng.candles.pop("BTC-EUR", None)
    ex = exchange_of(eng, mode)
    oid = next(o.order_id for o in ex.orders.values() if o.order_type == "stopLoss")
    await type(ex).cancel_order(ex, "BTC-EUR", oid, 1)
    orig_c = eng.rest.candles

    async def no_candles(*a: Any, **k: Any) -> list[Any]:
        return []
    eng.rest.candles = no_candles  # type: ignore[method-assign]
    assert await cycle(eng, clock, where="P5 sans niveau de stop") == {f"{mode}:BTC-EUR": "signalee"}
    eng.rest.candles = orig_c  # type: ignore[method-assign]
    # état de marché absent (marché retiré de la cote) : la position n'est jamais ignorée silencieusement
    st = eng.states.pop("BTC-EUR")
    await eng.manage_positions(exec_of(eng, mode))
    assert assert_protection_invariant(eng, "P5 sans état de marché") == {f"{mode}:BTC-EUR": "signalee"}
    eng.states["BTC-EUR"] = st


@MODES
async def test_P3_unprotected_position_notified_in_both_modes(tmp_path, mode):
    """Stop disparu chez l'échange et repose REFUSÉE (sans aucune sortie en cours) : marquée ET notifiée, en
    simulation aussi ; libellé et clé de déduplication propres au mode."""
    eng, db, clock, f = await setup(tmp_path, mode)
    ex = exchange_of(eng, mode)
    oid = next(o.order_id for o in ex.orders.values() if o.order_type == "stopLoss")
    await type(ex).cancel_order(ex, "BTC-EUR", oid, 1)
    f.when("create_order", lambda b: b.get("orderType") == "stopLoss",
           raising(BitvavoError(400, 216, "Insufficient balance (simulé)")), times=-1)
    assert await cycle(eng, clock, where="P3") == {f"{mode}:BTC-EUR": "signalee"}
    row = db.one("SELECT message, dedup_key FROM notifications WHERE kind='arret_protection'")
    label = "réelles" if mode == "real" else "simulées"
    assert row["message"] == f"Positions {label} NON protégées : ['BTC-EUR']"
    assert row["dedup_key"] == f"unprot-{mode}-BTC-EUR"
    f.hooks.clear()
    assert await cycle(eng, clock, where="P3 retour") == {f"{mode}:BTC-EUR": "stop"}


# ============================================================================== fautes combinées
@MODES
async def test_combined_unknown_sell_cut_restart_skew_and_unknown_elsewhere(tmp_path, mode):
    """Vente UNKNOWN + coupure de 3 cycles + redémarrage sans réseau + horloge −20 s + ordre inconnu ailleurs."""
    eng, db, clock, f = await setup(tmp_path, mode)
    f.when("create_order", is_market_sell, raising(OrderStatusUnknown("timeout (simulé)")))
    want_exit(eng)
    await cycle(eng, clock, where="C vente UNKNOWN")
    f.down, eng.rest.fail = True, True
    for i in range(3):
        await cycle(eng, clock, where=f"C coupure {i}", expect=(httpx.HTTPError,))
    eng2 = restart(tmp_path, eng, clock)  # le compte réel simulé (et ses fautes `f`) survit au redémarrage
    with pytest.raises(httpx.HTTPError):
        clock.advance(5_000)
        await eng2.step()
    assert_protection_invariant(eng2, "C redémarrage sans réseau")
    f.down = eng2.rest.fail = False
    orig = type(eng2.rest).ticker_book

    async def skewed() -> list[dict[str, Any]]:
        return [{**b, "timestamp": clock.now_ms() - 20_000} for b in await orig(eng2.rest)]
    eng2.rest.ticker_book = skewed  # type: ignore[method-assign]
    eng2.rest.skew_ms = -20_000
    await cycle(eng2, clock, where="C après redémarrage")
    f3 = Faults(eng2.paper) if mode == "simulation" else f
    f3.when("create_order", lambda b: b.get("market") == "ETH-EUR", raising(OrderStatusUnknown("timeout")))
    f3.when("get_order", lambda m, **k: m == "ETH-EUR", raising(OrderStatusUnknown("timeout")), times=-1)
    f3.when("trades", lambda m, *a, **k: m == "ETH-EUR", raising(OrderStatusUnknown("timeout")), times=-1)
    # ordre ETH envoyé pendant une fenêtre où le Guardian l'autorisait (il est en pause depuis la vente UNKNOWN)
    g_check = eng2.guardian.check

    def allow(it: IntentCheck):
        v = g_check(it)
        v.allowed, v.reasons = True, []
        return v
    eng2.guardian.check = allow  # type: ignore[method-assign]
    r = await exec_of(eng2, mode).submit(OrderRequest("ETH-EUR", "buy", "market", amount_quote=D(10),
                                                      ref_price=eng2.states["ETH-EUR"].best_ask, purpose="entree",
                                                      check=entry("ETH-EUR", D(10), mode, clock)), D(100))
    eng2.guardian.check = g_check  # type: ignore[method-assign]
    assert r.status == "UNKNOWN"
    f3.when("create_order", is_market_sell, raising(BitvavoError(400, 216, "rejet (simulé)")))
    want_exit(eng2)
    await cycle(eng2, clock, where="C sortie rejetée + UNKNOWN ailleurs")
    want_exit(eng2, False)
    for i in range(2):
        await cycle(eng2, clock, where=f"C fin {i}")
    assert stop_restored_or_sold(eng2, mode) == "stop"


@MODES
@pytest.mark.parametrize("seed", range(6))
async def test_combined_random_fault_schedule(tmp_path, mode, seed):
    """Calendrier ALÉATOIRE (graine fixe) de fautes sur 14 cycles : l'invariant tient après chacun."""
    rng = random.Random(seed * 7919 + (mode == "real"))
    eng, db, clock, f = await setup(tmp_path, mode)
    for i in range(14):
        f.hooks.clear()
        f.down = eng.rest.fail = False
        want_exit(eng, rng.random() < 0.6)
        k = rng.choice(["rien", "vente_unknown", "vente_rejet", "vente_reseau", "annul_rejet", "annul_timeout",
                        "coupure", "veto"])
        if k == "vente_unknown":
            f.when("create_order", is_market_sell, raising(OrderStatusUnknown("timeout")))
        elif k == "vente_rejet":
            f.when("create_order", is_market_sell, raising(BitvavoError(400, 216, "rejet")))
        elif k == "vente_reseau":
            async def net(orig: Any, *a: Any, **kw: Any) -> Any:
                f.down = True
                raise Faults.net_error("create_order")
            f.when("create_order", is_market_sell, net)
        elif k == "annul_rejet":
            f.when("cancel_order", any_call, raising(BitvavoError(400, 240, "refus")))
        elif k == "annul_timeout":
            f.when("cancel_order", any_call, after_then_raise(OrderStatusUnknown("timeout")))
        elif k == "coupure":
            f.down = eng.rest.fail = True
        orig_check = eng.guardian.check
        if k == "veto":
            def veto(it: IntentCheck, _o: Any = orig_check):
                v = _o(it)
                if it.side == "sell" and it.order_type == "market" and it.sell_amount_base and v.allowed and \
                        rng.random() < 0.5:
                    v.allowed, v.reasons = False, ["refus aléatoire"]
                return v
            eng.guardian.check = veto  # type: ignore[method-assign]
        await cycle(eng, clock, dt=rng.choice([5_000, 61_000, 61_000]), where=f"aléa {seed} #{i} {k}",
                    expect=(httpx.HTTPError,))
        eng.guardian.check = orig_check  # type: ignore[method-assign]
        if "BTC-EUR" not in eng.held_positions(mode):
            break
    f.hooks.clear()
    f.down = eng.rest.fail = False
    want_exit(eng, False)
    for i in range(3):
        await cycle(eng, clock, where=f"aléa {seed} retour {i}")
    assert stop_restored_or_sold(eng, mode) in ("vendue", "stop")


def test_invariant_helper_detects_silent_unprotected_position(tmp_path):
    """L'assertion elle-même est validée : une position « protégée » sans stop vivant est une violation."""
    import asyncio

    async def go() -> None:
        eng, db, clock, f = await setup(tmp_path, "simulation")
        oid = next(o.order_id for o in eng.paper.orders.values() if o.order_type == "stopLoss")
        await type(eng.paper).cancel_order(eng.paper, "BTC-EUR", oid, 1)
        with pytest.raises(AssertionError, match="non marquée"):
            assert_protection_invariant(eng, "auto-test")
        db.kv_set("protections_simulation", {"BTC-EUR": {"protege": False}})
        with pytest.raises(AssertionError, match="AUCUNE notification"):
            assert_protection_invariant(eng, "auto-test")
    asyncio.run(go())
