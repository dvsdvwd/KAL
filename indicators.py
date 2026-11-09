"""Additional technical indicators for the multi-signal trading engine.

These indicators are calculated from OHLCV candle data and combined into a
weighted directional score.  They are analytical tools, not a guarantee of
profit.  All return plain Python types so they can be serialized to the
dashboard without extra dependencies.
"""

import math


def true_range(candles):
    ranges = []
    for index, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        if index == 0:
            ranges.append(high - low)
            continue
        prev_close = float(candles[index - 1]["close"])
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return ranges


def atr(candles, period=14):
    ranges = true_range(candles)
    if len(ranges) < period:
        return None
    initial = sum(ranges[:period]) / period
    result = initial
    for value in ranges[period:]:
        result = (result * (period - 1) + value) / period
    return result


def bollinger_bands(closes, period=20, std_dev=2.0):
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((value - mean) ** 2 for value in window) / period
    std = math.sqrt(variance)
    return {
        "upper": mean + std_dev * std,
        "middle": mean,
        "lower": mean - std_dev * std,
        "bandwidth": (4 * std) / mean if mean > 0 else 0,
    }


def stochastic(candles, period=14):
    if len(candles) < period:
        return None
    window = candles[-period:]
    highs = [float(c["high"]) for c in window]
    lows = [float(c["low"]) for c in window]
    current_close = float(candles[-1]["close"])
    highest = max(highs)
    lowest = min(lows)
    if highest == lowest:
        return 50.0
    return ((current_close - lowest) / (highest - lowest)) * 100


def momentum(closes, period=10):
    if len(closes) <= period:
        return None
    return closes[-1] - closes[-period]


def roc(closes, period=10):
    """Rate of change as a percentage."""
    if len(closes) <= period:
        return None
    previous = closes[-period - 1]
    if previous == 0:
        return None
    return ((closes[-1] - previous) / previous) * 100


def adx(candles, period=14):
    """Simplified ADX measuring trend strength (0-100)."""
    if len(candles) < period * 2:
        return None
    plus_dm = []
    minus_dm = []
    trs = []
    for index in range(1, len(candles)):
        high = float(candles[index]["high"])
        low = float(candles[index]["low"])
        prev_high = float(candles[index - 1]["high"])
        prev_low = float(candles[index - 1]["low"])
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
        trs.append(max(high - low, abs(high - float(candles[index - 1]["close"])), abs(low - float(candles[index - 1]["close"]))))
    if len(trs) < period:
        return None
    atr_val = sum(trs[-period:]) / period
    if atr_val == 0:
        return 0
    plus_di = (sum(plus_dm[-period:]) / period) / atr_val * 100
    minus_di = (sum(minus_dm[-period:]) / period) / atr_val * 100
    denom = plus_di + minus_di
    if denom == 0:
        return 0
    dx = abs(plus_di - minus_di) / denom * 100
    return dx


def obv(candles):
    """On-Balance Volume — cumulative volume signed by direction."""
    if not candles:
        return None
    total = 0.0
    for index, candle in enumerate(candles):
        if index == 0:
            continue
        prev_close = float(candles[index - 1]["close"])
        close = float(candle["close"])
        vol = float(candle["volume"])
        if close > prev_close:
            total += vol
        elif close < prev_close:
            total -= vol
    return total


def vwap(candles):
    """Volume-weighted average price across the full window."""
    if not candles:
        return None
    total_volume = sum(float(c["volume"]) for c in candles)
    if total_volume == 0:
        return None
    weighted = sum(((float(c["high"]) + float(c["low"]) + float(c["close"])) / 3) * float(c["volume"]) for c in candles)
    return weighted / total_volume


def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    changes = [current - previous for previous, current in zip(closes, closes[1:])][-period:]
    gains = sum(change for change in changes if change > 0) / period
    losses = -sum(change for change in changes if change < 0) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def ema(values, period):
    if not values:
        raise ValueError("values must not be empty")
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def macd(closes):
    if len(closes) < 26:
        return None
    line_values = [ema(closes[:i + 1], 12) - ema(closes[:i + 1], 26) for i in range(25, len(closes))]
    signal = ema(line_values[-9:], 9)
    return {"line": line_values[-1], "signal": signal, "histogram": line_values[-1] - signal}


