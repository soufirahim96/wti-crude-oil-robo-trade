from typing import List, Dict, Any

def calculate_ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 2)

def calculate_rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
            
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)

def calculate_atr(candles: List[List[Any]], period: int = 14) -> float:
    if len(candles) < 2:
        return 1.0
    tr_list = []
    for i in range(1, len(candles)):
        h = float(candles[i][2])
        l = float(candles[i][3])
        prev_c = float(candles[i-1][4])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
    if not tr_list:
        return 1.0
    return sum(tr_list[-period:]) / min(len(tr_list), period)

def calculate_adx(candles: List[List[Any]], period: int = 7) -> float:
    """
    Calculates Welles Wilder's Average Directional Index (ADX) over specified period (default 7 for fast scalper response).
    Returns float rounded to 2 decimal places.
    """
    if len(candles) < period * 2:
        return 25.0
        
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    
    tr_list = []
    plus_dm = []
    minus_dm = []
    
    for i in range(1, len(candles)):
        h = highs[i]
        l = lows[i]
        prev_h = highs[i-1]
        prev_l = lows[i-1]
        prev_c = closes[i-1]
        
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
        
        up_move = h - prev_h
        down_move = prev_l - l
        
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        
    if len(tr_list) < period:
        return 25.0
        
    smooth_tr = sum(tr_list[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    
    dx_list = []
    p_di = (smooth_plus / smooth_tr * 100.0) if smooth_tr > 0 else 0.0
    m_di = (smooth_minus / smooth_tr * 100.0) if smooth_tr > 0 else 0.0
    di_sum = p_di + m_di
    dx_list.append(abs(p_di - m_di) / di_sum * 100.0 if di_sum > 0 else 0.0)
    
    for i in range(period, len(tr_list)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr_list[i]
        smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
        smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]
        p_di = (smooth_plus / smooth_tr * 100.0) if smooth_tr > 0 else 0.0
        m_di = (smooth_minus / smooth_tr * 100.0) if smooth_tr > 0 else 0.0
        di_sum = p_di + m_di
        dx_list.append(abs(p_di - m_di) / di_sum * 100.0 if di_sum > 0 else 0.0)
        
    if len(dx_list) < period:
        return round(sum(dx_list) / len(dx_list), 2)
        
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period
        
    return round(adx, 2)

def calculate_stochastic(candles: List[List[Any]], k_period: int = 14, d_period: int = 3) -> Dict[str, Any]:
    """
    Calculates Fast/Slow Stochastic Oscillator (%K and %D) over specified lookback period.
    Returns dict with k, d, k_prev, d_prev, cross_up, cross_down.
    """
    if len(candles) < k_period:
        return {"k": 50.0, "d": 50.0, "k_prev": 50.0, "d_prev": 50.0, "cross_up": False, "cross_down": False}
    
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    k_values = []
    for i in range(k_period - 1, len(candles)):
        window_high = max(highs[i - k_period + 1 : i + 1])
        window_low = min(lows[i - k_period + 1 : i + 1])
        c_price = closes[i]
        denom = window_high - window_low
        k_val = ((c_price - window_low) / denom * 100.0) if denom > 0 else 50.0
        k_values.append(k_val)

    if not k_values:
        return {"k": 50.0, "d": 50.0, "k_prev": 50.0, "d_prev": 50.0, "cross_up": False, "cross_down": False}

    d_values = []
    for i in range(len(k_values)):
        start = max(0, i - d_period + 1)
        sub = k_values[start : i + 1]
        d_values.append(sum(sub) / len(sub))

    cur_k = round(k_values[-1], 2)
    cur_d = round(d_values[-1], 2)
    prev_k = round(k_values[-2], 2) if len(k_values) > 1 else cur_k
    prev_d = round(d_values[-2], 2) if len(d_values) > 1 else cur_d

    cross_up = (prev_k <= prev_d) and (cur_k > cur_d)
    cross_down = (prev_k >= prev_d) and (cur_k < cur_d)

    return {
        "k": cur_k,
        "d": cur_d,
        "k_prev": prev_k,
        "d_prev": prev_d,
        "cross_up": cross_up,
        "cross_down": cross_down
    }

def extract_market_features(candles: List[List[Any]]) -> Dict[str, Any]:
    """
    Standardizes raw OHLCV candles into actionable market features for the 2-Stage Framework.
    Candle format: [timestamp, open, high, low, close, volume]
    """
    if not candles or len(candles) < 10:
        return {}
        
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    
    current_price = closes[-1]
    ema7 = calculate_ema(closes, 7)
    ema9 = calculate_ema(closes, 9)
    ema18 = calculate_ema(closes, 18)
    ema21 = calculate_ema(closes, 21)
    
    # Previous candle EMAs to detect fresh crosses
    ema7_prev = calculate_ema(closes[:-1], 7) if len(closes) > 1 else ema7
    ema9_prev = calculate_ema(closes[:-1], 9) if len(closes) > 1 else ema9
    ema18_prev = calculate_ema(closes[:-1], 18) if len(closes) > 1 else ema18
    ema21_prev = calculate_ema(closes[:-1], 21) if len(closes) > 1 else ema21
    
    # Fast reaction: EMA 7 vs EMA 9
    cross_7_above_9 = (ema7_prev <= ema9_prev) and (ema7 > ema9)
    cross_7_below_9 = (ema7_prev >= ema9_prev) and (ema7 < ema9)

    # Momentum vs some-time average: EMA 9 vs EMA 18
    cross_9_above_18 = (ema9_prev <= ema18_prev) and (ema9 > ema18)
    cross_9_below_18 = (ema9_prev >= ema18_prev) and (ema9 < ema18)

    cross_above = (ema9_prev <= ema21_prev) and (ema9 > ema21)
    cross_below = (ema9_prev >= ema21_prev) and (ema9 < ema21)
    
    rsi = calculate_rsi(closes, 14)
    adx = calculate_adx(candles, 7)
    stoch = calculate_stochastic(candles, 14, 3)
    
    # Swing Support & Resistance over preceding 14 candles (excluding current active candle)
    lookback = min(14, len(candles) - 1)
    if lookback > 0:
        resistance = max(highs[-lookback-1:-1])
        support = min(lows[-lookback-1:-1])
    else:
        resistance = highs[-1]
        support = lows[-1]
        
    close_above_resistance = current_price > resistance
    close_below_support = current_price < support
    
    # User's Candle Close Rule: Confirmed only if price closes upper or lower candle
    prev_candle = candles[-2] if len(candles) >= 2 else candles[-1]
    prev_open = float(prev_candle[1])
    prev_close = float(prev_candle[4])
    prev_high = float(prev_candle[2])
    prev_low = float(prev_candle[3])
    
    close_upper_candle = (current_price > max(prev_open, prev_close)) and (current_price > ema7)
    close_lower_candle = (current_price < min(prev_open, prev_close)) and (current_price < ema7)
    
    close_above_prev_open = current_price > prev_open
    close_below_prev_open = current_price < prev_open
    
    # Candle Gegar Filter (Violent candle range > 2.5x ATR)
    atr = calculate_atr(candles, 14)
    current_candle_range = highs[-1] - lows[-1]
    candle_gegar = current_candle_range > (2.5 * atr) if atr > 0 else False
    
    trend = "NEUTRAL"
    if current_price > ema7 > ema9 > ema18:
        trend = "STRONG_BULLISH (EMA 7>9>18)"
    elif current_price < ema7 < ema9 < ema18:
        trend = "STRONG_BEARISH (EMA 7<9<18)"
    elif current_price > ema9 > ema21:
        trend = "BULLISH_UPTREND"
    elif current_price < ema9 < ema21:
        trend = "BEARISH_DOWNTREND"
        
    return {
        "price": current_price,
        "ema7": ema7,
        "ema9": ema9,
        "ema18": ema18,
        "ema21": ema21,
        "ema7_prev": ema7_prev,
        "ema9_prev": ema9_prev,
        "ema18_prev": ema18_prev,
        "ema21_prev": ema21_prev,
        "cross_7_above_9": cross_7_above_9,
        "cross_7_below_9": cross_7_below_9,
        "cross_9_above_18": cross_9_above_18,
        "cross_9_below_18": cross_9_below_18,
        "cross_above": cross_above,
        "cross_below": cross_below,
        "close_upper_candle": close_upper_candle,
        "close_lower_candle": close_lower_candle,
        "prev_open": round(prev_open, 2),
        "prev_close": round(prev_close, 2),
        "prev_high": round(prev_high, 2),
        "prev_low": round(prev_low, 2),
        "close_above_prev_open": close_above_prev_open,
        "close_below_prev_open": close_below_prev_open,
        "rsi": rsi,
        "adx": adx,
        "adx_trending": adx > 23.0,
        "stoch_k": stoch["k"],
        "stoch_d": stoch["d"],
        "stoch_k_prev": stoch["k_prev"],
        "stoch_d_prev": stoch["d_prev"],
        "stoch_cross_up": stoch["cross_up"],
        "stoch_cross_down": stoch["cross_down"],
        "stoch_oversold": stoch["k"] < 20.0,
        "stoch_overbought": stoch["k"] > 80.0,
        "resistance": round(resistance, 2),
        "support": round(support, 2),
        "close_above_resistance": close_above_resistance,
        "close_below_support": close_below_support,
        "candle_gegar": candle_gegar,
        "high24h": max(highs[-24:]) if len(highs) >= 24 else max(highs),
        "low24h": min(lows[-24:]) if len(lows) >= 24 else min(lows),
        "trend": trend
    }
