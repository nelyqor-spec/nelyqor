"""Signature des commandes sensibles (activation du réel, liquidation) par le PROPRIÉTAIRE, vérifiée par le moteur.

- L'iPhone dérive k = PBKDF2-SHA256(mot de passe, sel_commande, ITER) (WebCrypto) et signe
  HMAC-SHA256(k, canonique(commande, ts, nonce, champs)). Le mot de passe ne quitte jamais l'iPhone pour cela.
- Le moteur détient k (secrets/owner_command_key, lisible par le moteur SEUL) ; l'API ne connaît que le sel
  (public). Une API compromise ne peut donc PAS forger une commande sensible.
- Fenêtre de validité 5 min ; nonce à usage unique (anti-rejeu)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

ITER = 600_000
VALIDITY_MS = 5 * 60_000
SENSITIVE = {"activate_real", "liquidation_confirm"}


def canonical(command: str, ts: int, nonce: str, fields: dict[str, Any]) -> bytes:
    body = {"command": command, "ts": ts, "nonce": nonce, "fields": fields}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def derive_key(password: str, salt_b64: str, iterations: int = ITER) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), iterations, dklen=32)


def new_salt() -> str:
    return base64.b64encode(secrets.token_bytes(16)).decode()


def sign(key: bytes, command: str, ts: int, nonce: str, fields: dict[str, Any]) -> str:
    return hmac.new(key, canonical(command, ts, nonce, fields), hashlib.sha256).hexdigest()


def verify(key: bytes | None, command: str, payload: dict[str, Any], now_ms: int, used_nonces: set[str]) -> str | None:
    """Renvoie None si valide, sinon la raison du refus."""
    if key is None:
        return "clé de vérification des commandes absente (secrets/owner_command_key)"
    sig = payload.get("signature")
    ts, nonce, fields = payload.get("sig_ts"), payload.get("sig_nonce"), payload.get("sig_fields")
    if not (isinstance(sig, str) and isinstance(ts, int) and isinstance(nonce, str) and isinstance(fields, dict)):
        return "commande sensible non signée par le propriétaire"
    if abs(now_ms - ts) > VALIDITY_MS:
        return "signature expirée"
    if nonce in used_nonces:
        return "signature déjà utilisée (rejeu)"
    if not hmac.compare_digest(sig, sign(key, command, ts, nonce, fields)):
        return "signature invalide"
    return None
