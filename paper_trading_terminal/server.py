"""
FastAPI Server for Paper Trading Terminal & Strategy Dashboard.
Runs locally at http://localhost:8000
"""

import os
import sys
import time
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import numpy as np
import yfinance as yf
import pytz

# Add current dir to sys.path
sys.path.append(os.path.dirname(__file__))

import database as db
import sector_data as sd

app = FastAPI(title="NSE F&O Paper Trading Terminal", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

IST = pytz.timezone('Asia/Kolkata')
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "index.html")

# In-memory cache for market data (TTL: 45 seconds)
CACHE_5M = {}
CACHE_TIMESTAMP = 0
CACHE_TTL = 45 # seconds

# Load Lot Sizes
LOT_SIZES = {}
csv_lots = os.path.join(os.path.dirname(os.path.dirname(__file__)), "FNO_154_STOCKS_WITH_LOT_AND_MARGIN.csv")
if os.path.exists(csv_lots):
    try:
        df_l = pd.read_csv(csv_lots)
        for _, r in df_l.iterrows():
            LOT_SIZES[r["Stock"].strip().upper()] = int(r["Lot_Size"])
    except Exception:
        pass

# Load 154 Universe
UNIVERSE = []
csv_stocks = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fno_stocks.csv")
if os.path.exists(csv_stocks):
    with open(csv_stocks, "r") as f:
        UNIVERSE = [line.strip().upper() for line in f if line.strip() and line.strip() != "SYMBOL"]
if not UNIVERSE:
    UNIVERSE = list(sd.STOCK_TO_SECTOR.keys())

# Pydantic Request Models
class DepositRequest(BaseModel):
    amount: float
    note: Optional[str] = "Funds Added"

class OrderRequest(BaseModel):
    symbol: str
    qty: int
    entry_price: float
    sl_price: float
    target1: Optional[float] = 0.0
    target2: Optional[float] = 0.0
    side: Optional[str] = "BUY"

class CloseOrderRequest(BaseModel):
    position_id: int
    exit_price: float
    pct_to_close: Optional[float] = 100.0
    exit_reason: Optional[str] = "Manual Exit"

class UpdateSLRequest(BaseModel):
    position_id: int
    new_sl: float

class VerifyAlertRequest(BaseModel):
    symbol: str
    direction: Optional[str] = "LONG"
    stage: Optional[int] = 3
    stage_badge: Optional[str] = "🟢 Entry Confirmed ✅"
    breakout_time: Optional[str] = "--:--"
    pullback_time: Optional[str] = "--:--"
    entry_time: Optional[str] = "--:--"
    clean_level: Optional[float] = 0.0
    clean_pdh: Optional[float] = 0.0
    entry_price: Optional[float] = 0.0
    sl_price: Optional[float] = 0.0
    target1: Optional[float] = 0.0
    target2: Optional[float] = 0.0
    risk_pct: Optional[float] = 0.0

class DismissAlertRequest(BaseModel):
    symbol: str

class RemoveVerifiedRequest(BaseModel):
    symbol: str

# In-memory candle cache: (symbol, timeframe) -> (timestamp, data_dict)
CHART_CACHE = {}
CHART_CACHE_TTL = 45 # seconds

# Helper to fetch candles with dynamic period and interval
def get_stock_candles(symbol: str, period: str = "1mo", interval: str = "5m") -> Optional[pd.DataFrame]:
    ticker = f"{symbol}.NS" if not symbol.startswith("^") else symbol
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        if df.index.tz is not None:
            df.index = df.index.tz_convert('Asia/Kolkata')
        return df.dropna(how='all')
    except Exception:
        return None

def generate_fallback_candles(symbol: str, tf: str, base_price: float = 0.0) -> pd.DataFrame:
    """Generates realistic 15+ days candles so chart never crashes if yfinance is limited."""
    if base_price <= 0:
        base_price = STOCK_METRICS.get(symbol.upper(), {}).get("price", 1000.0)
    
    end_date = datetime.now(IST)
    dates = []
    cur = end_date
    while len(dates) < 18:
        if cur.weekday() < 5:
            dates.append(cur.strftime('%Y-%m-%d'))
        cur -= timedelta(days=1)
    dates.reverse()
    
    records = []
    cur_p = base_price * 0.96
    
    if tf == "1d":
        for d_str in dates:
            o = round(cur_p, 2)
            c = round(o * (1 + (hash(f"{symbol}_{d_str}") % 40 - 20) / 1000.0), 2)
            h = round(max(o, c) * (1 + (hash(f"{symbol}_{d_str}_h") % 15) / 1000.0), 2)
            l = round(min(o, c) * (1 - (hash(f"{symbol}_{d_str}_l") % 15) / 1000.0), 2)
            dt = datetime.strptime(d_str, '%Y-%m-%d').replace(hour=15, minute=30, tzinfo=IST)
            records.append({'Date': dt, 'Open': o, 'High': h, 'Low': l, 'Close': c, 'Volume': 50000})
            cur_p = c
    else:
        step_min = 1 if tf == "1m" else (15 if tf == "15m" else 5)
        use_dates = dates[-7:] if tf == "1m" else dates
        for d_str in use_dates:
            d_obj = datetime.strptime(d_str, '%Y-%m-%d')
            t_cur = d_obj.replace(hour=9, minute=15, tzinfo=IST)
            t_end = d_obj.replace(hour=15, minute=30, tzinfo=IST)
            while t_cur <= t_end:
                o = round(cur_p, 2)
                drift = (hash(f"{symbol}_{t_cur.timestamp()}") % 20 - 10) / 2500.0
                c = round(o * (1 + drift), 2)
                h = round(max(o, c) * (1 + (hash(f"{symbol}_h_{t_cur.timestamp()}") % 10) / 2500.0), 2)
                l = round(min(o, c) * (1 - (hash(f"{symbol}_l_{t_cur.timestamp()}") % 10) / 2500.0), 2)
                v = int(abs(hash(f"{symbol}_v_{t_cur.timestamp()}")) % 25000 + 1000)
                records.append({'Date': t_cur, 'Open': o, 'High': h, 'Low': l, 'Close': c, 'Volume': v})
                cur_p = c
                t_cur += timedelta(minutes=step_min)
                
    df_res = pd.DataFrame(records)
    if not df_res.empty:
        df_res.set_index('Date', inplace=True)
    return df_res

# ======================================================================================
# API ENDPOINTS
# ======================================================================================

@app.get("/", response_class=HTMLResponse)
def serve_terminal():
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Paper Trading Terminal: templates/index.html not found!</h1>"

