"""Lightweight session classification — no internal imports.

Importable from both the backtest engine and the reporting layer without
creating circular dependencies. Contains only the minimal data structures
and pure functions needed to classify a UTC timestamp into a named session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass
class SessionWindow:
    """A DST-aware session window in a regional IANA timezone."""
    name: str
    timezone: str    # IANA name, e.g. "Asia/Tokyo"
    start: str       # "HH:MM" local time-of-day, inclusive
    end: str         # "HH:MM" local time-of-day, exclusive

    def describe(self) -> dict:
        return {"name": self.name, "timezone": self.timezone,
                "start": self.start, "end": self.end}


DEFAULT_SESSIONS: list[SessionWindow] = [
    SessionWindow(name="TOKYO",    timezone="Asia/Tokyo",        start="09:00", end="15:00"),
    SessionWindow(name="LONDON",   timezone="Europe/London",     start="08:00", end="17:00"),
    SessionWindow(name="NEW_YORK", timezone="America/New_York",  start="08:00", end="17:00"),
]

OVERLAP_NAME    = "LONDON_NY_OVERLAP"
OFF_SESSION_NAME = "OFF_SESSION"
PRECEDENCE = [OVERLAP_NAME, "LONDON", "NEW_YORK", "TOKYO"]


def in_window(ts_utc: datetime, window: SessionWindow) -> bool:
    """True when the UTC instant's local time-of-day is inside [start, end)."""
    local = ts_utc.astimezone(ZoneInfo(window.timezone)).time()
    start = time.fromisoformat(window.start)
    end   = time.fromisoformat(window.end)
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def classify_session(ts_utc: datetime, sessions: list[SessionWindow]) -> str:
    """Return the primary (mutually exclusive) session label for a UTC instant."""
    london   = next((w for w in sessions if w.name == "LONDON"),   None)
    new_york = next((w for w in sessions if w.name == "NEW_YORK"), None)
    for name in PRECEDENCE:
        if name == OVERLAP_NAME:
            if london and new_york and in_window(ts_utc, london) and in_window(ts_utc, new_york):
                return OVERLAP_NAME
        else:
            window = next((w for w in sessions if w.name == name), None)
            if window and in_window(ts_utc, window):
                return name
    return OFF_SESSION_NAME


def _validate_hhmm(session: str, field: str, value: str) -> None:
    try:
        time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Session {session}.{field} must be HH:MM, got {value!r}"
        ) from exc


def resolve_sessions(session_overrides: dict[str, dict[str, str]] | None = None) -> list[SessionWindow]:
    """Merge optional config overrides into the default session windows."""
    windows = {w.name: w for w in DEFAULT_SESSIONS}
    for name, override in (session_overrides or {}).items():
        if name not in windows:
            raise ValueError(
                f"Unknown session override {name!r}; valid sessions: {sorted(windows)}"
            )
        tz    = override.get("tz",    windows[name].timezone)
        start = override.get("start", windows[name].start)
        end   = override.get("end",   windows[name].end)
        _validate_hhmm(name, "start", start)
        _validate_hhmm(name, "end", end)
        try:
            ZoneInfo(tz)
        except Exception as exc:
            raise ValueError(f"Session {name} has unknown timezone {tz!r}: {exc}") from exc
        windows[name] = SessionWindow(name=name, timezone=tz, start=start, end=end)
    return list(windows.values())
