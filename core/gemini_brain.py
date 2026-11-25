import json
import logging
import os
import re
from google import genai
from google.genai import types
from backend.config import config
from core.trading_settings import get_trading_settings

logger = logging.getLogger("HaG.Brain")

RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "custom_analysis_rules.json")

def get_active_custom_rules() -> list:
    """Loads user-registered custom analysis rules from persistent JSON."""
    if not os.path.exists(RULES_FILE):
        return []
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            all_rules = json.load(f)
            return [r for r in all_rules if r.get("is_active", True)]
    except Exception:
        return []

def get_target_ticks_from_rules(default_ticks: int = 15) -> int:
    """
    Dynamically extracts the user's desired tick target from active custom rules.
    Prioritizes explicit target mentions (e.g. 'Target 15 ticks', '15 ticks', '15 tick').
    """
    import re
    rules = get_active_custom_rules()
    for r in reversed(rules):
        text = f"{r.get('logic_name', '')} {r.get('description', '')}"
        m = re.search(r'target\s*(\d+)\s*ticks?', text, re.IGNORECASE)
        if m:
            try:
                val = int(m.group(1))
                if 1 <= val <= 200:
                    return val
            except Exception:
                pass
        m2 = re.search(r'(\d+)\s*ticks?', text, re.IGNORECASE)
        if m2:
            try:
                val = int(m2.group(1))
                if 1 <= val <= 200:
                    return val
            except Exception:
                pass
    return default_ticks

