"""Journaux JSONL en AJOUT SEUL (jamais réécrits) + un petit fichier d'état dérivé (remplaçable).

data_dir/
  observations-AAAA-MM.jsonl  une ligne par observation nouvelle (source, id) — rotation mensuelle
  collectes.jsonl             une ligne par requête (disponibilité, HTTP, durée, volumes)
  reactions.jsonl             une ligne par (événement, marché, horizon) mesuré APRÈS l'horizon
  metriques.jsonl             relevés on-chain bruts
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
    with path.open("a", encoding="utf-8") as f:  # mode ajout : aucune ligne existante n'est modifiée
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
    out: list[Event] = []
    for p in sorted(data_dir.glob("observations-*.jsonl")):
        for d in read(p):
            if since_ms is not None and int(d.get("received_ms", 0)) < since_ms:
                continue
            try:
                out.append(Event.from_dict(d))
            except TypeError:
                continue
    out.sort(key=lambda e: e.received_ms)
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
