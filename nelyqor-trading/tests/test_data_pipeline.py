"""Pipeline de données : téléchargement incrémental, reprise après interruption, trous, doublons,
horodatages, anomalies, compression, provenance, séparation réel/synthétique. API Bitvavo SIMULÉE (MockTransport)."""
from __future__ import annotations

import httpx
import numpy as np
import pandas as pd
import pytest

from backend.data.downloader import download_market
from backend.data.store import REAL, SYNTH, CandleStore, ProvenanceError
from backend.data.validate import validate_candles
from backend.market_data.bitvavo_rest import BitvavoREST

H = 3_600_000
T0 = 1_700_000_000_000 // H * H


def fake_bitvavo(n: int, missing: set[int] = frozenset(), fail_on_call: int | None = None, listing: int = 0):
    """Bitvavo simulé : bougies horaires T0..T0+n, renvoyées les plus récentes en premier, limit/start/end."""
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if fail_on_call is not None and calls["n"] == fail_on_call:
            raise httpx.ConnectError("coupure réseau simulée", request=req)
        q = dict(req.url.params)
        start, end, limit = int(q["start"]), int(q["end"]), int(q["limit"])
        rows = []
        for i in range(listing, n):
            ts = T0 + i * H
            # sémantique RÉELLE mesurée sur l'API Bitvavo (phase 8, nelyqor-diag-donnees 46136440611) : `start`
            # porte sur l'ouverture, `end` sur la CLÔTURE de la bougie (incluse si ts + 1 h <= end)
            if start <= ts and ts + H <= end and i not in missing:
                o = close[i - 1] if i else close[0]
                rows.append([ts, str(o), str(max(o, close[i]) * 1.001), str(min(o, close[i]) * 0.999), str(close[i]), "10"])
        rows = sorted(rows, key=lambda r: -r[0])[:limit]
        return httpx.Response(200, json=rows)

    return handler, calls


def now_after(n: int) -> int:
    return T0 + n * H + 10


