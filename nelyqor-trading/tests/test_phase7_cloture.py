"""Clôture de la phase 7 — remarques restantes traitées avant la mise en service de la simulation.

R1 alertes bloquantes : un envoi ntfy/Telegram pouvait bloquer la boucle et la vigie jusqu'à 10 s par canal.
R2 intégrité des sauvegardes : chiffrement sans authentification (empreinte sha256 remplaçable avec le fichier).
R3 rétention par type : une sauvegarde partielle pouvait laisser une base du moteur sans son magasin d'état.
R8 redémarrage : une boucle figée était signalée mais jamais redémarrée.
R9 horodatage Bitvavo : /ticker/book sans horodatage de l'échange ; un flux figé (cache, réponse rejouée) passait
   pour frais.
"""
from __future__ import annotations

import time

from backend.core.clock import FakeClock
from backend.notify.notifier import Notifier
from tests.test_phase5_separation import make
from tests.test_phase7_exploitation import _env, _sh, _volumes

ROOT_SCRIPTS = "scripts"


# ======================================================================== R1 alertes non bloquantes
def test_R1_background_notifier_never_blocks_caller(tmp_path):
    eng, db = make(tmp_path, FakeClock(1_770_000_000_000))
    sent: list[str] = []
    n = Notifier(db, eng.clock.now_ms, ntfy_url="http://x", background=True)

    def slow(kind, msg):
        time.sleep(2.0)  # canal lent (réseau, ntfy indisponible)
        sent.append(kind)
        return True
    n._ntfy = slow  # type: ignore[method-assign]
    t0 = time.monotonic()
    assert n.notify("incident", "test")
    assert time.monotonic() - t0 < 0.5  # rendu immédiatement : la boucle n'attend pas
    assert sent == []
    assert n.flush(10)
    assert sent == ["incident"]
    row = db.one("SELECT kind, sent FROM notifications")
    assert row["kind"] == "incident" and row["sent"] == 1  # envoi lancé, compté dans le quota


def test_R1_synchronous_mode_unchanged_and_queue_bounded(tmp_path):
    eng, db = make(tmp_path, FakeClock(1_770_000_000_000))
    n = Notifier(db, eng.clock.now_ms, ntfy_url="http://x", background=True, max_per_day=1000)
    import threading
    gate = threading.Event()
    n._ntfy = lambda kind, msg: gate.wait(30)  # type: ignore[method-assign]
    for i in range(130):  # canal bloqué : la file (100) se remplit, jamais d'attente côté moteur
        n.notify("incident", f"m{i}", dedup_key=f"k{i}")
    assert n.dropped >= 25
    gate.set()
    assert n.flush(30)


def test_R1_production_uses_background_sending():
    src = open("backend/runner.py").read()
    assert "background=True)  # envois hors de la boucle" in src


