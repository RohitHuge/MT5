"""
Full-pipeline setup state-machine tests (Phase 3 P3.15–P3.17).

Fixture layout
==============

M15 timeline — 15-min steps from BASE_M15 = 2025-01-06 08:00 UTC
  idx  0–9  : flat at 1000.0
  idx 10    : swing HIGH at 1010.0  (H=1010, O=C=L=1000)
  idx 11    : flat
  idx 12    : flat  ← pivot confirmed here (left=2, right=2)
  idx 13    : BSL sweep candle (H=1010.17, C=999.80, back inside zone)
  idx 14+   : flat at 1000.0 (padding)

  For SSL/BUY fixture (mirrored):
  idx 10    : swing LOW at 990.0   (L=990, O=C=H=1000)
  idx 13    : SSL sweep candle (L=989.83, C=1000.20, back inside)

M5 timeline — 5-min steps from BASE_M5 = 2025-01-06 08:00 UTC
  M15 idx 13 opens at 11:15 UTC (= M5 idx 39) and CLOSES at 11:30 UTC (= M5 idx 42).
  First M5 bar eligible for MSS search = M5 idx 42 (timestamp >= M15 close time).
  M5 candles at idx 39-41 (11:15-11:25 UTC) overlap the still-open M15 sweep candle.

  For SELL setup (bearish MSS + retest):
  idx  0–9  : flat at 1000.0
  idx  5    : swing LOW  at 995.0 (L=995, O=C=H=1000) → confirmed at idx 7
  idx 10–41 : flat at 1000.0
  idx 42    : MSS break — close=994.50 (< 995.0 swing low)  [first bar after M15 close]
  idx 43    : flat at 994.0
  idx 44    : retest — H=995.02 (into band), C=994.60 (< 995.0 → bearish rejection)

  For BUY setup (bullish MSS + retest):
  idx  5    : swing HIGH at 1005.0 (H=1005) → confirmed at idx 7
  idx 42    : MSS break — close=1005.80 (> 1005.0 swing high)
  idx 44    : retest — L=1004.98, C=1005.40 (> 1005.0 → bullish rejection)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mt5_liquidity.config import BacktestConfig
from mt5_liquidity.data.candles import Candle
from mt5_liquidity.strategy.liquidity_zones import build_zones
from mt5_liquidity.strategy.models import ZoneStatus, ZoneType
from mt5_liquidity.strategy.setup import SetupState, run_pipeline
from mt5_liquidity.strategy.swings import detect_pivots

BASE_M15 = datetime(2025, 1, 6, 8, 0, tzinfo=timezone.utc)
BASE_M5  = datetime(2025, 1, 6, 8, 0, tzinfo=timezone.utc)
POINT = 0.01


def _m15(i: int) -> datetime:
    return BASE_M15 + i * timedelta(minutes=15)


def _m5(i: int) -> datetime:
    return BASE_M5 + i * timedelta(minutes=5)


def _c15(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=_m15(i), open=o, high=h, low=l, close=c, timeframe="M15")


def _c5(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=_m5(i), open=o, high=h, low=l, close=c, timeframe="M5")


def _flat15(n: int, price: float = 1000.0) -> list[Candle]:
    return [_c15(i, price, price, price, price) for i in range(n)]


def _flat5(n: int, price: float = 1000.0) -> list[Candle]:
    return [_c5(i, price, price, price, price) for i in range(n)]


def _default_config(**overrides) -> BacktestConfig:
    cfg = BacktestConfig(
        pivot_left=2,
        pivot_right=2,
        zone_tolerance_points=10,
        sweep_min_points=5,
        mss_timeout_bars=30,
        retest_timeout_bars=15,
        retest_band_points=5,
        sl_buffer_points=5,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _build_sell_fixtures():
    """
    Returns (m15_candles, m5_candles, m5_pivots, zones, config).
    Full SELL setup: BSL sweep → bearish MSS → bearish retest → ENTRY_READY.
    """
    cfg = _default_config()
    # M15
    m15 = _flat15(20)
    m15[10] = _c15(10, 1000, 1010, 1000, 1000)   # swing high
    # idx 11, 12 flat (pivot confirmed at 12)
    m15[13] = _c15(13, 1000, 1010.17, 999.70, 999.80)  # BSL sweep

    m15_pivots = detect_pivots(m15, cfg.pivot_left, cfg.pivot_right)
    zones = build_zones(m15, m15_pivots, cfg.zone_tolerance_points, POINT)

    # M5 — 60 candles total; M15 idx 13 opens at M5 idx 39 (11:15), closes at M5 idx 42 (11:30)
    m5 = _flat5(60)
    m5[5]  = _c5(5,  1000, 1000, 995, 1000)   # swing low; confirmed at idx 7
    # MSS: first valid M5 after M15 idx-13 close (11:30 UTC) = M5 idx 42
    m5[42] = _c5(42, 999,  999,  994, 994.5)   # close < 995.0 → bearish MSS
    # Retest: bar 44 trades into band [994.95, 995.05], close below 995.0
    m5[44] = _c5(44, 994.5, 995.02, 994.3, 994.60)

    m5_pivots = detect_pivots(m5, cfg.pivot_left, cfg.pivot_right)
    return m15, m5, m5_pivots, zones, cfg


def _build_buy_fixtures():
    """
    Returns (m15_candles, m5_candles, m5_pivots, zones, config).
    Full BUY setup: SSL sweep → bullish MSS → bullish retest → ENTRY_READY.
    """
    cfg = _default_config()
    # M15
    m15 = _flat15(20)
    m15[10] = _c15(10, 1000, 1000, 990, 1000)   # swing low
    m15[13] = _c15(13, 1000, 1000.30, 989.83, 1000.20)  # SSL sweep

    m15_pivots = detect_pivots(m15, cfg.pivot_left, cfg.pivot_right)
    zones = build_zones(m15, m15_pivots, cfg.zone_tolerance_points, POINT)

    # M5
    m5 = _flat5(60)
    m5[5]  = _c5(5,  1000, 1005, 1000, 1000)   # swing high; confirmed at idx 7
    # MSS: first valid M5 after M15 idx-13 close (11:30 UTC) = M5 idx 42
    m5[42] = _c5(42, 1001, 1006, 1000.5, 1005.80)
    # Retest: bar 44 dips into band [1004.95, 1005.05], close above 1005.0
    m5[44] = _c5(44, 1005.5, 1005.6, 1004.98, 1005.40)

    m5_pivots = detect_pivots(m5, cfg.pivot_left, cfg.pivot_right)
    return m15, m5, m5_pivots, zones, cfg


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_valid_sell_setup_full_pipeline():
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)

    sell_setups = [s for s in setups if s.direction == "SELL" and s.state == SetupState.ENTRY_READY]
    assert len(sell_setups) >= 1, f"Expected ENTRY_READY SELL; got: {[(s.direction, s.state) for s in setups]}"
    s = sell_setups[0]
    assert s.mss is not None
    assert s.mss.direction == "BEARISH"
    assert s.retest is not None
    assert s.retest.direction == "BEARISH"
    assert s.signal_price == pytest.approx(994.60)


def test_valid_buy_setup_full_pipeline():
    m15, m5, m5_piv, zones, cfg = _build_buy_fixtures()
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)

    buy_setups = [s for s in setups if s.direction == "BUY" and s.state == SetupState.ENTRY_READY]
    assert len(buy_setups) >= 1, f"Expected ENTRY_READY BUY; got: {[(s.direction, s.state) for s in setups]}"
    s = buy_setups[0]
    assert s.mss.direction == "BULLISH"
    assert s.retest.direction == "BULLISH"
    assert s.signal_price == pytest.approx(1005.40)


def test_setup_expires_no_mss():
    """BSL sweep confirmed, but no bearish MSS within timeout → EXPIRED."""
    cfg = _default_config(mss_timeout_bars=5)  # tight timeout
    m15 = _flat15(20)
    m15[10] = _c15(10, 1000, 1010, 1000, 1000)
    m15[13] = _c15(13, 1000, 1010.17, 999.70, 999.80)

    m15_pivots = detect_pivots(m15, cfg.pivot_left, cfg.pivot_right)
    zones = build_zones(m15, m15_pivots, cfg.zone_tolerance_points, POINT)

    # M5: 60 flat candles — price never breaks below any swing low
    m5 = _flat5(60)
    m5[5] = _c5(5, 1000, 1000, 995, 1000)   # swing low at 995 confirmed at 7
    # No candle closes below 995 within 5 bars of M5 idx 42 (first bar after M15 close)
    m5_pivots = detect_pivots(m5, cfg.pivot_left, cfg.pivot_right)

    setups = run_pipeline(m15, m5, m5_pivots, zones, cfg, POINT)
    expired = [s for s in setups if s.state == SetupState.EXPIRED]
    assert len(expired) >= 1


def test_setup_missed_no_retest():
    """MSS confirmed, but price never retests the level within timeout → MISSED."""
    cfg = _default_config(retest_timeout_bars=3)  # tight
    m15, m5, m5_piv, zones, cfg2 = _build_sell_fixtures()
    # Override tight retest timeout
    cfg2.retest_timeout_bars = 3

    setups = run_pipeline(m15, m5, m5_piv, zones, cfg2, POINT)
    missed = [s for s in setups if s.state == SetupState.MISSED]
    # With timeout=3, bars 43-45 include idx 44 (the retest bar) → retest found, not MISSED.
    # Use timeout=1 (only searches idx 43) to truly miss the retest at idx 44.
    cfg2.retest_timeout_bars = 1
    setups2 = run_pipeline(m15, m5, m5_piv, zones, cfg2, POINT)
    missed2 = [s for s in setups2 if s.state == SetupState.MISSED]
    assert len(missed2) >= 1


def test_one_zone_one_setup():
    """Same zone cannot produce two Setups."""
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    zone_ids = [s.sweep.zone_id for s in setups]
    assert len(zone_ids) == len(set(zone_ids)), "Duplicate zone_id in setups"


def test_no_look_ahead_in_pipeline():
    """Pivot used for bearish MSS must have been confirmed BEFORE the break candle."""
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    entry_setups = [s for s in setups if s.state == SetupState.ENTRY_READY]
    for s in entry_setups:
        assert s.mss.broken_pivot_confirmed_at < s.mss.break_candle_index, (
            f"Pivot confirmed_at={s.mss.broken_pivot_confirmed_at} "
            f">= break_candle={s.mss.break_candle_index}: look-ahead bias!"
        )


def test_all_states_recorded_in_events():
    """Events list must contain an entry for each state transition up to terminal state."""
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    entry_setups = [s for s in setups if s.state == SetupState.ENTRY_READY]
    assert entry_setups, "Need at least one ENTRY_READY setup"
    s = entry_setups[0]
    state_sequence = [e.state for e in s.events]
    assert SetupState.SWEEP_CONFIRMED in state_sequence
    assert SetupState.MSS_CONFIRMED in state_sequence
    assert SetupState.RETEST_PENDING in state_sequence
    assert SetupState.ENTRY_READY in state_sequence


def test_sl_above_sweep_high_for_sell():
    """SELL SL = sweep wick_extreme + sl_buffer_points * point."""
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    entry = [s for s in setups if s.state == SetupState.ENTRY_READY and s.direction == "SELL"]
    assert entry
    s = entry[0]
    expected_sl = s.sweep.wick_extreme + cfg.sl_buffer_points * POINT
    assert s.stop_loss_price == pytest.approx(expected_sl)


def test_sl_below_sweep_low_for_buy():
    """BUY SL = sweep wick_extreme - sl_buffer_points * point."""
    m15, m5, m5_piv, zones, cfg = _build_buy_fixtures()
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    entry = [s for s in setups if s.state == SetupState.ENTRY_READY and s.direction == "BUY"]
    assert entry
    s = entry[0]
    expected_sl = s.sweep.wick_extreme - cfg.sl_buffer_points * POINT
    assert s.stop_loss_price == pytest.approx(expected_sl)


# ── F4: run_pipeline idempotency / zone mutation tests ────────────────────────

def test_run_pipeline_idempotent_same_setups():
    """Calling run_pipeline twice with the same zones returns identical setups."""
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    setups1 = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    setups2 = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    assert len(setups1) == len(setups2)
    for s1, s2 in zip(setups1, setups2):
        assert s1.state == s2.state
        assert s1.direction == s2.direction
        assert s1.sweep.zone_id == s2.sweep.zone_id


def test_run_pipeline_does_not_mutate_caller_zones():
    """run_pipeline deep-copies zones; caller's zone objects are unchanged after call."""
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    original_statuses = {z.zone_id: z.status for z in zones}
    original_event_counts = {z.zone_id: len(z.events) for z in zones}

    run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)

    for z in zones:
        assert z.status == original_statuses[z.zone_id], (
            f"Zone {z.zone_id} status mutated: was {original_statuses[z.zone_id]}, now {z.status}"
        )
        assert len(z.events) == original_event_counts[z.zone_id], (
            f"Zone {z.zone_id} events mutated by run_pipeline"
        )


