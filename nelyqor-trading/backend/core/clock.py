"""Temps : UTC en interne, Europe/Paris uniquement à l'affichage."""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


class Clock:
    """Horloge injectable (tests, replay)."""

    def now_ms(self) -> int:
        return int(time.time() * 1000)


class FakeClock(Clock):
    def __init__(self, start_ms: int = 1_764_000_000_000) -> None:
        self.t = start_ms

    def now_ms(self) -> int:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += ms


def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def paris_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")


def new_id() -> str:
    return str(uuid.uuid4())