# ======================================================================== R8 redémarrage d'une boucle figée
def test_R8_frozen_loop_triggers_exit_once_after_15_min(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, _ = make(tmp_path, clock)
    calls: list[int] = []
    eng.on_frozen = lambda: calls.append(clock.now_ms())
    eng.last_cycle_ms = clock.now_ms()
    for _ in range(12 * 14):  # 14 min : alerte, mais pas encore de redémarrage
        clock.advance(5_000)
        eng.vigil_once()
    assert eng.loop_frozen() and calls == []
    for _ in range(12 * 2):
        clock.advance(5_000)
        eng.vigil_once()
    assert len(calls) == 1  # une seule demande de sortie
    kinds = [r["kind"] for r in eng.db.q("SELECT kind FROM notifications")]
    assert kinds.count("panne_prolongee") == 2  # « boucle figée » puis « redémarrage automatique »


def test_R8_no_exit_without_hook_and_production_hook_present(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, _ = make(tmp_path, clock)
    eng.last_cycle_ms = clock.now_ms()
    clock.advance(3_600_000)
    eng.vigil_once()  # aucun crochet (tests, outils) : jamais de sortie
    src = open("backend/runner.py").read()
    assert "eng.on_frozen = lambda: os._exit(70)" in src


# ======================================================================== R9 flux de prix figé
async def test_R9_frozen_book_feed_becomes_stale_then_recovers(tmp_path):
    clock = FakeClock(1_770_000_000_000)
    eng, db = make(tmp_path, clock)
    await eng.step()
    st = eng.states["BTC-EUR"]
    for _ in range(4):  # même carnet à chaque lecture pendant 4 min
        clock.advance(60_000)
        await eng.refresh_tickers()
    assert eng.feed_frozen
    assert clock.now_ms() - st.ts_received_ms > eng.limits.max_stale_ms  # données périmées : aucune entrée
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='incident'")["n"] == 1
    clock.advance(60_000)
    await eng.refresh_tickers()  # toujours figé : pas de 2e alerte
    # l'état GLOBAL publié (battement de cœur, PWA « données périmées ») le dit aussi : défaut trouvé par
    # incident_check.sh, run 46124328537 (marchés écartés, mais data_ok=true dans le battement)
    clock.advance(5_000)
    await eng.step()
    assert eng.db.kv_get("engine_heartbeat")["data_ok"] is False
    assert db.one("SELECT COUNT(*) n FROM notifications WHERE kind='incident'")["n"] == 1
    base = eng.rest.ticker_book

    async def moved():
        return [{**r, "bidSize": "7"} for r in await base()]
    eng.rest.ticker_book = moved  # type: ignore[method-assign]
    clock.advance(5_000)
    await eng.refresh_tickers()
    assert not eng.feed_frozen and st.ts_received_ms == clock.now_ms()
    await eng.step()
    assert eng.db.kv_get("engine_heartbeat")["data_ok"] is True


async def test_R9_outage_gap_is_not_a_frozen_feed(tmp_path):
    """Un trou sans lecture (panne) suivi d'un carnet identique n'est PAS un flux figé : seules des lectures
    réussies consécutives comptent."""
    clock = FakeClock(1_770_000_000_000)
    eng, _ = make(tmp_path, clock)
    await eng.refresh_tickers()
    clock.advance(600_000)
    await eng.refresh_tickers()
    assert not eng.feed_frozen


# ======================================================================== R2 / R3 sauvegardes
def _backup(tmp_path):
    env = _env(**_volumes(tmp_path), NELYQOR_RESTORE_OWNER="-")
    dest = tmp_path / "bk"
    r = _sh("backup.sh", str(dest), env=env)
    assert r.returncode == 0, r.stderr
    return env, dest, next(dest.glob("engine-*.db.enc"))


def test_R2_every_encrypted_file_is_authenticated(tmp_path):
    _, dest, _ = _backup(tmp_path)
    encs = sorted(dest.glob("*.enc"))
    assert encs and all((dest / (f.name + ".hmac")).exists() for f in encs)


def test_R2_restore_refuses_tampered_or_unauthenticated_backup(tmp_path):
    env, dest, eng_enc = _backup(tmp_path)
    eng_db = tmp_path / "volumes/nelyqor_engine_data/_data/engine.db"
    before = eng_db.read_bytes()
    # substitution délibérée : fichier modifié ET empreinte sha256 recalculée (ce que l'ancienne vérification acceptait)
    data = bytearray(eng_enc.read_bytes())
    data[-1] ^= 1
    eng_enc.write_bytes(bytes(data))
    import hashlib
    (dest / (eng_enc.name + ".sha256")).write_text(f"{hashlib.sha256(bytes(data)).hexdigest()}  {eng_enc}\n")
    r = _sh("restore.sh", str(eng_enc), env=env)
    assert r.returncode != 0 and "INVALIDE" in r.stderr
    assert eng_db.read_bytes() == before  # rien n'a été modifié
    # authentification absente : refus, sauf dérogation explicite pour une sauvegarde antérieure
    env2, dest2, enc2 = _backup(tmp_path / "b")
    (dest2 / (enc2.name + ".hmac")).unlink()
    r = _sh("restore.sh", str(enc2), env=env2)
    assert r.returncode != 0 and "absente" in r.stderr
    r = _sh("restore.sh", str(enc2), env={**env2, "NELYQOR_RESTORE_SANS_HMAC": "1"})
    assert r.returncode == 0 and "sans authentification" in r.stderr


def test_R2_scheduled_backup_verifies_authentication(tmp_path):
    src = open("scripts/sauvegarde_auto.sh").read()
    assert 'backup_mac.py verify "$PASS" "$last"' in src


def test_R3_retention_keeps_complete_sets_only():
    from scripts.backup_retention import plan
    names = []
    for ts, kinds in [("20261101T000000Z", ("engine", "etat-moteur")),
                      ("20261102T000000Z", ("engine",)),            # partiel (magasin d'état manquant)
                      ("20261103T000000Z", ("engine", "etat-moteur", "api")),
                      ("20261104T000000Z", ("engine", "etat-moteur")),
                      ("20261105T000000Z", ("engine",))]:           # partiel le plus récent : conservé
        for k in kinds:
            ext = "tar" if k == "etat-moteur" else "db"
            names += [f"{k}-{ts}.{ext}.enc", f"{k}-{ts}.{ext}.enc.sha256", f"{k}-{ts}.{ext}.enc.hmac"]
    gone = plan(names, 2)
    kept_ts = {n.split("-")[-1].split(".")[0] for n in names if n not in gone}
    assert kept_ts == {"20261103T000000Z", "20261104T000000Z", "20261105T000000Z"}
    assert all(("20261101" in n or "20261102" in n) for n in gone)
    assert plan(names, 5) == []  # pas plus de N jeux complets : rien n'est supprimé