class GeminiBrainPool:
    def __init__(self):
        self.keys = config.GEMINI_API_KEYS
        self.current_index = 0
        self.model_name = "gemini-4-hydrogen" # Current production Gemini 4 Hydrogen reasoning model

    def get_current_key(self) -> str:
        return self.keys[self.current_index]

    def rotate_key(self):
        old_idx = self.current_index
        self.current_index = (self.current_index + 1) % len(self.keys)
        logger.warning(f"[FAILOVER] Rotated Gemini Key from #{old_idx + 1} to #{self.current_index + 1}")

    def evaluate_market(self, market_snapshot: dict, model_tier: str = "fast") -> dict:
        """
        Sends the market snapshot to Gemini Brain and returns structured JSON decision.
        Incorporates user-defined custom logic (e.g. Elliott Wave & SMC Multi-Timeframe Safe Zone alignment for 17 ticks)
        and outputs an adaptive multi-scenario matrix: Plan A, Plan B, and Plan C.
        """
        timeframe = market_snapshot.get('timeframe', '5m')
        symbol = market_snapshot.get('symbol', 'MCLF2027')
        cur_price = market_snapshot.get('price', 84.38)
        multi_tf = market_snapshot.get('multi_tf', {})
        active_rules = get_active_custom_rules()

        # Build custom rules prompt text
        custom_rules_text = ""
        if active_rules:
            custom_rules_text = "\n### USER-DEFINED INSTITUTIONAL RULES (ACTIVE):\n"
            for r in active_rules:
                custom_rules_text += f"- [{r.get('id')}] {r.get('logic_name')} (Scope: {r.get('target_scope')}, Classification: {r.get('rule_type')}):\n"
                custom_rules_text += f"  Instructions: {r.get('description')}\n"
        else:
            custom_rules_text = """
### DEFAULT INSTITUTIONAL SCALPING CONFLUENCE:
- Safe Zone Identification: Confluence between Order Block / FVG and momentum EMA crosses.
- Thesis-Driven TP and SL: Derive targets and stops 100% dynamically from market structure and Elliott Wave levels.
- Multi-Plan Flexibility: Prepare Plan A (primary thesis), Plan B (invalidation reversal), Plan C (adaptive stall / early breakeven).
"""

        # Build Multi-Timeframe Context text (3-Tier Confluence Hierarchy)
        multi_tf_text = ""
        syn = multi_tf.get("alignment_synthesis", {}) if multi_tf else {}
        calc_tp = syn.get('target_price', cur_price)
        calc_sl = syn.get('contingency_sl', cur_price)
        measured_ticks = int(round(abs(calc_tp - cur_price) / 0.01)) if calc_tp else 0
        if multi_tf:
            d = multi_tf.get("daily", {})
            h4 = multi_tf.get("h4", {})
            h1 = multi_tf.get("h1", {})
            m30 = multi_tf.get("m30", {})
            m15 = multi_tf.get("m15", multi_tf.get("m30_m15", {}))
            multi_tf_text = f"""
### MULTI-TIMEFRAME CONFLUENCE & SAFE ZONE HIERARCHY:
1. POTENTIAL CONFLUENCE & MACRO SAFE ZONES (D1, 4H, 1H, 30m):
   - Daily (D1): Trend={d.get('trend')}, Safe Zone={d.get('safe_zone')}, SMC Phase={d.get('smc_bias')}, Elliott={d.get('elliott_phase')}
   - 4-Hour (4H): Trend={h4.get('trend')}, Order Block Safe Zone={h4.get('safe_zone')}, Elliott Wave={h4.get('elliott_phase')}
   - 1-Hour (1H): Trend={h1.get('trend')}, Hourly Safe Zone={h1.get('safe_zone')}, Elliott Wave={h1.get('elliott_phase')}
   - 30-Minute (30m): Trend={m30.get('trend')}, 30m Safe Zone={m30.get('safe_zone')}, Elliott Wave={m30.get('elliott_phase')}
   * Role: Macro potential confluence and structural boundary detection.

2. ADVANCE GUIDANCE SAFE ZONE (15m, 4-Candle Lookback):
   - Primary Operational Trend: {m15.get('trend')}
   - 15m Advance Guidance Safe Zone: {syn.get('advance_guidance_zone')}
   - 15m Structural Liquidity Target: ${calc_tp}
   * Role: Advance guidance on where the price is heading and structural liquidity targets.

3. OPERATIONAL SAFE ZONE & EXECUTION RETEST (5m, 4-Candle Lookback = 20 mins):
   - 5m Operational Retest Safe Zone: {syn.get('safe_zone_range')} [20m High: ${syn.get('zone_high')}, 20m Low: ${syn.get('zone_low')}]
   - Price Retest Status: In Zone = {syn.get('in_entry_zone')}, Extended Outside Zone = {syn.get('is_extended')}
   - Immediate 5m Invalidation SL: ${calc_sl}
   - Reaction Momentum: Fast EMA 7={market_snapshot.get('ema7')}, EMA 9={market_snapshot.get('ema9')}, Baseline EMA 18={market_snapshot.get('ema18')}
   - Candle Close Confirmation: Upper={market_snapshot.get('close_upper_candle')}, Lower={market_snapshot.get('close_lower_candle')}
   - Sideways / Consolidation Filter: Stochastic %K={market_snapshot.get('stoch_k')}, %D={market_snapshot.get('stoch_d')}
   * Role: Real-time active market price valuation. Governs active swing retest, anti-chase gate, and execution trigger.
- Confluence Alignment: {syn.get('confluence_score')}, Suggested Bias={syn.get('aligned_bias')}
"""

        prompt = f"""
You are HaG (Hermes Autonomous Gemini), an institutional AI quant scalper for Micro WTI Crude Oil ({symbol}) & crypto futures.
Your objective is to identify high-probability Safe Zone entries across multiple timeframes and formulate an adaptive multi-scenario plan.

MARKET STATE ({timeframe}):
- Symbol: {symbol}
- Current Price: ${cur_price}
- 7 EMA (Instant reaction): {market_snapshot.get('ema7')}
- 9 EMA (Fast momentum): {market_snapshot.get('ema9')}
- 18 EMA (Some-time average price baseline): {market_snapshot.get('ema18')}
- 21 EMA: {market_snapshot.get('ema21')}
- EMA 7/9 Instant Cross: Cross Above = {market_snapshot.get('cross_7_above_9')}, Cross Below = {market_snapshot.get('cross_7_below_9')}
- EMA 9/18 Momentum vs Average: Cross Above = {market_snapshot.get('cross_9_above_18')}, Cross Below = {market_snapshot.get('cross_9_below_18')}
- Candle Close Confirmation: Upper Candle Close = {market_snapshot.get('close_upper_candle')}, Lower Candle Close = {market_snapshot.get('close_lower_candle')}
- Previous Candle: Open=${market_snapshot.get('prev_open')}, Close=${market_snapshot.get('prev_close')}, High=${market_snapshot.get('prev_high')}, Low=${market_snapshot.get('prev_low')}
- 14 RSI: {market_snapshot.get('rsi')}
- Stochastic Oscillator (14, 3): %K={market_snapshot.get('stoch_k')}, %D={market_snapshot.get('stoch_d')} (Cross Up={market_snapshot.get('stoch_cross_up')}, Cross Down={market_snapshot.get('stoch_cross_down')}, Oversold(<20)={market_snapshot.get('stoch_oversold')}, Overbought(>80)={market_snapshot.get('stoch_overbought')})
- 7 ADX: {market_snapshot.get('adx')}
- Swing Resistance: ${market_snapshot.get('resistance')}
- Swing Support: ${market_snapshot.get('support')}
- Orderbook Spread: {market_snapshot.get('spread_pct', 0.0):.4%}
- Candle Volatility: {'VOLATILE / GEGAR' if market_snapshot.get('candle_gegar') else 'NORMAL'}
- Active Position: {market_snapshot.get('current_position')} (Holding: {market_snapshot.get('holding_hours', 0.0):.1f}h, PnL: ${market_snapshot.get('floating_pnl', 0.0)})
{multi_tf_text}
{custom_rules_text}

MANDATORY STRATEGY RULES:
1. STRICT POSITION LIMIT:
   - Allow multiple scalping transactions per hour, but strictly maximum 1 active holding position open at any time.
2. 5m OPERATIONAL SWING RETEST & ANTI-CHASE RULE:
   - Active operational swing retest to avoid price chasing focuses on the 5m timeframe (4 candles = 20 minutes) reflecting current active market reality.
   - If price is extended outside the 5m Operational Retest Zone, action MUST BE "HOLD" awaiting a pullback/rally into the 5m zone (unless in an active distribution phase as specified in user rules).
   - Advance guidance safe zone (15m) guides where price is heading structurally, while 5m dictates immediate active price valuation.
3. 5m INSTANT EXECUTION CONFIRMATION:
   - Once price is within or retesting the 5m zone, require 5m candle close confirmation:
     * BUY / LONG: Allowed ONLY if 5m confirms an UPPER close (Close > Previous Candle and Close > EMA 7).
     * SELL / SHORT: Allowed ONLY if 5m confirms a LOWER close (Close < Previous Candle and Close < EMA 7).
   - Fast EMA 7 leading EMA 9 for instant reaction; EMA 9 vs EMA 18 for average price baseline.
4. SIDEWAYS / CONSOLIDATION CONTEXT:
   - If the market is moving sideways, use Stochastic Oscillator (%K & %D) oversold bounce or overbought rejection.
   - If no confirmed upper or lower close, action MUST BE "HOLD".
5. 100% THESIS-DRIVEN STRUCTURAL TP & SL (ZERO HARDCODED TICKS):
   - Target Price and Stop Loss MUST be determined 100% dynamically from the market structure thesis in the active rules of the Analysis Logic Registry (5m Order Block / swing liquidity high/low for immediate execution target, with 15m as advance macro guidance).
   - NEVER use or assume any static or hardcoded tick targets in Python or Java. All targets and stops flow purely from your analysis.
   - Plan A evaluates BOTH Long and Short symmetrically based on market structure.
   - Plan B is the contingency reversal if the 5m structural invalidation level is swept.
   - Plan C is adaptive defense.
OUTPUT FORMAT (STRICT RAW JSON ONLY, NO MARKDOWN CODE BLOCKS, NO EXTRA TEXT):
{{
  "action": "BUY" | "SELL" | "HOLD" | "CLOSE",
  "confidence": 0.88,
  "safe_zone": "MCL {syn.get('safe_zone_range', f'${cur_price:.2f}')} (5m Retest | 15m Guidance: {syn.get('advance_guidance_zone', 'N/A')})",
  "target_price": {calc_tp},
  "stop_loss": {calc_sl},
  "ticks_target": {measured_ticks},
  "plan_a": "Primary: Direction from 5m operational safe zone targeting 5m liquidity at ${calc_tp} (+{measured_ticks} ticks) with SL at ${calc_sl} (15m guidance).",
  "plan_b": "Contingency Reversal: If liquidity sweeps opposite invalidation, flip targeting counter wave.",
  "plan_c": "Adaptive Stall: Ratchet trailing stop to protect capital once in profit.",
  "reasoning": "Clear explanation citing 5m operational retest status, 15m guidance path, EMA 7/9 reaction, and 5m candle close."
}}
"""
        # Determine prioritized (model, thinking_level, label) list based on tier
        if model_tier == "superior":
            models_to_try = [
                (config.SUPERIOR_MODEL_NAME, "HIGH", "Gemini 4 Hydrogen High"),
                (config.FAST_MODEL_NAME, "MEDIUM", "Gemini 4 Hydrogen Medium"),
                ("gemini-3.8-flash", None, "3.8 Flash"),
                ("gemini-3.7-flash", None, "3.7 Flash"),
                ("gemini-3.6-flash", None, "3.6 Flash"),
                ("gemini-flash-latest", None, "Flash Latest"),
            ]
        else:
            models_to_try = [
                (config.FAST_MODEL_NAME, "MEDIUM", "Gemini 4 Hydrogen Medium"),
                (config.LOW_MODEL_NAME, "LOW", "Gemini 4 Hydrogen Low"),
                ("gemini-3.8-flash", None, "3.8 Flash"),
                ("gemini-3.7-flash", None, "3.7 Flash"),
                ("gemini-3.6-flash", None, "3.6 Flash"),
                ("gemini-flash-latest", None, "Flash Latest"),
            ]

        ai_enabled = bool(get_trading_settings().get("ai_enabled", True))
        num_keys = max(1, len(self.keys))
        for target_model, think_level, tier_label in (models_to_try if ai_enabled else []):
            # Try all configured keys on this model before shifting to the next model version
            for _ in range(num_keys):
                active_key = self.get_current_key()
                client = genai.Client(api_key=active_key, http_options={"timeout": 30000})
                try:
                    gen_kwargs = {"model": target_model, "contents": prompt}
                    if think_level:
                        gen_kwargs["config"] = types.GenerateContentConfig(
                            thinking_config=types.ThinkingConfig(thinking_level=think_level)
                        )
                    response = client.models.generate_content(**gen_kwargs)
                    text = (response.text or "").strip()
                    m_json = re.search(r"\{.*\}", text, re.S)
                    if m_json:
                        text = m_json.group(0)

                    decision = json.loads(text)
                    decision["key_used"] = f"Account #{self.current_index + 1} ({tier_label})"
                    
                    # Mathematical sanity guard on returned decision to prevent inverted SL/TP
                    act = decision.get("action")
                    if act == "BUY":
                        if decision.get("stop_loss", 0) >= cur_price:
                            decision["stop_loss"] = syn.get("long_sl") or round(cur_price - 0.05, 2)
                        if decision.get("target_price", 0) <= cur_price:
                            decision["target_price"] = syn.get("long_target") or round(cur_price + 0.08, 2)
                        decision["ticks_target"] = int(round(abs(decision["target_price"] - cur_price) / 0.01))
                    elif act == "SELL":
                        if decision.get("stop_loss", 0) <= cur_price:
                            decision["stop_loss"] = syn.get("short_sl") or round(cur_price + 0.05, 2)
                        if decision.get("target_price", 0) >= cur_price:
                            decision["target_price"] = syn.get("short_target") or round(cur_price - 0.08, 2)
                        decision["ticks_target"] = int(round(abs(cur_price - decision["target_price"]) / 0.01))

                    # Round-robin: rotate to next account for subsequent cycle
                    self.rotate_key()
                    return decision
                except Exception as e:
                    err_msg = str(e)
                    logger.warning(f"Gemini call error on Key #{self.current_index + 1} ({target_model}): {err_msg[:120]}")
                    self.rotate_key()
                    # If the model itself is globally overloaded (503) or missing (404), shift to next model immediately
                    if "503" in err_msg or "UNAVAILABLE" in err_msg or "404" in err_msg or "NOT_FOUND" in err_msg:
                        break
                    # Otherwise (e.g. 429 RESOURCE_EXHAUSTED on this key), try the next key on the same model first!


        # Algorithmic intelligent fallback following EMA 7/9/18 and Upper/Lower Candle Close
        syn = multi_tf.get("alignment_synthesis", {}) if multi_tf else {}
        aligned_bias = syn.get("aligned_bias", "LONG")
        sz_range = syn.get("safe_zone_range", f"${cur_price - 0.05:.2f} - ${cur_price + 0.05:.2f}")

        close_upper = market_snapshot.get("close_upper_candle", False)
        close_lower = market_snapshot.get("close_lower_candle", False)
        ema7 = market_snapshot.get("ema7", cur_price)
        ema9 = market_snapshot.get("ema9", cur_price)
        ema18 = market_snapshot.get("ema18", cur_price)

        stoch_k = market_snapshot.get("stoch_k", 50.0) or 50.0
        stoch_d = market_snapshot.get("stoch_d", 50.0) or 50.0
        stoch_cross_up = market_snapshot.get("stoch_cross_up", False)
        stoch_cross_down = market_snapshot.get("stoch_cross_down", False)

        is_extended = syn.get("is_extended", False)
        in_entry_zone = syn.get("in_entry_zone", True)

        adv_range = syn.get("advance_guidance_zone", "N/A")

        if close_upper and ((ema7 >= ema9 and cur_price >= ema18) or (stoch_k < 35 and stoch_cross_up)):
            if is_extended:
                action = "HOLD"
                eval_bias = "LONG"
                reason = f"Anti-Chase Gate (5m Retest): Confirmed UPPER candle close, but price is extended (${cur_price:.2f}) outside 5m Retest Zone ({sz_range}). Waiting for setup pullback into 5m zone."
            else:
                action = "BUY"
                eval_bias = "LONG"
                reason = f"Confirmed UPPER close (${cur_price:.2f}) inside 5m Retest Zone ({sz_range}) with EMA 7/9 reaction above EMA 18." if (ema7 >= ema9) else f"Confirmed UPPER close (${cur_price:.2f}) inside 5m Retest Zone with Stochastic bounce (%K={stoch_k:.1f} > %D={stoch_d:.1f})."
        elif close_lower and ((ema7 <= ema9 and cur_price <= ema18) or (stoch_k > 65 and stoch_cross_down)):
            if is_extended:
                action = "HOLD"
                eval_bias = "SHORT"
                reason = f"Anti-Chase Gate (5m Retest): Confirmed LOWER candle close, but price is extended (${cur_price:.2f}) outside 5m Retest Zone ({sz_range}). Waiting for setup rally into 5m zone."
            else:
                action = "SELL"
                eval_bias = "SHORT"
                reason = f"Confirmed LOWER close (${cur_price:.2f}) inside 5m Retest Zone ({sz_range}) with EMA 7/9 reaction below EMA 18." if (ema7 <= ema9) else f"Confirmed LOWER close (${cur_price:.2f}) inside 5m Retest Zone with Stochastic rejection (%K={stoch_k:.1f} < %D={stoch_d:.1f})."
        else:
            action = "HOLD"
            eval_bias = aligned_bias
            reason = f"Candle close upper/lower not confirmed (Upper={close_upper}, Lower={close_lower}). Waiting for 5m swing retest setup in {sz_range}."

        # Dynamic Structural TP and Invalidation SL (Bidirectional 5m Execution, Guided by 15m)
        adv_bias = syn.get("advance_guidance_bias", aligned_bias)
        if eval_bias == "LONG":
            tp_price = syn.get("long_target") or round(cur_price + 0.08, 2)
            if tp_price <= cur_price:
                tp_price = round(cur_price + 0.08, 2)
            sl_price = syn.get("long_sl") or round(cur_price - 0.05, 2)
            if sl_price >= cur_price:
                sl_price = round(cur_price - 0.05, 2)
            inval_price = syn.get("long_invalidation") or round(sl_price - 0.02, 2)
            eval_sz = syn.get("long_safe_zone") or sz_range
            target_ticks = int(round(abs(tp_price - cur_price) / 0.01))
            plan_a = f"Primary LONG: Enter on 5m retest in {eval_sz}, target 5m liquidity at ${tp_price} (+{target_ticks} ticks) with invalidation SL at ${sl_price} (15m {adv_bias} guidance)."
            plan_b = f"Contingency Reversal: If support liquidity swept past ${inval_price}, flip SHORT targeting opposite 5m wave."
            plan_c = f"Adaptive Stall: Ratchet trailing stop to breakeven once in profit."
        else:
            tp_price = syn.get("short_target") or round(cur_price - 0.08, 2)
            if tp_price >= cur_price:
                tp_price = round(cur_price - 0.08, 2)
            sl_price = syn.get("short_sl") or round(cur_price + 0.05, 2)
            if sl_price <= cur_price:
                sl_price = round(cur_price + 0.05, 2)
            inval_price = syn.get("short_invalidation") or round(sl_price + 0.02, 2)
            eval_sz = syn.get("short_safe_zone") or sz_range
            target_ticks = int(round(abs(cur_price - tp_price) / 0.01))
            plan_a = f"Primary SHORT: Enter on 5m retest in {eval_sz}, target 5m liquidity at ${tp_price} (+{target_ticks} ticks) with invalidation SL at ${sl_price} (15m {adv_bias} guidance)."
            plan_b = f"Contingency Reversal: If resistance liquidity swept past ${inval_price}, flip LONG targeting opposite 5m wave."
            plan_c = f"Adaptive Stall: Ratchet trailing stop to breakeven once in profit."

        return {
            "action": action,
            "confidence": 0.85 if action != "HOLD" else 0.50,
            "safe_zone": f"MCL {sz_range} (5m Retest | 15m Guidance: {adv_range})",
            "ticks_target": target_ticks,
            "plan_a": plan_a,
            "plan_b": plan_b,
            "plan_c": plan_c,
            "reasoning": reason,
            "target_price": tp_price,
            "stop_loss": sl_price,
            "key_used": "Hermes Multi-TF Engine (Co-pilot)" if ai_enabled else "AI_KILL_SWITCH (Local Co-pilot)",
        }

gemini_brain = GeminiBrainPool()
