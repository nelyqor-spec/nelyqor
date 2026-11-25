"""Registre de modèles/stratégies : candidat -> simulation -> validé -> actif -> retiré.

Promotion vers « validé » uniquement avec un rapport de promotion positif (critères figés).
« actif » uniquement depuis « validé ». Retour arrière vers la dernière version validée.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..storage.db import Database

STATUSES = ("candidat", "simulation", "valide", "actif", "retire")
ALLOWED = {
    "candidat": {"simulation", "retire"},
    "simulation": {"valide", "retire", "candidat"},
    "valide": {"actif", "retire"},
    "actif": {"valide", "retire"},
    "retire": set(),
}


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()


class Registry:
    def __init__(self, db: Database, now_ms: Any, role: str = "engine") -> None:
        self.db, self._now, self.role = db, now_ms, role

    def register(self, name: str, version: str, params: dict[str, Any]) -> None:
        self.db.exec("INSERT OR IGNORE INTO model_registry(name,version,status,sha256,params,created_ms,updated_ms,evidence)"
                     " VALUES(?,?,?,?,?,?,?,?)", (name, version, "candidat", params_hash(params),
                                                   json.dumps(params, default=str), self._now(), self._now(), "{}"))

    def get(self, name: str, version: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM model_registry WHERE name=? AND version=?", (name, version))
        return dict(r) if r else None

    def set_status(self, name: str, version: str, status: str, evidence: dict[str, Any] | None = None) -> None:
        cur = self.get(name, version)
        if cur is None:
            raise KeyError(f"{name}@{version}")
        if status not in ALLOWED[cur["status"]]:
            raise ValueError(f"transition {cur['status']} -> {status} interdite")
        if status == "valide" and not (evidence or {}).get("promotion", {}).get("promu"):
            raise PermissionError("promotion refusée : critères non satisfaits")
        if status == "actif" and self.role == "lab":
            raise PermissionError("le laboratoire ne peut pas activer un modèle")
        if params_hash(json.loads(cur["params"])) != cur["sha256"]:
            raise RuntimeError("intégrité : hash des paramètres invalide")
        if status == "actif":
            # une seule version active par nom
            self.db.exec("UPDATE model_registry SET status='valide', updated_ms=? WHERE name=? AND status='actif'",
                         (self._now(), name))
        self.db.exec("UPDATE model_registry SET status=?, updated_ms=?, evidence=? WHERE name=? AND version=?",
                     (status, self._now(), json.dumps(evidence or json.loads(cur["evidence"]), default=str), name, version))

    def rollback(self, name: str) -> str | None:
        """Retire la version active et réactive la dernière validée ; None => pause recommandée."""
        act = self.db.one("SELECT version FROM model_registry WHERE name=? AND status='actif'", (name,))
        if act:
            self.db.exec("UPDATE model_registry SET status='retire', updated_ms=? WHERE name=? AND version=?",
                         (self._now(), name, act["version"]))
        prev = self.db.one("SELECT version FROM model_registry WHERE name=? AND status='valide' ORDER BY updated_ms DESC",
                           (name,))
        if prev:
            self.set_status(name, prev["version"], "actif")
            return str(prev["version"])
        return None

    def active(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.q("SELECT * FROM model_registry WHERE status='actif'")]

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.q("SELECT name,version,status,sha256,updated_ms FROM model_registry ORDER BY name")]
