"""Stockage des bougies en Parquet compressé (zstd), avec provenance et manifeste.

Séparation STRICTE des provenances :
- `<racine>/real/bitvavo/<MARCHÉ>/<intervalle>.parquet` : données téléchargées de l'API publique Bitvavo ;
- `<racine>/synthetic/...` : données générées. Le chargeur réel REFUSE tout fichier dont la provenance
  enregistrée n'est pas `bitvavo` ou qui ne se trouve pas sous `real/`. Les DataFrames portent
  `df.attrs["provenance"]` ; le laboratoire l'affiche dans chaque rapport.
Écriture atomique (fichier temporaire + renommage) : une interruption ne corrompt jamais un fichier existant.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REAL = "real"
SYNTH = "synthetic"
COLS = ["ts", "open", "high", "low", "close", "volume"]


class ProvenanceError(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class CandleStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path(self, kind: str, source: str, market: str, interval: str) -> Path:
        if kind not in (REAL, SYNTH):
            raise ValueError(kind)
        if kind == REAL and source != "bitvavo":
            raise ProvenanceError("seule la source 'bitvavo' est admise comme données réelles")
        return self.root / kind / source / market / f"{interval}.parquet"

    def manifest_path(self, kind: str, source: str, market: str, interval: str) -> Path:
        return self.path(kind, source, market, interval).with_suffix(".manifest.json")

    def read_manifest(self, kind: str, source: str, market: str, interval: str) -> dict[str, Any] | None:
        p = self.manifest_path(kind, source, market, interval)
        return json.loads(p.read_text()) if p.exists() else None

    def write(self, df: pd.DataFrame, kind: str, source: str, market: str, interval: str,
              manifest_extra: dict[str, Any]) -> dict[str, Any]:
        p = self.path(kind, source, market, interval)
        p.parent.mkdir(parents=True, exist_ok=True)
        df = df[COLS].sort_values("ts").reset_index(drop=True)
        meta = {b"provenance": f"{kind}/{source}".encode(), b"market": market.encode(), b"interval": interval.encode()}
        table = pa.Table.from_pandas(df, preserve_index=False).replace_schema_metadata(meta)
        tmp = p.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, p)
        man = {"kind": kind, "source": source, "market": market, "interval": interval, "rows": len(df),
               "first_ts": int(df["ts"].iloc[0]) if len(df) else None,
               "last_ts": int(df["ts"].iloc[-1]) if len(df) else None, "sha256": _sha256(p),
               "bytes": p.stat().st_size, **manifest_extra}
        mp = self.manifest_path(kind, source, market, interval)
        tmpm = mp.with_suffix(".tmp")
        tmpm.write_text(json.dumps(man, indent=2, ensure_ascii=False, default=str))
        os.replace(tmpm, mp)
        return man

    def read(self, kind: str, source: str, market: str, interval: str) -> pd.DataFrame:
        p = self.path(kind, source, market, interval)
        if not p.exists():
            return pd.DataFrame(columns=COLS)
        t = pq.read_table(p)
        prov = (t.schema.metadata or {}).get(b"provenance", b"").decode()
        if prov != f"{kind}/{source}":
            raise ProvenanceError(f"provenance enregistrée {prov!r} ≠ {kind}/{source} pour {p}")
        man = self.read_manifest(kind, source, market, interval)
        if man and man.get("sha256") != _sha256(p):
            raise ProvenanceError(f"empreinte sha256 différente du manifeste : {p}")
        df = t.to_pandas()
        df.attrs["provenance"] = prov
        return df

    def load_real(self, market: str, interval: str) -> pd.DataFrame:
        """Chargeur réservé aux données de marché RÉELLES (indexé par ts)."""
        df = self.read(REAL, "bitvavo", market, interval)
        if df.attrs.get("provenance") != "real/bitvavo" and len(df):
            raise ProvenanceError("données non réelles refusées")
        out = df.set_index("ts").astype(float)
        out.index = out.index.astype("int64")
        out.attrs["provenance"] = "real/bitvavo"
        return out

    def markets(self, kind: str = REAL, source: str = "bitvavo") -> list[str]:
        base = self.root / kind / source
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.exists() else []
