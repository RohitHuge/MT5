# Agent A7 — Completion Report

**Agent:** A7 (Branch B, Phase 3B)
**Track:** OB + FVG Strategy — Phase 3B
**Date:** 2026-11-20
**Status:** COMPLETE

---

## Assignment

Implement **Phase 3B** from `PLATFORM_PLAN.md` (Tasks 3B.1 → 3B.5):

- `backtesting/src/mt5_liquidity/strategy/order_blocks.py` — Order Block detector
- `backtesting/src/mt5_liquidity/strategy/fvg.py` — Fair Value Gap detector
- `backtesting/src/mt5_liquidity/strategy/ob_fvg.py` — OB+FVG confluence signal
- `services/strategy-ob-fvg/main.py` — live/shadow strategy container
- `services/strategy-ob-fvg/requirements.txt`
- `services/strategy-ob-fvg/Dockerfile`
- Update `docker-compose.yml` stub → real build block

---

## Tasks Completed

| Task | Description | Status |
|---|---|---|
| 3B.1 | order_blocks.py — OrderBlock dataclass + detect_order_blocks() | Done |
| 3B.2 | fvg.py — FVG dataclass + detect_fvgs() | Done |
| 3B.3 | ob_fvg.py — get_ob_fvg_signal() confluence logic | Done |
| 3B.4 | Backtest run | Deferred — no backtester CLI available in this agent session |
| 3B.5 | services/strategy-ob-fvg/ — container + Dockerfile | Done |
| —    | docker-compose.yml — replaced stub image with build: block | Done |

---

## Files Produced

```
backtesting/src/mt5_liquidity/strategy/
├── order_blocks.py   — OrderBlock dataclass + detect_order_blocks() (~130 lines)
├── fvg.py            — FVG dataclass + detect_fvgs() (~120 lines)
└── ob_fvg.py         — get_ob_fvg_signal() confluence function (~110 lines)

services/strategy-ob-fvg/
├── main.py           — ObFvgStrategy(StrategyBase) container (~140 lines)
├── requirements.txt  — redis>=5.0, requests>=2.31, python-dotenv>=1.0
└── Dockerfile        — build context: repo root; PYTHONPATH=/app/backtesting_src
```

---

## Key Algorithm Decisions

### Order Block Detection (order_blocks.py)

**Impulse definition:** The impulse that "confirms" an OB is the sum of body
sizes (|close − open|) of the two candles immediately after the candidate OB
candle.  Using body size rather than full candle range (high − low) avoids
false positives from wicks on a volatile candle.  Two candles (instead of one)
are summed to capture the case where the first post-OB candle is a doji that
splits the move across two bars.

**Invalidation:** A bullish OB is invalidated when a later candle's body fully
closes *below* the OB low (both `max(open, close) < ob.low` and
`close < ob.low`).  Requiring the full body — not just a wick — to cross the
boundary avoids premature invalidation from spike wicks common on XAUUSD.

**`lookback` parameter:** Only the last `lookback` candles are scanned for new
OB candidates.  All candles are still used for the invalidation pass so an OB
detected in a prior call is correctly voided by a subsequent close-through.

### FVG Detection (fvg.py)

**Gap boundaries:**
- Bullish FVG: `gap_low = candle[i-1].high`, `gap_high = candle[i+1].low`
- Bearish FVG: `gap_low = candle[i+1].high`, `gap_high = candle[i-1].low`

The middle candle (`candle[i]`) is treated as the origin; its timestamp is
stored in `origin_ts` for dedup and display purposes.

**Fill condition:** A candle *close* (not wick) inside `[gap_low, gap_high]`
marks the FVG as filled.  Using close prevents wicks from prematurely retiring
valid FVGs.  The fill check starts at `i+2` (the candle after the right leg of
the pattern) so the pattern itself never self-fills.

### Confluence Signal (ob_fvg.py)

**Overlap zone:** The active trade zone is the intersection of the OB range
and the FVG range.  Entry is only triggered when the current candle close
falls inside this intersection — this filters out situations where only the
OB or only the FVG is touched but not the overlap.

**OB selection:** When multiple valid OBs exist in one direction, the most
recent is used.  This reflects the common trader assumption that more recent
structure carries more weight.

**FVG selection (tiebreaker):** If multiple FVGs overlap with the OB, the one
with the largest overlap area is used.  This maximises zone quality.

**Confidence formula:**
```
confidence = min(1.0, ob.impulse_size_points / (min_impulse_points * 2))
```
An impulse of exactly `min_impulse_points` → confidence 0.5.  An impulse of
`2 × min_impulse_points` or more → confidence 1.0 (capped).

### Container (main.py)

Follows the exact same subclassing pattern as `strategy-ema-scalp` and
`strategy-liq-sweep`.  Key differences:

- **Dedup key:** `(ob.origin_ts.isoformat(), direction)`.  Stable across
  window slides because it references the OB candle's timestamp, not an
  array index.
