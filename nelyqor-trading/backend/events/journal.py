"""Journaux JSONL en AJOUT SEUL (jamais réécrits) + un petit fichier d'état dérivé (remplaçable).

data_dir/
  observations-AAAA-MM.jsonl  une ligne par observation nouvelle (source, id) — rotation mensuelle
  collectes.jsonl             une ligne par requête (disponibilité, HTTP, durée, volumes)
  reactions.jsonl             une ligne par (événement, marché, horizon) mesuré APRÈS l'horizon
  metriques.jsonl             relevés on-chain bruts
  index.jsonl                 index « déjà vu » à LONG TERME, sans texte (source, id, groupe, signature)
  etat.json                   dernier relevé des marchés Bitvavo / métriques (référence des écarts)
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model import Event


def append(path: Path, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    # une ligne tronquée par une coupure ne doit pas « avaler » la suivante : saut de ligne préalable
    needs_nl = False
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as fb:
            fb.seek(-1, os.SEEK_END)
            needs_nl = fb.read(1) != b"\n"
    with path.open("a", encoding="utf-8") as f:  # mode ajout : aucune ligne existante n'est modifiée
        if needs_nl:
            f.write("\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return len(rows)


def read(path: Path) -> Iterator[dict[str, Any]]:
    """Lignes valides seulement (une ligne tronquée par une coupure est ignorée)."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict):
                yield d


def observations_path(data_dir: Path, ms: int) -> Path:
    return data_dir / f"observations-{datetime.fromtimestamp(ms / 1000, UTC):%Y-%m}.jsonl"


def load_observations(data_dir: Path, since_ms: int | None = None) -> list[Event]:
    """Observations VALIDÉES (types contrôlés ; une ligne fautive est ignorée, jamais fatale)."""
    out: list[Event] = []
    min_month = None
    if since_ms is not None:
        min_month = f"observations-{datetime.fromtimestamp(since_ms / 1000, UTC):%Y-%m}.jsonl"
    for p in sorted(data_dir.glob("observations-*.jsonl")):
        if min_month is not None and p.name < min_month:
            continue  # fichier mensuel entièrement antérieur : non chargé
        for d in read(p):
            try:
                e = Event.from_dict(d)
            except (TypeError, ValueError):
                continue
            if since_ms is not None and e.received_ms < since_ms:
                continue
            out.append(e)
    out.sort(key=lambda e: e.received_ms)
    return out


def load_index(data_dir: Path) -> list[dict[str, Any]]:
    """Index long terme (lignes {s, i, c, g, t, o}) ; lignes de types inattendus ignorées."""
    out = []
    for d in read(data_dir / "index.jsonl"):
        if isinstance(d.get("s"), str) and isinstance(d.get("i"), str) and isinstance(d.get("c"), str) \
                and isinstance(d.get("g", ""), str) and isinstance(d.get("t"), int):
            out.append(d)
    return out


def append_observations(data_dir: Path, events: list[Event]) -> int:
    by_file: dict[Path, list[dict[str, Any]]] = {}
    for e in events:
        by_file.setdefault(observations_path(data_dir, e.received_ms), []).append(e.to_dict())
    return sum(append(p, rows) for p, rows in by_file.items())


def load_state(data_dir: Path) -> dict[str, Any]:
    p = data_dir / "etat.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(data_dir: Path, state: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / "etat.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(data_dir / "etat.json")
