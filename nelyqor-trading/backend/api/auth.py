"""Authentification du propriétaire : mot de passe scrypt, sessions serveur, cookie HttpOnly/Secure/SameSite=Strict,
jeton CSRF (en-tête X-CSRF-Token), limitation des tentatives par IP et globale."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def hash_password(pw: str, salt: bytes | None = None) -> str:
    if len(pw) < 12:
        raise ValueError("mot de passe trop court (12 caractères minimum)")
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


@dataclass
class Session:
    sid: str
    csrf: str
    created: float
    last_seen: float
    ip: str


@dataclass
class AuthManager:
    password_hash: str | None
    ttl_s: int = 12 * 3600
    idle_s: int = 2 * 3600
    max_attempts_ip: int = 5
    # global élevé : un attaquant ne doit pas pouvoir verrouiller durablement le propriétaire
    # (l'arrêt d'urgence reste aussi possible en SSH : scripts/emergency_stop.sh)
    max_attempts_global: int = 200
    window_s: int = 900
    sessions: dict[str, Session] = field(default_factory=dict)
    attempts: dict[str, list[float]] = field(default_factory=dict)

    def _recent(self, key: str, now: float) -> list[float]:
        lst = [t for t in self.attempts.get(key, []) if now - t < self.window_s]
        self.attempts[key] = lst
        return lst

    def locked(self, ip: str, now: float | None = None) -> bool:
        now = now or time.time()
        return len(self._recent(ip, now)) >= self.max_attempts_ip or len(self._recent("*", now)) >= self.max_attempts_global

    def login(self, pw: str, ip: str) -> Session | None:
        now = time.time()
        if self.locked(ip, now):
            return None
        if not self.password_hash or not verify_password(pw, self.password_hash):
            self.attempts.setdefault(ip, []).append(now)
            self.attempts.setdefault("*", []).append(now)
            return None
        self.attempts.pop(ip, None)
        s = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), now, now, ip)
        self.sessions[s.sid] = s
        return s

    def verify_reauth(self, pw: str, ip: str) -> bool:
        """Ré-authentification pour les actions sensibles (activation réel, liquidation)."""
        now = time.time()
        if self.locked(ip, now):
            return False
        ok = bool(self.password_hash) and verify_password(pw, self.password_hash or "")
        if not ok:
            self.attempts.setdefault(ip, []).append(now)
            self.attempts.setdefault("*", []).append(now)
        return ok

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        s = self.sessions.get(sid)
        now = time.time()
        if s is None or now - s.created > self.ttl_s or now - s.last_seen > self.idle_s:
            self.sessions.pop(sid or "", None)
            return None
        s.last_seen = now
        return s

    def logout(self, sid: str | None) -> None:
        self.sessions.pop(sid or "", None)
