import sqlite3
import os
import json
import time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "hag_journal.db")

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Table 1: Thought & Reasoning Journal
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS thought_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        symbol TEXT,
        price REAL,
        rsi REAL,
        trend TEXT,
        action TEXT,
        confidence REAL,
        reasoning TEXT,
        key_used TEXT,
        safe_zone TEXT,
        ticks_target INTEGER DEFAULT 17,
        plan_a TEXT,
        plan_b TEXT,
        plan_c TEXT
    )
    """)

    # Ensure columns exist if table was already created
    for col, col_type in [
        ("safe_zone", "TEXT"),
        ("ticks_target", "INTEGER DEFAULT 17"),
        ("plan_a", "TEXT"),
        ("plan_b", "TEXT"),
        ("plan_c", "TEXT")
    ]:
        try:
            cursor.execute(f"ALTER TABLE thought_journal ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists

    
    # Table 2: Executed Trades
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trade_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        symbol TEXT,
        side TEXT,
        quantity REAL,
        price REAL,
        order_id TEXT,
        status TEXT,
        realized_pnl REAL DEFAULT 0.0,
        commission REAL DEFAULT 0.0
    )
    """)

    try:
        cursor.execute("ALTER TABLE trade_history ADD COLUMN commission REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass  # column already exists
    
    conn.commit()
    conn.close()

def log_thought(symbol: str, price: float, rsi: float, trend: str, action: str, confidence: float, reasoning: str, key_used: str, safe_zone: str = None, ticks_target: int = 17, plan_a: str = None, plan_b: str = None, plan_c: str = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO thought_journal (timestamp, symbol, price, rsi, trend, action, confidence, reasoning, key_used, safe_zone, ticks_target, plan_a, plan_b, plan_c)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (time.time(), symbol, price, rsi, trend, action, confidence, reasoning, key_used, safe_zone, ticks_target, plan_a, plan_b, plan_c))
    conn.commit()
    conn.close()

def log_trade(symbol: str, side: str, qty: float, price: float, order_id: str, status: str, realized_pnl: float = 0.0, commission: float = 0.0):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO trade_history (timestamp, symbol, side, quantity, price, order_id, status, realized_pnl, commission)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (time.time(), symbol, side, qty, price, str(order_id), status, realized_pnl, commission))
    conn.commit()
    conn.close()

def get_recent_thoughts(limit: int = 20):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM thought_journal ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_recent_trades(limit: int = 100):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_history ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_all_trades():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_history ORDER BY id DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_trade_ledger_summary():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_history ORDER BY id ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    total_trades = len(rows)
    closed_trades = [r for r in rows if str(r.get("status", "")).upper() == "CLOSED"]
    wins = [r for r in closed_trades if (r["realized_pnl"] or 0.0) > 0]
    losses = [r for r in closed_trades if (r["realized_pnl"] or 0.0) < 0]
    total_realized_pnl = sum((r["realized_pnl"] or 0.0) for r in closed_trades)
    total_commission = sum((r.get("commission") or 0.0) for r in closed_trades)
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0

    return {
        "total_trades": total_trades,
        "closed_trades": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "total_realized_pnl": round(total_realized_pnl, 2),
        "total_commission": round(total_commission, 2),
        "win_rate": round(win_rate, 1)
    }

