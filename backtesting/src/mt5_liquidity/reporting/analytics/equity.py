"""Mark-to-market equity and drawdown analytics (plan section 2G).

Valuation convention (identical to the engine's OpenPosition.unrealized_pnl):
- BUY  positions are marked at the candle's bid close.
- SELL positions are marked at the ask close (bid close + spread), so the
  spread cost of closing a short is visible in floating P&L.

Limitation (documented): M5 OHLC provides one mark per candle (the close).
Intrabar equity paths cannot be reconstructed from OHLC data, so drawdown
between candle closes is not observable. Closing prices are conservative
end-of-bar marks and never use future candles.

Realised vs floating: realised_equity changes only when a trade closes;
mtm_equity = realised_equity + floating_pnl. The final observation (after
force-closes) has zero floating P&L and must equal ledger final_equity.
"""
from __future__ import annotations

from datetime import datetime

from .aggregation import max_drawdown_from_series
from .models import DrawdownSummary

# (timestamp, realised_equity, floating_pnl, mtm_equity, open_positions)
MtmPoint = tuple[datetime, float, float, float, int]


def analyze_mark_to_market(mtm_points: list[MtmPoint]) -> DrawdownSummary:
    """Drawdown over the mark-to-market equity series.

    Evaluates the drawdown on the mtm-equity column. Empty input returns an
    empty DrawdownSummary.
    """
    series = [(ts, mtm) for ts, _r, _f, mtm, _n in mtm_points]
    return max_drawdown_from_series(series)
