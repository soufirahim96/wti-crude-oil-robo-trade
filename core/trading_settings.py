import json
import os
from typing import Dict, Any

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "trading_settings.json")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "lot_size": 1,
    "commission_per_lot": 3.0,
    "ai_enabled": True,
}


def get_trading_settings() -> Dict[str, Any]:
    """Reads persisted lot size, commission fee, and AI kill switch settings, falling back to defaults."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            lot = float(data.get("lot_size", DEFAULT_SETTINGS["lot_size"]))
            comm = float(data.get("commission_per_lot", DEFAULT_SETTINGS["commission_per_lot"]))
            ai_enabled = bool(data.get("ai_enabled", True))
            if lot < 1:
                lot = 1
            if comm < 0:
                comm = 0.0
            lot_val = int(lot) if lot.is_integer() else round(lot, 2)
            return {
                "lot_size": lot_val,
                "commission_per_lot": round(comm, 2),
                "ai_enabled": ai_enabled,
            }
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_trading_settings(lot_size: float, commission_per_lot: float, ai_enabled: bool = None) -> Dict[str, Any]:
    """Validates and persists lot size (>=1), commission per lot (>=0), and preserves AI kill switch state."""
    current = get_trading_settings()
    lot = float(lot_size)
    comm = float(commission_per_lot)
    if lot < 1:
        raise ValueError("lot_size must be at least 1")
    if comm < 0:
        raise ValueError("commission_per_lot cannot be negative")

    lot_val = int(lot) if lot.is_integer() else round(lot, 2)
    settings = {
        "lot_size": lot_val,
        "commission_per_lot": round(comm, 2),
        "ai_enabled": current.get("ai_enabled", True) if ai_enabled is None else bool(ai_enabled),
    }
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    return settings


def set_ai_enabled(enabled: bool) -> Dict[str, Any]:
    """Toggles the Gemini AI API kill switch while preserving lot size and commission."""
    current = get_trading_settings()
    return save_trading_settings(current["lot_size"], current["commission_per_lot"], ai_enabled=bool(enabled))
