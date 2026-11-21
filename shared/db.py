"""Shared PostgreSQL client for all platform services.

Uses psycopg2 with a thread-safe connection pool — no ORM, no migration
framework. All SQL is explicit so it's easy to trace what queries run.

Usage (from any service):
    from shared.db import insert_virtual_trade, get_all_strategies

The module reads POSTGRES_URL from the environment on first use:
    postgresql://trader:password@postgres:5432/mt5

All datetimes passed in must be UTC-aware (tzinfo set). The DB stores them
as TIMESTAMPTZ so they round-trip correctly.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

log = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ["POSTGRES_URL"]
        _pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=url)
        log.info("DB connection pool created (min=1, max=10)")
    return _pool


@contextmanager
def _conn():
    """Yield a psycopg2 connection from the pool; commit on exit, rollback on error."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ─────────────────────────────────────────────────────────────────────────────
# strategies table
# ─────────────────────────────────────────────────────────────────────────────

def get_all_strategies() -> list[dict]:
    """Return all rows from the strategies table."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM strategies ORDER BY name")
            return [dict(r) for r in cur.fetchall()]


def set_strategy_mode(name: str, mode: str) -> None:
    """Set a strategy's mode (live/shadow/off).

    Enforces the one-live constraint via the partial unique index in schema.sql:
    if another strategy is already live, Postgres will raise IntegrityError.
    Callers that want to swap live strategy must demote the current one first.
    """
    if mode not in ("live", "shadow", "off"):
        raise ValueError(f"Invalid mode {mode!r}; must be live, shadow, or off")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE strategies SET mode = %s, updated_at = NOW() WHERE name = %s",
                (mode, name),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Strategy {name!r} not found in DB")


def swap_live_strategy(new_live: str) -> None:
    """Atomically demote current live strategy to shadow and promote new_live.

    Runs in a single transaction so the one-live constraint is never violated.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            # Demote any current live strategy (there should be at most one)
            cur.execute(
                "UPDATE strategies SET mode = 'shadow', updated_at = NOW() "
                "WHERE mode = 'live' AND name != %s",
                (new_live,),
            )
            # Promote the new one
            cur.execute(
                "UPDATE strategies SET mode = 'live', updated_at = NOW() WHERE name = %s",
                (new_live,),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Strategy {new_live!r} not found in DB")


# ─────────────────────────────────────────────────────────────────────────────
# virtual_trades table
# ─────────────────────────────────────────────────────────────────────────────

def insert_virtual_trade(
    *,
    strategy: str,
    symbol: str,
    direction: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    lots: float,
    signal_ts: datetime,
) -> int:
    """Insert a new open virtual trade. Returns the new row id."""
    now = datetime.now(tz=timezone.utc)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO virtual_trades
                    (strategy, symbol, direction, entry_price, sl_price, tp_price,
                     lots, signal_ts, opened_at, outcome)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
                RETURNING id
                """,
                (strategy, symbol, direction, entry_price, sl_price, tp_price,
                 lots, signal_ts, now),
            )
            return cur.fetchone()[0]


def close_virtual_trade(
    *,
    trade_id: int,
    close_price: float,
    outcome: str,
    gross_pnl: float,
    commission: float,
    net_pnl: float,
) -> None:
    """Mark a virtual trade as closed with final P&L."""
    if outcome not in ("TP", "SL", "FORCE_CLOSE"):
        raise ValueError(f"Invalid outcome {outcome!r}")
    closed_at = datetime.now(tz=timezone.utc)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE virtual_trades
                SET closed_at = %s, close_price = %s, outcome = %s,
                    gross_pnl = %s, commission = %s, net_pnl = %s
                WHERE id = %s AND outcome = 'OPEN'
                """,
                (closed_at, close_price, outcome, gross_pnl, commission, net_pnl,
                 trade_id),
            )


