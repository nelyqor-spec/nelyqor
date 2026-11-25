"""Notifications critiques uniquement, dédupliquées et limitées. Telegram optionnel (configuré par le propriétaire)."""
from __future__ import annotations

from typing import Any

import httpx

from ..core.clock import new_id
from ..core.logging import get_logger
from ..storage.db import Database

log = get_logger("notify")

CRITICAL_KINDS = {"panne_prolongee", "ordre_inconnu", "securite", "perte_inhabituelle", "krach", "arret_protection",
                  "flux_non_identifie", "incident"}


class Notifier:
    def __init__(self, db: Database, now_ms: Any, telegram_token: str | None = None, telegram_chat: str | None = None,
                 dedup_window_ms: int = 6 * 3600 * 1000, max_per_day: int = 20) -> None:
        self.db, self._now = db, now_ms
        self.token, self.chat = telegram_token, telegram_chat
        self.window, self.max_per_day = dedup_window_ms, max_per_day

    def notify(self, kind: str, message: str, severity: str = "critique", dedup_key: str | None = None) -> bool:
        if kind not in CRITICAL_KINDS:
            return False  # pas de bruit : bilans consultables dans l'application
        key = dedup_key or kind
        now = self._now()
        if self.db.one("SELECT 1 FROM notifications WHERE dedup_key=? AND ts>?", (key, now - self.window)):
            return False
        sent_today = self.db.one("SELECT COUNT(*) n FROM notifications WHERE ts>? AND sent=1", (now - 86_400_000,))
        sent = 0
        if self.token and self.chat and (sent_today["n"] if sent_today else 0) < self.max_per_day:
            try:
                httpx.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                           json={"chat_id": self.chat, "text": f"[Nelyqor] {message}"}, timeout=10)
                sent = 1
            except httpx.HTTPError as e:
                log.warning("échec Telegram", extra={"data": {"err": type(e).__name__}})
        self.db.exec("INSERT INTO notifications(id,ts,severity,kind,dedup_key,message,sent) VALUES(?,?,?,?,?,?,?)",
                     (new_id(), now, severity, kind, key, message, sent))
        return True
