#!/usr/bin/env python3
"""Mesures des sources et réaction DIFFÉRÉE du marché (bougies publiques Bitvavo, aucune clé).

  python scripts/events_measure.py reactions   # mesure les horizons 1 h / 4 h / 24 h ÉCOULÉS (ajout seul)
  python scripts/events_measure.py report      # rapport par source -> data/events/rapport.json + résumé
  python scripts/events_measure.py all         # les deux (défaut)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.events import journal  # noqa: E402
from backend.events.collector import now_ms  # noqa: E402
from backend.events.market import (  # noqa: E402
    HORIZONS_H,
    INTERVAL_MS,
    CandleCache,
    baseline_abs_return,
    bitvavo_candle_fetcher,
)
from backend.events.metrics import measure_reactions, source_report  # noqa: E402


def eur_markets(data_dir: Path) -> set[str]:
    st = journal.load_state(data_dir).get("bitvavo_marches") or {}
    return {m for m, s in st.items() if m.endswith("-EUR") and s == "trading"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="all", choices=["reactions", "report", "all"])
    ap.add_argument("--data-dir", default="data/events")
    ap.add_argument("--max-rows", type=int, default=3000)
    a = ap.parse_args()
    d = Path(a.data_dir)
    now = now_ms()
    cache = CandleCache(bitvavo_candle_fetcher())
    if a.action in ("reactions", "all"):
        print("réactions :", json.dumps(measure_reactions(d, cache, now, eur_markets(d), a.max_rows)))
    if a.action in ("report", "all"):
        ref = cache.get("BTC-EUR", now - 5 * 86_400_000, now)
        baseline = {h: baseline_abs_return(ref, h, INTERVAL_MS) if ref else None for h in HORIZONS_H}
        rep = source_report(journal.load_observations(d), list(journal.read(d / "collectes.jsonl")),
                            list(journal.read(d / "reactions.jsonl")), baseline)
        rep["genere_ms"] = now
        d.mkdir(parents=True, exist_ok=True)
        (d / "rapport.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{rep['groupes_total']} groupes ; référence |rendement| BTC : {rep['reference_btc_abs_moyenne']}")
        print(f"{'source':<20} {'dispo':>6} {'obs':>5} {'excl':>5} {'1er':>4} {'doubl':>6} {'délai_méd(min)':>14} "
              f"{'corrob':>6} {'|r|24h':>8}")
        for s, m in rep["sources"].items():
            disp = m["disponibilite"]["taux_succes"]
            print(f"{s:<20} {disp if disp is not None else '-':>6} {m['observations']:>5} {m['groupes_exclusifs']:>5} "
                  f"{m['premier_a_publier']:>4} {m['taux_doublons'] if m['taux_doublons'] is not None else '-':>6} "
                  f"{m['delai_publication_reception_min']['mediane'] or '-':>14} "
                  f"{m['corroboration_moyenne'] or '-':>6} {m['reaction_btc_abs_moyenne']['24h'] or '-':>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
