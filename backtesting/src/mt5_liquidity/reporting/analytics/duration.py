"""Trade holding-duration analytics (plan section 2C).

Duration = close_ts - fill_ts in minutes, computed from actual timestamps.
Bucket boundaries are lower-inclusive / upper-exclusive so a trade of exactly
15 minutes belongs to exactly one bucket; the final bucket is open-ended.

Bucket statistics are computed by grouping trades per bucket and reusing the
shared aggregate_trade_stats helper — identical metric definitions everywhere,
including the Phase 4 intratrade MAE/MFE aggregates.
"""
from __future__ import annotations

from mt5_liquidity.backtest.positions import ClosedPosition

from .aggregation import aggregate_trade_stats, median, trade_duration_minutes
from .models import DurationAnalytics, DurationBucket

DEFAULT_BUCKET_MINUTES: tuple[int, ...] = (5, 15, 30, 60, 120)


def bucket_labels(edges: tuple[int, ...] | list[int]) -> list[str]:
    labels: list[str] = []
    lower = 0
    for edge in edges:
        labels.append(f"{lower}-{edge}m")
        lower = edge
    labels.append(f"{lower}m+")
    return labels


def analyze_duration(
    trades: list[ClosedPosition],
    bucket_edges: tuple[int, ...] | list[int] | None = None,
) -> DurationAnalytics:
    """Build duration buckets plus overall duration statistics.

    bucket_edges: strictly increasing upper edges in minutes. None/empty uses
    DEFAULT_BUCKET_MINUTES. A trade at exactly an edge value belongs to the
    higher bucket.
    """
    edges = tuple(bucket_edges) if bucket_edges else DEFAULT_BUCKET_MINUTES
    if list(edges) != sorted(set(edges)) or any(e <= 0 for e in edges):
        raise ValueError(
            f"duration bucket edges must be positive, unique and strictly "
            f"increasing, got {edges}"
        )

    buckets: list[DurationBucket] = []
    lower = 0.0
    labels = bucket_labels(edges)
    for i, edge in enumerate(edges):
        buckets.append(DurationBucket(
            label=labels[i], min_minutes=lower, max_minutes=float(edge)))
        lower = float(edge)
    buckets.append(DurationBucket(
        label=labels[-1], min_minutes=lower, max_minutes=None))

    durations: list[float] = []
    same_candle = 0
    by_bucket: dict[int, list[ClosedPosition]] = {i: [] for i in range(len(buckets))}

    for trade in trades:
        minutes = trade_duration_minutes(trade)
        durations.append(minutes)
        if minutes == 0.0:
            same_candle += 1
        for i, bucket in enumerate(buckets):
            in_bucket = minutes >= bucket.min_minutes and (
                bucket.max_minutes is None or minutes < bucket.max_minutes
            )
            if in_bucket:
                by_bucket[i].append(trade)
                break
        else:  # pragma: no cover — defensive; buckets cover [0, inf)
            by_bucket[len(buckets) - 1].append(trade)

    for i, bucket in enumerate(buckets):
        bucket.stats = aggregate_trade_stats(by_bucket[i])

    winners = [t for t in trades if t.net_pnl_usd > 0]
    losers = [t for t in trades if t.net_pnl_usd <= 0]

    return DurationAnalytics(
        buckets=buckets,
        avg_duration_minutes=sum(durations) / len(durations) if durations else None,
        median_duration_minutes=median(durations),
        min_duration_minutes=min(durations) if durations else None,
        max_duration_minutes=max(durations) if durations else None,
        same_candle_closes=same_candle,
        winners=aggregate_trade_stats(winners),
        losers=aggregate_trade_stats(losers),
    )
