"""Sonde des BORNES de l'API publique Bitvavo /{market}/candles (diagnostic d'intégrité, phase 8).

Question : `start` et `end` sont-ils inclusifs ? Le téléchargeur (backend/data/downloader.py) découpe l'historique
en fenêtres [cursor, nxt-1] et reprend à `last_ts + 1 h` ; si une borne est exclusive, une bougie est perdue à
CHAQUE limite de fenêtre et à CHAQUE reprise incrémentale. Le diagnostic des caches (430 marchés sur 434 avec des
trous presque symétriques aux mêmes horodatages) le suggère.

Quelques requêtes publiques (aucune clé, aucun ordre), poids total < 20. Sortie JSON entre BORNES_DEBUT / BORNES_FIN.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.data.downloader import _rows_to_df  # noqa: E402
from backend.market_data.bitvavo_rest import BitvavoREST  # noqa: E402

H = 3_600_000
# horodatages absents d'un des deux caches (diagnostic 46135687867)
CAS = [("0G-EUR", 1_793_883_600_000), ("1INCH-EUR", 1_793_880_000_000), ("0G-EUR", 1_794_124_800_000)]


async def ts_list(rest: BitvavoREST, market: str, start: int, end: int, limit: int = 1440) -> list[int]:
    rows = await rest.candles(market, "1h", limit=limit, start=start, end=end)
    return sorted(int(r[0]) for r in rows)


async def main() -> dict:
    rest = BitvavoREST("https://api.bitvavo.com/v2")
    now = int(time.time() * 1000) // H * H
    t = now - 48 * H
    out: dict = {"t_ms": t}
    out["start_t_end_t+5h-1"] = await ts_list(rest, "BTC-EUR", t, t + 5 * H - 1)
    out["start_t_end_t+5h"] = await ts_list(rest, "BTC-EUR", t, t + 5 * H)
    out["start_t-1_end_t+5h-1"] = await ts_list(rest, "BTC-EUR", t - 1, t + 5 * H - 1)
    out["start_t+1_end_t+5h-1"] = await ts_list(rest, "BTC-EUR", t + 1, t + 5 * H - 1)
    rel = lambda xs: [(x - t) // H for x in xs]  # noqa: E731
    out["relatif_heures"] = {k: rel(v) for k, v in out.items() if isinstance(v, list)}
    cas = []
    for market, b in CAS:
        single = await ts_list(rest, market, b - 3 * H, b + 3 * H - 1)
        left = await ts_list(rest, market, b - 3 * H, b - 1)      # fenêtre qui se termine juste avant b
        right = await ts_list(rest, market, b, b + 3 * H - 1)     # fenêtre qui commence à b (comme une reprise)
        df = _rows_to_df(await rest.candles(market, "1h", limit=1440, start=b - 3 * H, end=b + 3 * H - 1))
        cas.append({"marche": market, "borne": b, "une_requete": [(x - b) // H for x in single],
                    "decoupe_gauche": [(x - b) // H for x in left], "decoupe_droite": [(x - b) // H for x in right],
                    "bougie_b_presente_requete_unique": b in single, "bougie_b_presente_decoupe": b in right,
                    "volume_b": float(df.loc[df["ts"] == b, "volume"].iloc[0]) if (df["ts"] == b).any() else None})
    out["cas"] = cas
    return out


if __name__ == "__main__":
    r = asyncio.run(main())
    print("BORNES_DEBUT")
    print(json.dumps(r, ensure_ascii=False))
    print("BORNES_FIN")
