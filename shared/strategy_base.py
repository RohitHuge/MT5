"""Base class for all strategy containers.

Each strategy container subclasses StrategyBase and overrides on_candle().
The base class handles all Redis wiring, signal publishing, and heartbeats so
the subclass only contains signal detection logic.

All strategies publish every signal to ``signals:all`` regardless of their
database mode. The executor queries the DB on each signal to decide whether to
place a real order; the shadow-tracker records every signal as a virtual trade.

Environment variables read at construction:
  REDIS_URL     — e.g. redis://redis:6379
  MT5_BRIDGE_URL — e.g. http://host.docker.internal:8000
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque

import redis as redis_lib
import requests

from .channels import CANDLE_CHANNEL, CANDLE_HISTORY_KEY, HEARTBEAT, SIGNAL_ALL
from .models import Candle, Signal

log = logging.getLogger(__name__)


class StrategyBase:
    """Subscribe to candles, call on_candle(), route the resulting Signal."""

    def __init__(
        self,
        name: str,
        symbol: str,
        timeframe: str,
        window_size: int,
    ) -> None:
        self.name = name
        self.symbol = symbol
        self.timeframe = timeframe
        self.window_size = window_size

        # Signals carry the mode they were emitted under. Routing is downstream
        # (executor/shadow-tracker read the DB), so this is informational only —
        # but every subclass passes mode=self.mode to Signal(), so it must exist.
        self.mode = os.getenv("MODE", "shadow")

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.mt5_bridge_url = os.getenv("MT5_BRIDGE_URL", "http://localhost:8000").rstrip("/")

        self._redis = redis_lib.from_url(redis_url, decode_responses=True)
        self._candles: Deque[Candle] = deque(maxlen=window_size)
        self._running = False

        # Symbol contract details — fetched once on startup
        self.point: float = 0.01
        self.tick_value: float = 1.0
        self.vol_min: float = 0.01
        self.vol_max: float = 100.0
        self.vol_step: float = 0.01

    # -------------------------------------------------------------------------
    # Override this in subclasses
    # -------------------------------------------------------------------------

    def on_candle(self, candles: list[Candle]) -> Signal | None:
        """Called with the current rolling window on every new candle.

        Return a Signal to trade, or None to pass. The base class publishes
        every returned Signal to ``signals:all``; downstream consumers (executor,
        shadow-tracker) decide what to do with it.
        """
        raise NotImplementedError

    def _get_indicator_state(self) -> dict:
        """Current indicator values, published to the dashboard for chart overlays.

        Subclasses override this. Returning per-strategy keys (EMA levels, OB/FVG
        zones, pivots) keeps the snapshot payload schema-less on the DB side.
        """
        return {}

    # -------------------------------------------------------------------------
    # Startup helpers
    # -------------------------------------------------------------------------

    def _register_with_dashboard(self) -> None:
        dashboard_url = os.getenv("DASHBOARD_URL", "").rstrip("/")
        if not dashboard_url:
            return
        try:
            requests.post(
                f"{dashboard_url}/api/strategies/register",
                json={
                    "name": self.name,
                    "display_name": self.name.replace("-", " ").title(),
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "mode": self.mode,
                },
                timeout=5,
            )
            log.info("Registered with dashboard: %s", self.name)
        except Exception as exc:
            log.warning("Could not register with dashboard (non-fatal): %s", exc)

    def _fetch_symbol_info(self) -> None:
        try:
            r = requests.get(
                f"{self.mt5_bridge_url}/symbol/{self.symbol}", timeout=10
            )
            r.raise_for_status()
            d = r.json()
            self.point = float(d.get("point", 0.01))
            self.tick_value = float(d.get("tick_value", 1.0))
            self.vol_min = float(d.get("volume_min", 0.01))
            self.vol_max = float(d.get("volume_max", 100.0))
            self.vol_step = float(d.get("volume_step", 0.01))
            log.info(
                "Symbol info: point=%.4f tick_value=%.4f vol_min=%.2f",
                self.point, self.tick_value, self.vol_min,
            )
        except Exception as exc:
            log.warning("Could not fetch symbol info, using defaults: %s", exc)

    def fetch_equity(self) -> float:
        """Fetch current account equity from the bridge. Returns 0.0 on error."""
        try:
            r = requests.get(f"{self.mt5_bridge_url}/account", timeout=10)
            r.raise_for_status()
            return float(r.json().get("equity", 0.0))
        except Exception as exc:
            log.warning("Could not fetch equity: %s", exc)
            return 0.0

    # -------------------------------------------------------------------------
    # Lot sizing helper
    # -------------------------------------------------------------------------

    def calc_lots(self, equity: float, risk_pct: float, sl_distance: float) -> float:
        """Return position size in lots given risk parameters.

        Args:
            equity:      current account equity in USD
            risk_pct:    fraction to risk per trade (e.g. 0.005 = 0.5%)
            sl_distance: absolute price distance from entry to SL
        """
        if sl_distance <= 0 or equity <= 0:
            return self.vol_min
        risk_usd = equity * risk_pct
        sl_points = sl_distance / self.point
        lots = risk_usd / (sl_points * self.tick_value)
        # Round down to nearest vol_step
        lots = max(self.vol_min, min(self.vol_max, lots))
        lots = round(int(lots / self.vol_step) * self.vol_step, 8)
        return max(self.vol_min, lots)

    # -------------------------------------------------------------------------
    # Signal routing
    # -------------------------------------------------------------------------

    def _publish_signal(self, signal: Signal) -> None:
        """Publish signal to the unified signals:all bus.

        Routing is entirely downstream: the executor checks the DB to decide
        whether to place a real order; the shadow-tracker records every signal
        as a virtual trade regardless of strategy mode.
        """
        self._redis.publish(SIGNAL_ALL, signal.to_json())
        log.info(
            "Signal published → %s | strategy=%s %s %s entry=%.2f sl=%.2f tp=%.2f lots=%.2f",
            SIGNAL_ALL, signal.strategy, signal.direction, signal.symbol,
            signal.entry_price, signal.sl_price, signal.tp_price, signal.lots,
        )

    # -------------------------------------------------------------------------
    # Heartbeat
    # -------------------------------------------------------------------------

    def _heartbeat_loop(self) -> None:
        channel = HEARTBEAT.format(service=self.name)
        while self._running:
            payload = json.dumps({
                "service": self.name,
                "candles_buffered": len(self._candles),
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            try:
                self._redis.publish(channel, payload)
            except Exception as exc:
                log.debug("Heartbeat publish failed: %s", exc)
            time.sleep(10)

    # -------------------------------------------------------------------------
    # Indicator snapshots (dashboard chart overlays)
    # -------------------------------------------------------------------------

    def _snapshot_loop(self) -> None:
        """Publish indicator state every 60s — Redis for the live overlay, Postgres for history.

        Batched rather than per-candle: snapshots only feed chart overlays, and one
        row per minute is plenty at M5. Postgres is best-effort so a service without
        psycopg2 (or a DB outage) degrades to Redis-only instead of dying.
        """
        db_mod = None
        try:
            from . import db as db_mod  # noqa: PLC0415 — optional dependency
        except Exception as exc:  # pragma: no cover
            log.warning("Snapshot DB writes disabled (shared.db unavailable): %s", exc)

        while self._running:
            try:
                if not self._candles:
                    time.sleep(60)
                    continue

                indicators = self._get_indicator_state()
                if indicators:
                    candle_ts = self._candles[-1].timestamp
                    state = {
                        "ts": candle_ts.isoformat(),
                        "strategy": self.name,
                        "indicators": indicators,
                    }
                    try:
                        self._redis.set(f"snapshot:{self.name}", json.dumps(state, default=str))
                    except Exception as exc:
                        log.debug("Snapshot Redis write failed: %s", exc)

                    if db_mod is not None:
                        try:
                            db_mod.save_snapshot(self.name, candle_ts, indicators)
                        except Exception as exc:
                            log.debug("Snapshot DB write failed: %s", exc)
            except Exception as exc:
                log.warning("Snapshot loop error: %s", exc)
            time.sleep(60)

    # -------------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------------

    def _seed_from_history(self) -> None:
        """Pre-fill the rolling window from the Redis history list written by candle-feed.

        candle-feed dual-writes every candle: RPUSH to the capped history list AND
        PUBLISH to the live channel. Reading the list on startup fills the window
        instantly so on_candle() has context from the first live candle received.
        """
        history_key = CANDLE_HISTORY_KEY.format(symbol=self.symbol, timeframe=self.timeframe)
        try:
            raw_list = self._redis.lrange(history_key, 0, -1)
            for raw in raw_list:
                try:
                    self._candles.append(Candle.from_json(raw))
                except Exception as exc:
                    log.debug("Bad history candle: %s", exc)
            log.info("Seeded %d candles from history list", len(self._candles))
        except Exception as exc:
            log.warning("Could not seed from history list: %s", exc)

    def run(self) -> None:
        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        log.info("Starting %s (window=%d) — publishing all signals to %s",
                 self.name, self.window_size, SIGNAL_ALL)

        self._fetch_symbol_info()
        self._register_with_dashboard()
        self._seed_from_history()

        self._running = True
        hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        hb_thread.start()
        snapshot_thread = threading.Thread(target=self._snapshot_loop, daemon=True)
        snapshot_thread.start()

        channel = CANDLE_CHANNEL.format(symbol=self.symbol, timeframe=self.timeframe)
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(channel)
        log.info("Subscribed to %s (window pre-filled: %d candles)", channel, len(self._candles))

        try:
            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    candle = Candle.from_json(message["data"])
                except Exception as exc:
                    log.warning("Bad candle message: %s", exc)
                    continue

                self._candles.append(candle)

                try:
                    signal = self.on_candle(list(self._candles))
                except Exception as exc:
                    log.exception("on_candle raised: %s", exc)
                    continue

                if signal is not None:
                    self._publish_signal(signal)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            pubsub.close()
            log.info("%s stopped", self.name)
