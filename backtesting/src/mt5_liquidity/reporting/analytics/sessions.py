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

from mt5_liquidity.backtest.positions import ClosedPosition

# Pure classification logic lives in session_classifier to avoid circular
# imports (engine also imports classify_session / resolve_sessions).
from mt5_liquidity.session_classifier import (  # noqa: F401 — re-exported
    DEFAULT_SESSIONS,
    OVERLAP_NAME,
    OFF_SESSION_NAME,
    PRECEDENCE,
    SessionWindow,
    classify_session,
    in_window,
    resolve_sessions,
)

from .aggregation import aggregate_trade_stats
from .models import SessionRow


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