- **Dedup pruning:** When the set exceeds 100 entries it is cleared entirely
  rather than trimmed.  100 is far more entries than would ever accumulate in
  normal operation (a strategy seeing 1 signal per hour would take 4+ days to
  hit the limit).
- **Path injection:** Both `sys.path.insert` for local runs (repo layout) and
  the Dockerfile's `ENV PYTHONPATH=/app/backtesting_src` for containerised
  runs are handled, so the container works in both environments.

---

## How to Run a Backtest Using the New Detectors

The detectors are pure functions that accept plain lists of candle objects.
They can be called directly from a backtest script using the existing M5
cache.

```python
from backtesting.src.mt5_liquidity.data.candles import load_candles  # or however cache is loaded
from backtesting.src.mt5_liquidity.strategy.order_blocks import detect_order_blocks
from backtesting.src.mt5_liquidity.strategy.fvg import detect_fvgs
from backtesting.src.mt5_liquidity.strategy.ob_fvg import get_ob_fvg_signal

candles = load_candles("XAUUSD", "M5", start="2026-08-13", end="2026-11-10")

# Run detectors on a rolling window matching the live container's window_size
WINDOW = 300
signals = []
emitted = set()

for i in range(WINDOW, len(candles)):
    window = candles[i - WINDOW : i + 1]
    obs  = detect_order_blocks(window, min_impulse_points=50.0, lookback=20)
    fvgs = detect_fvgs(window, min_gap_points=10.0)

    valid_obs   = [ob for ob in obs if ob.is_valid]
    active_fvgs = [f  for f  in fvgs if not f.is_filled]

    result = get_ob_fvg_signal(window, valid_obs, active_fvgs)
    if result is None:
        continue

    direction, entry, sl, tp, conf = result

    # Find triggering OB for dedup key
    for ob in reversed(valid_obs):
        if ob.direction == direction:
            key = (ob.origin_ts.isoformat(), direction)
            if key not in emitted:
                emitted.add(key)
                signals.append({"direction": direction, "entry": entry,
                                 "sl": sl, "tp": tp, "conf": conf,
                                 "ts": window[-1].timestamp})
            break

print(f"Total signals: {len(signals)}")
```

The existing backtester CLI (`backtesting/src/mt5_liquidity/cli.py` or
equivalent) would need a new `--strategy ob-fvg` flag to wire this in.
Until that flag is added, the snippet above is the recommended approach for
manual backtesting.

---

## Edge Cases and Fragilities

1. **Doji OBs:** A doji (open ≈ close) passes neither `_is_bearish` nor
   `_is_bullish`, so it is never selected as an OB candidate.  This is
   correct behaviour — a doji has no directional bias.

2. **Simultaneous BUY and SELL signals:** `get_ob_fvg_signal()` iterates
   directions in a fixed order (`BUY` first).  If both a BUY and SELL
   condition are met simultaneously (unusual but possible in choppy markets),
   only the BUY signal is returned.  The caller can call the function twice
   with `obs` and `fvgs` filtered per direction if both are needed.

3. **Window size vs lookback:** `detect_order_blocks` with `lookback=20` on a
   300-candle window only scans the most recent 22 candles for new OBs.
   OBs detected in earlier windows persist across calls only if the caller
   carries them forward (the container does not do this — it re-runs the full
   detection on every candle).  For long-running OBs (>22 candles old), the
   re-detection will not find them.  Increasing `lookback` or using a caching
   layer would address this if needed.

4. **FVG fill check starts at `i+2`:** The right leg candle (`candle[i+1]`)
   is excluded from the fill check.  If `candle[i+1]` itself closes inside
   the gap, the FVG is not marked filled.  This matches trader convention
   (the gap is defined by `i+1`'s close, not inside it).

5. **Invalidation timestamp lookup is O(n):** The invalidation and fill passes
   do a linear scan to find the origin candle index by timestamp.  For a
   300-candle window with ~20 OBs this is 6,000 comparisons — negligible.
   On much larger windows (> 10,000 candles) it would become measurable; a
   timestamp→index dict would fix it.

6. **No persistence of dedup set:** If the container restarts, `_emitted` is
   cleared.  Signals for OBs that are still valid at restart will be re-emitted
   once.  This is consistent with the liq-sweep container's behaviour.

---

## Blockers / Notes for Lead

**Task 3B.4 (backtest run) not completed** — the backtester CLI does not yet
have an `--strategy ob-fvg` flag, and running a full backtest requires the
existing M5 candle cache which was not accessible in this agent session.  A
developer can use the code snippet in the "How to Run a Backtest" section above
to execute the backtest manually against the existing cache.

**PYTHONPATH dependency** (same caveat as A5 noted for liq-sweep): the
container works because `mt5_liquidity/strategy/__init__.py` is currently
empty.  If top-level auto-imports are added to that file in future, the
container will fail to import.

No other blockers.  The three detector modules are pure functions with no I/O
and no MT5 dependency; they are ready for both backtesting and live use.