def get_open_virtual_trades(strategy: str | None = None) -> list[dict]:
    """Return all OPEN virtual trades, optionally filtered by strategy."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if strategy:
                cur.execute(
                    "SELECT * FROM virtual_trades WHERE outcome = 'OPEN' AND strategy = %s",
                    (strategy,),
                )
            else:
                cur.execute("SELECT * FROM virtual_trades WHERE outcome = 'OPEN'")
            return [dict(r) for r in cur.fetchall()]


def get_virtual_trades(
    strategy: str,
    limit: int = 200,
    offset: int = 0,
    outcome: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> list[dict]:
    """Return individual virtual trades for a strategy, newest signal first.

    Unlike get_open_virtual_trades this includes closed trades, so close_price,
    outcome, closed_at and the P&L columns are populated. Paginated via
    limit/offset; bounds apply to signal_ts because that is the indexed column
    (idx_vt_strategy_date) and closed_at is NULL for open rows.
    """
    clauses = ["strategy = %s"]
    params: list[Any] = [strategy]

    if outcome:
        clauses.append("outcome = %s")
        params.append(outcome)
    if from_ts is not None:
        clauses.append("signal_ts >= %s")
        params.append(from_ts)
    if to_ts is not None:
        clauses.append("signal_ts <= %s")
        params.append(to_ts)

    params.extend([max(1, limit), max(0, offset)])
    sql = (
        "SELECT * FROM virtual_trades WHERE "
        + " AND ".join(clauses)
        + " ORDER BY signal_ts DESC LIMIT %s OFFSET %s"
    )

    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [_jsonable(dict(r)) for r in cur.fetchall()]


def count_virtual_trades(
    strategy: str,
    outcome: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> int:
    """Total row count matching get_virtual_trades' filters (for pagination)."""
    clauses = ["strategy = %s"]
    params: list[Any] = [strategy]

    if outcome:
        clauses.append("outcome = %s")
        params.append(outcome)
    if from_ts is not None:
        clauses.append("signal_ts >= %s")
        params.append(from_ts)
    if to_ts is not None:
        clauses.append("signal_ts <= %s")
        params.append(to_ts)

    sql = "SELECT COUNT(*) AS n FROM virtual_trades WHERE " + " AND ".join(clauses)

    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            return int(row["n"]) if row else 0


# ─────────────────────────────────────────────────────────────────────────────
# real_trades table
# ─────────────────────────────────────────────────────────────────────────────

def upsert_real_trade(trade: dict[str, Any]) -> None:
    """Insert or update a real trade (keyed by ticket).

    Required keys: ticket, strategy, symbol, direction, lots, open_price,
                   opened_at, is_open
    Optional keys: close_price, sl_price, tp_price, closed_at,
                   gross_pnl, commission, swap, net_pnl
    """
    t = trade
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO real_trades
                    (ticket, strategy, symbol, direction, lots, open_price,
                     close_price, sl_price, tp_price, opened_at, closed_at,
                     gross_pnl, commission, swap, net_pnl, is_open, updated_at)
                VALUES
                    (%(ticket)s, %(strategy)s, %(symbol)s, %(direction)s,
                     %(lots)s, %(open_price)s,
                     %(close_price)s, %(sl_price)s, %(tp_price)s,
                     %(opened_at)s, %(closed_at)s,
                     %(gross_pnl)s, %(commission)s, %(swap)s, %(net_pnl)s,
                     %(is_open)s, NOW())
                ON CONFLICT (ticket) DO UPDATE SET
                    close_price = EXCLUDED.close_price,
                    sl_price    = EXCLUDED.sl_price,
                    tp_price    = EXCLUDED.tp_price,
                    closed_at   = EXCLUDED.closed_at,
                    gross_pnl   = EXCLUDED.gross_pnl,
                    commission  = EXCLUDED.commission,
                    swap        = EXCLUDED.swap,
                    net_pnl     = EXCLUDED.net_pnl,
                    is_open     = EXCLUDED.is_open,
                    updated_at  = NOW()
                """,
                {
                    "ticket":      t["ticket"],
                    "strategy":    t["strategy"],
                    "symbol":      t["symbol"],
                    "direction":   t["direction"],
                    "lots":        t["lots"],
                    "open_price":  t["open_price"],
                    "close_price": t.get("close_price"),
                    "sl_price":    t.get("sl_price"),
                    "tp_price":    t.get("tp_price"),
                    "opened_at":   t["opened_at"],
                    "closed_at":   t.get("closed_at"),
                    "gross_pnl":   t.get("gross_pnl"),
                    "commission":  t.get("commission"),
                    "swap":        t.get("swap"),
                    "net_pnl":     t.get("net_pnl"),
                    "is_open":     t["is_open"],
                },
            )


def get_open_real_trades() -> list[dict]:
    """Return all real trades currently marked is_open=TRUE.

    Used by the executor on startup to rebuild its in-memory ticket tracker
    after a container restart — avoids losing track of positions opened before
    the crash.
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM real_trades WHERE is_open = TRUE ORDER BY opened_at")
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# daily_summary table
# ─────────────────────────────────────────────────────────────────────────────

