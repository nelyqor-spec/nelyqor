"""Notifications critiques uniquement, dédupliquées et limitées.

Canaux optionnels (configurés par le propriétaire, aucun par défaut) :
- Telegram : jeton du bot dans secrets/telegram_bot_token, TELEGRAM_CHAT_ID dans .env ;
- ntfy (phase 7, gratuit, application iPhone « ntfy ») : URL complète du sujet dans secrets/ntfy_url
  (ex. https://ntfy.sh/<sujet aléatoire>). Le NOM DU SUJET est le secret : quiconque le connaît lit les messages.
  Par défaut, seuls le type d'alerte et un renvoi vers la PWA sont envoyés (aucun montant, aucun marché) ;
  NELYQOR_NTFY_DETAILS=true envoie le texte complet.
Un disque plein ne doit pas faire taire les alertes (phase 7) : l'envoi précède l'écriture en base, la
déduplication est aussi tenue en mémoire, et un échec d'écriture du journal des notifications est toléré."""
from __future__ import annotations

import os
import unicodedata
from typing import Any

import httpx

from ..core.clock import new_id
from ..core.logging import get_logger
from ..storage.db import Database

log = get_logger("notify")

CRITICAL_KINDS = {"panne_prolongee", "ordre_inconnu", "securite", "perte_inhabituelle", "krach", "arret_protection",
                  "flux_non_identifie", "incident", "disque"}
NTFY_TITLES = {"panne_prolongee": "Moteur en panne", "ordre_inconnu": "Ordre inconnu", "securite": "Sécurité",
               "perte_inhabituelle": "Perte inhabituelle", "krach": "Krach", "arret_protection": "Protection",
               "flux_non_identifie": "Flux non identifié", "incident": "Incident", "disque": "Disque"}


def _ascii(s: str) -> str:
    """En-têtes HTTP : ASCII seulement (accents retirés)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


class Notifier:
    def __init__(self, db: Database, now_ms: Any, telegram_token: str | None = None, telegram_chat: str | None = None,
                 dedup_window_ms: int = 6 * 3600 * 1000, max_per_day: int = 20, ntfy_url: str | None = None,
                 ntfy_details: bool | None = None) -> None:
        self.db, self._now = db, now_ms
        self.token, self.chat = telegram_token, telegram_chat
        self.ntfy_url = ntfy_url or None
        self.ntfy_details = (os.environ.get("NELYQOR_NTFY_DETAILS", "false").lower() == "true"
                             if ntfy_details is None else ntfy_details)
        self.window, self.max_per_day = dedup_window_ms, max_per_day
        self._recent: dict[str, int] = {}  # déduplication en mémoire (base pleine ou illisible)
        self._sent_mem: list[int] = []

    def _seen(self, key: str, now: int) -> bool:
        if now - self._recent.get(key, -self.window - 1) <= self.window:
            return True
        try:
            return self.db.one("SELECT 1 FROM notifications WHERE dedup_key=? AND ts>?", (key, now - self.window)) is not None
        except Exception:  # noqa: BLE001 — base indisponible : la mémoire suffit
            return False

    def _sent_today(self, now: int) -> int:
        self._sent_mem = [t for t in self._sent_mem if t > now - 86_400_000]
        try:
            r = self.db.one("SELECT COUNT(*) n FROM notifications WHERE ts>? AND sent=1", (now - 86_400_000,))
            return max(int(r["n"]) if r else 0, len(self._sent_mem))
        except Exception:  # noqa: BLE001
            return len(self._sent_mem)

    def notify(self, kind: str, message: str, severity: str = "critique", dedup_key: str | None = None) -> bool:
        if kind not in CRITICAL_KINDS:
            return False  # pas de bruit : bilans consultables dans l'application
        key = dedup_key or kind
        now = self._now()
        if self._seen(key, now):
            return False
        self._recent[key] = now
        sent = 0
        if self._sent_today(now) < self.max_per_day:
            sent = int(self._telegram(message) | self._ntfy(kind, message))
            if sent:
                self._sent_mem.append(now)
        log.warning("notification critique", extra={"data": {"kind": kind, "sent": sent}})
        try:
            self.db.exec("INSERT INTO notifications(id,ts,severity,kind,dedup_key,message,sent) VALUES(?,?,?,?,?,?,?)",
                         (new_id(), now, severity, kind, key, message, sent))
        except Exception as e:  # noqa: BLE001 — disque plein : l'alerte est déjà partie, la base n'est pas en cause
            log.error("journal des notifications non écrit", extra={"data": {"kind": kind, "err": type(e).__name__}})
        return True

    def _telegram(self, message: str) -> bool:
        if not (self.token and self.chat):
            return False
        try:
            httpx.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                       json={"chat_id": self.chat, "text": f"[Nelyqor] {message}"}, timeout=10)
            return True
        except httpx.HTTPError as e:
            log.warning("échec Telegram", extra={"data": {"err": type(e).__name__}})
            return False

    def _ntfy(self, kind: str, message: str) -> bool:
        if not self.ntfy_url:
            return False
        body = message if self.ntfy_details else f"Alerte critique « {kind} » : détails dans la PWA (Incidents)."
        try:
            r = httpx.post(self.ntfy_url, content=body.encode(), timeout=10,
                           headers={"Title": _ascii(f"Nelyqor : {NTFY_TITLES.get(kind, kind)}"),
                                    "Priority": "high", "Tags": "warning"})
            if r.status_code >= 400:
                log.warning("échec ntfy", extra={"data": {"status": r.status_code}})
                return False
            return True
        except httpx.HTTPError as e:
            log.warning("échec ntfy", extra={"data": {"err": type(e).__name__}})
            return False
