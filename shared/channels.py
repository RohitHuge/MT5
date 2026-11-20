"""Redis channel name constants.

All services must use these constants — never hardcode channel strings.
This file is the single source of truth so a rename here fixes everything.

Usage:
    from shared.channels import CANDLE_CHANNEL, SIGNAL_ALL

    redis.publish(CANDLE_CHANNEL.format(symbol="XAUUSD", timeframe="M5"), payload)
    redis.subscribe(SIGNAL_ALL)
"""

# Candle feed → all strategy containers
# Published by: candle-feed
# Subscribed by: every strategy container, shadow-tracker
CANDLE_CHANNEL = "candles:{symbol}:{timeframe}"   # e.g. candles:XAUUSD:M5

# Unified signal bus — ALL strategies publish here regardless of mode.
# The executor reads the DB to decide whose signal to execute (mode=live).
# The shadow-tracker records every signal as a virtual trade.
# Published by: every strategy container
# Subscribed by: executor, shadow-tracker
SIGNAL_ALL = "signals:all"

# --- Kept for backwards compatibility (referenced in reports/docs) ---
# These channels are no longer written to by strategy containers.
# Live strategy → executor  (legacy; superseded by SIGNAL_ALL + DB routing)
SIGNAL_LIVE = "signals:live"

# Shadow strategy → shadow-tracker  (legacy; superseded by SIGNAL_ALL)
SIGNAL_SHADOW = "signals:shadow:{strategy}"        # e.g. signals:shadow:ob-fvg

# Health monitoring — each service publishes its name + timestamp every 10s
# Subscribed by: dashboard (optional alerting)
HEARTBEAT = "heartbeat:{service}"                  # e.g. heartbeat:candle-feed

# Convenience: pattern for subscribing to ALL shadow signals at once (legacy)
SIGNAL_SHADOW_PATTERN = "signals:shadow:*"

# Redis List written by candle-feed on every candle (capped at 500, oldest→newest).
# Strategy containers read this on startup to pre-fill their rolling window without
# waiting for live candles. Published by: candle-feed. Read by: strategy containers.
CANDLE_HISTORY_KEY = "candles:{symbol}:{timeframe}:history"  # e.g. candles:XAUUSD:M5:history
