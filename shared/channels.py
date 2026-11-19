"""Redis channel name constants.

All services must use these constants — never hardcode channel strings.
This file is the single source of truth so a rename here fixes everything.

Usage:
    from shared.channels import CANDLE_CHANNEL, SIGNAL_LIVE, SIGNAL_SHADOW

    redis.publish(CANDLE_CHANNEL.format(symbol="XAUUSD", timeframe="M5"), payload)
    redis.subscribe(SIGNAL_SHADOW.format(strategy="ob-fvg"))
"""

# Candle feed → all strategy containers
# Published by: candle-feed
# Subscribed by: every strategy container, shadow-tracker
CANDLE_CHANNEL = "candles:{symbol}:{timeframe}"   # e.g. candles:XAUUSD:M5

# Live strategy → executor
# Published by: whichever strategy container has MODE=live
# Subscribed by: executor
SIGNAL_LIVE = "signals:live"

# Shadow strategy → shadow-tracker
# Published by: strategy containers with MODE=shadow
# Subscribed by: shadow-tracker (pattern subscribe signals:shadow:*)
SIGNAL_SHADOW = "signals:shadow:{strategy}"        # e.g. signals:shadow:ob-fvg

# Health monitoring — each service publishes its name + timestamp every 10s
# Subscribed by: dashboard (optional alerting)
HEARTBEAT = "heartbeat:{service}"                  # e.g. heartbeat:candle-feed

# Convenience: pattern for subscribing to ALL shadow signals at once
SIGNAL_SHADOW_PATTERN = "signals:shadow:*"
