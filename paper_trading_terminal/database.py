"""
SQLite database management for Paper Trading Terminal.
Handles Wallet, Ledger, Positions, and Trades History.
"""

import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "paper_trading.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    
    # 1. Wallet Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallet (
            id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 100000.0,
            used_margin REAL DEFAULT 0.0,
            realized_pnl REAL DEFAULT 0.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Initialize wallet with default ₹1,00,000 if not exists
    c.execute("SELECT COUNT(*) FROM wallet")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO wallet (id, balance, used_margin, realized_pnl) VALUES (1, 100000.0, 0.0, 0.0)")
        
    # 2. Funds Ledger Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS funds_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            amount REAL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 3. Positions Table (Active Open Trades)
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT DEFAULT 'BUY',
            qty INTEGER,
            initial_qty INTEGER,
            entry_price REAL,
            sl_price REAL,
            target1_price REAL,
            target2_price REAL,
            status TEXT DEFAULT 'OPEN',
            entry_time TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 4. Trades Table (Closed Trade History for Reports)
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT DEFAULT 'BUY',
            qty INTEGER,
            entry_price REAL,
            exit_price REAL,
            sl_price REAL,
            pnl REAL,
            pnl_pct REAL,
            exit_reason TEXT,
            entry_time TEXT,
            exit_time TEXT,
            trade_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 5. Verified Signals Table (User-confirmed trade setups wishlist)
    c.execute("""
        CREATE TABLE IF NOT EXISTS verified_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            direction TEXT DEFAULT 'LONG',
            stage INTEGER,
            stage_badge TEXT,
            breakout_time TEXT,
            pullback_time TEXT,
            entry_time TEXT,
            clean_pdh REAL,
            entry_price REAL,
            sl_price REAL,
            target1 REAL,
            target2 REAL,
            risk_pct REAL,
            verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        c.execute("ALTER TABLE verified_signals ADD COLUMN direction TEXT DEFAULT 'LONG'")
    except Exception:
        pass

    # 6. Radar Dismissed Table (User-cancelled radar alerts)
    c.execute("""
        CREATE TABLE IF NOT EXISTS radar_dismissed (
            symbol TEXT PRIMARY KEY,
            dismissed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

# --------------------------------------------------------------------------------------
# WALLET FUNCTIONS
# --------------------------------------------------------------------------------------
def get_wallet() -> Dict[str, Any]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM wallet WHERE id = 1").fetchone()
    conn.close()
    if row:
        balance = float(row["balance"])
        used_margin = float(row["used_margin"])
        realized_pnl = float(row["realized_pnl"])
        available_margin = max(0.0, balance - used_margin)
        return {
            "balance": round(balance, 2),
            "used_margin": round(used_margin, 2),
            "available_margin": round(available_margin, 2),
            "realized_pnl": round(realized_pnl, 2)
        }
    return {"balance": 100000.0, "used_margin": 0.0, "available_margin": 100000.0, "realized_pnl": 0.0}

def deposit_funds(amount: float, note: str = "Funds Added") -> Dict[str, Any]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE wallet SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (amount,))
    c.execute("INSERT INTO funds_ledger (type, amount, note) VALUES ('DEPOSIT', ?, ?)", (amount, note))
    conn.commit()
    conn.close()
    return get_wallet()

# --------------------------------------------------------------------------------------
# POSITION & ORDER FUNCTIONS
# --------------------------------------------------------------------------------------
def open_position(symbol: str, qty: int, entry_price: float, sl_price: float, target1: float, target2: float, side: str = "BUY") -> Dict[str, Any]:
    conn = get_connection()
    c = conn.cursor()
    
    # Calculate required margin (Assuming 5x intraday leverage: Margin = (qty * entry_price) / 5)
    req_margin = round((qty * entry_price) / 5.0, 2)
    wallet = get_wallet()
    if wallet["available_margin"] < req_margin:
        conn.close()
        raise ValueError(f"Insufficient Available Margin! Required: ₹{req_margin:,.2f}, Available: ₹{wallet['available_margin']:,.2f}")
        
    entry_time = datetime.now().strftime("%H:%M:%S")
    order_side = side.upper() if side else "BUY"
    c.execute("""
        INSERT INTO positions (symbol, side, qty, initial_qty, entry_price, sl_price, target1_price, target2_price, status, entry_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    """, (symbol.upper(), order_side, qty, qty, entry_price, sl_price, target1, target2, entry_time))
    
    # Update used margin in wallet
    c.execute("UPDATE wallet SET used_margin = used_margin + ? WHERE id = 1", (req_margin,))
    conn.commit()
    pos_id = c.lastrowid
    conn.close()
    return {"status": "SUCCESS", "position_id": pos_id, "symbol": symbol, "side": order_side, "qty": qty, "entry_price": entry_price}

def get_open_positions() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM positions WHERE status != 'CLOSED' ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def close_position_partial(position_id: int, exit_price: float, pct_to_close: float = 100.0, exit_reason: str = "Manual Exit") -> Dict[str, Any]:
    conn = get_connection()
    c = conn.cursor()
    
    pos = c.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
    if not pos:
        conn.close()
        raise ValueError("Position not found")
        
    current_qty = int(pos["qty"])
    entry_price = float(pos["entry_price"])
    symbol = pos["symbol"]
    side = pos["side"] if ("side" in pos.keys() and pos["side"]) else "BUY"
    entry_time = pos["entry_time"]
    
    if pct_to_close >= 99.0 or current_qty <= 1:
        qty_to_close = current_qty
        new_status = "CLOSED"
        remaining_qty = 0
    else:
        qty_to_close = max(1, int(current_qty * (pct_to_close / 100.0)))
        remaining_qty = current_qty - qty_to_close
        new_status = "PARTIAL_50" if pct_to_close == 50.0 else "PARTIAL_75"
        
    if side == "SELL":
        pnl = round(qty_to_close * (entry_price - exit_price), 2)
        pnl_pct = round(((entry_price - exit_price) / entry_price) * 100.0, 2)
    else:
        pnl = round(qty_to_close * (exit_price - entry_price), 2)
        pnl_pct = round(((exit_price - entry_price) / entry_price) * 100.0, 2)
        
    released_margin = round((qty_to_close * entry_price) / 5.0, 2)
    
    now = datetime.now()
    exit_time = now.strftime("%H:%M:%S")
    trade_date = now.strftime("%Y-%m-%d")
    
    # 1. Record closed trade in history
    c.execute("""
        INSERT INTO trades (symbol, side, qty, entry_price, exit_price, sl_price, pnl, pnl_pct, exit_reason, entry_time, exit_time, trade_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, side, qty_to_close, entry_price, exit_price, float(pos["sl_price"]), pnl, pnl_pct, exit_reason, entry_time, exit_time, trade_date))
    
    # 2. Update Position table
    if remaining_qty == 0:
        c.execute("UPDATE positions SET status = 'CLOSED', qty = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (position_id,))
    else:
        c.execute("UPDATE positions SET qty = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (remaining_qty, new_status, position_id))
        
    # 3. Update Wallet (Release margin + Add realized PnL)
    c.execute("""
        UPDATE wallet SET 
            balance = balance + ?, 
            used_margin = MAX(0.0, used_margin - ?), 
            realized_pnl = realized_pnl + ?,
            updated_at = CURRENT_TIMESTAMP 
        WHERE id = 1
    """, (pnl, released_margin, pnl))
    
    conn.commit()
    conn.close()
    return {
        "status": "SUCCESS",
        "symbol": symbol,
        "closed_qty": qty_to_close,
        "remaining_qty": remaining_qty,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "exit_price": exit_price,
        "exit_reason": exit_reason
    }

def update_position_sl(position_id: int, new_sl: float) -> Dict[str, Any]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE positions SET sl_price = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_sl, position_id))
    conn.commit()
    conn.close()
    return {"status": "SUCCESS", "new_sl": new_sl}

# --------------------------------------------------------------------------------------
# REPORTS & ANALYTICS FUNCTIONS
# --------------------------------------------------------------------------------------
def get_trades_report(filter_period: str = "ALL") -> Dict[str, Any]:
    """
    filter_period: 'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY', 'ALL'
    """
    conn = get_connection()
    query = "SELECT * FROM trades"
    params = []
    
    today = datetime.now()
    if filter_period == "DAILY":
        query += " WHERE trade_date = ?"
        params.append(today.strftime("%Y-%m-%d"))
    elif filter_period == "WEEKLY":
        # Current ISO week
        year, week, _ = today.isocalendar()
        # Filter past 7 days
        query += " WHERE trade_date >= date('now', '-7 days')"
    elif filter_period == "MONTHLY":
        query += " WHERE strftime('%Y-%m', trade_date) = ?"
        params.append(today.strftime("%Y-%m"))
    elif filter_period == "YEARLY":
        query += " WHERE strftime('%Y', trade_date) = ?"
        params.append(today.strftime("%Y"))
        
    query += " ORDER BY id DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    trades = [dict(r) for r in rows]
    total_trades = len(trades)
    
    if total_trades == 0:
        return {
            "period": filter_period,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
            "net_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "trades": []
        }
        
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    
    gross_profit = round(sum(t["pnl"] for t in wins), 2)
    gross_loss = round(abs(sum(t["pnl"] for t in losses)), 2)
    net_pnl = round(sum(t["pnl"] for t in trades), 2)
    win_rate = round((len(wins) / total_trades) * 100.0, 2)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)
    
    avg_win = round(gross_profit / len(wins), 2) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (avg_win if avg_win > 0 else 1.0)
    
    return {
        "period": filter_period,
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "net_pnl": net_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "trades": trades
    }

# --------------------------------------------------------------------------------------
# RADAR & VERIFIED SIGNALS FUNCTIONS
# --------------------------------------------------------------------------------------
def add_verified_signal(alert: Dict[str, Any]) -> bool:
    conn = get_connection()
    c = conn.cursor()
    sym = alert.get("symbol", "").upper()
    if not sym:
        conn.close()
        return False
    # If previously dismissed, remove from dismissed
    c.execute("DELETE FROM radar_dismissed WHERE symbol = ?", (sym,))
    direction = alert.get("direction", "LONG").upper() if alert.get("direction") else ("SHORT" if "Short" in str(alert.get("stage_badge")) or "Breakdown" in str(alert.get("stage_badge")) else "LONG")
    clean_lvl = float(alert.get("clean_level", alert.get("clean_pdh", 0.0)))
    
    c.execute("""
        INSERT INTO verified_signals (
            symbol, direction, stage, stage_badge, breakout_time, pullback_time, entry_time,
            clean_pdh, entry_price, sl_price, target1, target2, risk_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            direction = excluded.direction,
            stage = excluded.stage,
            stage_badge = excluded.stage_badge,
            breakout_time = excluded.breakout_time,
            pullback_time = excluded.pullback_time,
            entry_time = excluded.entry_time,
            clean_pdh = excluded.clean_pdh,
            entry_price = excluded.entry_price,
            sl_price = excluded.sl_price,
            target1 = excluded.target1,
            target2 = excluded.target2,
            risk_pct = excluded.risk_pct,
            verified_at = CURRENT_TIMESTAMP
    """, (
        sym,
        direction,
        alert.get("stage", 3),
        alert.get("stage_badge", "🟢 Entry Confirmed ✅"),
        alert.get("breakout_time", "--:--"),
        alert.get("pullback_time", "--:--"),
        alert.get("entry_time", "--:--"),
        clean_lvl,
        float(alert.get("entry_price", 0.0)),
        float(alert.get("sl_price", 0.0)),
        float(alert.get("target1", 0.0)),
        float(alert.get("target2", 0.0)),
        float(alert.get("risk_pct", 0.0))
    ))
    conn.commit()
    conn.close()
    return True

def get_verified_signals() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM verified_signals ORDER BY id DESC").fetchall()
    conn.close()
    res = []
    for r in rows:
        dir_val = r["direction"] if "direction" in r.keys() and r["direction"] else ("SHORT" if "Short" in str(r["stage_badge"]) or "Breakdown" in str(r["stage_badge"]) else "LONG")
        res.append({
            "id": r["id"],
            "symbol": r["symbol"],
            "direction": dir_val,
            "stage": r["stage"],
            "stage_badge": r["stage_badge"],
            "breakout_time": r["breakout_time"],
            "pullback_time": r["pullback_time"],
            "entry_time": r["entry_time"],
            "clean_pdh": float(r["clean_pdh"]),
            "entry_price": float(r["entry_price"]),
            "sl_price": float(r["sl_price"]),
            "target1": float(r["target1"]),
            "target2": float(r["target2"]),
            "risk_pct": float(r["risk_pct"]),
            "verified_at": str(r["verified_at"])
        })
    return res

def get_verified_symbols() -> List[str]:
    conn = get_connection()
    rows = conn.execute("SELECT symbol FROM verified_signals").fetchall()
    conn.close()
    return [r["symbol"].upper() for r in rows]

def remove_verified_signal(symbol: str) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM verified_signals WHERE symbol = ?", (symbol.upper(),))
    conn.commit()
    conn.close()
    return True

def dismiss_radar_signal(symbol: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    sym = symbol.upper()
    c.execute("""
        INSERT INTO radar_dismissed (symbol, dismissed_at)
        VALUES (?, CURRENT_TIMESTAMP)
        ON CONFLICT(symbol) DO UPDATE SET dismissed_at = CURRENT_TIMESTAMP
    """, (sym,))
    conn.commit()
    conn.close()
    return True

def get_dismissed_symbols() -> List[str]:
    conn = get_connection()
    rows = conn.execute("SELECT symbol FROM radar_dismissed").fetchall()
    conn.close()
    return [r["symbol"].upper() for r in rows]

def reset_dismissed_radar() -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM radar_dismissed")
    conn.commit()
    conn.close()
    return True

init_db()
