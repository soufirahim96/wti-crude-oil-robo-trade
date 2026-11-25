import time
import requests
import logging
from typing import List, Dict, Any
from adapters.base import BaseBrokerAdapter
from core.trading_settings import get_trading_settings

logger = logging.getLogger("HaG.MCL_TradingView")

class MCLTradingViewAdapter(BaseBrokerAdapter):
    """
    Free Market Data & Paper Trading Adapter for WTI Micro Crude Oil (MCL).
    Targeting the 3-month contract: January 2027 (MCLF2027 / MCLF27.NYM).
    - 100% Free: Uses TradingView & Yahoo Finance public data feeds with zero API fees.
    - Zero DB foot-print: Keeps candles and temporary paper positions strictly in container memory.
    """
    def __init__(self, contract: str = "MCLF27"):
        self.contract = contract
        self.tv_symbol = f"NYMEX:{contract}2027" if len(contract) <= 6 else contract
        self.yf_symbol = f"{contract}.NYM" if not contract.endswith(".NYM") else contract
        
        # In-memory ephemeral paper trading ledger (never saved to DB)
        self.virtual_balance = 5000.0  # $5,000 USD virtual demo capital
        self.active_position = None     # {"symbol": ..., "side": ..., "size": ..., "entryPrice": ..., "unrealizedProfit": ...}
        self.last_cached_price = 84.19
        self.in_memory_candles = []     # Ephemeral cache in container memory

    def fetch_ticker(self, symbol: str = None) -> Dict[str, Any]:
        """Fetches live / delayed price for January 2027 Micro Crude Oil."""
        # Method 1: Try TradingView scanner endpoint
        try:
            url = "https://scanner.tradingview.com/futures/scan"
            payload = {
                "symbols": {"tickers": [f"NYMEX:MCLF2027", "NYMEX:MCL1!"]},
                "columns": ["close", "change", "high", "low", "volume"]
            }
            r = requests.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    price = float(data[0].get("d", [])[0] or self.last_cached_price)
                    self.last_cached_price = price
                    return {
                        "symbol": "MCLF2027",
                        "price": price,
                        "change": float(data[0].get("d", [])[1] or 0.0),
                        "high": float(data[0].get("d", [])[2] or price),
                        "low": float(data[0].get("d", [])[3] or price),
                        "volume": float(data[0].get("d", [])[4] or 0.0)
                    }
        except Exception as e:
            logger.debug(f"TradingView ticker fallback: {e}")

        # Method 2: Fallback to Yahoo Finance chart API for MCLF27.NYM
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{self.yf_symbol}?interval=5m&range=1d"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]["meta"]
                price = float(res.get("regularMarketPrice", self.last_cached_price))
                self.last_cached_price = price
                return {"symbol": "MCLF2027", "price": price}
        except Exception as e:
            logger.debug(f"Yahoo ticker fallback: {e}")

        return {"symbol": "MCLF2027", "price": self.last_cached_price}

    def fetch_orderbook_spread(self, symbol: str = None) -> Dict[str, float]:
        """Estimated bid/ask spread for Micro WTI Crude Oil (typically 1-2 ticks = $0.01-$0.02)."""
        ticker = self.fetch_ticker(symbol)
        price = ticker["price"]
        spread = 0.02  # $0.02 / barrel = 2 ticks
        bid = price - 0.01
        ask = price + 0.01
        spread_pct = spread / price if price > 0 else 0.0002
        return {
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread": spread,
            "spread_pct": spread_pct
        }

    def fetch_candles(self, symbol: str = None, timeframe: str = "5m", limit: int = 50) -> List[List[Any]]:
        """
        Fetches 5m or 10m OHLCV candles without saving anything to the database.
        Returns: List of [timestamp_ms, open, high, low, close, volume]
        """
        # Determine Yahoo interval (5m base)
        yf_sym = self.yf_symbol
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=5m&range=5d"
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if r.status_code == 200:
                result = r.json()["chart"]["result"][0]
                timestamps = result.get("timestamp", [])
                quote = result["indicators"]["quote"][0]
                opens = quote.get("open", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                closes = quote.get("close", [])
                volumes = quote.get("volume", [])

                raw_candles = []
                for i in range(len(timestamps)):
                    if closes[i] is not None:
                        t = timestamps[i] * 1000
                        o = float(opens[i] or closes[i])
                        h = float(highs[i] or closes[i])
                        l = float(lows[i] or closes[i])
                        c = float(closes[i])
                        v = float(volumes[i] or 1.0)
                        raw_candles.append([t, o, h, l, c, v])

                if timeframe == "10m":
                    # Resample pairs of 5m candles into true 10m candles
                    candles_10m = []
                    for i in range(0, len(raw_candles) - 1, 2):
                        c1 = raw_candles[i]
                        c2 = raw_candles[i + 1]
                        candles_10m.append([
                            c1[0],
                            c1[1],
                            max(c1[2], c2[2]),
                            min(c1[3], c2[3]),
                            c2[4],
                            c1[5] + c2[5]
                        ])
                    return candles_10m[-limit:]
                
                return raw_candles[-limit:]
        except Exception as e:
            logger.warning(f"Error fetching free MCL candles: {e}")

        # If network error, return synthetic baseline from last price
        p = self.last_cached_price
        now = int(time.time() * 1000)
        return [[now - (i * 300000), p, p + 0.1, p - 0.1, p, 10.0] for i in range(limit, 0, -1)]

    def get_account_summary(self) -> Dict[str, Any]:
        """Provides in-memory simulated paper trading summary."""
        ticker = self.fetch_ticker()
        cur_price = ticker["price"]

        if self.active_position:
            side = str(self.active_position.get("side", "")).upper()
            entry = self.active_position["entryPrice"]
            size = self.active_position["size"]
            # 1 contract of MCL = 100 barrels. $0.01 move = $1.00 PnL
            is_long = side in ("LONG", "BUY")
            diff = (cur_price - entry) if is_long else (entry - cur_price)
            unrealized = round(diff * 100.0 * size, 2)
            self.active_position["unrealizedProfit"] = unrealized
            self.active_position["currentPrice"] = cur_price

        return {
            "walletBalance": self.virtual_balance,
            "availableBalance": round(self.virtual_balance - (1000.0 * (self.active_position["size"] if self.active_position else 0)), 2),
            "activePosition": self.active_position
        }

    def place_order(self, symbol: str, side: str, qty: float, order_type: str = "MARKET", price: float = None) -> Dict[str, Any]:
        """Simulates placing an order in container memory."""
        ticker = self.fetch_ticker(symbol)
        exec_price = price or ticker["price"]
        order_id = f"SIM-{int(time.time())}"
        norm_side = "LONG" if str(side).upper() in ("LONG", "BUY") else "SHORT"

        self.active_position = {
            "symbol": "MCLF2027",
            "side": norm_side,
            "size": qty,
            "entryPrice": exec_price,
            "unrealizedProfit": 0.0,
            "leverage": 1  # 1 MCL contract = 100 barrels
        }
        logger.info(f"[SIMULATED PAPER TRADE] {norm_side} {qty} MCLF2027 @ ${exec_price:.2f} (Order #{order_id})")
        return {"status": "FILLED", "orderId": order_id, "price": exec_price, "size": qty}

    def close_position(self, symbol: str = None) -> Dict[str, Any]:
        """Closes active position in memory and updates virtual balance."""
        if not self.active_position:
            return {"status": "NO_POSITION", "realized_pnl": 0.0}

        settings = get_trading_settings()
        commission_per_lot = float(settings.get("commission_per_lot", 3.0))

        ticker = self.fetch_ticker()
        cur_price = ticker["price"]
        side = str(self.active_position.get("side", "")).upper()
        entry = self.active_position["entryPrice"]
        size = self.active_position["size"]
        is_long = side in ("LONG", "BUY")
        diff = (cur_price - entry) if is_long else (entry - cur_price)
        gross_pnl = round(diff * 100.0 * size, 2)
        commission = round(commission_per_lot * size, 2)
        pnl = round(gross_pnl - commission, 2)

        self.virtual_balance = round(self.virtual_balance + pnl, 2)
        old_pos = self.active_position
        self.active_position = None

        logger.info(f"[SIMULATED PAPER CLOSE] Closed {size} lot(s) @ ${cur_price:.2f} | Gross: ${gross_pnl:.2f} | Comm: -${commission:.2f} | Net Realized PnL: ${pnl:.2f}. New Balance: ${self.virtual_balance:.2f}")
        return {
            "status": "CLOSED",
            "orderId": f"CLOSE-{int(time.time())}",
            "realized_pnl": pnl,
            "gross_pnl": gross_pnl,
            "commission": commission,
            "exit_price": cur_price
        }

    def fetch_multitimeframe_summary(self, symbol: str = None) -> Dict[str, Any]:
        """
        Synthesizes Multi-Timeframe (D1, 4H, 1H, 30m, 15m, 5m) SMC & Elliott Wave context.
        All kept in container memory; 0 bytes written to database.
        """
        cur_ticker = self.fetch_ticker(symbol)
        price = cur_ticker["price"]
        
        # Helper: Calculate EMA
        def calc_ema(prices: List[float], period: int) -> float:
            if not prices:
                return price
            if len(prices) < period:
                return round(sum(prices) / len(prices), 2)
            k = 2.0 / (period + 1)
            e = prices[0]
            for p in prices[1:]:
                e = (p * k) + (e * (1.0 - k))
            return round(e, 2)

        # Helper: Calculate RSI
        def calc_rsi(prices: List[float], period: int = 14) -> float:
            if len(prices) <= period:
                return 50.0
            diffs = [prices[i] - prices[i-1] for i in range(1, len(prices))]
            gains = [d for d in diffs if d > 0]
            losses = [abs(d) for d in diffs if d < 0]
            avg_gain = sum(gains[-period:]) / period if gains else 0.0001
            avg_loss = sum(losses[-period:]) / period if losses else 0.0001
            rs = avg_gain / avg_loss
            return round(100.0 - (100.0 / (1.0 + rs)), 1)

        # 1. Fetch 60m, 30m, 15m, 5m raw candles from Yahoo Finance
        candles_60m = []
        candles_30m = []
        candles_15m = []
        candles_5m = self.fetch_candles(timeframe="5m", limit=60)

        for tf, rng, target_list in [("60m", "5d", "60m"), ("30m", "5d", "30m"), ("15m", "5d", "15m")]:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{self.yf_symbol}?interval={tf}&range={rng}"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
                if r.status_code == 200:
                    res = r.json()["chart"]["result"][0]
                    ts = res.get("timestamp", [])
                    q = res["indicators"]["quote"][0]
                    closes = q.get("close", [])
                    highs = q.get("high", [])
                    lows = q.get("low", [])
                    opens = q.get("open", [])
                    vols = q.get("volume", [])
                    cdls = []
                    for i in range(len(ts)):
                        if closes[i] is not None:
                            cdls.append([
                                ts[i] * 1000,
                                float(opens[i] or closes[i]),
                                float(highs[i] or closes[i]),
                                float(lows[i] or closes[i]),
                                float(closes[i]),
                                float(vols[i] or 1.0)
                            ])
                    if target_list == "60m":
                        candles_60m = cdls
                    elif target_list == "30m":
                        candles_30m = cdls
                    elif target_list == "15m":
                        candles_15m = cdls
            except Exception as e:
                logger.debug(f"Multi-TF fetch fallback for {tf}: {e}")

        # Construct 4H candles from 60m candles (groups of 4)
        candles_4h = []
        if len(candles_60m) >= 4:
            for i in range(0, len(candles_60m) - 3, 4):
                chunk = candles_60m[i:i+4]
                candles_4h.append([
                    chunk[0][0],
                    chunk[0][1],
                    max(c[2] for c in chunk),
                    min(c[3] for c in chunk),
                    chunk[-1][4],
                    sum(c[5] for c in chunk)
                ])

        # Analyze 1D (Dynamic Moving Average & Range Structure)
        d_close = price
        d_ema9 = calc_ema([c[4] for c in candles_60m[-48:]] if candles_60m else [price], 9)
        d_ema21 = calc_ema([c[4] for c in candles_60m[-48:]] if candles_60m else [price], 21)
        d_trend = "BULLISH" if (d_close >= d_ema9 and d_ema9 >= d_ema21) else ("BEARISH" if (d_close <= d_ema9 and d_ema9 <= d_ema21) else "NEUTRAL")
        d_sw_high = round(price + 1.20, 2)
        d_sw_low = round(price - 1.10, 2)

        # Analyze 4H (SMC Macro Order Block & Elliott Wave)
        h4_closes = [c[4] for c in candles_4h] if candles_4h else [price]
        h4_trend = "BULLISH" if (len(h4_closes) > 1 and h4_closes[-1] >= h4_closes[0]) else "BEARISH"
        h4_sw_high = round(max([c[2] for c in candles_4h[-6:]]) if candles_4h else price + 0.8, 2)
        h4_sw_low = round(min([c[3] for c in candles_4h[-6:]]) if candles_4h else price - 0.8, 2)
        h4_ob_low = round(h4_sw_low + (h4_sw_high - h4_sw_low) * 0.25, 2)
        h4_ob_high = round(h4_sw_low + (h4_sw_high - h4_sw_low) * 0.38, 2)
        h4_elliott = "Wave 3 Impulse Expansion" if h4_trend == "BULLISH" else "Wave C Corrective Descent"

        # Analyze 1H (Hourly Safe Entry Zone)
        h1_closes = [c[4] for c in candles_60m] if candles_60m else [price]
        h1_rsi = calc_rsi(h1_closes)
        h1_trend = "BULLISH" if h1_rsi >= 50 else "BEARISH"
        h1_sw_high = round(max([c[2] for c in candles_60m[-12:]]) if candles_60m else price + 0.5, 2)
        h1_sw_low = round(min([c[3] for c in candles_60m[-12:]]) if candles_60m else price - 0.5, 2)
        h1_safe_low = round(price - 0.12, 2)
        h1_safe_high = round(price + 0.08, 2)
        h1_elliott = "Wave 4 Pullback into Safe Demand" if h1_rsi < 55 else "Wave 5 Thrust toward Liquidity"

        # ----------------------------------------------------
        # TIER 1: POTENTIAL CONFLUENCE (D1, 4H, 1H, 30m)
        # Macro SMC order flow and Elliott Wave cycle filters
        # ----------------------------------------------------
        m30_closes = [c[4] for c in candles_30m] if candles_30m else [price]
        m30_rsi = calc_rsi(m30_closes)
        m30_trend = "BULLISH" if m30_rsi >= 50 else "BEARISH"
        m30_elliott = "Wave 3 Impulse Expansion" if m30_trend == "BULLISH" else "Wave C Corrective Channel"

        macro_bull_votes = sum([d_trend == "BULLISH", h4_trend == "BULLISH", h1_trend == "BULLISH", m30_trend == "BULLISH"])
        macro_bear_votes = sum([d_trend == "BEARISH", h4_trend == "BEARISH", h1_trend == "BEARISH", m30_trend == "BEARISH"])

        # ----------------------------------------------------
        # TIER 2: ADVANCE CONFLUENCE & 15m SWING RETEST (15m)
        # 15m is the primary operational trend that price follows.
        # Swing retest to avoid price chasing ONLY follows 15m (3-4 candles lookback = 45-60 mins).
        # ----------------------------------------------------
        m15_closes = [c[4] for c in candles_15m] if candles_15m else [price]
        m15_rsi = calc_rsi(m15_closes)
        m15_trend = "BULLISH" if m15_rsi >= 50 else "BEARISH"
        m15_elliott = "Wave 3 / Wave 5 Impulse Thrust" if m15_trend == "BULLISH" else "Wave A / Wave C Descent"

        # Confluence Weighting: Macro potential confluence + 15m Advance Confluence precedence
        bull_score = macro_bull_votes + (2 if m15_trend == "BULLISH" else 0)
        bear_score = macro_bear_votes + (2 if m15_trend == "BEARISH" else 0)
        if bull_score > bear_score:
            aligned_bias = "LONG"
        elif bear_score > bull_score:
            aligned_bias = "SHORT"
        else:
            aligned_bias = "LONG" if m15_trend == "BULLISH" else "SHORT"

        # ----------------------------------------------------
        # TIER 2: ADVANCE GUIDANCE SAFE ZONE (15m, 4 Candles = 60 mins)
        # Guides the overarching structural path and macro liquidity targets
        # ----------------------------------------------------
        c_recent_15m = candles_15m[-4:] if len(candles_15m) >= 4 else (candles_15m if candles_15m else [[0, price, price+0.08, price-0.08, price, 0]])
        m15_sw_high = round(max([c[2] for c in c_recent_15m]), 2)
        m15_sw_low = round(min([c[3] for c in c_recent_15m]), 2)
        m15_advance_safe_zone = f"${m15_sw_low} - ${m15_sw_high}"

        # ----------------------------------------------------
        # TIER 3: OPERATIONAL SAFE ZONE & EXECUTION RETEST (5m, 4 Candles = 20 mins)
        # Captures what is really currently happening in the active market price
        # ----------------------------------------------------
        m5_closes = [c[4] for c in candles_5m] if candles_5m else [price]
        m5_ema7 = calc_ema(m5_closes, 7)
        m5_ema9 = calc_ema(m5_closes, 9)
        m5_ema18 = calc_ema(m5_closes, 18)
        m5_ema21 = calc_ema(m5_closes, 21)
        m5_rsi = calc_rsi(m5_closes)
        m5_cross = "BULLISH_CROSS" if m5_ema7 > m5_ema9 else "BEARISH_CROSS"

        c_recent_5m = candles_5m[-4:] if len(candles_5m) >= 4 else (candles_5m if candles_5m else [[0, price, price+0.04, price-0.04, price, 0]])
        m5_sw_high = round(max([c[2] for c in c_recent_5m]), 2)
        m5_sw_low = round(min([c[3] for c in c_recent_5m]), 2)
        m5_range = max(0.04, round(m5_sw_high - m5_sw_low, 2))

        # Operational Retest & Execution Boundaries (Bidirectional 5m, Guided by 15m)
        zone_width = max(0.03, round(m5_range * 0.45, 2))

        # Long Setup (15m Compass Directional: 5-10 tick TP, 6-8 tick SL)
        long_zone_low = m5_sw_low
        long_zone_high = round(long_zone_low + zone_width, 2)
        long_safe_zone = f"${long_zone_low} - ${long_zone_high}"
        long_in_entry_zone = (long_zone_low - 0.02) <= price <= (long_zone_high + 0.02)
        long_is_extended = price > (long_zone_high + 0.03)
        # Target strictly 5 to 10 ticks (clamp realistic 5m scalp)
        raw_long_tp = round(m5_sw_high if (m5_sw_high - price) >= 0.05 else (price + 0.08), 2)
        long_target = min(raw_long_tp, round(price + 0.09, 2))
        if long_target < round(price + 0.05, 2):
            long_target = round(price + 0.08, 2)
        # Stop loss strictly 6 to 8 ticks below entry
        long_sl = round(max(long_zone_low - 0.02, price - 0.07), 2)
        if long_sl >= price:
            long_sl = round(price - 0.07, 2)
        long_inval = round(long_sl - 0.02, 2)
        long_ticks = int(round(abs(long_target - price) / 0.01))

        # Short Setup (15m Compass Directional: 5-10 tick TP, 6-8 tick SL)
        short_zone_high = m5_sw_high
        short_zone_low = round(short_zone_high - zone_width, 2)
        short_safe_zone = f"${short_zone_low} - ${short_zone_high}"
        short_in_entry_zone = (short_zone_low - 0.02) <= price <= (short_zone_high + 0.02)
        short_is_extended = price < (short_zone_low - 0.03)
        # Target strictly 5 to 10 ticks (clamp realistic 5m scalp)
        raw_short_tp = round(m5_sw_low if (price - m5_sw_low) >= 0.05 else (price - 0.08), 2)
        short_target = max(raw_short_tp, round(price - 0.09, 2))
        if short_target > round(price - 0.05, 2):
            short_target = round(price - 0.08, 2)
        # Stop loss strictly 6 to 8 ticks above entry
        short_sl = round(min(short_zone_high + 0.02, price + 0.07), 2)
        if short_sl <= price:
            short_sl = round(price + 0.07, 2)
        short_inval = round(short_sl + 0.02, 2)
        short_ticks = int(round(abs(price - short_target) / 0.01))

        # Primary baseline follows aligned_bias
        if aligned_bias == "LONG":
            operational_safe_zone = long_safe_zone
            in_entry_zone = long_in_entry_zone
            is_extended = long_is_extended
            zone_low = long_zone_low
            zone_high = long_zone_high
            target_price = long_target
            contingency_sl = long_sl
            invalidation_level = long_inval
            structural_ticks = long_ticks
        else:
            operational_safe_zone = short_safe_zone
            in_entry_zone = short_in_entry_zone
            is_extended = short_is_extended
            zone_low = short_zone_low
            zone_high = short_zone_high
            target_price = short_target
            contingency_sl = short_sl
            invalidation_level = short_inval
            structural_ticks = short_ticks

        return {
            "current_price": price,
            "daily": {
                "trend": d_trend,
                "ema9": d_ema9,
                "ema21": d_ema21,
                "swing_high": d_sw_high,
                "swing_low": d_sw_low,
                "safe_zone": f"${d_sw_low} - ${d_sw_high}",
                "smc_bias": "Discount Institutional Zone" if price < (d_sw_high + d_sw_low)/2 else "Premium Premium Zone",
                "elliott_phase": "Primary Wave 3 Macro Trend"
            },
            "h4": {
                "trend": h4_trend,
                "swing_high": h4_sw_high,
                "swing_low": h4_sw_low,
                "order_block": f"${h4_ob_low} - ${h4_ob_high}",
                "safe_zone": f"${h4_ob_low} - ${h4_ob_high}",
                "elliott_phase": h4_elliott
            },
            "h1": {
                "trend": h1_trend,
                "rsi": h1_rsi,
                "hourly_safe_zone": f"${h1_safe_low} - ${h1_safe_high}",
                "safe_zone": f"${h1_safe_low} - ${h1_safe_high}",
                "swing_high": h1_sw_high,
                "swing_low": h1_sw_low,
                "elliott_phase": h1_elliott
            },
            "m30": {
                "trend": m30_trend,
                "rsi": m30_rsi,
                "safe_zone": f"${round(price - 0.20, 2)} - ${round(price + 0.20, 2)}",
                "elliott_phase": m30_elliott
            },
            "m15": {
                "trend": m15_trend,
                "rsi": m15_rsi,
                "elliott_phase": m15_elliott,
                "swing_high": m15_sw_high,
                "swing_low": m15_sw_low,
                "advance_guidance_safe_zone": m15_advance_safe_zone,
                "safe_zone": m15_advance_safe_zone
            },
            "m30_m15": {
                "trend": m15_trend,
                "rsi": m15_rsi,
                "safe_zone": operational_safe_zone,
                "advance_safe_zone": m15_advance_safe_zone,
                "local_fvg": f"${round(price - 0.04, 2)} - ${round(price + 0.03, 2)}"
            },
            "m5_trigger": {
                "ema7": m5_ema7,
                "ema9": m5_ema9,
                "ema18": m5_ema18,
                "ema21": m5_ema21,
                "cross_state": m5_cross,
                "rsi": m5_rsi,
                "operational_safe_zone": operational_safe_zone,
                "safe_zone": operational_safe_zone
            },
            "alignment_synthesis": {
                "aligned_bias": aligned_bias,
                "confluence_score": f"Macro: {macro_bull_votes}B/{macro_bear_votes}S | 15m Guidance: {m15_trend} | 5m Trigger: {m5_cross}",
                "safe_zone_range": operational_safe_zone,
                "advance_guidance_bias": m15_trend,
                "advance_guidance_zone": m15_advance_safe_zone,
                "in_entry_zone": in_entry_zone,
                "is_extended": is_extended,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "target_ticks": structural_ticks,
                "target_price": target_price,
                "target_17_ticks": target_price,
                "contingency_sl": contingency_sl,
                "invalidation_level": invalidation_level,
                "ticks_value": structural_ticks,
                "tick_price_gain": round(abs(target_price - price), 2),
                # Explicit Bidirectional Presets (Prevents Inverted SL/TP)
                "long_target": long_target,
                "long_sl": long_sl,
                "long_invalidation": long_inval,
                "long_safe_zone": long_safe_zone,
                "long_ticks": long_ticks,
                "short_target": short_target,
                "short_sl": short_sl,
                "short_invalidation": short_inval,
                "short_safe_zone": short_safe_zone,
                "short_ticks": short_ticks
            }
        }


mcl_adapter = MCLTradingViewAdapter("MCLF27")
