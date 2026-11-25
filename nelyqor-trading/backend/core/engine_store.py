"""Magasin d'état RÉSERVÉ AU MOTEUR (phase 4, suite à la contre-expertise sécurité).

Problème corrigé : l'état du Guardian (état, cible de reprise, révocation), les nonces anti-rejeu et les
métadonnées des positions réelles étaient stockés dans la base SQLite que l'API peut ÉCRIRE. Même signés HMAC,
d'anciens enregistrements signés pouvaient être REJOUÉS (aucun compteur), et une ligne SUPPRIMÉE retombait sur
sa valeur par défaut sans détection.

Ici : un seul document JSON, signé HMAC-SHA256 avec séparation de domaine, contenant un compteur monotone,
écrit atomiquement (fichier temporaire + rename) dans un répertoire que SEUL le moteur monte
(`/state`, volume Docker `engine_state`, jamais monté dans les conteneurs api/lab). Toute incohérence —
signature invalide, fichier disparu alors qu'un marqueur d'initialisation existe, compteur qui recule — est une
falsification. La base ne reçoit plus qu'une COPIE d'affichage (non autoritaire) pour la PWA.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

DOMAIN = b"nelyqor-engine-store-v1\0"


class EngineStore:
    def __init__(self, path: Path, key: bytes) -> None:
        self.path = Path(path)
        self.key = key
        self.tampered = False
        self.reason = ""
        self._doc: dict[str, Any] = {"seq": 0, "data": {}}
        self._marker = self.path.with_suffix(".initialise")
        self._load()

    # ------------------------------------------------------------------ interne
    def _sig(self, seq: int, data: dict[str, Any]) -> str:
        msg = DOMAIN + json.dumps({"seq": seq, "data": data}, sort_keys=True, default=str).encode()
        return hmac.new(self.key, msg, hashlib.sha256).hexdigest()

    def _load(self) -> None:
        if not self.path.exists():
            if self._marker.exists():
                self._flag("magasin d'état du moteur disparu après initialisation")
            return
        try:
            raw = json.loads(self.path.read_text())
            seq, data, sig = int(raw["seq"]), raw["data"], str(raw["sig"])
        except Exception:  # noqa: BLE001
            self._flag("magasin d'état illisible")
            return
        if not hmac.compare_digest(sig, self._sig(seq, data)):
            self._flag("signature du magasin d'état invalide")
            return
        last = int(self._marker.read_text() or 0) if self._marker.exists() else 0
        if seq < last:
            self._flag(f"compteur du magasin d'état revenu en arrière ({seq} < {last}) : rejeu")
            return
        self._doc = {"seq": seq, "data": data}

    def _flag(self, why: str) -> None:
        self.tampered = True
        self.reason = why

    def _write(self) -> None:
        self._doc["seq"] += 1
        body = {"seq": self._doc["seq"], "data": self._doc["data"], "sig": self._sig(self._doc["seq"], self._doc["data"])}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            f.write(json.dumps(body, default=str))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self._marker.write_text(str(self._doc["seq"]))  # plus haut compteur connu (anti-rejeu d'un ancien fichier)

    # ------------------------------------------------------------------ API
    def get(self, key: str, default: Any = None) -> Any:
        return self._doc["data"].get(key, default)

    def has(self, key: str) -> bool:
        return key in self._doc["data"]

    def set(self, key: str, value: Any) -> None:
        self._doc["data"][key] = value
        self._write()

    def update(self, values: dict[str, Any]) -> None:
        self._doc["data"].update(values)
        self._write()

    def reload(self) -> bool:
        """Relit le fichier (détection d'une modification externe) ; renvoie False si falsifié."""
        self._load()
        return not self.tampered
