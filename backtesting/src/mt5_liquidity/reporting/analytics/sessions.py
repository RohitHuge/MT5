"""Trading-session analytics (plan section 2E).

Session definitions are DST-aware: each window is expressed as a local
time-of-day in a regional IANA timezone and evaluated per-instant via
zoneinfo, so UTC offsets follow daylight-saving transitions automatically.

Built-in defaults (standard FX conventions, configurable via
BacktestConfig.session_overrides):

    TOKYO      09:00-15:00  Asia/Tokyo
    LONDON     08:00-17:00  Europe/London
    NEW_YORK   08:00-17:00  America/New_York

LONDON_NY_OVERLAP is the intersection of the London and New York windows for
each instant, and OFF_SESSION is everything else.

Attribution policy (documented, mutually exclusive primary classification):
each trade is attributed to exactly one session by its fill timestamp using
the precedence LONDON_NY_OVERLAP > LONDON > NEW_YORK > TOKYO > OFF_SESSION.
Because classification is mutually exclusive, session trade counts always sum
to the total closed-trade population. The overlap row is a subset of both
London and New York by definition; per-session windows above remain available
for multi-label analysis if ever needed.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from mt5_liquidity.backtest.positions import ClosedPosition

from .aggregation import aggregate_trade_stats
from .models import SessionRow, SessionWindow

DEFAULT_SESSIONS: list[SessionWindow] = [
    SessionWindow(name="TOKYO", timezone="Asia/Tokyo", start="09:00", end="15:00"),
    SessionWindow(name="LONDON", timezone="Europe/London", start="08:00", end="17:00"),
    SessionWindow(name="NEW_YORK", timezone="America/New_York", start="08:00", end="17:00"),
]

OVERLAP_NAME = "LONDON_NY_OVERLAP"
OFF_SESSION_NAME = "OFF_SESSION"
# Precedence when a trade falls inside more than one window. The overlap is
# checked first so it wins over its constituent sessions.
PRECEDENCE = [OVERLAP_NAME, "LONDON", "NEW_YORK", "TOKYO"]


def resolve_sessions(session_overrides: dict[str, dict[str, str]] | None = None) -> list[SessionWindow]:
    """Merge optional config overrides into the default session windows.

    Override format: {"LONDON": {"tz": "Europe/London", "start": "08:00",
    "end": "16:30"}}. Unknown keys and malformed values raise ValueError so
    bad configuration surfaces instead of silently misclassifying trades.
    """
    windows = {w.name: w for w in DEFAULT_SESSIONS}
    for name, override in (session_overrides or {}).items():
        if name not in windows:
            raise ValueError(
                f"Unknown session override {name!r}; valid sessions: "
                f"{sorted(windows)}"
            )
        tz = override.get("tz", windows[name].timezone)
        start = override.get("start", windows[name].start)
        end = override.get("end", windows[name].end)
        _validate_hhmm(name, "start", start)
        _validate_hhmm(name, "end", end)
        try:
            ZoneInfo(tz)
        except Exception as exc:
            raise ValueError(f"Session {name} has unknown timezone {tz!r}: {exc}") from exc
        windows[name] = SessionWindow(name=name, timezone=tz, start=start, end=end)
    return list(windows.values())


def _validate_hhmm(session: str, field: str, value: str) -> None:
    try:
        time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Session {session}.{field} must be HH:MM, got {value!r}"
        ) from exc


def in_window(ts_utc: datetime, window: SessionWindow) -> bool:
    """True when the UTC instant's local time-of-day is inside the window.

    Windows are [start, end) in local wall time. An overnight window
    (start > end) spans midnight. DST is handled by zoneinfo per-instant.
    """
    local = ts_utc.astimezone(ZoneInfo(window.timezone)).time()
    start = time.fromisoformat(window.start)
    end = time.fromisoformat(window.end)
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def classify_session(
    ts_utc: datetime,
    sessions: list[SessionWindow],
) -> str:
    """Return the primary (mutually exclusive) session label for an instant."""
    london = next((w for w in sessions if w.name == "LONDON"), None)
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


def analyze_sessions(
    trades: list[ClosedPosition],
    sessions: list[SessionWindow],
) -> list[SessionRow]:
    """Aggregate closed trades by primary entry session.

    Rows are ordered: configured sessions first (default order), then
    LONDON_NY_OVERLAP, then OFF_SESSION — every closed trade appears in
    exactly one row.
    """
    order = [w.name for w in sessions] + [OVERLAP_NAME, OFF_SESSION_NAME]
    by_session: dict[str, list[ClosedPosition]] = {name: [] for name in order}
    for trade in trades:
        label = classify_session(trade.fill_ts, sessions)
        by_session[label].append(trade)

    rows: list[SessionRow] = []
    for name in order:
        rows.append(SessionRow(
            session=name,
            stats=aggregate_trade_stats(by_session[name]),
        ))
    return rows
