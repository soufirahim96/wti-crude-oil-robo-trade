import time

import logging

import asyncio

from backend.config import config

from core.strategy_logic import extract_market_features

from core.gemini_brain import gemini_brain, get_active_custom_rules

from core.trading_settings import get_trading_settings

import re

def get_dynamic_ratchet_rules():

    """Reads trailing stop parameters dynamically from active Analysis Logic Registry (Item 3)."""

    rules = get_active_custom_rules()

    for r in reversed(rules):

        desc = r.get("description", "")

        # Match "reach 5 or more ticks -> increase SL by 5" or "every 5 ticks profit -> advance SL by 5"

        m = re.search(r'(?:reach|every|once)\s*(\d+)\s*(?:or more)?\s*ticks?.*?auto make the sl increase by\s*(\d+)\s*tick', desc, re.IGNORECASE)

        if m:

            return float(m.group(1)), float(m.group(2))

        m2 = re.search(r'(?:every|once|reach)\s*(\d+)\s*ticks?.*?advance\s*SL\s*by\s*(\d+)\s*ticks?', desc, re.IGNORECASE)

        if m2:

            return float(m2.group(1)), float(m2.group(2))

    # Default to 5-tick trigger and 5-tick advance as agreed in Item 3

    return 5.0, 5.0

from core.journal import log_thought, log_trade, get_recent_trades

from adapters.binance_adapter import binance_adapter

from adapters.mcl_tradingview_adapter import mcl_adapter

def get_broker(symbol: str):

    if "MCL" in symbol:

        return mcl_adapter

    return binance_adapter

logger = logging.getLogger("HaG.Agent")

