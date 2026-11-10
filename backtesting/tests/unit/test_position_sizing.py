"""Unit tests for position sizing (risk/position_sizing.py)."""
from __future__ import annotations

import pytest

from mt5_liquidity.risk.position_sizing import calculate_position_size

# XAUUSD typical values
XAUUSD = dict(
    tick_value=1.0,
    tick_size=0.01,
    point=0.01,
    volume_min=0.01,
    volume_max=100.0,
    volume_step=0.01,
)


def _size(signal_price, sl_price, equity=10_000.0, risk_pct=0.5, **overrides):
    kwargs = {**XAUUSD, **overrides}
    return calculate_position_size(
        signal_price=signal_price,
        stop_loss_price=sl_price,
        equity=equity,
        risk_pct=risk_pct,
        **kwargs,
    )


def test_typical_buy_sizing():
    # signal=2000, sl=1990 → SL distance = 10 points = 1000 pts
    # risk = 10000 * 0.5% = $50
    # value_per_point_per_lot = (1.0/0.01)*0.01 = 1.0
    # risk_per_lot = 1000 * 1.0 = $1000
    # lots_raw = 50 / 1000 = 0.05 → floor to volume_step 0.01 → 0.05
    result = _size(signal_price=2000.0, sl_price=1990.0)
    assert result.rejection_reason is None
    assert result.lots == pytest.approx(0.05)
    assert result.risk_amount == pytest.approx(50.0)
    assert result.sl_points == pytest.approx(1000.0)


def test_typical_sell_sizing():
    # signal=2000, sl=2010 → same distance, should produce same lots
    result = _size(signal_price=2000.0, sl_price=2010.0)
    assert result.rejection_reason is None
    assert result.lots == pytest.approx(0.05)


def test_round_down_to_volume_step():
    # signal=2000, sl=1991 → 900 pts, risk=50 → lots_raw = 50/(900*1) ≈ 0.0556 → floor to 0.05
    result = _size(signal_price=2000.0, sl_price=1991.0)
    assert result.rejection_reason is None
    assert result.lots == pytest.approx(0.05)


def test_reject_below_volume_min():
    # signal=2000, sl=1800 → 20000 pts, risk=50 → lots_raw = 50/20000 = 0.0025 → below 0.01
    result = _size(signal_price=2000.0, sl_price=1800.0)
    assert result.rejection_reason is not None
    assert result.lots == 0.0
    assert "volume_min" in result.rejection_reason


def test_reject_zero_sl_distance():
    result = _size(signal_price=2000.0, sl_price=2000.0)
    assert result.rejection_reason is not None
    assert result.lots == 0.0


def test_cap_at_volume_max():
    # Huge equity, tiny SL → lots_raw > volume_max
    result = _size(signal_price=2000.0, sl_price=1999.99, equity=10_000_000.0, risk_pct=5.0)
    assert result.rejection_reason is None
    assert result.lots <= XAUUSD["volume_max"]