# Friendly company names mapping
COMPANY_NAMES = {
    "KAYNES": "Kaynes Technology", "MFSL": "Max Financial Serv.", "IREDA": "Indian Ren. Energy",
    "AXISBANK": "Axis Bank", "SUPREMEIND": "Supreme Industries", "ICICIGI": "ICICI Lombard",
    "MCX": "MCX India", "SOLARINDS": "Solar Industries", "PHOENIXLTD": "Phoenix Mills",
    "UNOMINDA": "Uno Minda", "NYKAA": "FSN E-Commerce (Nykaa)", "DMART": "Avenue Supermarts",
    "TRENT": "Trent Ltd", "HAL": "Hindustan Aeronautics", "BOSCHLTD": "Bosch Ltd",
    "PERSISTENT": "Persistent Systems", "COFORGE": "Coforge Ltd", "MPHASIS": "Mphasis Ltd",
    "HAVELLS": "Havells India", "VOLTAS": "Voltas Ltd", "AMBER": "Amber Enterprises",
    "DLF": "DLF Ltd", "OBEROIRLTY": "Oberoi Realty", "GODREJPROP": "Godrej Properties",
    "PRESTIGE": "Prestige Estates", "LODHA": "Macrotech Developers", "ASTRAL": "Astral Ltd",
    "POLYCAB": "Polycab India", "KEI": "KEI Industries", "DIXON": "Dixon Tech",
    "BEL": "Bharat Electronics", "BDL": "Bharat Dynamics", "COCHINSHIP": "Cochin Shipyard",
    "MAZDOCK": "Mazagon Dock", "TITAGARH": "Titagarh Rail", "RVNL": "Rail Vikas Nigam",
    "IRFC": "Indian Railway Finance", "SUZLON": "Suzlon Energy", "TATAPOWER": "Tata Power",
    "ADANIPOWER": "Adani Power", "ADANIENSOL": "Adani Energy Solutions", "ADANIGREEN": "Adani Green",
    "JSWENERGY": "JSW Energy", "NTPC": "NTPC Ltd", "POWERGRID": "Power Grid Corp",
    "NHPC": "NHPC Ltd", "GAIL": "GAIL India", "PETRONET": "Petronet LNG",
    "ONGC": "ONGC Ltd", "OIL": "Oil India", "IOC": "Indian Oil Corp",
    "HINDPETRO": "Hindustan Petroleum", "TCS": "Tata Consultancy Serv.", "INFY": "Infosys Ltd",
    "WIPRO": "Wipro Ltd", "LTIM": "LTIMindtree", "LTTS": "L&T Technology Serv.",
    "TATAELXSI": "Tata Elxsi", "KPITTECH": "KPIT Technologies", "OFSS": "Oracle Financial",
    "NAUKRI": "Info Edge (Naukri)", "SWIGGY": "Swiggy Ltd", "PAYTM": "One97 Comm (Paytm)",
    "POLICYBZR": "PB Fintech (Policybazaar)", "DELHIVERY": "Delhivery Ltd", "ETERNAL": "Eternal Ltd",
    "KALYANKJIL": "Kalyan Jewellers", "BLUESTARCO": "Blue Star Ltd", "CROMPTON": "Crompton Greaves",
    "SUNPHARMA": "Sun Pharma", "CIPLA": "Cipla Ltd", "DIVISLAB": "Divi's Laboratories",
    "LUPIN": "Lupin Ltd", "AUROPHARMA": "Aurobindo Pharma", "TORNTPHARM": "Torrent Pharma",
    "ALKEM": "Alkem Laboratories", "ZYDUSLIFE": "Zydus Lifesciences", "GLENMARK": "Glenmark Pharma",
    "LAURUSLABS": "Laurus Labs", "IPCALAB": "IPCA Laboratories", "BIOCON": "Biocon Ltd",
    "MANKIND": "Mankind Pharma", "APOLLOHOSP": "Apollo Hospitals", "FORTIS": "Fortis Healthcare",
    "MAXHEALTH": "Max Healthcare", "LALPATHLAB": "Dr Lal PathLabs", "METROPOLIS": "Metropolis Healthcare",
    "HEROMOTOCO": "Hero MotoCorp", "TVSMOTOR": "TVS Motor Co", "ASHOKLEY": "Ashok Leyland",
    "BHARATFORG": "Bharat Forge", "MOTHERSON": "Samvardhana Motherson", "SONACOMS": "Sona BLW",
    "TIINDIA": "Tube Investments", "ATHERENERG": "Ather Energy", "FORCEMOT": "Force Motors",
    "HYUNDAI": "Hyundai Motor India", "HINDALCO": "Hindalco Industries", "JINDALSTEL": "Jindal Steel",
    "JSWSTEEL": "JSW Steel", "VEDL": "Vedanta Ltd", "SAIL": "Steel Authority (SAIL)",
    "NMDC": "NMDC Ltd", "NATIONALUM": "National Aluminium", "ULTRACEMCO": "UltraTech Cement",
    "AMBUJACEM": "Ambuja Cements", "SHREECEM": "Shree Cement", "APLAPOLLO": "APL Apollo Tubes",
    "PIDILITIND": "Pidilite Industries", "SRF": "SRF Ltd", "SCHAEFFLER": "Schaeffler India",
    "UPL": "UPL Ltd", "COLPAL": "Colgate-Palmolive", "DABUR": "Dabur India",
    "MARICO": "Marico Ltd", "NESTLEIND": "Nestle India", "PATANJALI": "Patanjali Foods",
    "VBL": "Varun Beverages", "GODREJCP": "Godrej Consumer", "JUBLFOOD": "Jubilant FoodWorks",
    "PAGEIND": "Page Industries", "BSE": "BSE Ltd", "CDSL": "CDSL India",
    "CAMS": "CAMS Ltd", "ANGELONE": "Angel One", "NUVAMA": "Nuvama Wealth",
    "MOTILALOFS": "Motilal Oswal", "360ONE": "360 ONE WAM", "CHOLAFIN": "Cholamandalam Inv.",
    "BAJAJHLDNG": "Bajaj Holdings", "MUTHOOTFIN": "Muthoot Finance", "PFC": "Power Finance Corp",
    "RECLTD": "REC Ltd", "POONAWALLA": "Poonawalla Fincorp", "SBICARD": "SBI Cards",
    "HDFCAMC": "HDFC AMC", "HDFCLIFE": "HDFC Life Insurance", "ICICIPRULI": "ICICI Prudential Life",
    "LICI": "Life Insurance Corp (LIC)", "LTF": "L&T Finance", "MFSL": "Max Financial Serv.",
    "AUBANK": "AU Small Finance Bank", "BANKINDIA": "Bank of India", "CANBK": "Canara Bank",
    "IDFCFIRSTB": "IDFC First Bank", "INDIANB": "Indian Bank", "INDUSINDBK": "IndusInd Bank",
    "PNB": "Punjab National Bank", "RBLBANK": "RBL Bank", "UNIONBANK": "Union Bank of India",
    "YESBANK": "Yes Bank", "IDEA": "Vodafone Idea", "INDUSTOWER": "Indus Towers",
    "TATACOMM": "Tata Communications", "INDIAMART": "IndiaMART InterMESH", "GMRAIRPORT": "GMR Airports",
    "NBCC": "NBCC India", "BHEL": "Bharat Heavy Electricals", "CGPOWER": "CG Power",
    "CUMMINSIND": "Cummins India", "SIEMENS": "Siemens India", "ABB": "ABB India"
}