def upsert_daily_summary(
    *,
    strategy: str,
    date: date,
    is_shadow: bool,
    trades: int = 0,
    winners: int = 0,
    losers: int = 0,
    gross_pnl: float = 0.0,
    commission: float = 0.0,
    net_pnl: float = 0.0,
    win_rate: float = 0.0,
) -> None:
    """Insert or replace the daily summary row for a given strategy + date."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO daily_summary
                    (strategy, date, is_shadow, trades, winners, losers,
                     gross_pnl, commission, net_pnl, win_rate, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (strategy, date, is_shadow) DO UPDATE SET
                    trades     = EXCLUDED.trades,
                    winners    = EXCLUDED.winners,
                    losers     = EXCLUDED.losers,
                    gross_pnl  = EXCLUDED.gross_pnl,
                    commission = EXCLUDED.commission,
                    net_pnl    = EXCLUDED.net_pnl,
                    win_rate   = EXCLUDED.win_rate,
                    updated_at = NOW()
                """,
                (strategy, date, is_shadow, trades, winners, losers,
                 gross_pnl, commission, net_pnl, win_rate),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Analytics queries (used by dashboard)
# ─────────────────────────────────────────────────────────────────────────────

def get_strategy_performance(strategy: str, days: int = 30) -> list[dict]:
    """Return daily_summary rows for the last N days for a strategy.

    Covers both shadow (is_shadow=TRUE) rows. Returns rows ordered by date asc.
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM daily_summary
                WHERE strategy = %s
                  AND date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY date ASC, is_shadow
                """,
                (strategy, days),
            )
            return [dict(r) for r in cur.fetchall()]


def get_comparison_summary(days: int = 30) -> list[dict]:
    """Aggregate stats per strategy for the last N days (for the comparison table)."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    ds.strategy,
                    s.display_name,
                    s.mode,
                    SUM(ds.trades)                                 AS total_trades,
                    SUM(ds.winners)                                AS total_winners,
                    SUM(ds.net_pnl)                                AS total_net_pnl,
                    CASE WHEN SUM(ds.trades) > 0
                         THEN ROUND(SUM(ds.winners)::numeric /
                                    SUM(ds.trades) * 100, 1)
                         ELSE 0 END                                AS win_rate_pct,
                    COUNT(CASE WHEN ds.net_pnl > 0 THEN 1 END)    AS profitable_days,
                    COUNT(ds.date)                                 AS trading_days
                FROM daily_summary ds
                JOIN strategies s ON s.name = ds.strategy
                WHERE ds.date >= CURRENT_DATE - INTERVAL '%s days'
                GROUP BY ds.strategy, s.display_name, s.mode
                ORDER BY total_net_pnl DESC
                """,
                (days,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_equity_curve(strategy: str, days: int = 30, is_shadow: bool = True) -> list[dict]:
    """Return cumulative net_pnl series for charting (oldest → newest)."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    date,
                    net_pnl,
                    SUM(net_pnl) OVER (ORDER BY date ROWS UNBOUNDED PRECEDING)
                        AS cumulative_pnl
                FROM daily_summary
                WHERE strategy = %s
                  AND is_shadow = %s
                  AND date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY date ASC
                """,
                (strategy, is_shadow, days),
            )
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# backtest_runs table
# ─────────────────────────────────────────────────────────────────────────────

