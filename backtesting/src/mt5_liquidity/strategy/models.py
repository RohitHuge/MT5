"""Shared domain models for the strategy layer. No MT5 imports here."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class PivotType(Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class ZoneType(Enum):
    BSL = "BSL"   # buy-side liquidity — around swing highs
    SSL = "SSL"   # sell-side liquidity — around swing lows


class ZoneStatus(Enum):
    ACTIVE = "ACTIVE"
    SWEPT = "SWEPT"            # wick pierced zone, candle closed back inside (Phase 3 signal)
    CONSUMED = "CONSUMED"      # fully consumed by Phase 3 trade entry
    INVALIDATED = "INVALIDATED"  # level broken (close beyond boundary) without sweep
    EXPIRED = "EXPIRED"        # too many bars with no interaction


@dataclass
class SweepEvidence:
    """Evidence that a valid liquidity sweep occurred on this zone."""
    candle_index: int
    candle_timestamp: datetime
    wick_extreme: float   # high that pierced zone.upper (BSL) or low that pierced zone.lower (SSL)
    close_price: float    # closed back inside zone boundary
    zone_id: str


@dataclass
class Pivot:
    index: int               # candle index in the series
    confirmed_at_index: int  # index + right lookback — first usable by strategy
    timestamp: datetime      # candle's own timestamp
    confirmed_at: datetime   # timestamp of the candle at confirmed_at_index
    pivot_type: PivotType
    price: float             # high for HIGH, low for LOW
    candle_high: float
    candle_low: float


@dataclass
class LiquidityZone:
    zone_id: str
    zone_type: ZoneType
    upper: float
    lower: float
    source_pivot_indices: list[int]
    source_pivots: list[Pivot]        # full Pivot objects for provenance
    created_at_index: int
    created_at: datetime
    status: ZoneStatus = ZoneStatus.ACTIVE
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None
    sweep_evidence: SweepEvidence | None = None
    events: list[dict] = field(default_factory=list)

    def add_event(
        self,
        event_type: str,
        timestamp: datetime,
        candle_index: int = -1,
        price: float = 0.0,
        detail: str = "",
    ) -> None:
        self.events.append({
            "type": event_type,
            "ts": timestamp.isoformat(),
            "candle_index": candle_index,
            "price": price,
            "detail": detail,
        })
