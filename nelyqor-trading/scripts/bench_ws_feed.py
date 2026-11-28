"""Vérification RÉELLE du flux WebSocket public Bitvavo branché au moteur (phase 8) : backend/market_data/ws_feed.py.

API publique uniquement : aucune clé, aucun canal privé, aucun ordre. Refuse de tourner si le réel est permis.
Pendant `--minutes` (défaut 8), sur les `--marches` (30) marchés EUR les plus liquides :
1. régime établi : statuts (ok / immobile / desynchro / incoherent / flux_coupe / retard), recoupement REST toutes
   les 20 s, retard échange → réception corrigé de l'horloge (/v2/time), ruptures de nonce, resynchronisations ;
2. reconnexions FORCÉES (fermeture volontaire de la connexion) : délai de reprise jusqu'à ce que tous les marchés
   suivis soient de nouveau synchronisés ;
3. coupure SIMULÉE du flux (tâche arrêtée) : détection « flux_coupe » ;
4. distinction marché immobile / flux défaillant : un marché « immobile » n'est compté comme tel que si le flux vit
   et que le REST concorde.

Sortie : JSON entre WS_JSON_DEBUT et WS_JSON_FIN, critères avec leur résultat. Code de sortie : 0 si tous les critères
passent, 1 sinon, 3 si Bitvavo renvoie 429 (arrêt immédiat), 4 si le réseau est indisponible.

Usage : python scripts/bench_ws_feed.py --minutes 8 --out ../bench/ws_feed.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.market_data import ws_feed as W  # noqa: E402
from backend.market_data.bitvavo_rest import BitvavoREST  # noqa: E402

REST_URL = "https://api.bitvavo.com/v2"
WS_URL = "wss://ws.bitvavo.com/v2/"


def now_ms() -> int:
    return int(time.time() * 1000)


class RateLimited(Exception):
    pass


async def guarded(coro: Any) -> Any:
    try:
        return await coro
    except Exception as e:
        if "429" in repr(e) or "105" in repr(e):
            raise RateLimited(repr(e)[:200]) from e
        raise


async def skew(rest: BitvavoREST) -> int:
    t0 = now_ms()
    server = int(await guarded(rest.time()))
    t1 = now_ms()
    return server - (t0 + t1) // 2


def statuses(feed: W.PublicBookFeed, books: dict[str, dict[str, Any]]) -> dict[str, str]:
    out = {}
    for m in feed.markets:
        b = books.get(m) or {}
        bid = Decimal(b["bid"]) if b.get("bid") else None
        ask = Decimal(b["ask"]) if b.get("ask") else None
        out[m] = feed.status(m, bid, ask) or "?"
    return out


async def wait_all_synced(feed: W.PublicBookFeed, timeout_s: float) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if feed.feed_alive() and all(not mf.resync_pending and mf.book.synced for mf in feed.markets.values()):
            return round(time.monotonic() - t0, 2)
        await asyncio.sleep(0.25)
    return None


async def run(minutes: float, n_markets: int) -> dict[str, Any]:
    rest = BitvavoREST(REST_URL)
    res: dict[str, Any] = {"debut_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "aucune_cle": True,
                           "aucun_ordre": True, "marches_demandes": n_markets}
    vols = await guarded(rest.ticker_24h())
    eur = sorted((t for t in vols if str(t.get("market", "")).endswith("-EUR") and t.get("volumeQuote")),
                 key=lambda t: float(t["volumeQuote"]), reverse=True)
    markets = [t["market"] for t in eur[:n_markets]]
    feed = W.PublicBookFeed(WS_URL, lambda m, d: guarded(rest.book(m, depth=d)), now_ms,
                            W.FeedConfig(max_markets=n_markets))
    feed.set_markets(markets)
    sk = [await skew(rest)]
    feed.clock_skew_ms = sk[0]
    task = asyncio.create_task(feed.run_forever())
    end = time.monotonic() + minutes * 60
    first = await wait_all_synced(feed, 60)
    res["synchronisation_initiale_s"] = first
    samples: list[dict[str, str]] = []
    reconnect_recovery: list[float | None] = []
    plan = [0.30, 0.50, 0.70]  # reconnexions forcées à ces fractions de la durée
    dead_done = False
    dead: dict[str, Any] = {}
    t_start = time.monotonic()
    while time.monotonic() < end:
        await asyncio.sleep(20)
        books = {b["market"]: b for b in await guarded(rest.ticker_book()) if b.get("market") in feed.markets}
        samples.append(statuses(feed, books))
        if len(sk) < 1 + int((time.monotonic() - t_start) // 120):
            sk.append(await skew(rest))
            feed.clock_skew_ms = sk[-1]
        frac = (time.monotonic() - t_start) / (minutes * 60)
        if plan and frac >= plan[0]:
            plan.pop(0)
            ws = feed._ws
            if ws is not None:
                await ws.close()
            await asyncio.sleep(0.5)
            reconnect_recovery.append(await wait_all_synced(feed, 60))
        elif not dead_done and frac >= 0.85:
            dead_done = True
            task.cancel()  # coupure SIMULÉE : plus aucun message (la connexion n'est pas refermée proprement)
            await asyncio.gather(task, return_exceptions=True)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 40 and feed.status(markets[0]) != W.FLUX_COUPE:
                await asyncio.sleep(0.5)
            dead["detection_s"] = round(time.monotonic() - t0, 2)
            dead["statut"] = feed.status(markets[0])
            dead["seuil_s"] = feed.cfg.feed_silence_ms / 1000
            feed.on_disconnect()
            task = asyncio.create_task(feed.run_forever())
            dead["reprise_s"] = await wait_all_synced(feed, 60)
    feed.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    counts: dict[str, int] = {}
    for smp in samples:
        for s in smp.values():
            counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values()) or 1
    lat = sorted(feed.stats.latencies_ms)
    res.update({
        "marches": markets, "echantillons": len(samples), "statuts_cumules": counts,
        "part_ok_ou_immobile": round((counts.get(W.OK, 0) + counts.get(W.IMMOBILE, 0)) / total, 4),
        "retard_echange_ms": {"n": len(lat), "mediane": statistics.median(lat) if lat else None,
                              "p90": lat[int(0.9 * (len(lat) - 1))] if lat else None,
                              "p99": lat[int(0.99 * (len(lat) - 1))] if lat else None,
                              "min": lat[0] if lat else None, "max": lat[-1] if lat else None},
        "decalage_horloge_ms": sk, "reconnexions_forcees_reprise_s": reconnect_recovery, "coupure_simulee": dead,
        "flux": feed.summary(),
    })
    med = res["retard_echange_ms"]["mediane"]
    crit = {
        "synchronisation initiale < 60 s": first is not None,
        "régime établi : ≥ 90 % des statuts ok ou immobile": res["part_ok_ou_immobile"] >= 0.90,
        "retard échange médian entre 0 et 2000 ms (horodatage en ms, horloge corrigée)": med is not None and 0 <= med <= 2000,
        "3 reconnexions forcées, chacune resynchronisée < 60 s": len(reconnect_recovery) == 3
        and all(r is not None for r in reconnect_recovery),
        "coupure simulée détectée (flux_coupe) en ≤ seuil + 5 s": dead.get("statut") == W.FLUX_COUPE
        and dead.get("detection_s", 99) <= dead.get("seuil_s", 15) + 5,
        "reprise après la coupure < 60 s": dead.get("reprise_s") is not None,
        "aucun échec d'instantané REST": feed.stats.resync_failures == 0,
        "décalage d'horloge mesuré < 5 s": all(abs(x) < 5000 for x in sk),
    }
    res["criteres"] = crit
    res["tous_les_criteres"] = all(crit.values())
    res["ce_que_cela_prouve"] = ["comportement du flux public réel depuis un runner GitHub (pas le serveur définitif)",
                                 "horodatage échange exploitable, reprises, détection de coupure"]
    res["ce_que_cela_ne_prouve_pas"] = ["l'exécution d'ordres réels", "la tenue sur plusieurs jours",
                                        "la latence depuis le serveur définitif"]
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=8.0)
    ap.add_argument("--marches", type=int, default=30)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if os.environ.get("NELYQOR_REAL_TRADING_ALLOWED", "false").lower() == "true":
        print("REFUS : ce banc ne tourne jamais avec le réel permis", file=sys.stderr)
        return 2
    code = 0
    try:
        res = asyncio.run(run(a.minutes, a.marches))
        code = 0 if res["tous_les_criteres"] else 1
    except RateLimited as e:
        res, code = {"arret": f"limite d'API atteinte, arrêt immédiat : {e}"}, 3
    except OSError as e:
        res, code = {"arret": f"réseau indisponible : {e!r}"[:300]}, 4
    res["code_sortie"] = code
    txt = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(txt)
    print("WS_JSON_DEBUT")
    print(txt)
    print("WS_JSON_FIN")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