def score_signal(indicators):
    """Combine all indicators into a weighted directional vote.

    Returns (direction, score, confidence, details) where score is -100..100
    and confidence is 0..100.
    """
    votes = []
    detail = {}

    bb = indicators.get("bollinger")
    if bb and bb["upper"] is not None:
        price = indicators.get("price", 0)
        if price > bb["upper"]:
            votes.append(("DOWN", 1.5, "BB overbought"))
            detail["bollinger"] = "overbought"
        elif price < bb["lower"]:
            votes.append(("UP", 1.5, "BB oversold"))
            detail["bollinger"] = "oversold"
        else:
            detail["bollinger"] = "neutral"

    stoch = indicators.get("stochastic")
    if stoch is not None:
        if stoch > 80:
            votes.append(("DOWN", 1.0, "Stoch overbought"))
            detail["stochastic"] = f"overbought ({stoch:.0f})"
        elif stoch < 20:
            votes.append(("UP", 1.0, "Stoch oversold"))
            detail["stochastic"] = f"oversold ({stoch:.0f})"
        else:
            detail["stochastic"] = f"neutral ({stoch:.0f})"

    rsi_val = indicators.get("rsi14")
    if rsi_val is not None:
        if rsi_val > 70:
            votes.append(("DOWN", 1.0, "RSI overbought"))
            detail["rsi"] = f"overbought ({rsi_val:.0f})"
        elif rsi_val < 30:
            votes.append(("UP", 1.0, "RSI oversold"))
            detail["rsi"] = f"oversold ({rsi_val:.0f})"
        else:
            detail["rsi"] = f"neutral ({rsi_val:.0f})"

    macd_data = indicators.get("macd")
    if macd_data:
        hist = macd_data["histogram"]
        if hist > 0:
            votes.append(("UP", 1.2, "MACD bullish"))
            detail["macd"] = "bullish"
        elif hist < 0:
            votes.append(("DOWN", 1.2, "MACD bearish"))
            detail["macd"] = "bearish"
        else:
            detail["macd"] = "neutral"

    mom = indicators.get("momentum")
    if mom is not None:
        if mom > 0:
            votes.append(("UP", 0.8, "Momentum positive"))
            detail["momentum"] = "positive"
        elif mom < 0:
            votes.append(("DOWN", 0.8, "Momentum negative"))
            detail["momentum"] = "negative"

    roc_val = indicators.get("roc")
    if roc_val is not None:
        if roc_val > 1:
            votes.append(("UP", 0.8, "ROC positive"))
            detail["roc"] = f"+{roc_val:.1f}%"
        elif roc_val < -1:
            votes.append(("DOWN", 0.8, "ROC negative"))
            detail["roc"] = f"{roc_val:.1f}%"

    adx_val = indicators.get("adx")
    if adx_val is not None:
        detail["adx"] = f"{adx_val:.0f} strength"

    ema_signal = indicators.get("emaSignal")
    if ema_signal:
        if ema_signal == "UP":
            votes.append(("UP", 1.5, "EMA 9/21 bullish"))
            detail["ema"] = "bullish"
        elif ema_signal == "DOWN":
            votes.append(("DOWN", 1.5, "EMA 9/21 bearish"))
            detail["ema"] = "bearish"
        else:
            detail["ema"] = "flat"

    vwap_val = indicators.get("vwap")
    price = indicators.get("price", 0)
    if vwap_val is not None and price:
        if price > vwap_val:
            votes.append(("UP", 0.5, "Above VWAP"))
            detail["vwap"] = "above"
        elif price < vwap_val:
            votes.append(("DOWN", 0.5, "Below VWAP"))
            detail["vwap"] = "below"

    up_weight = sum(w for direction, w, _ in votes if direction == "UP")
    down_weight = sum(w for direction, w, _ in votes if direction == "DOWN")
    total_weight = up_weight + down_weight

    if total_weight == 0:
        return "FLAT", 0, 0, detail

    score = ((up_weight - down_weight) / total_weight) * 100
    confidence = min(100, max(0, abs(score)))
    # A directional label is only emitted when trend, momentum, and strength
    # agree. This filters weak/contradictory setups; it is not a win-probability
    # estimate and does not imply a particular hit rate.
    adx_value = indicators.get("adx")
    macd_histogram = macd_data["histogram"] if macd_data else None
    aligned_up = ema_signal == "UP" and macd_histogram is not None and macd_histogram > 0
    aligned_down = ema_signal == "DOWN" and macd_histogram is not None and macd_histogram < 0

    if score >= 40 and adx_value is not None and adx_value >= 18 and aligned_up:
        direction = "UP"
    elif score <= -40 and adx_value is not None and adx_value >= 18 and aligned_down:
        direction = "DOWN"
    else:
        direction = "FLAT"
        detail["filter"] = "Needs stronger EMA, MACD, and ADX agreement"

    return direction, round(score, 1), round(confidence, 1), detail