def test_run_pipeline_does_not_mutate_active_zones():
    """Active zones (not swept by build_zones) remain ACTIVE in the original list after run_pipeline."""
    cfg = _default_config()
    # Build zones from M15 data that does NOT include the sweep candle.
    m15_no_sweep = _flat15(13)   # only idx 0-12; sweep would be at idx 13
    m15_no_sweep[10] = _c15(10, 1000, 1010, 1000, 1000)
    m15_pivots = detect_pivots(m15_no_sweep, cfg.pivot_left, cfg.pivot_right)
    zones = build_zones(m15_no_sweep, m15_pivots, cfg.zone_tolerance_points, POINT)

    active_zones = [z for z in zones if z.status == ZoneStatus.ACTIVE]
    assert active_zones, "Need at least one ACTIVE zone for this test"

    # Full M15 candles include the sweep — detect_sweeps would mutate without deep copy.
    m15_full = _flat15(20)
    m15_full[10] = _c15(10, 1000, 1010, 1000, 1000)
    m15_full[13] = _c15(13, 1000, 1010.17, 999.70, 999.80)
    m5 = _flat5(60)
    m5[5]  = _c5(5,  1000, 1000, 995, 1000)
    m5[42] = _c5(42, 999, 999, 994, 994.5)
    m5[44] = _c5(44, 994.5, 995.02, 994.3, 994.60)
    m5_piv = detect_pivots(m5, cfg.pivot_left, cfg.pivot_right)

    run_pipeline(m15_full, m5, m5_piv, zones, cfg, POINT)

    for z in active_zones:
        assert z.status == ZoneStatus.ACTIVE, (
            f"Zone {z.zone_id} was mutated to {z.status} by run_pipeline (deep copy missing)"
        )


