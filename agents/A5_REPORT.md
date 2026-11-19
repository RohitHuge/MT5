# Agent A5 — Completion Report

**Agent:** A5 (Branch A3)
**Track:** Strategy Containers — Phase 2C
**Date:** 2026-11-19
**Status:** COMPLETE

---

## Assignment

Implement **Phase 2C** from `PLATFORM_PLAN.md`:
- `shared/strategy_base.py` — base class all strategy containers inherit
- `services/strategy-ema-scalp/` — EMA 9/21/50 + RSI14 container
- `services/strategy-liq-sweep/` — Liquidity Sweep container
- Update `docker-compose.yml` with build paths

---

## Tasks Completed

| Task | Description | Status |
|---|---|---|
| 2C.1 | shared/strategy_base.py — StrategyBase with Redis, heartbeat, lot sizing | Done |
| 2C.2 | services/strategy-ema-scalp/ — main.py, requirements.txt, Dockerfile | Done |
| 2C.3 | services/strategy-liq-sweep/ — main.py, requirements.txt, Dockerfile | Done |
| — | docker-compose.yml — replaced stub images with build: paths | Done |

---

## Files Produced

```
shared/
└── strategy_base.py          — StrategyBase class (~150 lines)

services/strategy-ema-scalp/
├── main.py                   — EmaScalpStrategy + inlined signal logic (~140 lines)
├── requirements.txt          — redis, requests, python-dotenv
└── Dockerfile                — build context: repo root

services/strategy-liq-sweep/
├── main.py                   — LiquiditySweepStrategy + M15 aggregation (~150 lines)
├── requirements.txt          — redis, requests, python-dotenv
└── Dockerfile                — build context: repo root; PYTHONPATH=/app/backtesting_src
```

---

## Architecture Decisions

### 1. strategy_base.py — Blocking `pubsub.listen()` + daemon heartbeat thread

The main Redis subscription loop uses the blocking `pubsub.listen()` iterator.
This is the simplest correct approach — no asyncio required, the entire container
is I/O-bound waiting for candles.

The heartbeat runs in a `daemon=True` thread on a 10-second `time.sleep` loop.
Daemon threads are killed automatically when the main thread exits, so no extra
cleanup needed.

### 2. EMA Scalp — Signal logic inlined, not imported from backtesting

`backtesting/pyproject.toml` lists `MetaTrader5>=5.0.6231` as a required
dependency. `pip install -e /backtesting` would fail on a Linux Docker container
because the MT5 wheel doesn't build on Linux.

The `detect_scalp_signals` function is ~80 lines of pure arithmetic. Inlining
it into `main.py` avoids the packaging problem entirely and keeps the container
dependency-free (only `redis`, `requests`, `python-dotenv`).

The inlined version differs from the backtesting original in one way: it returns
the **last signal only** (as a plain tuple) rather than all signals in the window.
This is correct for live use — only the most recent crossover matters.

### 3. Liq Sweep — PYTHONPATH approach, not pip install

The liquidity sweep pipeline spans 7 files across the backtesting strategy
package. Rewriting or inlining all of them would create a maintenance burden.

Instead, the Dockerfile copies `backtesting/src/` to `/app/backtesting_src/`
and sets `ENV PYTHONPATH=/app/backtesting_src`. The import then works:

```python
from mt5_liquidity.strategy.liquidity_sweep import run_liquidity_sweep_strategy
```

This works because:
- `mt5_liquidity/__init__.py` only sets `__version__` — no auto-imports
- `mt5_liquidity/data/__init__.py` is empty
- `mt5_liquidity/strategy/__init__.py` is empty
- The strategy files only import from `.models`, `.sweeps`, `.retests`,
  `.market_structure`, `.setup` and `..data.candles` — none of which
  import `mt5_adapter` or `MetaTrader5`

### 4. Liq Sweep — Rolling window re-runs the full pipeline each candle

The backtesting pipeline is stateless — it takes a list of candles and returns
all setups. For live use, the strategy runs the full pipeline on the rolling
window every candle and uses a dedup set keyed by
`(sweep_candle_ts.isoformat(), direction)` to avoid re-emitting known setups.