def get_performance_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_history ORDER BY id ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    total_trades = len(rows)
    closed_trades = [r for r in rows if str(r.get("status", "")).upper() == "CLOSED"]
    wins = [r for r in closed_trades if (r["realized_pnl"] or 0.0) > 0]
    losses = [r for r in closed_trades if (r["realized_pnl"] or 0.0) < 0]
    
    total_realized_pnl = sum((r["realized_pnl"] or 0.0) for r in closed_trades)
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0
    
    gross_profit = sum((r["realized_pnl"] or 0.0) for r in wins)
    gross_loss = abs(sum((r["realized_pnl"] or 0.0) for r in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

    # Build cumulative equity curve for Chart.js & Daily Trade Summary (12:00 AM - 11:59 PM)
    equity_curve = []
    cumulative = 0.0
    daily_buckets = {}
    for t in closed_trades:
        pnl = t["realized_pnl"] or 0.0
        comm = t.get("commission") or 0.0
        cumulative += pnl
        equity_curve.append({
            "timestamp": t["timestamp"],
            "date": time.strftime("%H:%M:%S", time.localtime(t["timestamp"])),
            "pnl": round(pnl, 2),
            "cumulative": round(cumulative, 2)
        })

        day_key = time.strftime("%Y-%m-%d", time.localtime(t["timestamp"]))
        if day_key not in daily_buckets:
            daily_buckets[day_key] = {
                "date": day_key,
                "total_realized_pnl": 0.0,
                "total_commission": 0.0,
                "wins": 0,
                "losses": 0,
                "closed_trades": 0,
            }
        bucket = daily_buckets[day_key]
        bucket["total_realized_pnl"] += pnl
        bucket["total_commission"] += comm
        bucket["closed_trades"] += 1
        if pnl > 0:
            bucket["wins"] += 1
        elif pnl < 0:
            bucket["losses"] += 1

    daily_summary = []
    for day_key in sorted(daily_buckets.keys(), reverse=True):
        b = daily_buckets[day_key]
        wr = (b["wins"] / b["closed_trades"] * 100) if b["closed_trades"] > 0 else 0.0
        daily_summary.append({
            "date": b["date"],
            "total_realized_pnl": round(b["total_realized_pnl"], 2),
            "total_commission": round(b["total_commission"], 2),
            "wins": b["wins"],
            "losses": b["losses"],
            "closed_trades": b["closed_trades"],
            "win_rate": round(wr, 1),
        })

    return {
        "total_trades": total_trades,
        "closed_trades": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "net_pnl": round(total_realized_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "equity_curve": equity_curve,
        "daily_summary": daily_summary,
    }


def audit_and_patch_trades(limit_recent: int = None, commission_per_lot: float = 3.0, force_commission: bool = False):
    """
    Audits closed trades by pairing each CLOSE with its preceding BUY/SELL entry.
    If limit_recent is set, checks only the latest N closed transactions; otherwise patches all.
    Returns {"updated_count": int, "total_realized_pnl": float}.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trade_history ORDER BY id ASC")
    rows = [dict(r) for r in cursor.fetchall()]

    last_entry = None
    pairs = []
    for r in rows:
        side = str(r.get("side", "")).upper()
        status = str(r.get("status", "")).upper()
        if side in ("BUY", "SELL", "LONG", "SHORT"):
            last_entry = r
        elif status == "CLOSED" and last_entry is not None:
            pairs.append((last_entry, r))
            last_entry = None

    target_pairs = pairs[-limit_recent:] if (limit_recent is not None and limit_recent > 0) else pairs
    updated_count = 0

    for entry_row, close_row in target_pairs:
        entry_side = str(entry_row.get("side", "")).upper()
        entry_price = float(entry_row.get("price") or 0.0)
        close_price = float(close_row.get("price") or 0.0)
        size = float(close_row.get("quantity") or entry_row.get("quantity") or 1.0)
        if size <= 0:
            size = 1.0

        if entry_side in ("BUY", "LONG"):
            base_pnl = (close_price - entry_price) * 100.0
        else:
            base_pnl = (entry_price - close_price) * 100.0

        existing_comm = close_row.get("commission")
        if force_commission or existing_comm is None or float(existing_comm) == 0.0:
            comm = round(commission_per_lot * size, 2)
        else:
            comm = round(float(existing_comm), 2)

        expected_net = round((base_pnl * size) - comm, 2)
        current_net = round(float(close_row.get("realized_pnl") or 0.0), 2)
        current_comm = round(float(existing_comm or 0.0), 2)

        if abs(current_net - expected_net) > 1e-6 or abs(current_comm - comm) > 1e-6:
            cursor.execute(
                "UPDATE trade_history SET realized_pnl = ?, commission = ? WHERE id = ?",
                (expected_net, comm, close_row["id"]),
            )
            updated_count += 1

    conn.commit()
    cursor.execute("SELECT COALESCE(SUM(realized_pnl), 0.0) FROM trade_history WHERE UPPER(status) = 'CLOSED'")
    total_realized = round(float(cursor.fetchone()[0] or 0.0), 2)
    conn.close()
    return {"updated_count": updated_count, "total_realized_pnl": total_realized}

def reset_journal():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM thought_journal")
    cursor.execute("DELETE FROM trade_history")
    conn.commit()
    conn.close()

init_db()