_BACKTEST_METRIC_COLUMNS = (
    "total_trades",
    "winners",
    "win_rate",
    "net_pnl",
    "max_drawdown",
    "profit_factor",
    "sharpe",
)


def _jsonable(value: Any) -> Any:
    """Coerce datetimes / dates / Decimals (and nested containers) to JSON-safe types."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def save_backtest_run(
    strategy: str,
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str,
    params: dict,
    metrics: dict,
    trade_log: list | None,
) -> int:
    """Insert a backtest run row and return the new id.

    *metrics* keys map onto the metric columns; missing keys are stored as NULL.
    """
    values = {col: metrics.get(col) for col in _BACKTEST_METRIC_COLUMNS}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_runs
                    (strategy, symbol, timeframe, from_date, to_date, params,
                     total_trades, winners, win_rate, net_pnl, max_drawdown,
                     profit_factor, sharpe, trade_log)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    strategy, symbol, timeframe, from_date, to_date,
                    json.dumps(_jsonable(params or {})),
                    values["total_trades"], values["winners"], values["win_rate"],
                    values["net_pnl"], values["max_drawdown"],
                    values["profit_factor"], values["sharpe"],
                    json.dumps(_jsonable(trade_log or [])),
                ),
            )
            return cur.fetchone()[0]


def get_backtest_history(strategy: str | None = None, limit: int = 20) -> list[dict]:
    """Return backtest runs newest first, optionally filtered by strategy."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if strategy:
                cur.execute(
                    "SELECT * FROM backtest_runs WHERE strategy = %s "
                    "ORDER BY run_at DESC LIMIT %s",
                    (strategy, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM backtest_runs ORDER BY run_at DESC LIMIT %s",
                    (limit,),
                )
            return [_jsonable(dict(r)) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# strategy_snapshots table
# ─────────────────────────────────────────────────────────────────────────────

def save_snapshot(strategy: str, ts: datetime, indicators: dict) -> None:
    """Store one indicator snapshot for a strategy."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO strategy_snapshots (strategy, ts, indicators)
                VALUES (%s, %s, %s)
                """,
                (strategy, ts, json.dumps(_jsonable(indicators or {}))),
            )


def get_snapshots(strategy: str, limit: int = 200) -> list[dict]:
    """Return the most recent snapshots for a strategy, newest first."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM strategy_snapshots
                WHERE strategy = %s
                ORDER BY ts DESC
                LIMIT %s
                """,
                (strategy, limit),
            )
            return [_jsonable(dict(r)) for r in cur.fetchall()]


def prune_old_snapshots(days: int = 7) -> None:
    """Delete snapshots older than N days (keeps the overlay table lean)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM strategy_snapshots WHERE ts < NOW() - INTERVAL '%s days'",
                (days,),
            )


# ─────────────────────────────────────────────────────────────────────────────
# strategies self-registration
# ─────────────────────────────────────────────────────────────────────────────

def register_strategy(
    name: str,
    display_name: str,
    description: str = "",
    mode: str = "shadow",
) -> str:
    """Upsert a strategy row from a strategy service's startup call.

    mode is only applied when the row is first created — an existing strategy's
    mode is never overwritten (promotion is a dashboard/DB decision, not the
    strategy's). Returns the mode currently stored in the DB.
    """
    if mode not in ("live", "shadow", "off"):
        raise ValueError(f"Invalid mode {mode!r}; must be live, shadow, or off")
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO strategies (name, display_name, mode, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    description  = EXCLUDED.description,
                    updated_at   = NOW()
                RETURNING mode
                """,
                (name, display_name, mode, description),
            )
            return cur.fetchone()["mode"]
