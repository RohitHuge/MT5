-- MT5 Platform — PostgreSQL schema
-- Loaded automatically by the postgres container on first start via
-- docker-entrypoint-initdb.d/init.sql (mounted in docker-compose.yml).
--
-- All timestamps are TIMESTAMPTZ (stored as UTC, displayed in local tz).
-- Never store prices as TEXT; use FLOAT for all price/pnl columns.

-- -------------------------------------------------------------------------
-- strategies
-- Registry of all strategy containers. The dashboard reads this table to
-- know which strategies exist and what mode they are in.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategies (
    name         TEXT PRIMARY KEY,          -- "ema-scalp", "ob-fvg", "liq-sweep"
    display_name TEXT NOT NULL,             -- "EMA 9/21/50 + RSI14"
    mode         TEXT NOT NULL DEFAULT 'shadow'
                     CHECK (mode IN ('live', 'shadow', 'off')),
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only one strategy can be live at a time.
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategies_one_live
    ON strategies (mode)
    WHERE mode = 'live';

-- Seed the initial strategies.
INSERT INTO strategies (name, display_name, mode, description) VALUES
    ('ema-scalp',  'EMA 9/21/50 + RSI14',       'live',   'EMA crossover with RSI confirmation on M5'),
    ('liq-sweep',  'Liquidity Sweep',             'shadow', 'Sweep + structure shift + retest on M5'),
    ('ob-fvg',     'Order Block + FVG',           'shadow', 'OB and FVG confluence entries on M5')
ON CONFLICT (name) DO NOTHING;


-- -------------------------------------------------------------------------
-- virtual_trades
-- Every trade signal emitted by a shadow-mode strategy. Outcome is updated
-- by shadow-tracker as subsequent candles arrive.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS virtual_trades (
    id            SERIAL PRIMARY KEY,
    strategy      TEXT NOT NULL REFERENCES strategies(name),
    symbol        TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    entry_price   FLOAT NOT NULL,
    sl_price      FLOAT NOT NULL,
    tp_price      FLOAT NOT NULL,
    lots          FLOAT NOT NULL,
    signal_ts     TIMESTAMPTZ NOT NULL,     -- candle that fired the signal
    opened_at     TIMESTAMPTZ NOT NULL,     -- when shadow-tracker recorded it
    closed_at     TIMESTAMPTZ,              -- NULL while still open
    close_price   FLOAT,
    outcome       TEXT NOT NULL DEFAULT 'OPEN'
                      CHECK (outcome IN ('OPEN', 'TP', 'SL', 'FORCE_CLOSE')),
    gross_pnl     FLOAT,                   -- (close - entry) * lots * tick_value / point
    commission    FLOAT,                   -- estimated, same formula as backtester
    net_pnl       FLOAT                    -- gross_pnl - commission
);

CREATE INDEX IF NOT EXISTS idx_vt_strategy      ON virtual_trades (strategy);
CREATE INDEX IF NOT EXISTS idx_vt_signal_ts     ON virtual_trades (signal_ts);
CREATE INDEX IF NOT EXISTS idx_vt_outcome       ON virtual_trades (outcome);
CREATE INDEX IF NOT EXISTS idx_vt_strategy_date ON virtual_trades (strategy, signal_ts);


-- -------------------------------------------------------------------------
-- real_trades
-- Real trades placed on the MT5 demo account by the executor service.
-- Synced periodically from mt5-bridge GET /positions and trade history.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS real_trades (
    ticket        BIGINT PRIMARY KEY,
    strategy      TEXT NOT NULL,            -- from the MT5 comment field
    symbol        TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    lots          FLOAT NOT NULL,
    open_price    FLOAT NOT NULL,
    close_price   FLOAT,
    sl_price      FLOAT,
    tp_price      FLOAT,
    opened_at     TIMESTAMPTZ NOT NULL,
    closed_at     TIMESTAMPTZ,
    gross_pnl     FLOAT,
    commission    FLOAT,
    swap          FLOAT,
    net_pnl       FLOAT,                   -- gross_pnl + swap - commission
    is_open       BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rt_strategy  ON real_trades (strategy);
CREATE INDEX IF NOT EXISTS idx_rt_opened_at ON real_trades (opened_at);
CREATE INDEX IF NOT EXISTS idx_rt_is_open   ON real_trades (is_open);


-- -------------------------------------------------------------------------
-- daily_summary
-- Pre-aggregated daily P&L per strategy. Written by shadow-tracker at
-- UTC midnight for virtual trades, and by executor for real trades.
-- The dashboard reads this for charts — avoids expensive GROUP BY queries.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_summary (
    strategy    TEXT    NOT NULL,
    date        DATE    NOT NULL,
    is_shadow   BOOLEAN NOT NULL,           -- TRUE = virtual, FALSE = real
    trades      INT     NOT NULL DEFAULT 0,
    winners     INT     NOT NULL DEFAULT 0,
    losers      INT     NOT NULL DEFAULT 0,
    gross_pnl   FLOAT   NOT NULL DEFAULT 0,
    commission  FLOAT   NOT NULL DEFAULT 0,
    net_pnl     FLOAT   NOT NULL DEFAULT 0,
    win_rate    FLOAT   NOT NULL DEFAULT 0, -- winners / trades, 0 when no trades
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (strategy, date, is_shadow)
);

CREATE INDEX IF NOT EXISTS idx_ds_strategy_date ON daily_summary (strategy, date);


-- -------------------------------------------------------------------------
-- backtest_runs
-- One row per POST /api/backtest/run invocation. Written by the dashboard
-- after a strategy service returns its simulated trade list; read by the
-- Backtesting page history tab.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_runs (
    id            SERIAL PRIMARY KEY,
    strategy      TEXT NOT NULL,
    symbol        TEXT NOT NULL DEFAULT 'XAUUSD',
    timeframe     TEXT NOT NULL DEFAULT 'M5',
    from_date     DATE NOT NULL,
    to_date       DATE NOT NULL,
    params        JSONB NOT NULL DEFAULT '{}',   -- strategy-specific overrides
    total_trades  INT,
    winners       INT,
    win_rate      FLOAT,                         -- winners / total_trades
    net_pnl       FLOAT,
    max_drawdown  FLOAT,
    profit_factor FLOAT,
    sharpe        FLOAT,
    trade_log     JSONB,                         -- full trade list from the strategy
    run_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bt_strategy_run_at ON backtest_runs (strategy, run_at DESC);


-- -------------------------------------------------------------------------
-- strategy_snapshots
-- Indicator state captured after each strategy evaluation (EMA/RSI values,
-- OB/FVG zones, pivots). The dashboard overlays these on the candle chart.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    strategy    TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,            -- candle timestamp of the snapshot
    indicators  JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snaps_strategy_ts ON strategy_snapshots (strategy, ts DESC);
-- Prune rows older than 7 days (kept lean — only used for live chart overlays)
CREATE INDEX IF NOT EXISTS idx_snaps_ts ON strategy_snapshots (ts);