This is correct: `setup_id` contains the candle index which shifts as the deque
drops old candles, making it unstable as a dedup key. The sweep timestamp +
direction combination is stable across window slides.

### 5. M15 Aggregation — Inlined, not from backtesting

`aggregate_m5_to_m15` in `backtesting/src/mt5_liquidity/data/candles.py` is
simple. Inlining a clean version into `main.py` avoids a cross-module dependency
on `bt_candles`. Only complete 3-candle windows become M15 candles — identical
semantics to the backtester.

### 6. Lot sizing — Live equity fetch per signal

`calc_lots()` in `StrategyBase` calls `GET /account` on the bridge every time a
signal fires to get current equity. This is correct for live trading — equity
changes as trades open and close. The overhead is one HTTP call per signal
(not per candle), so it's negligible.

### 7. Build context — Repo root for both Dockerfiles

Both strategy Dockerfiles need files from outside their own service directory
(`shared/` and for liq-sweep, `backtesting/src/`). The docker-compose build
context is the repo root (`.`), with `dockerfile:` pointing to the service
subdirectory. This is reflected in the updated `docker-compose.yml`.

---

## Interface: What Phase 3B (OB+FVG) Builder Needs to Know

### How to add a new strategy container

1. Create `services/strategy-{name}/`
2. Write `main.py` subclassing `StrategyBase`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.models import Candle, Signal
from shared.strategy_base import StrategyBase

class MyStrategy(StrategyBase):
    def __init__(self):
        super().__init__(
            name=os.getenv("STRATEGY_NAME", "my-strategy"),
            symbol="XAUUSD",
            timeframe="M5",
            window_size=200,   # how many M5 candles to keep in memory
        )
        # read your env vars here

    def on_candle(self, candles: list[Candle]) -> Signal | None:
        # your signal detection logic here
        # return Signal(...) or None
        pass

if __name__ == "__main__":
    MyStrategy().run()
```

3. `requirements.txt`: `redis>=5.0`, `requests>=2.31`, `python-dotenv>=1.0`
   (add others as needed; no MetaTrader5)

4. `Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY services/strategy-{name}/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/strategy-{name}/main.py .
CMD ["python", "main.py"]
```
If you need backtesting source: add `COPY backtesting/src/ /app/backtesting_src/`
and `ENV PYTHONPATH=/app/backtesting_src`.

5. Add to `docker-compose.yml` following the `strategy-ema-scalp` block as a template.

### Key StrategyBase methods available to subclasses

| Method | Description |
|---|---|
| `self.point` | Symbol point size (fetched on startup from bridge) |
| `self.tick_value` | Tick value USD per lot (fetched on startup) |
| `self.vol_min/max/step` | Volume limits (fetched on startup) |
| `self.mode` | "live" / "shadow" / "off" |
| `self.symbol` | Trading symbol |
| `self.fetch_equity()` | GET /account → float equity |
| `self.calc_lots(equity, risk_pct, sl_distance)` | Returns rounded lots |

### Signal field notes

- `entry_price`: best estimate of fill price (last candle close for market orders)
- `sl_price`: absolute price, not points
- `tp_price`: absolute price computed by strategy using `rr_target`
- `lots`: use `self.calc_lots()` — it handles vol_min/step clamping
- `confidence`: 0.0–1.0, strategy-defined; used for display only, not execution
- `mode`: set by `StrategyBase.run()` before calling `_publish_signal()`; don't set it in `on_candle()`

---

## Blockers / Notes for Lead

**One thing to verify**: the `strategy-liq-sweep` container's PYTHONPATH approach
depends on `mt5_liquidity/data/__init__.py` and `mt5_liquidity/strategy/__init__.py`
remaining empty (not auto-importing from `mt5_adapter`). If those files gain
top-level imports in future, the container will fail to start with an ImportError.
This is documented in the Dockerfile as a comment.

No other blockers. Phase 2A (candle-feed) must be running before either strategy
container can receive candles.
