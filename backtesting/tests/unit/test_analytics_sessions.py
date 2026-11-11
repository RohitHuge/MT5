"""Unit tests for session analytics (reporting/analytics/sessions.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.backtest.positions import ClosedPosition
from mt5_liquidity.reporting.analytics.sessions import (
    OFF_SESSION_NAME,
    OVERLAP_NAME,
    classify_session,
    in_window,
    resolve_sessions,
    DEFAULT_SESSIONS,
)

UTC = timezone.utc
MONDAY = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)  # a normal winter Monday


def _trade_at(ts: datetime, pnl: float = 10.0) -> ClosedPosition:
    return ClosedPosition(
        setup_id=f"S{ts.isoformat()}", direction="BUY", fill_price=2000.0,
        close_price=2001.0, tp_price=2004.0, sl_price=1998.0,
        lots=0.1, pnl_usd=pnl, exit_reason="TP" if pnl > 0 else "SL",
        fill_candle_index=1, close_candle_index=3, fill_ts=ts,
        close_ts=ts + timedelta(minutes=5), bars_held=1,
        value_per_point_per_lot=1.0, point=0.01, commission_usd=0.0,
    )


class TestResolveSessions:
    def test_defaults_are_distributed(self):
        sessions = resolve_sessions(None)
        names = {s.name for s in sessions}
        assert names == {"TOKYO", "LONDON", "NEW_YORK"}

    def test_override_merges(self):
        sessions = resolve_sessions({"LONDON": {"tz": "Europe/London", "start": "07:00", "end": "16:00"}})
        london = next(s for s in sessions if s.name == "LONDON")
        assert (london.start, london.end) == ("07:00", "16:00")
        # untouched defaults remain
        tokyo = next(s for s in sessions if s.name == "TOKYO")
        assert tokyo.start == "09:00"

    def test_unknown_session_rejected(self):
        with pytest.raises(ValueError, match="Unknown session override"):
            resolve_sessions({"SYDNEY": {"tz": "Australia/Sydney", "start": "09:00", "end": "16:00"}})

    def test_bad_time_format_rejected(self):
        with pytest.raises(ValueError, match="HH:MM"):
            resolve_sessions({"LONDON": {"tz": "Europe/London", "start": "8am", "end": "17:00"}})

    def test_bad_timezone_rejected(self):
        with pytest.raises(ValueError, match="unknown timezone"):
            resolve_sessions({"LONDON": {"tz": "Mars/Olympus", "start": "08:00", "end": "17:00"}})


class TestInWindow:
    def test_tokyo_window_winter(self):
        tokyo = next(w for w in DEFAULT_SESSIONS if w.name == "TOKYO")
        # 09:00 JST == 00:00 UTC (JST = UTC+9, no DST)
        assert in_window(MONDAY.replace(hour=0), tokyo) is True
        assert in_window(MONDAY.replace(hour=6), tokyo) is False   # 15:00 JST -> excluded
        assert in_window(MONDAY.replace(hour=5, minute=59), tokyo) is True

    def test_london_window_winter(self):
        london = next(w for w in DEFAULT_SESSIONS if w.name == "LONDON")
        assert in_window(MONDAY.replace(hour=8), london) is True    # GMT = UTC
        assert in_window(MONDAY.replace(hour=17), london) is False  # end exclusive
        assert in_window(MONDAY.replace(hour=16, minute=59), london) is True

    def test_window_end_is_exclusive(self):
        new_york = next(w for w in DEFAULT_SESSIONS if w.name == "NEW_YORK")
        # Winter: EST = UTC-5 → NY 08:00-17:00 == 13:00-22:00 UTC
        assert in_window(MONDAY.replace(hour=22), new_york) is False
        assert in_window(MONDAY.replace(hour=13), new_york) is True


class TestClassifySession:
    def test_overlap_takes_precedence_winter(self):
        # Monday 2026-11-09: London GMT (UTC+0), NY EST (UTC-5)
        # London 08:00-17:00 UTC; NY 13:00-22:00 UTC → overlap 13:00-17:00 UTC
        assert classify_session(MONDAY.replace(hour=14), DEFAULT_SESSIONS) == OVERLAP_NAME

    def test_london_only_winter(self):
        assert classify_session(MONDAY.replace(hour=9), DEFAULT_SESSIONS) == "LONDON"

    def test_new_york_only_winter(self):
        assert classify_session(MONDAY.replace(hour=18), DEFAULT_SESSIONS) == "NEW_YORK"

    def test_tokyo(self):
        assert classify_session(MONDAY.replace(hour=2), DEFAULT_SESSIONS) == "TOKYO"

    def test_off_session(self):
        assert classify_session(MONDAY.replace(hour=23), DEFAULT_SESSIONS) == OFF_SESSION_NAME

    def test_dst_transition_us_spring_2026(self):
        # US DST starts 2026-03-08; UK DST starts 2026-03-29.
        # On 2026-03-16 London is still GMT (UTC+0), NY is EDT (UTC-4):
        # London 08:00-17:00 UTC; NY 12:00-21:00 UTC → overlap 12:00-17:00 UTC
        monday = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
        assert classify_session(monday, DEFAULT_SESSIONS) == OVERLAP_NAME
        # 12:30 UTC = 12:30 London (in session) and 08:30 NY (in session)
        assert classify_session(monday.replace(minute=30), DEFAULT_SESSIONS) == OVERLAP_NAME

    def test_dst_gap_week_us_spring(self):
        # 2026-03-09: NY already EDT (UTC-4 → 12:00 UTC open), London GMT (08:00 UTC)
        assert classify_session(datetime(2026, 3, 9, 12, 30, tzinfo=UTC),
                                DEFAULT_SESSIONS) == OVERLAP_NAME
        assert classify_session(datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
                                DEFAULT_SESSIONS) == OVERLAP_NAME

    def test_eu_dst_ends_before_us_2026(self):
        # UK DST ends 2026-10-25; US DST ends 2026-11-01.
        # On 2026-10-26: London GMT (08:00-17:00 UTC), NY still EDT (12:00-21:00 UTC)
        # → overlap 12:00-17:00 UTC (unchanged), London-only 08:00-12:00.
        assert classify_session(datetime(2026, 10, 26, 10, 0, tzinfo=UTC),
                                DEFAULT_SESSIONS) == "LONDON"
        assert classify_session(datetime(2026, 10, 26, 14, 0, tzinfo=UTC),
                                DEFAULT_SESSIONS) == OVERLAP_NAME

    def test_winter_ny_extension_after_london_close(self):
        # Winter: London closes 17:00 UTC; NY runs to 22:00 UTC.
        assert classify_session(datetime(2026, 11, 9, 19, 0, tzinfo=UTC),
                                DEFAULT_SESSIONS) == "NEW_YORK"


class TestAnalyzeSessions:
    def test_each_trade_in_exactly_one_row_and_counts_reconcile(self):
        from mt5_liquidity.reporting.analytics.sessions import analyze_sessions

        stamps = [
            MONDAY.replace(hour=2),    # TOKYO
            MONDAY.replace(hour=9),    # LONDON
            MONDAY.replace(hour=14),   # OVERLAP
            MONDAY.replace(hour=19),   # NEW_YORK
            MONDAY.replace(hour=23),   # OFF_SESSION
        ]
        trades = [_trade_at(ts) for ts in stamps]
        rows = analyze_sessions(trades, DEFAULT_SESSIONS)
        names = [r.session for r in rows]
        assert names.count(OVERLAP_NAME) == 1
        assert names[-1] == OFF_SESSION_NAME
        assert sum(r.stats.trade_count for r in rows) == len(trades)
        assert sum(r.stats.net_pnl for r in rows) == pytest.approx(
            sum(t.net_pnl_usd for t in trades))

    def test_overlapping_entry_is_not_double_counted(self):
        from mt5_liquidity.reporting.analytics.sessions import analyze_sessions

        trades = [_trade_at(MONDAY.replace(hour=14))]   # inside overlap
        rows = analyze_sessions(trades, DEFAULT_SESSIONS)
        by_name = {r.session: r.stats.trade_count for r in rows}
        assert by_name[OVERLAP_NAME] == 1
        assert by_name["LONDON"] == 0      # mutually exclusive primary labels
        assert by_name["NEW_YORK"] == 0

    def test_empty_trades_produce_zeroed_rows(self):
        from mt5_liquidity.reporting.analytics.sessions import analyze_sessions

        rows = analyze_sessions([], DEFAULT_SESSIONS)
        assert all(r.stats.trade_count == 0 for r in rows)
