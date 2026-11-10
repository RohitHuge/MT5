# Phase 4.1 — Backtesting Accuracy & Risk Corrections

## Scope
This change set improves Phase 4 accounting and execution-model transparency. It does not change the Phase 3 strategy, introduce live order execution, or claim profitability.

## Changes
- Added `trading_day_timezone` configuration (IANA timezone; defaults to UTC). Daily risk baselines use each candle's date after conversion to that timezone. Invalid zones and naive candle timestamps fail fast.
- Daily floating equity now subtracts estimated round-trip commission for open positions. New position sizing uses the same current net marked equity. Closed trades continue to deduct commission once, at close.
- Added separate gross and net profit factor metrics. The existing `profit_factor` remains a backwards-compatible alias for gross profit factor. The console report displays both.
- Added CLI flags for commission, trading-day timezone, and the six symbol metadata values. Manual metadata is accepted only when the full set is supplied; otherwise metadata is retrieved from MT5. Partial metadata is rejected.
- Documented OHLC intrabar uncertainty and conservative SL-first handling.
- Added regression coverage for net profit factor, timezone validation, and ambiguous entry-candle outcomes.

## Execution assumptions and limitations
- Candle OHLC does not encode the sequence of intrabar high and low. If SL and TP are both touched in one bar, SL-first is used as a conservative assumption. This is not tick-level reconstruction.
- Trading-day baseline is realised equity observed at the first candle in each configured local trading date. Positions carried over midnight remain open; their marked P&L and estimated round-trip commission are compared with the new day's baseline.
- Symbol metadata validation checks numeric plausibility but cannot independently verify broker provenance or account-currency conversion. Manual metadata must match the selected broker symbol and account currency.
- Swap, variable spread, variable commission schedules, partial fills, latency, and tick-level sequencing remain outside this phase.

## Validation status
Repository changes and regression tests were committed to a feature branch. The GitHub connector used for this change can read and write repository content but does not provide a Python shell to execute pytest locally. No test result is claimed here. Run the full suite in CI or locally before merging:

```powershell
cd backtesting
python -m pip install -e ".[dev]"
python -m pytest tests/unit -q
```

Run MT5 integration tests separately on a machine with a configured MT5 terminal and demo account. Do not use live trading credentials or enable order execution for these tests.