# ── F3: M15/M5 alignment tests ────────────────────────────────────────────────

def test_m5_inside_m15_window_excluded_from_mss():
    """
    M5 candles whose timestamps fall within the M15 sweep candle's window must not
    trigger MSS even if they qualify by price — they overlap an unfinished M15 bar.

    M15 idx 13 opens at 11:15, closes at 11:30.
    M5 idx 40 (11:20) and idx 41 (11:25) are inside the window → excluded.
    M5 idx 42 (11:30) is the first valid bar.
    """
    cfg = _default_config(mss_timeout_bars=5)
    m15 = _flat15(20)
    m15[10] = _c15(10, 1000, 1010, 1000, 1000)
    m15[13] = _c15(13, 1000, 1010.17, 999.70, 999.80)

    m15_pivots = detect_pivots(m15, cfg.pivot_left, cfg.pivot_right)
    zones = build_zones(m15, m15_pivots, cfg.zone_tolerance_points, POINT)

    m5 = _flat5(60)
    m5[5]  = _c5(5,  1000, 1000, 995, 1000)   # swing low at 995
    # Bearish MSS candles placed INSIDE the M15 sweep window — must be ignored
    m5[40] = _c5(40, 999, 999, 994, 994.0)    # 11:20 — inside M15 window (11:15-11:30)
    m5[41] = _c5(41, 994, 994, 993, 993.5)    # 11:25 — inside M15 window
    # No valid MSS candle after 11:30 within the tight timeout

    m5_piv = detect_pivots(m5, cfg.pivot_left, cfg.pivot_right)
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)

    entry_ready = [s for s in setups if s.state == SetupState.ENTRY_READY]
    assert len(entry_ready) == 0, (
        "No ENTRY_READY setup expected: MSS candles were inside M15 sweep window"
    )


def test_m5_at_m15_close_time_included_in_mss():
    """
    M5 candle whose timestamp equals M15 close time (m15_open + 15 min) IS eligible.
    """
    cfg = _default_config()
    m15, m5, m5_piv, zones, cfg = _build_sell_fixtures()
    # The fixture places MSS at M5 idx 42 (timestamp = 11:30 = M15 close time).
    setups = run_pipeline(m15, m5, m5_piv, zones, cfg, POINT)
    entry_ready = [s for s in setups if s.state == SetupState.ENTRY_READY]
    assert len(entry_ready) >= 1, "M5 at exact M15 close time must be eligible for MSS"
    assert entry_ready[0].mss.break_candle_index == 42