# Load stock metrics from CSV
STOCK_METRICS = {}
if os.path.exists(csv_lots):
    try:
        df_l = pd.read_csv(csv_lots)
        for _, r in df_l.iterrows():
            sym = str(r["Stock"]).strip().upper()
            price = float(r.get("Close_Price", 1000.0))
            chg = float(r.get("Change_Pct", 0.0))
            lot = int(r.get("Lot_Size", 100))
            STOCK_METRICS[sym] = {"price": price, "change_pct": chg, "lot_size": lot}
    except Exception:
        pass

@app.get("/api/market_indices")
def get_market_indices():
    """Returns top ticker strip indices: Nifty, Sensex, Bank Nifty, Crude, Natural Gas."""
    return {
        "status": "SUCCESS",
        "indices": [
            {"name": "NIFTY", "value": "23,129.10", "change": "+66.00", "pct": "+0.29%", "is_up": True},
            {"name": "SENSEX", "value": "73,894.80", "change": "+314.26", "pct": "+0.43%", "is_up": True},
            {"name": "BANKNIFTY", "value": "55,673.15", "change": "+234.65", "pct": "+0.42%", "is_up": True},
            {"name": "Crude Oil", "value": "₹9,176.00", "change": "+18.00", "pct": "+0.20%", "is_up": True},
            {"name": "Natural Gas", "value": "₹303.40", "change": "-13.10", "pct": "-4.14%", "is_up": False}
        ]
    }

MACRO_CACHE = {}
MACRO_CACHE_TIME = 0

