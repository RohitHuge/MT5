# Phase 4 Test Results — Backtest Engine, Fills, Risk Sizing, Trade Ledger

**Date:** 2026-11-10  
**Total tests:** 142 (109 baseline from Phases 1–3 + 33 new Phase 4 tests)  
**Result:** ALL PASS ✓

---

## New Files Created

| File | Purpose |
|------|---------|
| `src/mt5_liquidity/risk/__init__.py` | Risk package marker |
| `src/mt5_liquidity/risk/position_sizing.py` | Lot sizing from risk % |
| `src/mt5_liquidity/backtest/__init__.py` | Backtest package marker |
| `src/mt5_liquidity/backtest/execution.py` | Fill simulation (next-bar fill) |
| `src/mt5_liquidity/backtest/positions.py` | Open position bar-by-bar resolution |
| `src/mt5_liquidity/backtest/ledger.py` | Closed trade collector + statistics |
| `src/mt5_liquidity/backtest/engine.py` | Full backtest orchestrator |
| `src/mt5_liquidity/reporting/metrics.py` | Console report printer |

## Files Updated

| File | Change |
|------|--------|
| `src/mt5_liquidity/config.py` | Added `max_daily_loss_pct=3.0`, `initial_equity=10_000.0` |
| `src/mt5_liquidity/cli.py` | Added `backtest` subcommand |

---

## Phase 4 Test Coverage (33 tests)

### `test_position_sizing.py` — 6 tests

| Test | Scenario | Result |
|------|----------|--------|
| `test_typical_buy_sizing` | XAUUSD 10-pt SL, $50 risk → 0.05 lots | PASS |
| `test_typical_sell_sizing` | Same distance from SELL side | PASS |
| `test_round_down_to_volume_step` | 0.0556 lots → floor to 0.05 | PASS |
| `test_reject_below_volume_min` | 200-pt SL → 0.0025 lots < 0.01 min | PASS |
| `test_reject_zero_sl_distance` | signal == stop_loss | PASS |
| `test_cap_at_volume_max` | Huge equity / tiny SL → capped at 100 lots | PASS |

### `test_execution.py` — 6 tests

| Test | Scenario | Result |
|------|----------|--------|
| `test_buy_fill_price` | BUY = open + (spread+slip)*point | PASS |
| `test_sell_fill_price` | SELL = open - slip*point | PASS |
| `test_buy_tp_placement` | TP = fill + SL_dist * rr_target | PASS |
| `test_sell_tp_placement` | TP = fill - SL_dist * rr_target | PASS |
| `test_fill_uses_next_candle` | Fill at signal_idx+1 | PASS |
| `test_returns_none_when_no_next_bar` | Last candle signal → None | PASS |

### `test_positions.py` — 10 tests

| Test | Scenario | Result |
|------|----------|--------|
| `test_buy_tp_hit` | High touches TP → HIT_TP | PASS |
| `test_buy_sl_hit` | Low below SL → HIT_SL | PASS |
| `test_buy_ambiguous_bar_sl_wins` | Same bar hits both → SL (conservative) | PASS |
| `test_buy_gap_through_sl` | Opens below SL → fill at open | PASS |
| `test_buy_still_open` | No touch → OPEN | PASS |
| `test_buy_pnl_on_tp` | P&L = (price_diff/point) * vpp * lots | PASS |
| `test_sell_tp_hit` | Low touches TP → HIT_TP | PASS |
| `test_sell_sl_hit` | High above SL → HIT_SL | PASS |
| `test_sell_gap_through_sl` | Opens above SL → fill at open | PASS |
| `test_sell_pnl_on_sl_loss` | -$10 loss on 10-pt SL hit | PASS |
| `test_bars_held` | bars_held = close_idx - fill_idx | PASS |

### `test_ledger.py` — 8 tests

| Test | Scenario | Result |
|------|----------|--------|
| `test_empty_ledger` | Zero trades → zeroed stats | PASS |
| `test_single_win` | 1 winner → PF = inf | PASS |
| `test_single_loss` | 1 loser → PF = 0 | PASS |
| `test_profit_factor` | 2W+1L → PF = 3.0 | PASS |
| `test_max_drawdown` | Peak-to-trough $300 on $10,200 peak | PASS |
| `test_final_equity` | Equity update after trades | PASS |
| `test_win_rate` | 3/5 → 60% | PASS |
| `test_avg_win_avg_loss` | Average of wins/losses separately | PASS |

### `test_engine.py` — 6 tests

| Test | Scenario | Result |
|------|----------|--------|
| `test_no_setups_returns_empty` | No signals → 0 trades | PASS |
| `test_sell_tp_hit` | SELL setup → TP hit → net profit | PASS |
| `test_buy_sl_hit` | BUY setup → SL hit → net loss | PASS |
| `test_max_positions_blocks_second_entry` | 1 position already open → blocks 2nd | PASS |
| `test_daily_loss_limit_blocks_entry` | Loss exceeds daily % cap → blocks next entry | PASS |
| `test_force_close_at_end_of_data` | No close bar → force-close at last candle | PASS |

---

## Key Design Decisions

### Fill model (bid-based M5 candles)
- **BUY** fill = `next_candle.open + (spread_points + slippage_points) * point`
- **SELL** fill = `next_candle.open - slippage_points * point`
- Spread already factored into BUY; SELL fills at bid minus slippage.

### P&L formula
```
pnl_usd = (price_diff / point) * value_per_point_per_lot * lots
value_per_point_per_lot = (tick_value / tick_size) * point
```
For XAUUSD: `(1.0 / 0.01) * 0.01 = 1.0 $/point/lot`  
A $10 move on 1 lot = `(10 / 0.01) * 1.0 * 1.0 = $1,000`

### Conservative ambiguity rule
When a single candle hits both SL and TP (high ≥ TP and low ≤ SL for a BUY), the SL is assumed to have been hit first. This is worst-case realistic.

### Gap handling
If `candle.open` already beyond SL (gap through), position closes at `candle.open` — the actual fill a broker would give.

### Swap
Swap is excluded from Phase 4. `cost_note = "swap excluded (Phase 4)"` is attached to every FillResult. Phase 5+ can add swap tables.

### No MT5 / no order_send
No `import MetaTrader5` anywhere in `risk/` or `backtest/`. Verified by grep:
```
grep -r "MetaTrader5\|order_send" src/mt5_liquidity/risk/ src/mt5_liquidity/backtest/
# → 0 results
```

---

## CLI Usage

```bash
# Run backtest on cached data
python -m mt5_liquidity backtest --from 2025-01-01 --to 2025-06-30

# Custom risk parameters
python -m mt5_liquidity backtest --from 2025-01-01 --to 2025-06-30 \
  --risk-pct 1.0 --rr-target 2.0 --max-daily-loss-pct 3.0 --equity 10000
```

---

## Baseline Regression

All 109 Phase 1–3 tests continue to pass. No regressions introduced.