class HermesTradingAgent:

    def __init__(self):

        self.is_running = False

        self.last_cycle_time = 0.0

        self.last_macro_time = 0.0

        self.last_entry_time = None

        self.position_entry_time = None

        self.position_entry_price = None

        self.position_side = None

        self.position_sl = None

        self.position_tp = None

        self.locked_15m_bucket = None

        self.locked_decision = None

        self.last_exit_time = None

        self.last_exit_reason = None

        self.last_flat_thought_ts = 0.0

        self.last_flat_thought_key = ""

    def _log_flat_thought(self, force: bool = False, **kwargs):
        """Throttle flat HOLD thought_journal writes to once every 60s unless forced or gate changes."""
        now = time.time()
        key_used = str(kwargs.get("key_used", ""))
        if force or (now - self.last_flat_thought_ts >= 60.0) or (key_used != self.last_flat_thought_key):
            log_thought(**kwargs)
            self.last_flat_thought_ts = now
            self.last_flat_thought_key = key_used

    def execute_cycle(self, is_macro: bool = False) -> dict:

        """

        Executes one autonomous cycle:

        - If is_macro=True: Uses M10 chart and Gemini 4 Hydrogen High (Superior macro reasoning).

        - If is_macro=False: Uses M5 chart and Gemini 4 Hydrogen Medium / Low cascade (Fast 5-minute sentry).

        Follows user's Stage 1 & Stage 2 Framework:

        - Stage 1: Buy (EMA9 cross 21 + RSI > 55 + Close > Resistance). Sell (EMA9 cross 21 + RSI < 45 + Close < Support).

        - Stage 2: Reversal Exit on opposite EMA cross, 4-hour hold limit, or continuation entries.

        """

        symbol = config.TARGET_SYMBOL

        timeframe = config.TIMEFRAME_MACRO if is_macro else config.TIMEFRAME_FAST

        model_tier = "superior" if is_macro else "fast"

        # 1. Perception & Features on active timeframe (5m or 10m)

        broker = get_broker(symbol)

        is_mcl = "MCL" in symbol

        ticker = broker.fetch_ticker(symbol)

        candles = broker.fetch_candles(symbol, timeframe=timeframe, limit=50)

        features = extract_market_features(candles)

        spread_info = broker.fetch_orderbook_spread(symbol)

        account = broker.get_account_summary()

        current_pos = account.get("activePosition")

        wallet_balance = account.get("walletBalance", 5000.0)

        tick_val = 0.01 if is_mcl else config.TICK_VALUE

        trading_settings = get_trading_settings()

        trade_qty = float(trading_settings.get("lot_size", 1))

        available_balance = float(account.get("availableBalance", wallet_balance))

        # ----------------------------------------------------

        # STAGE 2: ACTIVE POSITION MANAGEMENT (IF IN POSITION)

        # ----------------------------------------------------

        if current_pos:

            if not self.position_entry_time:

                recent_tr = get_recent_trades(1)

                if recent_tr and recent_tr[0].get("side") in ("BUY", "SELL", "LONG", "SHORT"):

                    self.position_entry_time = recent_tr[0]["timestamp"]

                else:

                    self.position_entry_time = time.time()

            pos_side = str(current_pos.get("side", "")).upper()

            is_long = pos_side in ("LONG", "BUY")

            is_short = pos_side in ("SHORT", "SELL")

            floating_pnl = current_pos.get("unrealizedProfit", 0.0)

            floating_loss_pct = (abs(floating_pnl) / wallet_balance) if (floating_pnl < 0 and wallet_balance > 0) else 0.0

            force_close = False

            close_reason = ""

            # Guaranteed Profit Lock & Dynamic Trailing Ratchet (100% driven by active Analysis Logic Registry)

            if self.position_entry_price:

                trigger_ticks, lock_ticks = get_dynamic_ratchet_rules()

                if trigger_ticks is not None and lock_ticks is not None:

                    if is_long:

                        profit_ticks = (ticker["price"] - self.position_entry_price) / tick_val

                        if profit_ticks >= trigger_ticks:

                            # Auto increase SL to +5 ticks profit relative to entry (or trail higher)

                            locked_profit_sl = round(self.position_entry_price + (lock_ticks * tick_val), 2)

                            ratchet_sl = max(locked_profit_sl, round(ticker["price"] - (3 * tick_val), 2))

                            if not self.position_sl or ratchet_sl > self.position_sl:

                                logger.info(f"[PROFIT LOCK RATCHET] Profit +{profit_ticks:.1f}t (>={trigger_ticks}t). Locked LONG SL from ${self.position_sl} to ${ratchet_sl} (+{lock_ticks}t profit lock).")

                                self.position_sl = ratchet_sl

                    elif is_short:

                        profit_ticks = (self.position_entry_price - ticker["price"]) / tick_val

                        if profit_ticks >= trigger_ticks:

                            # Auto increase SL to +5 ticks profit relative to entry (or trail lower)

                            locked_profit_sl = round(self.position_entry_price - (lock_ticks * tick_val), 2)

                            ratchet_sl = min(locked_profit_sl, round(ticker["price"] + (3 * tick_val), 2))

                            if not self.position_sl or ratchet_sl < self.position_sl:

                                logger.info(f"[PROFIT LOCK RATCHET] Profit +{profit_ticks:.1f}t (>={trigger_ticks}t). Locked SHORT SL from ${self.position_sl} to ${ratchet_sl} (+{lock_ticks}t profit lock).")

                                self.position_sl = ratchet_sl

            # Dynamic Thesis Stop Loss, Profit Lock, or Take Profit hit check

            if self.position_sl and self.position_tp:

                if is_long:

                    if ticker["price"] <= self.position_sl:

                        force_close = True

                        is_profit_sl = self.position_sl >= (self.position_entry_price or 0)

                        close_reason = f"{'Guaranteed Profit Lock' if is_profit_sl else 'Thesis Stop Loss'} hit at ${ticker['price']} (SL: ${self.position_sl})."

                    elif ticker["price"] >= self.position_tp:

                        force_close = True

                        close_reason = f"Thesis Take Profit hit at ${ticker['price']} (TP: ${self.position_tp})."

                elif is_short:

                    if ticker["price"] >= self.position_sl:

                        force_close = True

                        is_profit_sl = self.position_sl <= (self.position_entry_price or float('inf'))

                        close_reason = f"{'Guaranteed Profit Lock' if is_profit_sl else 'Thesis Stop Loss'} hit at ${ticker['price']} (SL: ${self.position_sl})."

                    elif ticker["price"] <= self.position_tp:

                        force_close = True

                        close_reason = f"Thesis Take Profit hit at ${ticker['price']} (TP: ${self.position_tp})."

            if force_close:

                self.last_exit_time = time.time()

                self.last_exit_reason = close_reason

                close_res = broker.close_position(symbol)

                log_thought(

                    symbol=symbol,

                    price=ticker["price"],

                    rsi=features.get("rsi", 0.0),

                    trend=features.get("trend", "NEUTRAL"),

                    action="CLOSE",

                    confidence=1.0,

                    reasoning=close_reason,

                    key_used=f"HERMES_{timeframe.upper()}"

                )

                actual_pnl = close_res.get("realized_pnl", floating_pnl)

                actual_exit_price = close_res.get("exit_price", ticker["price"])

                actual_comm = close_res.get("commission", 0.0)

                log_trade(symbol, "CLOSE", current_pos["size"], actual_exit_price, close_res.get("orderId"), "CLOSED", actual_pnl, actual_comm)

                # Reset position tracking

                self.position_entry_time = None

                self.position_entry_price = None

                self.position_side = None

                self.position_sl = None

                self.position_tp = None

                self.locked_15m_bucket = None

                self.locked_decision = None

                return {

                    "timestamp": time.time(),

                    "action": "CLOSE",

                    "reason": close_reason,

                    "account": broker.get_account_summary()

                }

        # ----------------------------------------------------

        # FILTER CHECKS: SPREAD & CANDLE GEGAR

        # ----------------------------------------------------

        spread_pct = spread_info.get("spread_pct", 0.0)

        if spread_pct > config.MAX_SPREAD_PCT:

            reason = f"Filter active: Orderbook spread is high ({spread_pct:.4%} > {config.MAX_SPREAD_PCT:.4%}). Entry withheld."

            log_thought(symbol, ticker["price"], features.get("rsi", 0.0), features.get("trend", "NEUTRAL"), "HOLD", 0.0, reason, "SPREAD_FILTER")

            return {"timestamp": time.time(), "action": "HOLD", "reason": reason}

        if features.get("candle_gegar"):

            reason = f"Filter active: Candle gegar detected on {timeframe} (erratic volatility spike > 2.5x ATR). Entry withheld."

            log_thought(symbol, ticker["price"], features.get("rsi", 0.0), features.get("trend", "NEUTRAL"), "HOLD", 0.0, reason, "GEGAR_FILTER")

            return {"timestamp": time.time(), "action": "HOLD", "reason": reason}

        adx_val = features.get("adx", 25.0)

        # ----------------------------------------------------

        # BRAIN SYNTHESIS (GEMINI EVALUATION & MULTI-TIMEFRAME)

        # ----------------------------------------------------

        holding_h = (time.time() - self.position_entry_time) / 3600.0 if (current_pos and self.position_entry_time) else 0.0

        float_pnl = current_pos.get("unrealizedProfit", 0.0) if current_pos else 0.0

        multi_tf_data = {}

        if hasattr(broker, "fetch_multitimeframe_summary"):

            try:

                multi_tf_data = broker.fetch_multitimeframe_summary(symbol)

            except Exception as e:

                logger.warning(f"Error fetching multi-timeframe summary: {e}")

        market_snapshot = {

            "symbol": symbol,

            "timeframe": timeframe,

            "price": ticker["price"],

            "ema7": features.get("ema7"),

            "ema9": features.get("ema9"),

            "ema18": features.get("ema18"),

            "ema21": features.get("ema21"),

            "ema7_prev": features.get("ema7_prev"),

            "ema9_prev": features.get("ema9_prev"),

            "ema18_prev": features.get("ema18_prev"),

            "ema21_prev": features.get("ema21_prev"),

            "cross_7_above_9": features.get("cross_7_above_9"),

            "cross_7_below_9": features.get("cross_7_below_9"),

            "cross_9_above_18": features.get("cross_9_above_18"),

            "cross_9_below_18": features.get("cross_9_below_18"),

            "cross_above": features.get("cross_above"),

            "cross_below": features.get("cross_below"),

            "close_upper_candle": features.get("close_upper_candle"),

            "close_lower_candle": features.get("close_lower_candle"),

            "prev_open": features.get("prev_open"),

            "prev_close": features.get("prev_close"),

            "prev_high": features.get("prev_high"),

            "prev_low": features.get("prev_low"),

            "rsi": features.get("rsi"),

            "adx": adx_val,

            "adx_trending": features.get("adx_trending", True),

            "stoch_k": features.get("stoch_k"),

            "stoch_d": features.get("stoch_d"),

            "stoch_cross_up": features.get("stoch_cross_up"),

            "stoch_cross_down": features.get("stoch_cross_down"),

            "stoch_oversold": features.get("stoch_oversold"),

            "stoch_overbought": features.get("stoch_overbought"),

            "resistance": features.get("resistance"),

            "support": features.get("support"),

            "close_above_resistance": features.get("close_above_resistance"),

            "close_below_support": features.get("close_below_support"),

            "prev_open": features.get("prev_open"),

            "close_above_prev_open": features.get("close_above_prev_open"),

            "close_below_prev_open": features.get("close_below_prev_open"),

            "spread_pct": spread_pct,

            "candle_gegar": features.get("candle_gegar"),

            "current_position": current_pos["side"] if current_pos else "NONE",

            "holding_hours": holding_h,

            "floating_pnl": float_pnl,

            "multi_tf": multi_tf_data

        }

        # ----------------------------------------------------

        # 15m CLOSED-CANDLE THOUGHT LOCK (AVOID MOVING GOALPOSTS WHEN FLAT)

        # ----------------------------------------------------

        candle_15m_bucket = int(time.time() // 900)  # 900s = 15m candle boundary

        syn = multi_tf_data.get("alignment_synthesis", {}) if multi_tf_data else {}

        aligned_bias = str(syn.get("aligned_bias", "SHORT")).upper()

        ema_18 = features.get("ema_18", ticker["price"])

        pullback_invalidated = False

        if not current_pos and self.locked_decision is not None and self.locked_15m_bucket == candle_15m_bucket:

            locked_act = str(self.locked_decision.get("action", "HOLD")).upper()

            if locked_act == "BUY" and (aligned_bias == "SHORT" or (features.get("close_lower_candle") and ticker["price"] < ema_18)):

                pullback_invalidated = True

                logger.info(f"[ADAPTIVE 15m UNLOCK] Locked BUY invalidated by live pullback/reversal (bias={aligned_bias}, price=${ticker['price']}, EMA18=${ema_18:.2f}). Re-analyzing fresh.")

            elif locked_act == "SELL" and (aligned_bias == "LONG" or (features.get("close_upper_candle") and ticker["price"] > ema_18)):

                pullback_invalidated = True

                logger.info(f"[ADAPTIVE 15m UNLOCK] Locked SELL invalidated by live pullback/reversal (bias={aligned_bias}, price=${ticker['price']}, EMA18=${ema_18:.2f}). Re-analyzing fresh.")

        is_new_15m_candle = (self.locked_15m_bucket != candle_15m_bucket) or (self.locked_decision is None) or pullback_invalidated

        if not current_pos:

            if is_new_15m_candle:

                # Fresh 15m candle close or pullback invalidation: compute and lock battle plan

                decision = gemini_brain.evaluate_market(market_snapshot, model_tier=model_tier)

                self.locked_decision = decision

                self.locked_15m_bucket = candle_15m_bucket

                plan_prefix = "[Adaptive 15m Pullback Re-Analysis]" if pullback_invalidated else "[15m Compass Locked Plan]"

                log_thought(

                    symbol=symbol,

                    price=ticker["price"],

                    rsi=features.get("rsi", 0.0),

                    trend=f"{features.get('trend', 'UNKNOWN')} (ADX7: {adx_val:.1f})",

                    action=decision.get("action", "HOLD"),

                    confidence=decision.get("confidence", 0.0),

                    reasoning=f"{plan_prefix}: {decision.get('reasoning', '')}",

                    key_used=decision.get("key_used", "N/A"),

                    safe_zone=decision.get("safe_zone"),

                    ticks_target=decision.get("ticks_target", 0),

                    plan_a=decision.get("plan_a"),

                    plan_b=decision.get("plan_b"),

                    plan_c=decision.get("plan_c")

                )

            else:

                # Still in same 15m candle & structure agrees: use locked decision so targets stay stable

                decision = self.locked_decision

            # Live 5m trigger verification against the locked plan & live alignment

            action = str(decision.get("action", "HOLD")).upper()

            if features.get("close_upper_candle") and aligned_bias == "LONG":

                action = "BUY"

            elif features.get("close_lower_candle") and aligned_bias == "SHORT":

                action = "SELL"

            elif action == "BUY" and aligned_bias != "LONG":

                action = "HOLD"

            elif action == "SELL" and aligned_bias != "SHORT":

                action = "HOLD"

        else:

            # ACTIVE HOLDING: Live intra-candle updates to monitor trailing ratchet & TP/SL

            self.locked_decision = None

            decision = gemini_brain.evaluate_market(market_snapshot, model_tier=model_tier)

            action = decision.get("action", "HOLD")

            self._log_flat_thought(force=(is_new_15m_candle or pullback_invalidated),

                symbol=symbol,

                price=ticker["price"],

                rsi=features.get("rsi", 0.0),

                trend=f"{features.get('trend', 'UNKNOWN')} (ADX7: {adx_val:.1f})",

                action=action,

                confidence=decision.get("confidence", 0.0),

                reasoning=f"[Active Trade Monitoring]: {decision.get('reasoning', '')}",

                key_used=decision.get("key_used", "N/A"),

                safe_zone=decision.get("safe_zone"),

                ticks_target=decision.get("ticks_target", 0),

                plan_a=decision.get("plan_a"),

                plan_b=decision.get("plan_b"),

                plan_c=decision.get("plan_c")

            )

        confidence = decision.get("confidence", 0.0)

        reasoning = decision.get("reasoning", "")

        key_used = decision.get("key_used", "N/A")

        safe_zone = decision.get("safe_zone")

        ticks_target = decision.get("ticks_target") or (int(round(abs((decision.get("target_price") or ticker["price"]) - ticker["price"]) / tick_val)) if decision.get("target_price") else 0)

        plan_a = decision.get("plan_a")

        plan_b = decision.get("plan_b")

        plan_c = decision.get("plan_c")

        # ----------------------------------------------------

        # ITEM 5: UNIVERSAL POST-EXIT COOLDOWN GATE (2 MINUTES AFTER EVERY EXIT)

        # ----------------------------------------------------

        if self.last_exit_time and not current_pos:

            elapsed_since_exit = time.time() - self.last_exit_time

            min_cooldown = 120.0  # 2 minutes (120s) mandatory pause after EVERY closed transaction (TP or SL)

            if elapsed_since_exit < min_cooldown:

                remaining_wait = int(min_cooldown - elapsed_since_exit)

                hold_reason = f"Post-Exit Cooldown Gate: Transaction recently closed ({self.last_exit_reason or 'Exit'}). Mandatory 2-minute pause ({remaining_wait}s remaining) to let pullbacks/bounces settle before planning next entry."

                self._log_flat_thought(force=(is_new_15m_candle or pullback_invalidated),

                    symbol=symbol,

                    price=ticker["price"],

                    rsi=features.get("rsi", 0.0),

                    trend=features.get("trend"),

                    action="HOLD",

                    confidence=0.0,

                    reasoning=hold_reason,

                    key_used="POST_EXIT_GATE",

                    safe_zone=safe_zone,

                    ticks_target=ticks_target,

                    plan_a=plan_a,

                    plan_b=plan_b,

                    plan_c=plan_c

                )

                return {"timestamp": time.time(), "action": "HOLD", "reason": hold_reason}


            else:

                self.last_exit_time = None

                self.last_exit_reason = None

        # ----------------------------------------------------

        # ACTION EXECUTION (UPPER/LOWER CANDLE CLOSE ONLY; MAX 1 ACTIVE POSITION)

        # ----------------------------------------------------

        trade_result = None

        if action in ("BUY", "SELL") and not current_pos:

            now_ts = time.time()

            # Candle Close Upper / Lower confirmation

            if action == "BUY" and not features.get("close_upper_candle"):

                hold_reason = "Candle Close Rule: BUY allowed ONLY on confirmed UPPER candle close (Close > Prev Candle & Close > EMA 7). Entry withheld."

                self._log_flat_thought(force=(is_new_15m_candle or pullback_invalidated),

                    symbol=symbol,

                    price=ticker["price"],

                    rsi=features.get("rsi", 0.0),

                    trend=features.get("trend"),

                    action="HOLD",

                    confidence=0.0,

                    reasoning=hold_reason,

                    key_used="CANDLE_CLOSE_FILTER",

                    safe_zone=safe_zone,

                    ticks_target=ticks_target,

                    plan_a=plan_a,

                    plan_b=plan_b,

                    plan_c=plan_c

                )

                return {"timestamp": time.time(), "action": "HOLD", "reason": hold_reason}


            if action == "SELL" and not features.get("close_lower_candle"):

                hold_reason = "Candle Close Rule: SELL allowed ONLY on confirmed LOWER candle close (Close < Prev Candle & Close < EMA 7). Entry withheld."

                self._log_flat_thought(force=(is_new_15m_candle or pullback_invalidated),

                    symbol=symbol,

                    price=ticker["price"],

                    rsi=features.get("rsi", 0.0),

                    trend=features.get("trend"),

                    action="HOLD",

                    confidence=0.0,

                    reasoning=hold_reason,

                    key_used="CANDLE_CLOSE_FILTER",

                    safe_zone=safe_zone,

                    ticks_target=ticks_target,

                    plan_a=plan_a,

                    plan_b=plan_b,

                    plan_c=plan_c

                )

                return {"timestamp": time.time(), "action": "HOLD", "reason": hold_reason}

        if action == "BUY":

            if current_pos:

                pos_side = str(current_pos.get("side", "")).upper()

                is_long = pos_side in ("LONG", "BUY")

                is_short = pos_side in ("SHORT", "SELL")

                if is_long:

                    # Same trend -> keep the position

                    pass

                elif is_short:

                    # Opposite position -> close all before making new entry

                    close_res = broker.close_position(symbol)

                    actual_pnl = close_res.get("realized_pnl", current_pos.get("unrealizedProfit", 0.0))

                    actual_exit_price = close_res.get("exit_price", ticker["price"])

                    actual_comm = close_res.get("commission", 0.0)

                    log_trade(symbol, "CLOSE", current_pos["size"], actual_exit_price, close_res.get("orderId"), "CLOSED", actual_pnl, actual_comm)

                    current_pos = None

            if not current_pos:

                if available_balance < (1000.0 * trade_qty):

                    logger.warning(f"[MARGIN GUARD] Insufficient Available Margin (${available_balance:.2f}) for {trade_qty} lot(s) (${1000.0 * trade_qty:.2f} required).")

                else:

                    trade_result = broker.place_order(symbol, "BUY", trade_qty, "MARKET")

                    self.last_entry_time = time.time()

                    self.position_entry_time = time.time()

                    self.position_entry_price = ticker["price"]

                    self.position_side = "LONG"

                    raw_sl = decision.get("stop_loss")

                    raw_tp = decision.get("target_price")

                    if not raw_sl or raw_sl >= ticker["price"]:

                        raw_sl = round(ticker["price"] - 0.06, 2)

                    if not raw_tp or raw_tp <= ticker["price"]:

                        raw_tp = round(ticker["price"] + 0.08, 2)

                    self.position_sl = raw_sl

                    self.position_tp = raw_tp

                    log_trade(symbol, "BUY", trade_qty, ticker["price"], trade_result.get("orderId"), "FILLED")

        elif action == "SELL":

            if current_pos:

                pos_side = str(current_pos.get("side", "")).upper()

                is_long = pos_side in ("LONG", "BUY")

                is_short = pos_side in ("SHORT", "SELL")

                if is_short:

                    # Same trend -> keep the position

                    pass

                elif is_long:

                    # Opposite position -> close all before making new entry

                    close_res = broker.close_position(symbol)

                    actual_pnl = close_res.get("realized_pnl", current_pos.get("unrealizedProfit", 0.0))

                    actual_exit_price = close_res.get("exit_price", ticker["price"])

                    actual_comm = close_res.get("commission", 0.0)

                    log_trade(symbol, "CLOSE", current_pos["size"], actual_exit_price, close_res.get("orderId"), "CLOSED", actual_pnl, actual_comm)

                    current_pos = None

            if not current_pos:

                if available_balance < (1000.0 * trade_qty):

                    logger.warning(f"[MARGIN GUARD] Insufficient Available Margin (${available_balance:.2f}) for {trade_qty} lot(s) (${1000.0 * trade_qty:.2f} required).")

                else:

                    trade_result = broker.place_order(symbol, "SELL", trade_qty, "MARKET")

                    self.last_entry_time = time.time()

                    self.position_entry_time = time.time()

                    self.position_entry_price = ticker["price"]

                    self.position_side = "SHORT"

                    raw_sl = decision.get("stop_loss")

                    raw_tp = decision.get("target_price")

                    if not raw_sl or raw_sl <= ticker["price"]:

                        raw_sl = round(ticker["price"] + 0.06, 2)

                    if not raw_tp or raw_tp >= ticker["price"]:

                        raw_tp = round(ticker["price"] - 0.08, 2)

                    self.position_sl = raw_sl

                    self.position_tp = raw_tp

                    log_trade(symbol, "SELL", trade_qty, ticker["price"], trade_result.get("orderId"), "FILLED")

        elif action == "CLOSE" and current_pos:

            trade_result = broker.close_position(symbol)

            self.last_exit_time = time.time()

            self.last_exit_reason = "Manual/Action Close"

            actual_pnl = trade_result.get("realized_pnl", current_pos.get("unrealizedProfit", 0.0))

            actual_exit_price = trade_result.get("exit_price", ticker["price"])

            actual_comm = trade_result.get("commission", 0.0)

            log_trade(symbol, "CLOSE", current_pos["size"], actual_exit_price, trade_result.get("orderId"), "CLOSED", actual_pnl, actual_comm)

            self.position_entry_time = None

            self.position_entry_price = None

            self.position_side = None

            self.position_sl = None

            self.position_tp = None

        self.locked_15m_bucket = None

        self.locked_decision = None

        return {

            "timestamp": time.time(),

            "timeframe": timeframe,

            "tier": model_tier,

            "market": market_snapshot,

            "decision": decision,

            "trade": trade_result,

            "account": account

        }

    async def run_loop(self):

        self.is_running = True

        logger.info("HaG Autonomous Agent started. Active position monitor: 8s, live scanner: 25s.")

        # Give Uvicorn 2 seconds to bind port 8000 and start web server instantly

        await asyncio.sleep(2)

        while self.is_running:

            sleep_duration = 25.0

            try:

                now = time.time()

                broker = get_broker(config.TARGET_SYMBOL)

                account = broker.get_account_summary()

                has_active_pos = bool(account.get("activePosition"))

                # Active position check every 8s for immediate SL/TP/Rule 5 & 6 hourly exit

                # When flat, scan every 25s to stream fresh thought journeys continuously

                sleep_duration = 8.0 if has_active_pos else 25.0

                # Priority rule: If 10 minutes have elapsed since last macro evaluation, prioritize Gemini 4 Hydrogen High!

                if (now - self.last_macro_time) >= config.MACRO_INTERVAL_SECONDS:

                    is_macro = True

                    self.last_macro_time = now

                    logger.info("Executing MACRO cycle with superior model (Gemini 4 Hydrogen High on M10)...")

                else:

                    is_macro = False

                    logger.info("Executing FAST SENTRY cycle (Gemini 4 Hydrogen Medium on M5)...")

                # Run blocking network operations on a worker thread so FastAPI stays 100% responsive

                cycle_data = await asyncio.to_thread(self.execute_cycle, is_macro)

                logger.info(f"Cycle completed [{cycle_data.get('timeframe')} | {cycle_data.get('tier')}]: {cycle_data.get('action') or cycle_data.get('decision', {}).get('action')}")

            except Exception as e:

                logger.error(f"Error in HaG cycle: {e}")

            await asyncio.sleep(sleep_duration)

hermes_agent = HermesTradingAgent()