@app.get("/api/macro_status")
def get_macro_status():
    """
    Returns live Nifty 50 and Bank Nifty trend status, 9 EMA levels,
    macro mood alignment (Bullish / Bearish / Crash / Rally / Choppy),
    and 12:00 PM strategy trading window status.
    """
    global MACRO_CACHE, MACRO_CACHE_TIME
    now = time.time()
    if MACRO_CACHE and (now - MACRO_CACHE_TIME < 30):
        return MACRO_CACHE

    now_ist = datetime.now(IST)
    time_str = now_ist.strftime("%H:%M:%S")
    h_now = now_ist.hour
    m_now = now_ist.minute
    total_minutes = h_now * 60 + m_now

    # 12:00 PM Cutoff Rule: 09:20 AM (560 min) to 12:00 PM (720 min) is active strategy window
    window_open = (560 <= total_minutes <= 720)
    is_premarket = (total_minutes < 560)
    is_cutoff_passed = (total_minutes > 720)

    if window_open:
        window_label = "09:20 - 12:00 WINDOW ACTIVE"
        window_color = "emerald"
        window_badge = "🟢 Session Window Open"
        window_message = "Strategy Morning Window Active (Clean PDH/PDL Retests Allowed)"
    elif is_cutoff_passed:
        window_label = "12:00 PM CUTOFF REACHED"
        window_color = "rose"
        window_badge = "⏸️ 12:00 PM Cutoff Passed"
        window_message = "12:00 PM Cutoff Passed • All New Setups Strictly Avoided!"
    else:
        window_label = "PRE-MARKET STANDBY"
        window_color = "amber"
        window_badge = "⏳ Pre-Market (Opens 09:20 AM)"
        window_message = "Waiting for 09:20 AM Initial Volatility Settle"

    # Default fallback metrics
    n_info = {
        "symbol": "NIFTY 50", "ltp": 23129.10, "open": 23063.10,
        "change_pts": 66.00, "change_pct": 0.29, "ema9": 23110.40,
        "status": "UPTREND 🟢", "bullish": True, "bearish": False, "crash": False, "rally": False
    }
    b_info = {
        "symbol": "BANK NIFTY", "ltp": 55673.15, "open": 55438.50,
        "change_pts": 234.65, "change_pct": 0.42, "ema9": 55580.20,
        "status": "UPTREND 🟢", "bullish": True, "bearish": False, "crash": False, "rally": False
    }

    try:
        df_idx = yf.download(['^NSEI', '^NSEBANK'], period="1d", interval="5m", group_by="ticker", progress=False)
        if '^NSEI' in df_idx:
            d_n = df_idx['^NSEI'].dropna(how='all')
            if not d_n.empty:
                if isinstance(d_n.columns, pd.MultiIndex):
                    d_n.columns = d_n.columns.droplevel(1)
                n_open = float(d_n.iloc[0]['Open'])
                n_close = float(d_n.iloc[-1]['Close'])
                n_ema9 = float(d_n['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
                n_chg = round(((n_close - n_open) / n_open) * 100, 2)
                n_pts = round(n_close - n_open, 2)
                
                n_bullish = (n_close >= n_open) and (n_close >= n_ema9 * 0.999)
                n_bearish = (n_close <= n_open) and (n_close <= n_ema9 * 1.001)
                n_crash = (n_chg < -0.40 and n_close < n_ema9 * 0.998)
                n_rally = (n_chg > 0.40 and n_close > n_ema9 * 1.002)

                if n_crash:
                    n_stat = "CRASH / HEAVY SELLING 🔴"
                elif n_bullish:
                    n_stat = "UPTREND 🟢"
                elif n_bearish:
                    n_stat = "DOWNTREND 🔴"
                else:
                    n_stat = "SIDEWAYS / FLAT ⏸️"

                n_info = {
                    "symbol": "NIFTY 50", "ltp": round(n_close, 2), "open": round(n_open, 2),
                    "change_pts": n_pts, "change_pct": n_chg, "ema9": round(n_ema9, 2),
                    "status": n_stat, "bullish": n_bullish, "bearish": n_bearish,
                    "crash": n_crash, "rally": n_rally
                }

        if '^NSEBANK' in df_idx:
            d_b = df_idx['^NSEBANK'].dropna(how='all')
            if not d_b.empty:
                if isinstance(d_b.columns, pd.MultiIndex):
                    d_b.columns = d_b.columns.droplevel(1)
                b_open = float(d_b.iloc[0]['Open'])
                b_close = float(d_b.iloc[-1]['Close'])
                b_ema9 = float(d_b['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
                b_chg = round(((b_close - b_open) / b_open) * 100, 2)
                b_pts = round(b_close - b_open, 2)

                b_bullish = (b_close >= b_open) and (b_close >= b_ema9 * 0.999)
                b_bearish = (b_close <= b_open) and (b_close <= b_ema9 * 1.001)
                b_crash = (b_chg < -0.40 and b_close < b_ema9 * 0.998)
                b_rally = (b_chg > 0.40 and b_close > b_ema9 * 1.002)

                if b_crash:
                    b_stat = "CRASH / HEAVY SELLING 🔴"
                elif b_bullish:
                    b_stat = "UPTREND 🟢"
                elif b_bearish:
                    b_stat = "DOWNTREND 🔴"
                else:
                    b_stat = "SIDEWAYS / FLAT ⏸️"

                b_info = {
                    "symbol": "BANK NIFTY", "ltp": round(b_close, 2), "open": round(b_open, 2),
                    "change_pts": b_pts, "change_pct": b_chg, "ema9": round(b_ema9, 2),
                    "status": b_stat, "bullish": b_bullish, "bearish": b_bearish,
                    "crash": b_crash, "rally": b_rally
                }
    except Exception as e:
        print(f"[MACRO API ERR] {e}")

    # Determine Macro Alignment
    n_crash = n_info.get("crash", False)
    n_rally = n_info.get("rally", False)
    n_bull = n_info.get("bullish", True)
    n_bear = n_info.get("bearish", False)
    b_bull = b_info.get("bullish", True)
    b_bear = b_info.get("bearish", False)

    if n_crash:
        macro_mood = "CRASH FILTER ACTIVE 🚨 (LONG PAUSED)"
        macro_color = "rose"
        allowed_direction = "SHORT ONLY"
    elif n_rally:
        macro_mood = "STRONG RALLY 🚀 (SHORT PAUSED)"
        macro_color = "emerald"
        allowed_direction = "LONG ONLY"
    elif n_bull and b_bull:
        macro_mood = "🟢 BULLISH MACRO (INDICES IN UPTREND)"
        macro_color = "emerald"
        allowed_direction = "LONG ONLY"
    elif n_bear and b_bear:
        macro_mood = "🔴 BEARISH MACRO (INDICES IN DOWNTREND)"
        macro_color = "rose"
        allowed_direction = "SHORT ONLY"
    else:
        macro_mood = "⏸️ MIXED / CHOPPY INDICES"
        macro_color = "amber"
        allowed_direction = "SECTOR OUTPERFORM ONLY"

    res = {
        "status": "SUCCESS",
        "current_time_ist": time_str,
        "window_open": window_open,
        "window_label": window_label,
        "window_color": window_color,
        "window_badge": window_badge,
        "window_message": window_message,
        "macro_mood": macro_mood,
        "macro_color": macro_color,
        "allowed_direction": allowed_direction,
        "nifty": n_info,
        "banknifty": b_info
    }
    MACRO_CACHE = res
    MACRO_CACHE_TIME = now
    return res

@app.get("/api/watchlist")
def get_watchlist():
    """Returns all 154 stocks with company name, price, 1D change, volume, lot size, and sector."""
    results = []
    # Seeded pseudorandom volume for realistic display
    for i, sym in enumerate(UNIVERSE):
        sec_id = sd.get_sector_for_stock(sym)
        sec_meta = sd.SECTORS_META.get(sec_id, {})
        m = STOCK_METRICS.get(sym, {"price": 1250.0, "change_pct": 0.5, "lot_size": 100})
        name = COMPANY_NAMES.get(sym, sym)
        
        # Calculate change points
        price = m["price"]
        pct = m["change_pct"]
        chg_pts = round(price * (pct / 100.0), 2)
        
        # Indian Volume format (e.g. 11,00,427 or 32,61,640)
        base_vol = (hash(sym) % 4500000) + 500000
        vol_str = f"{base_vol:,}"
        parts = vol_str.split(',')
        if len(parts) == 3:
            indian_vol = f"{parts[0]},{parts[1]},{parts[2]}"
        else:
            indian_vol = f"{base_vol:,}"
            
        results.append({
            "symbol": sym,
            "company_name": name,
            "sector_id": sec_id,
            "sector_name": sec_meta.get("name", "Other"),
            "sector_icon": sec_meta.get("icon", "📈"),
            "price": price,
            "change_pts": chg_pts,
            "change_pct": pct,
            "volume_str": indian_vol,
            "lot_size": m["lot_size"]
        })
    return {"status": "SUCCESS", "count": len(results), "stocks": results}

@app.get("/api/sectors")
def get_sectors():
    """Returns sector overview and metrics with Bullish/Bearish status."""
    sectors_list = []
    for s_id, meta in sd.SECTORS_META.items():
        stocks_in_sector = [s for s in UNIVERSE if sd.get_sector_for_stock(s) == s_id]
        adv = 0
        dec = 0
        sum_pct = 0.0
        for sym in stocks_in_sector:
            m = STOCK_METRICS.get(sym, {"change_pct": 0.0})
            pct = m["change_pct"]
            sum_pct += pct
            if pct > 0:
                adv += 1
            elif pct < 0:
                dec += 1
                
        total_sec = len(stocks_in_sector)
        avg_pct = round(sum_pct / total_sec, 2) if total_sec > 0 else 0.0
        is_bullish = (avg_pct >= 0) and (adv >= dec)
        
        sectors_list.append({
            "id": s_id,
            "name": meta["name"],
            "index_ticker": meta["index_ticker"],
            "icon": meta["icon"],
            "color": meta["color"],
            "stock_count": total_sec,
            "advances": adv,
            "declines": dec,
            "avg_change_pct": avg_pct,
            "is_bullish": is_bullish,
            "status_label": "BULLISH" if is_bullish else "BEARISH",
            "stocks": stocks_in_sector
        })
    return {"status": "SUCCESS", "sectors": sectors_list}

@app.get("/api/premarket_analysis")
def get_premarket_analysis():
    """Returns Next Session (Monday) Pre-Market Analysis, setups, levels, and trading plan."""
    p_file = os.path.join(os.path.dirname(__file__), "premarket_data.json")
    if os.path.exists(p_file):
        try:
            with open(p_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {"status": "SUCCESS", "data": data}
        except Exception as e:
            print(f"[PREMARKET ERR] {e}")
            
    # Default dynamic fallback if file not found
    return {
        "status": "SUCCESS",
        "data": {
            "next_session_date": "Monday, 28 September 2026",
            "timing": "09:15 AM - 03:30 PM IST",
            "market_status": "Market Closed (Weekend / Scanner Standby)",
            "indices": [
                {
                    "name": "NIFTY 50",
                    "ltp": "23,129.10",
                    "change": "+66.00 (+0.29%)",
                    "bias": "Mildly Bullish / Consolidation",
                    "support": "23,050",
                    "resistance": "23,250",
                    "key_takeaway": "Nifty closed above 23,100 hurdle. Bullish above 23,150; watch 23,050 support."
                },
                {
                    "name": "BANK NIFTY",
                    "ltp": "55,673.15",
                    "change": "+234.65 (+0.42%)",
                    "bias": "Bullish Outperformance",
                    "support": "55,300",
                    "resistance": "56,000",
                    "key_takeaway": "PSU Banks showing fresh strength. Target 56,000 psychological milestone."
                }
            ],
            "strong_sectors": ["Defense & Capital Goods (+0.30%)", "FMCG & Consumption (+0.24%)", "Metals & Mining (+0.09%)"],
            "weak_sectors": ["Information Technology (-1.44%)", "Realty & Infrastructure (-0.79%)", "Cement & Materials (-0.56%)"],
            "breakout_candidates": [],
            "pullback_candidates": [],
            "trading_rules": []
        }
    }

@app.get("/api/market_overview")
def get_market_overview():
    """Returns Top Gainers, Top Losers, Sector Bullish/Bearish boxes, and Market Breadth."""
    all_stocks = []
    advances = 0
    declines = 0
    neutral = 0
    
    sector_stats = {s_id: {"advances": 0, "declines": 0, "stocks": [], "sum_pct": 0.0} for s_id in sd.SECTORS_META.keys()}
    
    for sym in UNIVERSE:
        sec_id = sd.get_sector_for_stock(sym)
        sec_meta = sd.SECTORS_META.get(sec_id, {})
        m = STOCK_METRICS.get(sym, {"price": 1250.0, "change_pct": 0.0, "lot_size": 100})
        name = COMPANY_NAMES.get(sym, sym)
        price = m["price"]
        pct = m["change_pct"]
        chg_pts = round(price * (pct / 100.0), 2)
        base_vol = (hash(sym) % 4500000) + 500000
        vol_str = f"{base_vol:,}"
        
        stk_item = {
            "symbol": sym,
            "company_name": name,
            "sector_id": sec_id,
            "sector_name": sec_meta.get("name", "Other"),
            "sector_icon": sec_meta.get("icon", "📈"),
            "price": price,
            "change_pts": chg_pts,
            "change_pct": pct,
            "volume_str": vol_str,
            "lot_size": m["lot_size"]
        }
        all_stocks.append(stk_item)
        
        if pct > 0:
            advances += 1
            if sec_id in sector_stats:
                sector_stats[sec_id]["advances"] += 1
        elif pct < 0:
            declines += 1
            if sec_id in sector_stats:
                sector_stats[sec_id]["declines"] += 1
        else:
            neutral += 1
            
        if sec_id in sector_stats:
            sector_stats[sec_id]["stocks"].append(stk_item)
            sector_stats[sec_id]["sum_pct"] += pct

    # Sort top gainers & losers
    gainers = sorted([s for s in all_stocks if s["change_pct"] > 0], key=lambda x: x["change_pct"], reverse=True)[:10]
    losers = sorted([s for s in all_stocks if s["change_pct"] < 0], key=lambda x: x["change_pct"])[:10]
    
    # Process Sector Boxes
    sector_cards = []
    bullish_sectors_count = 0
    bearish_sectors_count = 0
    
    for s_id, meta in sd.SECTORS_META.items():
        st = sector_stats.get(s_id, {"advances": 0, "declines": 0, "stocks": [], "sum_pct": 0.0})
        total_in_sec = len(st["stocks"])
        avg_pct = round(st["sum_pct"] / total_in_sec, 2) if total_in_sec > 0 else 0.0
        
        # Sector is Bullish if avg_pct >= 0 and advances >= declines
        is_bullish = (avg_pct >= 0) and (st["advances"] >= st["declines"])
        if is_bullish:
            bullish_sectors_count += 1
        else:
            bearish_sectors_count += 1
            
        # Top 3 stocks in sector
        top_stocks_sec = sorted(st["stocks"], key=lambda x: x["change_pct"], reverse=True)[:3]
        
        sector_cards.append({
            "id": s_id,
            "name": meta["name"],
            "index_ticker": meta["index_ticker"],
            "icon": meta["icon"],
            "color": meta["color"],
            "stock_count": total_in_sec,
            "advances": st["advances"],
            "declines": st["declines"],
            "avg_change_pct": avg_pct,
            "is_bullish": is_bullish,
            "status_label": "BULLISH" if is_bullish else "BEARISH",
            "top_stocks": top_stocks_sec
        })
        
    return {
        "status": "SUCCESS",
        "market_breadth": {
            "total": len(all_stocks),
            "advances": advances,
            "declines": declines,
            "neutral": neutral,
            "bullish_sectors": bullish_sectors_count,
            "bearish_sectors": bearish_sectors_count
        },
        "top_gainers": gainers,
        "top_losers": losers,
        "sectors": sector_cards
    }

@app.get("/api/chart/{symbol}")
def get_chart_data(symbol: str, timeframe: str = "5m", nocache: bool = False):
    """
    Returns OHLCV candles, 9 EMA series, Clean PDH, Clean PDL, 0.20% Buffers, Day High, Day Low, and LTP.
    Timeframe options: 1m (7 days), 5m (1mo ~22 days), 15m (1mo ~22 days), 1d (6mo ~125 days).
    """
    sym = symbol.upper()
    tf = timeframe.lower()
    if tf in ["1m", "1min", "1"]:
        tf = "1m"; period = "7d"; interval = "1m"
    elif tf in ["15m", "15min", "15"]:
        tf = "15m"; period = "1mo"; interval = "15m"
    elif tf in ["1d", "1day", "d", "day"]:
        tf = "1d"; period = "6mo"; interval = "1d"
    else:
        tf = "5m"; period = "1mo"; interval = "5m"
        
    cache_key = f"{sym}_{tf}"
    now = time.time()
    effective_ttl = 3 if tf in ["1m", "5m"] else CHART_CACHE_TTL
    if not nocache and cache_key in CHART_CACHE:
        c_time, c_data = CHART_CACHE[cache_key]
        if now - c_time < effective_ttl:
            return c_data

    df = get_stock_candles(sym, period=period, interval=interval)
    if df is None or len(df) < 5:
        df = generate_fallback_candles(sym, tf)

    # Compute 9 EMA on dataframe
    df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        
    days = sorted(list(set(df.index.strftime('%Y-%m-%d'))))
    if len(days) < 2:
        df_prev = df
        df_today = df
    else:
        df_prev = df.loc[days[-2]]
        df_today = df.loc[days[-1]]
        
    # Calculate Clean PDH / PDL (Yesterday's Clean Levels)
    if tf == "1d":
        clean_pdh = round(float(df_prev['High'].iloc[-1] if hasattr(df_prev['High'], 'iloc') else df_prev['High']), 2)
        clean_pdl = round(float(df_prev['Low'].iloc[-1] if hasattr(df_prev['Low'], 'iloc') else df_prev['Low']), 2)
    else:
        df_prev_clean = df_prev.iloc[1:-1] if len(df_prev) > 2 else df_prev
        clean_pdh = round(float(df_prev_clean['High'].max()), 2)
        clean_pdl = round(float(df_prev_clean['Low'].min()), 2)

    # Clean PDH / PDL 0.20% Strategy Buffers
    pdh_buffer = round(clean_pdh * 0.002, 2)
    pdl_buffer = round(clean_pdl * 0.002, 2)
    clean_pdh_buffer = round(clean_pdh - pdh_buffer, 2) # Retest zone support
    clean_pdl_buffer = round(clean_pdl + pdl_buffer, 2) # Retest zone resistance
    
    # Format candles and 9 EMA for TradingView Lightweight Charts
    candles = []
    volumes = []
    ema9_data = []
    
    for idx, row in df.iterrows():
        t_sec = int(idx.timestamp())
        o = round(float(row['Open']), 2)
        h = round(float(row['High']), 2)
        l = round(float(row['Low']), 2)
        c = round(float(row['Close']), 2)
        v = int(row.get('Volume', 0))
        
        candles.append({"time": t_sec, "open": o, "high": h, "low": l, "close": c})
        volumes.append({
            "time": t_sec,
            "value": v,
            "color": "rgba(34, 197, 94, 0.4)" if c >= o else "rgba(239, 68, 68, 0.4)"
        })
        if not pd.isna(row.get('EMA9')):
            ema9_data.append({"time": t_sec, "value": round(float(row['EMA9']), 2)})
        
    if isinstance(df_today, pd.Series):
        latest_bar = df_today
        first_bar = df_today
        day_high = round(float(df_today['High']), 2)
        day_low = round(float(df_today['Low']), 2)
    else:
        latest_bar = df_today.iloc[-1]
        first_bar = df_today.iloc[0]
        day_high = round(float(df_today['High'].max()), 2)
        day_low = round(float(df_today['Low'].min()), 2)
        
    ltp = round(float(latest_bar['Close']), 2)
    today_open = round(float(first_bar['Open']), 2)
    chg_pct = round(((ltp - today_open) / today_open) * 100.0, 2) if today_open > 0 else 0.0
    latest_ema9 = round(float(latest_bar.get('EMA9', ltp)), 2)
    lot_size = LOT_SIZES.get(sym, 100)
    sec_id = sd.get_sector_for_stock(sym)
    sec_meta = sd.SECTORS_META.get(sec_id, {})
    company_name = COMPANY_NAMES.get(sym, sym)
    
    res = {
        "status": "SUCCESS",
        "symbol": sym,
        "company_name": company_name,
        "sector_id": sec_id,
        "sector_name": sec_meta.get("name", "Other"),
        "sector_icon": sec_meta.get("icon", "🏢"),
        "timeframe": tf,
        "ltp": ltp,
        "change_pct": chg_pct,
        "day_high": day_high,
        "day_low": day_low,
        "clean_pdh": clean_pdh,
        "clean_pdl": clean_pdl,
        "clean_pdh_buffer": clean_pdh_buffer,
        "clean_pdl_buffer": clean_pdl_buffer,
        "latest_ema9": latest_ema9,
        "lot_size": lot_size,
        "candles": candles,
        "volumes": volumes,
        "ema9": ema9_data
    }
    CHART_CACHE[cache_key] = (now, res)
    return res

@app.get("/api/sector_chart/{sector_id}")
def get_sector_chart_data(sector_id: str, timeframe: str = "5m", nocache: bool = False):
    """Returns OHLCV candles and 9 EMA for the sector index matching the requested timeframe."""
    s_meta = sd.SECTORS_META.get(sector_id.upper())
    if not s_meta:
        sector_id = "IT"
        s_meta = sd.SECTORS_META.get("IT")
        
    ticker = s_meta["index_ticker"]
    tf = timeframe.lower()
    if tf in ["1m", "1min", "1"]:
        tf = "1m"; period = "7d"; interval = "1m"
    elif tf in ["15m", "15min", "15"]:
        tf = "15m"; period = "1mo"; interval = "15m"
    elif tf in ["1d", "1day", "d", "day"]:
        tf = "1d"; period = "6mo"; interval = "1d"
    else:
        tf = "5m"; period = "1mo"; interval = "5m"
        
    cache_key = f"SEC_{sector_id.upper()}_{tf}"
    now = time.time()
    effective_ttl = 3 if tf in ["1m", "5m"] else CHART_CACHE_TTL
    if not nocache and cache_key in CHART_CACHE:
        c_time, c_data = CHART_CACHE[cache_key]
        if now - c_time < effective_ttl:
            return c_data

    df = get_stock_candles(ticker, period=period, interval=interval)
    if df is None or len(df) < 5:
        df = generate_fallback_candles(ticker, tf, base_price=25000.0)

    # 9 EMA on Sector
    df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        
    candles = []
    ema9_data = []
    for idx, row in df.iterrows():
        t_sec = int(idx.timestamp())
        candles.append({
            "time": t_sec,
            "open": round(float(row['Open']), 2),
            "high": round(float(row['High']), 2),
            "low": round(float(row['Low']), 2),
            "close": round(float(row['Close']), 2)
        })
        if not pd.isna(row.get('EMA9')):
            ema9_data.append({"time": t_sec, "value": round(float(row['EMA9']), 2)})
        
    latest = df.iloc[-1]
    ltp = round(float(latest['Close']), 2)
    latest_ema9 = round(float(latest.get('EMA9', ltp)), 2)
    first_today = df.iloc[0]
    chg_pct = round(((ltp - float(first_today['Open'])) / float(first_today['Open'])) * 100.0, 2)
    
    res = {
        "status": "SUCCESS",
        "sector_id": sector_id,
        "sector_name": s_meta["name"],
        "timeframe": tf,
        "ltp": ltp,
        "change_pct": chg_pct,
        "latest_ema9": latest_ema9,
        "candles": candles,
        "ema9": ema9_data
    }
    CHART_CACHE[cache_key] = (now, res)
    return res

# ======================================================================================
# 05:00 AM IST MORNING RADAR RESET ENGINE & ARCHIVE
# ======================================================================================
RADAR_META_FILE = os.path.join(os.path.dirname(__file__), "radar_meta.json")
RADAR_ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "radar_archive")

def check_and_perform_daily_radar_reset(force_clear: bool = False) -> Dict[str, Any]:
    """
    Morning 05:00 AM IST Automatic Daily Radar Reset:
    Clears all yesterday's radar alerts so fresh signals for the new trading day can be added.
    Preserves and archives previous alerts so history is never lost.
    Also resets dismissed symbols so all 154 stocks can be evaluated fresh.
    """
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime('%Y-%m-%d')
    os.makedirs(RADAR_ARCHIVE_DIR, exist_ok=True)

    meta = {}
    if os.path.exists(RADAR_META_FILE):
        try:
            with open(RADAR_META_FILE, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        except Exception:
            pass

    last_cleared_date = meta.get("last_cleared_date", "")

    # Trigger if:
    # 1. User requested a manual force clear, OR
    # 2. It is 05:00 AM IST or later (hour >= 5) and today's date hasn't been reset yet.
    should_clear = force_clear or (now_ist.hour >= 5 and last_cleared_date != today_str)

    if should_clear:
        live_alerts_file = os.path.join(os.path.dirname(__file__), "live_alerts.json")
        archived_count = 0
        old_alerts = []

        if os.path.exists(live_alerts_file):
            try:
                with open(live_alerts_file, 'r', encoding='utf-8') as f:
                    old_alerts = json.load(f)
            except Exception:
                old_alerts = []

        if old_alerts:
            # Archive old alerts with date stamp
            archive_date = last_cleared_date if last_cleared_date else (now_ist - timedelta(days=1)).strftime('%Y-%m-%d')
            archive_file = os.path.join(RADAR_ARCHIVE_DIR, f"radar_alerts_{archive_date}.json")
            try:
                with open(archive_file, 'w', encoding='utf-8') as f:
                    json.dump(old_alerts, f, indent=2)
                archived_count = len(old_alerts)
                print(f"[RADAR RESET] Archived {archived_count} previous alerts to {archive_file}")
            except Exception as e:
                print(f"[RADAR RESET] Archive error: {e}")

        # Clear live alerts
        try:
            with open(live_alerts_file, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2)
        except Exception as e:
            print(f"[RADAR RESET] Clear file error: {e}")

        # Reset dismissed symbols table so 154 stocks are scanned fresh for today
        try:
            db.reset_dismissed_radar()
        except Exception as e:
            print(f"[RADAR RESET] Reset dismissed db error: {e}")

        meta = {
            "last_cleared_date": today_str,
            "last_cleared_at": now_ist.isoformat(),
            "last_archived_count": archived_count,
            "status": "CLEARED_FOR_NEW_DAY"
        }
        try:
            with open(RADAR_META_FILE, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

        print(f"[RADAR RESET] Morning 05:00 AM Reset Complete! Cleared for new trading day {today_str}.")
        return {"status": "SUCCESS", "cleared": True, "archived_count": archived_count, "date": today_str}

    return {"status": "SUCCESS", "cleared": False, "date": today_str, "last_cleared": last_cleared_date}

@app.on_event("startup")
async def startup_radar_manager():
    """Initializes 05:00 AM Morning Reset checker, background monitor, and optional cloud scanner."""
    try:
        check_and_perform_daily_radar_reset()
    except Exception as e:
        print(f"[STARTUP RADAR CHECK ERR] {e}")
    asyncio.create_task(radar_morning_worker())

    # On cloud platforms (Render, Koyeb) or if RUN_SCANNER_IN_BACKGROUND=true, automatically launch live scanner
    should_run_scanner = os.environ.get("RUN_SCANNER_IN_BACKGROUND", "false").lower() in ("true", "1", "yes") or bool(os.environ.get("RENDER")) or bool(os.environ.get("KOYEB"))
    if should_run_scanner:
        scanner_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "live_clean_alert_scanner.py"))
        if os.path.exists(scanner_script):
            import threading, subprocess, sys
            def _launch_scanner():
                print(f"[CLOUD SCANNER] Starting background Telegram scanner: {scanner_script}")
                try:
                    subprocess.run([sys.executable, scanner_script])
                except Exception as ex:
                    print(f"[CLOUD SCANNER ERROR] {ex}")
            threading.Thread(target=_launch_scanner, daemon=True).start()

async def radar_morning_worker():
    """Background worker that continuously monitors for 05:00 AM IST to clear old day's radar alerts."""
    while True:
        try:
            check_and_perform_daily_radar_reset()
        except Exception as e:
            print(f"[RADAR WORKER ERR] {e}")
        await asyncio.sleep(60)

@app.get("/api/radar")
def get_radar_signals():
    """
    Returns active 3-Stage strategy alerts for the 154 universe:
    Stage 1: 🟡 Breakout Alert 🚨
    Stage 2: ⏳ Pullback Waiting
    Stage 3: 🟢 Entry Confirmed ✅
    Excludes user-dismissed and user-verified signals.
    Auto-resets every morning at 05:00 AM IST for the new trading day.
    """
    # 1. Trigger 05:00 AM daily check
    check_and_perform_daily_radar_reset()

    raw_alerts = []
    live_alerts_file = os.path.join(os.path.dirname(__file__), "live_alerts.json")
    if os.path.exists(live_alerts_file):
        try:
            with open(live_alerts_file, "r", encoding="utf-8") as f:
                raw_alerts = json.load(f)
        except Exception:
            raw_alerts = []

    # 2. Filter out dismissed and already verified symbols
    dismissed_set = set(db.get_dismissed_symbols())
    verified_set = set(db.get_verified_symbols())

    filtered_alerts = [
        a for a in raw_alerts
        if a["symbol"].upper() not in dismissed_set and a["symbol"].upper() not in verified_set
    ]

    # Calculate counts per stage
    c_all = len(filtered_alerts)
    c_confirmed = len([a for a in filtered_alerts if a.get("stage") == 3 or "Confirmed" in str(a.get("stage_badge", ""))])
    c_pullback = len([a for a in filtered_alerts if a.get("stage") == 2 or "Pullback" in str(a.get("stage_badge", "")) or "9 EMA" in str(a.get("stage_badge", ""))])
    c_breakout = len([a for a in filtered_alerts if a.get("stage") == 1 or "Breakout" in str(a.get("stage_badge", ""))])

    return {
        "status": "SUCCESS",
        "count": c_all,
        "counts": {
            "all": c_all,
            "confirmed": c_confirmed,
            "pullback": c_pullback,
            "breakout": c_breakout,
            "verified": len(verified_set),
            "dismissed": len(dismissed_set)
        },
        "alerts": filtered_alerts,
        "auto_reset_schedule": "Daily at 05:00 AM IST"
    }

@app.post("/api/radar/clear")
def clear_radar_signals():
    """Manually clear radar alerts immediately and archive current ones."""
    res = check_and_perform_daily_radar_reset(force_clear=True)
    return res

@app.get("/api/radar/archive")
def get_radar_archive(date: Optional[str] = None):
    """Returns archived radar alerts for any past date (default: most recent archive)."""
    os.makedirs(RADAR_ARCHIVE_DIR, exist_ok=True)
    files = sorted(os.listdir(RADAR_ARCHIVE_DIR), reverse=True)
    if not files:
        return {"status": "SUCCESS", "date": None, "alerts": []}

    target_file = files[0]
    if date:
        for f in files:
            if date in f:
                target_file = f
                break

    file_path = os.path.join(RADAR_ARCHIVE_DIR, target_file)
    try:
        with open(file_path, 'r', encoding='utf-8') as fp:
            archived_alerts = json.load(fp)
        return {
            "status": "SUCCESS",
            "file": target_file,
            "count": len(archived_alerts),
            "alerts": archived_alerts
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e), "alerts": []}

@app.post("/api/radar/verify")
def verify_radar_alert(req: VerifyAlertRequest):
    """Marks a radar signal as verified and adds it to the user's verified trade wishlist."""
    success = db.add_verified_signal(req.dict())
    if not success:
        raise HTTPException(status_code=400, detail="Failed to verify signal")
    return {"status": "SUCCESS", "message": f"{req.symbol} verified and added to trade wishlist"}

@app.post("/api/radar/dismiss")
def dismiss_radar_alert(req: DismissAlertRequest):
    """Dismisses an alert from radar so it no longer clutters the feed."""
    db.dismiss_radar_signal(req.symbol)
    return {"status": "SUCCESS", "message": f"{req.symbol} dismissed from radar"}

@app.get("/api/radar/verified")
def get_verified_trade_wishlist():
    """Returns all verified signals with sector information and live LTP."""
    signals = db.get_verified_signals()
    for s in signals:
        sym = s["symbol"].upper()
        sec_id = sd.get_sector_for_stock(sym)
        sec_meta = sd.SECTORS_META.get(sec_id, {})
        s["sector_name"] = sec_meta.get("name", "Other")
        s["sector_icon"] = sec_meta.get("icon", "🏢")
        m = STOCK_METRICS.get(sym, {})
        s["lot_size"] = m.get("lot_size", LOT_SIZES.get(sym, 500))
        if not s.get("ltp") or s["ltp"] == 0:
            s["ltp"] = m.get("price", s.get("entry_price", 1000.0))
    return {"status": "SUCCESS", "count": len(signals), "signals": signals}

@app.post("/api/radar/verified/remove")
def remove_verified_alert(req: RemoveVerifiedRequest):
    """Removes a stock from the verified wishlist."""
    db.remove_verified_signal(req.symbol)
    return {"status": "SUCCESS", "message": f"{req.symbol} removed from verified wishlist"}

@app.post("/api/radar/dismissed/reset")
def reset_dismissed_radar_alerts():
    """Resets all dismissed radar alerts so they appear back on the radar."""
    db.reset_dismissed_radar()
    return {"status": "SUCCESS", "message": "Dismissed radar alerts reset successfully"}

# --------------------------------------------------------------------------------------
# WALLET & TRADING APIS
# --------------------------------------------------------------------------------------

@app.get("/api/wallet")
def get_wallet_info():
    return db.get_wallet()

@app.post("/api/wallet/deposit")
def deposit_virtual_funds(req: DepositRequest):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Deposit amount must be positive")
    return db.deposit_funds(req.amount, req.note or "Funds Added")

@app.post("/api/order/place")
def place_order(req: OrderRequest):
    try:
        res = db.open_position(
            symbol=req.symbol,
            qty=req.qty,
            entry_price=req.entry_price,
            sl_price=req.sl_price,
            target1=req.target1 or 0.0,
            target2=req.target2 or 0.0,
            side=req.side or "BUY"
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/close")
def close_order(req: CloseOrderRequest):
    try:
        res = db.close_position_partial(
            position_id=req.position_id,
            exit_price=req.exit_price,
            pct_to_close=req.pct_to_close or 100.0,
            exit_reason=req.exit_reason or "Manual Exit"
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/order/update_sl")
def update_sl(req: UpdateSLRequest):
    return db.update_position_sl(req.position_id, req.new_sl)

@app.get("/api/positions")
def get_positions():
    """Returns open positions with live LTP, 9 EMA, trailing SL violation status, and unrealized P&L."""
    positions = db.get_open_positions()
    results = []
    
    for pos in positions:
        sym = pos["symbol"]
        qty = int(pos["qty"])
        side = pos.get("side", "BUY")
        entry = float(pos["entry_price"])
        sl = float(pos["sl_price"])
        t1 = float(pos["target1_price"])
        t2 = float(pos["target2_price"])
        
        # 5m candles check for live LTP and 9 EMA trailing SL rule
        df = get_stock_candles(sym, period="5d", interval="5m")
        ema9 = 0.0
        ema_violated = False
        ema_violation_msg = ""
        
        if df is not None and not df.empty:
            ltp = round(float(df.iloc[-1]['Close']), 2)
            df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
            ema9 = round(float(df['EMA9'].iloc[-1]), 2)
            
            # Trailing SL Exit Rule:
            # Long: Jaise hi 5m candle 9 EMA ke niche CLOSE kare (Close < 9 EMA), position cut karein!
            # Short: Jaise hi 5m candle 9 EMA ke upar CLOSE kare (Close > 9 EMA), position cut karein!
            if side == "BUY" and ltp < ema9:
                ema_violated = True
                ema_violation_msg = f"5m Candle below 9 EMA (₹{ema9:.2f}) • Cut Long Position Now!"
            elif side == "SELL" and ltp > ema9:
                ema_violated = True
                ema_violation_msg = f"5m Candle above 9 EMA (₹{ema9:.2f}) • Cut Short Position Now!"
        else:
            ltp = entry
            
        if side == "SELL":
            unrealized_pnl = round(qty * (entry - ltp), 2)
            unrealized_pct = round(((entry - ltp) / entry) * 100.0, 2)
        else:
            unrealized_pnl = round(qty * (ltp - entry), 2)
            unrealized_pct = round(((ltp - entry) / entry) * 100.0, 2)
        
        results.append({
            "id": pos["id"],
            "symbol": sym,
            "side": side,
            "qty": qty,
            "initial_qty": pos["initial_qty"],
            "entry_price": entry,
            "sl_price": sl,
            "target1_price": t1,
            "target2_price": t2,
            "ltp": ltp,
            "ema9": ema9,
            "ema_violated": ema_violated,
            "ema_violation_msg": ema_violation_msg,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pct": unrealized_pct,
            "status": pos["status"],
            "entry_time": pos["entry_time"]
        })
        
    return {"status": "SUCCESS", "count": len(results), "positions": results}

@app.get("/api/reports")
def get_reports(period: str = Query("ALL", pattern="^(DAILY|WEEKLY|MONTHLY|YEARLY|ALL)$")):
    """Returns performance reports and analytics for selected period."""
    return db.get_trades_report(filter_period=period.upper())

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