async def test_incremental_download_and_resume_after_interruption(tmp_path, monkeypatch):
    import asyncio

    async def nosleep(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", nosleep)
    n = 3000
    # run de référence sans interruption
    h_ref, _ = fake_bitvavo(n)
    ref_store = CandleStore(tmp_path / "ref")
    rest = BitvavoREST("https://x/v2", transport=httpx.MockTransport(h_ref))
    await download_market(rest, ref_store, "BTC-EUR", "1h", days=3000 // 24 + 2, now_ms=now_after(n), limit=500)
    ref = ref_store.read(REAL, "bitvavo", "BTC-EUR", "1h")
    assert len(ref) == n
    # run interrompu (coupure répétée au-delà des retries) puis repris
    h, calls = fake_bitvavo(n, fail_on_call=None)
    store = CandleStore(tmp_path / "s")

    class Flaky:
        def __init__(self):
            self.inner = BitvavoREST("https://x/v2", transport=httpx.MockTransport(h))
            self.k = 0

        async def candles(self, *a, **k):
            self.k += 1
            if self.k == 4:
                raise RuntimeError("processus tué")
            return await self.inner.candles(*a, **k)

    fl = Flaky()
    with pytest.raises(RuntimeError):
        await download_market(fl, store, "BTC-EUR", "1h", days=3000 // 24 + 2, now_ms=now_after(n), limit=500)
    partial = store.read(REAL, "bitvavo", "BTC-EUR", "1h")
    assert 0 < len(partial) < n  # écrit jusqu'à la dernière fenêtre complète, fichier non corrompu
    r = await download_market(fl, store, "BTC-EUR", "1h", days=3000 // 24 + 2, now_ms=now_after(n), limit=500)
    final = store.read(REAL, "bitvavo", "BTC-EUR", "1h")
    pd.testing.assert_frame_equal(final.reset_index(drop=True), ref.reset_index(drop=True))
    assert r.new_rows == n - len(partial)
    # relancer sans nouvelle donnée : aucune requête inutile au-delà de la fenêtre courante, rien d'ajouté
    r2 = await download_market(fl, store, "BTC-EUR", "1h", days=999, now_ms=now_after(n), limit=500)
    assert r2.new_rows == 0


async def test_no_candle_lost_at_window_boundaries(tmp_path, monkeypatch):
    """Défaut trouvé en phase 8 : avec end = limite − 1 ms, la dernière bougie de CHAQUE fenêtre était perdue
    (18 bougies par marché sur 1100 jours, à des heures différentes selon la date du téléchargement) : le rejeu
    exact de v3 divergeait entre deux caches. Un historique complet doit n'avoir AUCUN trou."""
    import asyncio

    async def nosleep(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", nosleep)
    n = 2000
    h, _ = fake_bitvavo(n)
    st = CandleStore(tmp_path / "s")
    rest = BitvavoREST("https://x/v2", transport=httpx.MockTransport(h))
    res = await download_market(rest, st, "BTC-EUR", "1h", days=n // 24 + 2, now_ms=now_after(n), limit=300)
    df = st.read(REAL, "bitvavo", "BTC-EUR", "1h")
    assert res.report.get("missing_intervals") == 0, res.report.get("gaps")
    assert len(df) == n and int(df["ts"].iloc[0]) == T0 and int(df["ts"].iloc[-1]) == T0 + (n - 1) * H
    # reprise incrémentale (1 h plus tard) : la bougie suivante est ajoutée, rien n'est perdu
    h2, _ = fake_bitvavo(n + 1)
    rest2 = BitvavoREST("https://x/v2", transport=httpx.MockTransport(h2))
    await download_market(rest2, st, "BTC-EUR", "1h", days=n // 24 + 2, now_ms=now_after(n + 1), limit=300)
    assert len(st.read(REAL, "bitvavo", "BTC-EUR", "1h")) == n + 1


async def test_gaps_detected_and_reported(tmp_path):
    h, _ = fake_bitvavo(600, missing={100, 101, 102, 400})
    store = CandleStore(tmp_path)
    rest = BitvavoREST("https://x/v2", transport=httpx.MockTransport(h))
    r = await download_market(rest, store, "ETH-EUR", "1h", days=26, now_ms=now_after(600))
    assert r.report["missing_intervals"] == 4
    assert [g[1] for g in r.report["gaps"]] == [3, 1]
    man = store.read_manifest(REAL, "bitvavo", "ETH-EUR", "1h")
    assert man["endpoint"].startswith("GET /ETH-EUR/candles") and len(man["sha256"]) == 64


async def test_listing_after_start_no_fake_history(tmp_path):
    h, _ = fake_bitvavo(500, listing=300)
    store = CandleStore(tmp_path)
    rest = BitvavoREST("https://x/v2", transport=httpx.MockTransport(h))
    await download_market(rest, store, "NEW-EUR", "1h", days=25, now_ms=now_after(500))
    df = store.read(REAL, "bitvavo", "NEW-EUR", "1h")
    assert int(df["ts"].min()) == T0 + 300 * H  # aucune bougie inventée avant le listing


def _df(rows):
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


def test_validation_duplicates_timestamps_anomalies():
    rows = [[T0 + i * H, 100, 101, 99, 100, 1] for i in range(50)]
    rows.append([T0 + 5 * H, 100, 101, 99, 100, 1])  # doublon identique
    rows.append([T0 + 7 * H, 100, 101, 99, 105, 1])  # doublon contradictoire
    rows.append([T0 + 60 * H + 123, 100, 101, 99, 100, 1])  # non aligné
    rows.append([T0 + 70 * H, 100, 90, 99, 100, 1])  # high < low : incohérent
    rows.append([T0 + 71 * H, -1, 101, 99, 100, 1])  # prix négatif
    rows.append([T0 + 80 * H, 100, 3000, 99, 2500, 1])  # rendement extrême (signalé, conservé)
    now = T0 + 80 * H + 10  # la bougie de T0+80h est en cours -> quarantaine
    clean, q, rep = validate_candles(_df(rows), H, now)
    assert rep.duplicates_identical == 1 and rep.duplicates_conflicting == 1
    assert rep.misaligned == 1 and rep.ohlc_invalid == 1 and rep.nonpositive == 1 and rep.future_or_open == 1
    assert not rep.ok
    assert T0 + 7 * H not in set(clean["ts"])  # aucune version arbitraire choisie
    assert set(q["reason"]) >= {"doublon_contradictoire", "horodatage_non_aligne", "ohlc_incoherent",
                                "valeur_nulle_negative_ou_absente", "bougie_en_cours_ou_future"}
    assert clean["ts"].is_monotonic_increasing and clean["ts"].is_unique


def test_extreme_return_flagged_not_removed():
    rows = [[T0 + i * H, 100, 101, 99, 100 + (i % 3) * 0.1, 1] for i in range(100)]
    rows[60] = [T0 + 60 * H, 100, 101, 40, 50, 1]  # krach réel possible : conservé et signalé
    clean, q, rep = validate_candles(_df(rows), H, T0 + 200 * H)
    assert rep.extreme_returns >= 1 and T0 + 60 * H in rep.extreme_return_ts and len(clean) == 100


def test_compressed_and_integrity_checked(tmp_path):
    st = CandleStore(tmp_path)
    df = _df([[T0 + i * H, 100.0, 101.0, 99.0, 100.0, 1.0] for i in range(20000)])
    st.write(df, REAL, "bitvavo", "BTC-EUR", "1h", {})
    p = st.path(REAL, "bitvavo", "BTC-EUR", "1h")
    assert p.stat().st_size < 20000 * 48 / 5  # au moins ×5 plus petit que le brut (zstd)
    raw = bytearray(p.read_bytes())
    raw[-100] ^= 0xFF
    p.write_bytes(bytes(raw))
    with pytest.raises(Exception):  # empreinte ou fichier corrompu : jamais lu silencieusement
        st.read(REAL, "bitvavo", "BTC-EUR", "1h")


def test_real_and_synthetic_strictly_separated(tmp_path):
    st = CandleStore(tmp_path)
    df = _df([[T0 + i * H, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(10)])
    st.write(df, SYNTH, "generateur", "BTC-EUR", "1h", {})
    with pytest.raises(ProvenanceError):
        st.path(REAL, "generateur", "BTC-EUR", "1h")  # une source autre que bitvavo n'est jamais « réelle »
    # un fichier synthétique copié sous real/ est rejeté (provenance interne)
    src = st.path(SYNTH, "generateur", "BTC-EUR", "1h")
    dst = st.path(REAL, "bitvavo", "BTC-EUR", "1h")
    dst.parent.mkdir(parents=True)
    dst.write_bytes(src.read_bytes())
    with pytest.raises(ProvenanceError):
        st.load_real("BTC-EUR", "1h")


def test_synthetic_frames_are_labelled():
    from backend.research.synthetic import synthetic_candles
    assert synthetic_candles(300).attrs["provenance"].startswith("synthetic")
