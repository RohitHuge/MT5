"""Account-level risk manager (Phase 3).

An independent account supervisor that decides whether new trades are allowed
and what happens to open positions when account-level thresholds are reached.
It is deterministic, unit-testable, and independent of MT5 APIs, the strategy
layer, and the filesystem (persistence is an explicit, validated operation).

States (typed, per plan 3B):
    ACTIVE, DAILY_TARGET_REACHED, DAILY_LOSS_REACHED,
    EQUITY_TRAILING_TRIGGERED, MAX_DRAWDOWN_TRIGGERED,
    LOSS_STREAK_REACHED, MANUAL_HALT

Lock semantics:
- Daily locks (DAILY_TARGET_REACHED, DAILY_LOSS_REACHED, LOSS_STREAK_REACHED)
  reset at the configured daily boundary (UTC by default).
- Account-wide locks (EQUITY_TRAILING_TRIGGERED, MAX_DRAWDOWN_TRIGGERED,
  MANUAL_HALT) never reset automatically; only reset_manual_lock() clears them
  (a day rollover must not clear a protective account-wide lock).

Measure conventions (documented):
- Daily P&L = (realised equity + floating P&L) - daily baseline. The baseline
  is the mark-to-market equity observed at the first evaluation of the day
  (matching the engine's UTC baseline).
- Trailing and drawdown protection evaluate the mark-to-market equity
  (realised + floating) against the account high-water mark.
- When both monetary and percentage forms are configured for one rule, the
  rule triggers at whichever threshold is crossed first (the tighter one).

Trigger precedence when several thresholds are crossed on the same candle
(deterministic, worst-first):
    MAX_DRAWDOWN > EQUITY_TRAILING > DAILY_LOSS > DAILY_TARGET > LOSS_STREAK

Zero-P&L (breakeven) trades break a loss streak; only net P&L < 0 extends it.

No MT5 imports. No strategy imports. No order_send.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 1


class RiskState(Enum):
    ACTIVE = "ACTIVE"
    DAILY_TARGET_REACHED = "DAILY_TARGET_REACHED"
    DAILY_LOSS_REACHED = "DAILY_LOSS_REACHED"
    EQUITY_TRAILING_TRIGGERED = "EQUITY_TRAILING_TRIGGERED"
    MAX_DRAWDOWN_TRIGGERED = "MAX_DRAWDOWN_TRIGGERED"
    LOSS_STREAK_REACHED = "LOSS_STREAK_REACHED"
    MANUAL_HALT = "MANUAL_HALT"


# Deterministic precedence for the reported state when several locks are
# active at once (index 0 = highest precedence).
LOCK_PRECEDENCE: list[RiskState] = [
    RiskState.MANUAL_HALT,
    RiskState.MAX_DRAWDOWN_TRIGGERED,
    RiskState.EQUITY_TRAILING_TRIGGERED,
    RiskState.DAILY_LOSS_REACHED,
    RiskState.DAILY_TARGET_REACHED,
    RiskState.LOSS_STREAK_REACHED,
]

# Exit reason recorded on positions force-closed by each protection.
RISK_EXIT_REASONS: dict[RiskState, str] = {
    RiskState.DAILY_TARGET_REACHED: "RISK_DAILY_TARGET",
    RiskState.DAILY_LOSS_REACHED: "RISK_DAILY_LOSS",
    RiskState.EQUITY_TRAILING_TRIGGERED: "RISK_EQUITY_TRAIL",
    RiskState.MAX_DRAWDOWN_TRIGGERED: "RISK_MAX_DRAWDOWN",
    RiskState.LOSS_STREAK_REACHED: "RISK_LOSS_STREAK",
    RiskState.MANUAL_HALT: "RISK_MANUAL_HALT",
}

# Locks cleared when the daily boundary rolls over. Account-wide locks are
# deliberately absent: a protective high-water-mark lock must survive resets.
DAILY_RESETTABLE_LOCKS: set[RiskState] = {
    RiskState.DAILY_TARGET_REACHED,
    RiskState.DAILY_LOSS_REACHED,
    RiskState.LOSS_STREAK_REACHED,
}


class RiskStateError(RuntimeError):
    """Raised when persisted risk state is corrupt, stale, or mismatched."""


@dataclass
class RiskEvent:
    """One structured account-risk observation or action (plan 3H)."""
    timestamp: datetime
    event_type: str                 # e.g. DAILY_LOSS_TRIGGERED, DAY_RESET, MANUAL_HALT, FORCE_CLOSE
    state: str                      # RiskState value after the event
    reason: str
    realised_equity: float
    floating_pnl: float
    mtm_equity: float
    daily_pnl: float | None = None  # None outside daily evaluations
    threshold_value: float | None = None
    observed_value: float | None = None
    peak_equity: float | None = None
    floor_equity: float | None = None
    open_positions: int = 0
    affected_positions: list[str] = field(default_factory=list)
    action: str = "NONE"            # NONE | CLOSE_ALL | STOP_NEW_ENTRIES

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload


@dataclass
class RiskDecision:
    """Result of an entry-admission or per-trade risk check."""
    allowed: bool
    state: RiskState
    reason_code: str | None = None      # e.g. "RISK_DAILY_LOSS", "MAX_RISK_PER_TRADE"
    observed_value: float | None = None
    threshold_value: float | None = None
    timestamp: datetime | None = None
    action: str = "NONE"


def _first_positive(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


class AccountRiskManager:
    """Deterministic account-level protection supervisor for one backtest run."""

    def __init__(
        self,
        *,
        initial_equity: float,
        daily_target_usd: float | None = None,
        daily_target_pct: float | None = None,
        daily_loss_usd: float | None = None,
        daily_loss_pct: float | None = None,
        equity_trail_giveback_usd: float | None = None,
        equity_trail_giveback_pct: float | None = None,
        max_drawdown_usd: float | None = None,
        max_drawdown_pct: float | None = None,
        max_consecutive_losses: int | None = None,
        max_risk_per_trade_usd: float | None = None,
        max_risk_per_trade_pct: float | None = None,
        open_position_action: str = "STOP_NEW_ENTRIES",
        daily_reset_timezone: str = "UTC",
        config_fingerprint: str | None = None,
    ) -> None:
        self.initial_equity = initial_equity
        self.daily_target_usd = daily_target_usd
        self.daily_target_pct = daily_target_pct
        self.daily_loss_usd = daily_loss_usd
        self.daily_loss_pct = daily_loss_pct
        self.equity_trail_giveback_usd = equity_trail_giveback_usd
        self.equity_trail_giveback_pct = equity_trail_giveback_pct
        self.max_drawdown_usd = max_drawdown_usd
        self.max_drawdown_pct = max_drawdown_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.max_risk_per_trade_usd = max_risk_per_trade_usd
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.open_position_action = open_position_action
        self._reset_tz = ZoneInfo(daily_reset_timezone)
        self.config_fingerprint = config_fingerprint

        self._locks: set[RiskState] = set()
        self._daily_baseline: float | None = None
        self._current_day: date | None = None
        self._peak_equity: float = initial_equity
        self._floor_equity: float | None = None
        self._loss_streak: int = 0
        self._last_processed_ts: datetime | None = None
        self.events: list[RiskEvent] = []
        self.blocked_by_reason: dict[str, int] = {}
        self.forced_closures: int = 0

    # -- helpers ----------------------------------------------------------

    def _effective_daily_loss_threshold(self, baseline: float) -> float | None:
        return _first_positive(self.daily_loss_usd,
                               baseline * self.daily_loss_pct / 100.0 if self.daily_loss_pct else None)

    def _effective_daily_target_threshold(self, baseline: float) -> float | None:
        return _first_positive(self.daily_target_usd,
                               baseline * self.daily_target_pct / 100.0 if self.daily_target_pct else None)

    def _effective_trail_giveback(self) -> float | None:
        usd = self.equity_trail_giveback_usd
        pct_based = (self._peak_equity * self.equity_trail_giveback_pct / 100.0
                     if self.equity_trail_giveback_pct else None)
        return _first_positive(usd, pct_based)

    def _effective_max_drawdown(self) -> float | None:
        usd = self.max_drawdown_usd
        pct_based = (self._peak_equity * self.max_drawdown_pct / 100.0
                     if self.max_drawdown_pct else None)
        return _first_positive(usd, pct_based)

    @property
    def state(self) -> RiskState:
        if not self._locks:
            return RiskState.ACTIVE
        for candidate in LOCK_PRECEDENCE:
            if candidate in self._locks:
                return candidate
        return RiskState.ACTIVE  # pragma: no cover — LOCK_PRECEDENCE is exhaustive

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def floor_equity(self) -> float | None:
        return self._floor_equity

    @property
    def loss_streak(self) -> int:
        return self._loss_streak

    @property
    def locks(self) -> frozenset[RiskState]:
        return frozenset(self._locks)

    def _emit(self, event: RiskEvent) -> None:
        self.events.append(event)
        logger.info(
            "RISK %s state=%s observed=%s threshold=%s action=%s",
            event.event_type, event.state, event.observed_value,
            event.threshold_value, event.action,
        )

    def _add_lock(self, state: RiskState, event_type: str, ts: datetime, *,
                  reason: str, realised: float, floating: float, daily_pnl: float | None,
                  threshold: float | None, observed: float, mtm: float,
                  open_positions: int) -> None:
        self._locks.add(state)
        self._emit(RiskEvent(
            timestamp=ts, event_type=event_type, state=state.value, reason=reason,
            realised_equity=realised, floating_pnl=floating, mtm_equity=mtm,
            daily_pnl=daily_pnl, threshold_value=threshold, observed_value=observed,
            peak_equity=self._peak_equity, floor_equity=self._floor_equity,
            open_positions=open_positions, action=self.open_position_action,
        ))

    # -- per-candle evaluation (plan 3F: after MTM valuation, before entries) --

    def on_candle(
        self,
        ts: datetime,
        *,
        realised_equity: float,
        floating_pnl: float,
        open_positions: int,
    ) -> list[RiskEvent]:
        """Process one end-of-candle observation.

        Handles day rollover, high-water-mark updates, and every threshold
        evaluation. Returns the events emitted during this call.
        """
        before = len(self.events)
        mtm = realised_equity + floating_pnl

        # Day rollover: reset daily locks and re-baseline (automatic, UTC by
        # default). Account-wide locks are intentionally preserved.
        today = ts.astimezone(self._reset_tz).date()
        if self._current_day is not None and today != self._current_day:
            self._daily_baseline = mtm
            reset_locks = sorted(l.value for l in self._locks if l in DAILY_RESETTABLE_LOCKS)
            self._locks -= DAILY_RESETTABLE_LOCKS
            self._emit(RiskEvent(
                timestamp=ts, event_type="DAY_RESET",
                state=self.state.value,
                reason=f"Daily boundary rollover ({self._reset_tz.key}); "
                       f"cleared locks: {reset_locks or 'none'}",
                realised_equity=realised_equity, floating_pnl=floating_pnl,
                mtm_equity=mtm, daily_pnl=0.0, peak_equity=self._peak_equity,
                floor_equity=self._floor_equity, open_positions=open_positions,
            ))
        elif self._daily_baseline is None:
            self._daily_baseline = mtm
        self._current_day = today
        self._last_processed_ts = ts

        # High-water mark and (monotonic) trailing floor from MTM equity.
        if mtm > self._peak_equity:
            self._peak_equity = mtm
        giveback = self._effective_trail_giveback()
        if giveback is not None:
            candidate_floor = self._peak_equity - giveback
            if self._floor_equity is None or candidate_floor > self._floor_equity:
                self._floor_equity = candidate_floor

        daily_pnl = mtm - self._daily_baseline if self._daily_baseline is not None else 0.0

        # 1. Maximum drawdown (account-wide).
        if RiskState.MAX_DRAWDOWN_TRIGGERED not in self._locks:
            dd_limit = self._effective_max_drawdown()
            if dd_limit is not None:
                drawdown = self._peak_equity - mtm
                if drawdown >= dd_limit:
                    self._add_lock(
                        RiskState.MAX_DRAWDOWN_TRIGGERED, "MAX_DRAWDOWN_TRIGGERED", ts,
                        reason="Account drawdown from high-water mark reached configured maximum",
                        realised=realised_equity, floating=floating_pnl, daily_pnl=daily_pnl,
                        threshold=dd_limit, observed=drawdown, mtm=mtm,
                        open_positions=open_positions)

        # 2. Equity trailing protection (account-wide). Suppressed once max
        #    drawdown has triggered to avoid contradictory duplicate triggers.
        if RiskState.EQUITY_TRAILING_TRIGGERED not in self._locks \
                and RiskState.MAX_DRAWDOWN_TRIGGERED not in self._locks \
                and self._floor_equity is not None:
            if mtm <= self._floor_equity:
                self._add_lock(
                    RiskState.EQUITY_TRAILING_TRIGGERED, "EQUITY_TRAILING_TRIGGERED", ts,
                    reason="Mark-to-market equity at or below the trailing protection floor",
                    realised=realised_equity, floating=floating_pnl, daily_pnl=daily_pnl,
                    threshold=self._floor_equity, observed=mtm, mtm=mtm,
                    open_positions=open_positions)

        # 3. Daily loss (includes floating P&L by construction).
        if RiskState.DAILY_LOSS_REACHED not in self._locks:
            loss_limit = self._effective_daily_loss_threshold(self._daily_baseline or 0.0)
            if loss_limit is not None and -daily_pnl >= loss_limit:
                self._add_lock(
                    RiskState.DAILY_LOSS_REACHED, "DAILY_LOSS_TRIGGERED", ts,
                    reason="Daily loss (realised + floating) reached configured limit",
                    realised=realised_equity, floating=floating_pnl, daily_pnl=daily_pnl,
                    threshold=loss_limit, observed=-daily_pnl, mtm=mtm,
                    open_positions=open_positions)

        # 4. Daily profit target (locks in the day's result).
        if RiskState.DAILY_TARGET_REACHED not in self._locks:
            target = self._effective_daily_target_threshold(self._daily_baseline or 0.0)
            if target is not None and daily_pnl >= target:
                self._add_lock(
                    RiskState.DAILY_TARGET_REACHED, "DAILY_TARGET_TRIGGERED", ts,
                    reason="Daily profit (realised + floating) reached configured target",
                    realised=realised_equity, floating=floating_pnl, daily_pnl=daily_pnl,
                    threshold=target, observed=daily_pnl, mtm=mtm,
                    open_positions=open_positions)

        return self.events[before:]

    def close_all_requested(self) -> bool:
        """True only when a protection lock is active AND the configured
        open-position policy demands closing. An ACTIVE state never closes
        positions."""
        return (self.open_position_action == "CLOSE_ALL"
                and self.state is not RiskState.ACTIVE)

    def record_forced_closures(self, ts: datetime, closed_positions: list,
                               realised_equity: float) -> None:
        """Book protection-triggered closures (engine CLOSE_ALL action).

        Records the FORCE_CLOSE risk event with the affected setup IDs, counts
        the closures, and feeds each realised result into the loss streak so
        risk closures are treated exactly like any other closed trade.
        """
        self.forced_closures += len(closed_positions)
        affected = [c.setup_id for c in closed_positions]
        net = sum(c.net_pnl_usd for c in closed_positions)
        self._emit(RiskEvent(
            timestamp=ts, event_type="FORCE_CLOSE", state=self.state.value,
            reason=f"Open positions closed by {self.state.value} protection "
                   f"(net {net:+.2f} USD)",
            realised_equity=realised_equity, floating_pnl=0.0,
            mtm_equity=realised_equity, peak_equity=self._peak_equity,
            floor_equity=self._floor_equity, open_positions=0,
            affected_positions=affected, action="CLOSE_ALL"))
        for closed in closed_positions:
            self.on_trade_closed(ts, closed.net_pnl_usd, realised_equity=realised_equity)

    def daily_loss_remaining(self, *, realised_equity: float, floating_pnl: float) -> float | None:
        """Remaining daily loss budget in USD; None means no daily limit is configured."""
        if self._daily_baseline is None:
            return self._effective_daily_loss_threshold(self.initial_equity)
        limit = self._effective_daily_loss_threshold(self._daily_baseline)
        if limit is None:
            return None
        daily_pnl = realised_equity + floating_pnl - self._daily_baseline
        return max(0.0, limit + daily_pnl)

    def check_daily_risk_budget(self, *, proposed_risk_usd: float,
                                realised_equity: float, floating_pnl: float,
                                ts: datetime) -> RiskDecision:
        """Reject a proposed trade if its planned stop risk exceeds remaining daily budget.

        This is an admission-control guard, not a guarantee against execution
        gaps/slippage. It reserves only the risk to the initial stop and keeps
        the daily lock logic authoritative for realised + floating equity.
        """
        remaining = self.daily_loss_remaining(
            realised_equity=realised_equity, floating_pnl=floating_pnl)
        if remaining is None or proposed_risk_usd <= remaining:
            return RiskDecision(allowed=True, state=self.state, timestamp=ts)
        reason = "DAILY_RISK_BUDGET_EXCEEDED"
        self.blocked_by_reason[reason] = self.blocked_by_reason.get(reason, 0) + 1
        return RiskDecision(
            allowed=False, state=self.state, reason_code=reason,
            observed_value=proposed_risk_usd, threshold_value=remaining,
            timestamp=ts, action="STOP_NEW_ENTRIES",
        )

    # -- closed-trade feedback (loss streak) ------------------------------

    def on_trade_closed(self, ts: datetime, net_pnl_usd: float, *,
                        realised_equity: float, floating_pnl: float = 0.0) -> None:
        """Update the consecutive-loss streak from a closed trade.

        net_pnl_usd < 0 extends the streak; zero-P&L and winning trades break
        it (documented policy). A reached limit locks new entries until the
        next daily reset.
        """
        mtm = realised_equity + floating_pnl
        # Track the day so a day whose only observations are closed trades
        # still anchors the next day's rollover (protective locks must reset
        # on schedule even when no candle evaluation ran that day).
        trade_day = ts.astimezone(self._reset_tz).date()
        if self._current_day is None:
            self._current_day = trade_day
            if self._daily_baseline is None:
                self._daily_baseline = mtm
        if net_pnl_usd < 0:
            self._loss_streak += 1
        else:
            if self._loss_streak:
                self._emit(RiskEvent(
                    timestamp=ts, event_type="LOSS_STREAK_RESET", state=self.state.value,
                    reason=f"Loss streak broken by {'breakeven' if net_pnl_usd == 0 else 'winning'} trade",
                    realised_equity=realised_equity, floating_pnl=floating_pnl,
                    mtm_equity=mtm, peak_equity=self._peak_equity,
                    floor_equity=self._floor_equity,
                ))
            self._loss_streak = 0

        if self.max_consecutive_losses is not None \
                and RiskState.LOSS_STREAK_REACHED not in self._locks \
                and self._loss_streak >= self.max_consecutive_losses:
            self._add_lock(
                RiskState.LOSS_STREAK_REACHED, "LOSS_STREAK_TRIGGERED", ts,
                reason=f"{self._loss_streak} consecutive losing trades reached the configured limit",
                realised=realised_equity, floating=floating_pnl, daily_pnl=None,
                threshold=float(self.max_consecutive_losses),
                observed=float(self._loss_streak), mtm=mtm, open_positions=0)

    # -- pre-trade gates ---------------------------------------------------

    def evaluate_entry(self, ts: datetime, *, realised_equity: float,
                       floating_pnl: float) -> RiskDecision:
        """Gate a proposed new entry against all active locks (req. 4/5).

        Reason codes follow the deterministic LOCK_PRECEDENCE order, so the
        reported reason is always the most severe active protection.
        """
        state = self.state
        if state is RiskState.ACTIVE:
            return RiskDecision(allowed=True, state=state, timestamp=ts)
        reason_code = RISK_EXIT_REASONS.get(state, state.value)
        self.blocked_by_reason[reason_code] = self.blocked_by_reason.get(reason_code, 0) + 1
        return RiskDecision(
            allowed=False, state=state, reason_code=reason_code,
            timestamp=ts, action=self.open_position_action,
        )

    def check_trade_risk(self, ts: datetime, *, proposed_risk_usd: float,
                         current_equity: float) -> RiskDecision:
        """Gate one sized trade against the per-trade risk ceiling.

        This is a trade-level rejection (the setup is skipped), not an
        account lock. Both USD and percentage limits block when either is
        exceeded (documented OR semantics).
        """
        limits: list[tuple[float, str]] = []
        if self.max_risk_per_trade_usd is not None:
            limits.append((self.max_risk_per_trade_usd, "MAX_RISK_PER_TRADE_USD"))
        if self.max_risk_per_trade_pct is not None:
            limits.append((current_equity * self.max_risk_per_trade_pct / 100.0,
                           "MAX_RISK_PER_TRADE_PCT"))
        for limit, code in limits:
            if proposed_risk_usd > limit:
                self.blocked_by_reason[code] = self.blocked_by_reason.get(code, 0) + 1
                return RiskDecision(
                    allowed=False, state=self.state, reason_code=code,
                    observed_value=proposed_risk_usd, threshold_value=limit,
                    timestamp=ts)
        return RiskDecision(allowed=True, state=self.state, timestamp=ts)

    # -- manual halt (req. 7) ----------------------------------------------

    def halt(self, ts: datetime, *, realised_equity: float, floating_pnl: float = 0.0,
             reason: str = "Manual trading halt requested") -> RiskEvent:
        if RiskState.MANUAL_HALT not in self._locks:
            mtm = realised_equity + floating_pnl
            self._locks.add(RiskState.MANUAL_HALT)
            event = RiskEvent(
                timestamp=ts, event_type="MANUAL_HALT", state=RiskState.MANUAL_HALT.value,
                reason=reason, realised_equity=realised_equity,
                floating_pnl=floating_pnl, mtm_equity=mtm,
                peak_equity=self._peak_equity, floor_equity=self._floor_equity,
                action=self.open_position_action)
            self._emit(event)
            return event
        return self.events[-1] if self.events else RiskEvent(timestamp=ts, event_type="MANUAL_HALT", state=self.state.value, reason=reason, realised_equity=realised_equity, floating_pnl=floating_pnl, mtm_equity=realised_equity + floating_pnl)

    def reset_manual_lock(self, ts: datetime, *, realised_equity: float,
                          floating_pnl: float = 0.0) -> None:
        """Operator-acknowledged reset of the manual halt (req. 5/7)."""
        if RiskState.MANUAL_HALT in self._locks:
            self._locks.discard(RiskState.MANUAL_HALT)
            self._emit(RiskEvent(
                timestamp=ts, event_type="MANUAL_HALT_RESET", state=self.state.value,
                reason="Manual halt released by operator",
                realised_equity=realised_equity, floating_pnl=floating_pnl,
                mtm_equity=realised_equity + floating_pnl,
                peak_equity=self._peak_equity, floor_equity=self._floor_equity))

    # -- reporting summary --------------------------------------------------

    def summary(self) -> dict:
        return {
            "enabled": True,
            "final_state": self.state.value,
            "active_locks": sorted(l.value for l in self._locks),
            "locks_triggered": sorted({
                e.state for e in self.events
                if e.event_type.endswith("TRIGGERED")
            }),
            "blocked_by_reason": dict(sorted(self.blocked_by_reason.items())),
            "forced_closures": self.forced_closures,
            "peak_equity": round(self._peak_equity, 2),
            "floor_equity": round(self._floor_equity, 2) if self._floor_equity is not None else None,
            "daily_baseline": round(self._daily_baseline, 2) if self._daily_baseline is not None else None,
            "loss_streak": self._loss_streak,
            "open_position_action": self.open_position_action,
            "event_count": len(self.events),
        }

    # -- persistence (plan 3G) ----------------------------------------------

    def snapshot_state(self) -> dict:
        """Serializable, versioned risk state for restart recovery."""
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "config_fingerprint": self.config_fingerprint,
            "active_locks": sorted(l.value for l in self._locks),
            "daily_baseline": self._daily_baseline,
            "current_day": self._current_day.isoformat() if self._current_day else None,
            "peak_equity": self._peak_equity,
            "floor_equity": self._floor_equity,
            "loss_streak": self._loss_streak,
            "last_processed_ts": self._last_processed_ts.isoformat() if self._last_processed_ts else None,
            "events": [e.as_dict() for e in self.events],
            "blocked_by_reason": dict(self.blocked_by_reason),
            "forced_closures": self.forced_closures,
        }

    def restore_state(self, state: dict) -> None:
        """Restore from snapshot_state(), failing safe on any inconsistency."""
        if not isinstance(state, dict):
            raise RiskStateError("Risk state must be a JSON object")
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise RiskStateError(
                f"Unsupported risk-state schema version: {state.get('schema_version')!r}")
        if self.config_fingerprint is not None \
                and state.get("config_fingerprint") != self.config_fingerprint:
            raise RiskStateError(
                "Risk state belongs to a different account/configuration "
                f"(state={state.get('config_fingerprint')!r}, "
                f"expected={self.config_fingerprint!r})")
        try:
            locks = {RiskState(v) for v in state["active_locks"]}
            baseline = state["daily_baseline"]
            current_day = date.fromisoformat(state["current_day"]) if state["current_day"] else None
            peak = float(state["peak_equity"])
            floor = state["floor_equity"]
            streak = int(state["loss_streak"])
            last_ts = (datetime.fromisoformat(state["last_processed_ts"])
                       if state["last_processed_ts"] else None)
            events = [RiskEvent(
                timestamp=datetime.fromisoformat(e["timestamp"]),
                event_type=e["event_type"], state=e["state"], reason=e["reason"],
                realised_equity=e["realised_equity"], floating_pnl=e["floating_pnl"],
                mtm_equity=e["mtm_equity"], daily_pnl=e.get("daily_pnl"),
                threshold_value=e.get("threshold_value"),
                observed_value=e.get("observed_value"),
                peak_equity=e.get("peak_equity"), floor_equity=e.get("floor_equity"),
                open_positions=e.get("open_positions", 0),
                affected_positions=list(e.get("affected_positions", [])),
                action=e.get("action", "NONE"),
            ) for e in state["events"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise RiskStateError(f"Corrupt risk state: {exc}") from exc

        # Fail safe: never silently adopt a high-water mark below the current
        # known peak, and never import a peak below the starting equity.
        if peak < self._peak_equity:
            raise RiskStateError(
                f"Persisted peak equity {peak} is below the current known peak "
                f"{self._peak_equity}; refusing to weaken protection")
        self._locks = locks
        self._daily_baseline = baseline
        self._current_day = current_day
        self._peak_equity = peak
        self._floor_equity = float(floor) if floor is not None else None
        self._loss_streak = streak
        self._last_processed_ts = last_ts
        self.events = events
        self.blocked_by_reason = dict(state.get("blocked_by_reason", {}))
        self.forced_closures = int(state.get("forced_closures", 0))

    def save_state(self, path: str | Path) -> Path:
        """Atomically persist the risk state (a partial write cannot corrupt
        the latest known state)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot_state(), indent=2), encoding="utf-8")
        os.replace(tmp, target)
        logger.info("Risk state saved to %s", target)
        return target

    def load_state(self, path: str | Path) -> None:
        """Restore from a state file. Missing file raises RiskStateError —
        callers must fail safe rather than silently resetting protection."""
        source = Path(path)
        if not source.is_file():
            raise RiskStateError(f"Risk state file not found: {source}")
        try:
            state = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RiskStateError(f"Unreadable risk state file {source}: {exc}") from exc
        self.restore_state(state)


def config_fingerprint(parts: dict) -> str:
    """Stable fingerprint of the account-identity inputs for state validation."""
    canonical = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_account_risk_manager(config) -> AccountRiskManager | None:
    """Construct the run's risk manager from BacktestConfig, or None when
    disabled (the engine then keeps its legacy inline daily-loss guard).

    Continuity rule: when account_risk.daily_loss_pct is unset the legacy
    BacktestConfig.max_daily_loss_pct value is used, so enabling the manager
    with defaults preserves the historical daily-loss protection level.
    """
    from ..config import validate_account_risk_config

    arc = config.account_risk
    if not arc.enabled:
        return None
    error = validate_account_risk_config(arc)
    if error:
        raise ValueError(f"Invalid account risk configuration: {error}")
    from dataclasses import asdict as _asdict

    return AccountRiskManager(
        initial_equity=config.initial_equity,
        daily_target_usd=arc.daily_target_usd,
        daily_target_pct=arc.daily_target_pct,
        daily_loss_usd=arc.daily_loss_usd,
        daily_loss_pct=(arc.daily_loss_pct if arc.daily_loss_pct is not None
                        else config.max_daily_loss_pct),
        equity_trail_giveback_usd=arc.equity_trail_giveback_usd,
        equity_trail_giveback_pct=arc.equity_trail_giveback_pct,
        max_drawdown_usd=arc.max_drawdown_usd,
        max_drawdown_pct=arc.max_drawdown_pct,
        max_consecutive_losses=arc.max_consecutive_losses,
        max_risk_per_trade_usd=arc.max_risk_per_trade_usd,
        max_risk_per_trade_pct=arc.max_risk_per_trade_pct,
        open_position_action=arc.open_position_action,
        daily_reset_timezone=arc.daily_reset_timezone,
        config_fingerprint=config_fingerprint({
            "symbol": config.symbol,
            "initial_equity": config.initial_equity,
            "account_risk": _asdict(arc),
        }),
    )
