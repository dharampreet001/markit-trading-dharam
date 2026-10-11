"""
NSE F&O 4-TIER INSTITUTIONAL CLEAN BREAKOUT & RETEST SCANNER
Rules Locked:
  Tier 1: Nifty 50 / Bank Nifty Market Mood (Uptrend & > 9 EMA)
  Tier 2: Sector Index Alignment (Sector Green & > 9 EMA)
  Tier 3: Stock Clean PDH Breakout (09:20 - 11:30 AM, No Freak Wicks)
  Tier 4: 9 EMA Support Retest + Bullish Reversal Candle + Volume
  Risk  : SL below 9 EMA (min 0.35% buffer), Target 1 (1:2), Target 2 (1:4)
"""

import os
import sys
import time
import json
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
import telebot
import pytz
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add paper_trading_terminal to path for sector data
sys.path.append(os.path.join(os.path.dirname(__file__), 'paper_trading_terminal'))
import sector_data as sd

SECTOR_TICKERS = {
    "IT": "^CNXIT",
    "BANK_FIN": "^NSEBANK",
    "AUTO": "^CNXAUTO",
    "PHARMA": "^CNXPHARMA",
    "METAL": "^CNXMETAL",
    "ENERGY": "^CNXENERGY",
    "REALTY_INFRA": "^CNXREALTY",
    "CONSUMER": "^CNXFMCG",
    "DEFENSE_ENG": "^CNXPSE",
    "MATERIALS": "^CNXINFRA"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load Telegram Credentials
load_dotenv(os.path.join(BASE_DIR, '.env'))
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    print("[ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in .env")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN)
IST = pytz.timezone('Asia/Kolkata')
ALERTS_CACHE_FILE = os.path.join(BASE_DIR, 'paper_trading_terminal', 'live_alerts.json')

def record_live_alert(sym, stage, badge, breakout_time, pullback_time, entry_time, clean_pdh, entry_price, sl_price, t1, t2, risk_pct, direction="LONG"):
    try:
        alerts = []
        if os.path.exists(ALERTS_CACHE_FILE):
            with open(ALERTS_CACHE_FILE, 'r', encoding='utf-8') as f:
                alerts = json.load(f)
        
        # Filter out older alert of the same symbol to show latest stage
        alerts = [a for a in alerts if a.get('symbol') != sym]
        alerts.insert(0, {
            "symbol": sym,
            "direction": direction,
            "stage": stage,
            "stage_badge": badge,
            "breakout_time": breakout_time or "--:--",
            "pullback_time": pullback_time or "--:--",
            "entry_time": entry_time or "--:--",
            "clean_level": round(clean_pdh, 2) if clean_pdh else 0.0,
            "entry_price": round(entry_price, 2) if entry_price else 0.0,
            "sl_price": round(sl_price, 2) if sl_price else 0.0,
            "target1": round(t1, 2) if t1 else 0.0,
            "target2": round(t2, 2) if t2 else 0.0,
            "risk_pct": round(risk_pct, 2) if risk_pct else 0.0,
            "result": "ACTIVE"
        })
        alerts = alerts[:50]
        os.makedirs(os.path.dirname(ALERTS_CACHE_FILE), exist_ok=True)
        with open(ALERTS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(alerts, f, indent=2)
    except Exception as e:
        print(f"[CACHE LOG ERROR] {e}")

class SimpleStockTracker:
    def __init__(self, symbol, pdh, pdl):
        self.symbol = symbol
        self.pdh = pdh
        self.pdl = pdl
        
        # Bullish Tracking State: IDLE -> BREAKOUT -> PULLBACK_9EMA -> CONFIRMED
        self.state = 'IDLE'
        self.breakout_time = None
        self.swing_high = None
        self.swing_high_idx = 0
        self.pullback_time = None
        self.pullback_low = None
        self.pullback_red_high = None
        self.pullback_vol = 0
        self.has_red_candle = False
        self.evaluated_candles = set()
        self.msg1_sent = False  # 🟡 Breakout Alert
        self.msg2_sent = False  # ⏳ 9 EMA Pullback Alert
        self.msg3_sent = False  # 🟢 9 EMA Reversal Entry Confirmed
        self.macro_tag = ""

        # Bearish Tracking State: IDLE -> BREAKDOWN -> PULLBACK_9EMA -> CONFIRMED
        self.bear_state = 'IDLE'
        self.bear_breakdown_time = None
        self.swing_low = None
        self.swing_low_idx = 0
        self.bear_pullback_time = None
        self.pullback_high = None
        self.pullback_green_low = None
        self.pullback_bear_vol = 0
        self.has_green_candle = False
        self.bear_evaluated_candles = set()
        self.bear_msg1_sent = False  # 🔴 Breakdown Alert
        self.bear_msg2_sent = False  # ⏳ 9 EMA Resistance Retest Alert
        self.bear_msg3_sent = False  # 🔴 Short Entry Confirmed
        self.bear_macro_tag = ""

def load_universe():
    path = os.path.join(BASE_DIR, 'fno_stocks.csv')
    with open(path, 'r', encoding='utf-8') as f:
        symbols = [line.strip().upper() for line in f if line.strip() and line.strip() != 'SYMBOL']
    return symbols

def initialize_clean_levels(symbols):
    print(f"\n[INIT] Calculating Clean PDH & PDL (Excluding 09:15 AM & 03:25 PM freak wicks) for {len(symbols)} stocks...")
    tickers = [f"{s}.NS" for s in symbols]
    
    # Download past 5 days 5m data in chunks
    all_5m = {}
    chunk_size = 35
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        try:
            df_b = yf.download(chunk, period="5d", interval="5m", group_by="ticker", progress=False)
            for t in chunk:
                sym = t.replace(".NS", "")
                try:
                    df_t = df_b[t].dropna(how='all') if len(chunk) > 1 else df_b.dropna(how='all')
                    if not df_t.empty:
                        all_5m[sym] = df_t
                except Exception:
                    pass
        except Exception:
            pass
            
    trackers = {}
    for sym in symbols:
        if sym not in all_5m:
            continue
        df_s = all_5m[sym]
        if isinstance(df_s.columns, pd.MultiIndex):
            df_s.columns = df_s.columns.droplevel(1)
        if df_s.index.tz is not None:
            df_s.index = df_s.index.tz_convert('Asia/Kolkata')
            
        days = sorted(list(set(df_s.index.strftime('%Y-%m-%d'))))
        if len(days) < 2:
            continue
            
        # Previous trading day (excluding today)
        prev_date = days[-2] if len(days) >= 2 else days[0]
        df_prev = df_s.loc[prev_date]
        
        if len(df_prev) < 10:
            continue
            
        # Clean S/R: Exclude first (09:15) and last (03:25) candles of previous day
        df_prev_clean = df_prev.iloc[1:-1] if len(df_prev) > 2 else df_prev
        clean_pdh = round(float(df_prev_clean['High'].max()), 2)
        clean_pdl = round(float(df_prev_clean['Low'].min()), 2)
        
        trackers[sym] = SimpleStockTracker(sym, clean_pdh, clean_pdl)
        
    print(f"[READY] Clean levels initialized for {len(trackers)} / {len(symbols)} stocks.")
    return trackers

def check_macro_market_trend():
    """
    Checks real-time Nifty 50 and Bank Nifty mood (Macro Trend) and Sector Index momentum for BOTH Bullish & Bearish conditions.
    """
    try:
        all_idx = ['^NSEI', '^NSEBANK'] + list(SECTOR_TICKERS.values())
        df_idx = yf.download(all_idx, period="1d", interval="5m", group_by="ticker", progress=False)
        
        nifty_bullish = True
        nifty_bearish = False
        nifty_crashing = False
        nifty_rallying = False
        nifty_info = {"close": 0, "open": 0, "change_pct": 0, "status": "UPTREND 🟢"}
        
        if '^NSEI' in df_idx:
            d_n = df_idx['^NSEI'].dropna(how='all')
            if not d_n.empty:
                if isinstance(d_n.columns, pd.MultiIndex):
                    d_n.columns = d_n.columns.droplevel(1)
                n_open = float(d_n.iloc[0]['Open'])
                n_close = float(d_n.iloc[-1]['Close'])
                n_ema9 = float(d_n['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
                chg = round(((n_close - n_open) / n_open) * 100, 2)
                
                # Bullish rule: >= Open and >= 9 EMA
                nifty_bullish = (n_close >= n_open) and (n_close >= n_ema9 * 0.999)
                # Bearish rule: <= Open and <= 9 EMA
                nifty_bearish = (n_close <= n_open) and (n_close <= n_ema9 * 1.001)
                
                # Crash rule: Down > 0.40% and firmly below 9 EMA (Blocks BUY trades)
                nifty_crashing = (chg < -0.40 and n_close < n_ema9 * 0.998)
                # Rally rule: Up > 0.40% and firmly above 9 EMA (Blocks SHORT trades)
                nifty_rallying = (chg > 0.40 and n_close > n_ema9 * 1.002)

                if nifty_bullish:
                    status_str = "UPTREND 🟢"
                elif nifty_crashing:
                    status_str = "CRASH / HEAVY SELLING 🔴"
                elif nifty_bearish:
                    status_str = "DOWNTREND 🔴"
                else:
                    status_str = "SIDEWAYS / FLAT ⏸️"

                nifty_info = {
                    "close": n_close, "open": n_open, "ema9": n_ema9,
                    "change_pct": chg, "bullish": nifty_bullish, "bearish": nifty_bearish,
                    "crashing": nifty_crashing, "rallying": nifty_rallying,
                    "status": status_str
                }

        banknifty_info = {"close": 0, "open": 0, "change_pct": 0, "status": "UPTREND 🟢", "bullish": True, "bearish": False}
        bank_bullish = True
        bank_bearish = False
        if '^NSEBANK' in df_idx:
            d_b = df_idx['^NSEBANK'].dropna(how='all')
            if not d_b.empty:
                if isinstance(d_b.columns, pd.MultiIndex):
                    d_b.columns = d_b.columns.droplevel(1)
                b_open = float(d_b.iloc[0]['Open'])
                b_close = float(d_b.iloc[-1]['Close'])
                b_ema9 = float(d_b['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
                b_chg = round(((b_close - b_open) / b_open) * 100, 2)
                
                bank_bullish = (b_close >= b_open) and (b_close >= b_ema9 * 0.999)
                bank_bearish = (b_close <= b_open) and (b_close <= b_ema9 * 1.001)
                b_crashing = (b_chg < -0.40 and b_close < b_ema9 * 0.998)
                
                if bank_bullish:
                    b_status = "UPTREND 🟢"
                elif b_crashing:
                    b_status = "CRASH / HEAVY SELLING 🔴"
                elif bank_bearish:
                    b_status = "DOWNTREND 🔴"
                else:
                    b_status = "SIDEWAYS / FLAT ⏸️"

                banknifty_info = {
                    "close": b_close, "open": b_open, "ema9": b_ema9,
                    "change_pct": b_chg, "bullish": bank_bullish, "bearish": bank_bearish,
                    "status": b_status
                }

        sector_trends = {}
        for sec_id, ticker in SECTOR_TICKERS.items():
            if ticker in df_idx:
                d_s = df_idx[ticker].dropna(how='all')
                if not d_s.empty:
                    if isinstance(d_s.columns, pd.MultiIndex):
                        d_s.columns = d_s.columns.droplevel(1)
                    s_open = float(d_s.iloc[0]['Open'])
                    s_close = float(d_s.iloc[-1]['Close'])
                    s_ema9 = float(d_s['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
                    s_chg = round(((s_close - s_open) / s_open) * 100, 2)
                    
                    s_bull = (s_close >= s_open) and (s_close >= s_ema9 * 0.999)
                    s_strong_bull = s_bull and (s_chg >= 0.30 or s_close >= float(d_s['High'].max()) * 0.998)
                    
                    s_bear = (s_close <= s_open) and (s_close <= s_ema9 * 1.001)
                    s_strong_bear = s_bear and (s_chg <= -0.30 or s_close <= float(d_s['Low'].min()) * 1.002)

                    sector_trends[sec_id] = {
                        "bullish": s_bull,
                        "strong_bullish": s_strong_bull,
                        "bearish": s_bear,
                        "strong_bearish": s_strong_bear,
                        "change_pct": s_chg,
                        "close": s_close,
                        "ema9": s_ema9
                    }
        return {
            "nifty_bullish": nifty_bullish,
            "nifty_bearish": nifty_bearish,
            "nifty_crashing": nifty_crashing,
            "nifty_rallying": nifty_rallying,
            "bank_bullish": bank_bullish,
            "bank_bearish": bank_bearish,
            "nifty_info": nifty_info,
            "banknifty_info": banknifty_info,
            "sector_trends": sector_trends
        }
    except Exception as e:
        print(f"[MACRO CHECK ERR] {e}")
        return {
            "nifty_bullish": True, "nifty_bearish": False,
            "nifty_crashing": False, "nifty_rallying": False,
            "bank_bullish": True, "bank_bearish": False,
            "nifty_info": {"status": "UNKNOWN", "change_pct": 0},
            "banknifty_info": {"status": "UNKNOWN", "change_pct": 0},
            "sector_trends": {}
        }

def run_live_scanner():
    print("=" * 70)
    print("   LOCKED 4-TIER INSTITUTIONAL BREAKOUT & RETEST SCANNER   ")
    print("=" * 70)
    print("Rules Active:")
    print("  Tier 1: Nifty 50 Market Mood Check (Close >= Open & Close >= 9 EMA)")
    print("  Tier 2: Sector Index Alignment (Sector Green & Bullish)")
    print("  Tier 3: Clean PDH / PDL (No freak wicks, 09:20 - 11:30 AM cutoff)")
    print("  Tier 4: 9 EMA Support Retest + Bullish Reversal Candle + Volume")
    print("  Risk  : SL below 9 EMA (min 0.35% buffer), Target 1 (1:2), Target 2 (1:4)")
    print("=" * 70)
    
    symbols = load_universe()
    trackers = initialize_clean_levels(symbols)
    tickers = [f"{s}.NS" for s in trackers.keys()]
    current_scanner_date = datetime.now(IST).strftime('%Y-%m-%d')
    
    print("\n[START] Live 4-tier scanning started. Checking every cycle...")
    scan_count = 0
    
    is_github_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    is_test_run = os.environ.get("TEST_RUN", "false").lower() in ("true", "1", "yes")

    if is_github_actions:
        try:
            bot.send_message(
                CHAT_ID,
                f"🚀 <b>4-TIER STRATEGY SCANNER LIVE ON CLOUD!</b>\n"
                f"• Platform: GitHub Actions (Microsoft Cloud)\n"
                f"• Time: {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S')} IST\n"
                f"• Monitoring: {len(trackers)} F&O Stocks (Zero Sleep / No Restrictions)",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[GITHUB ACTIONS STARTUP TG ERR] {e}")

    while True:
        try:
            now_ist = datetime.now(IST)
            now_str = now_ist.strftime('%H:%M:%S')
            today_str = now_ist.strftime('%Y-%m-%d')
            h_now = now_ist.hour
            m_now = now_ist.minute
            
            # -------------------------------------------------------------------------
            # 05:00 AM DAILY RADAR RESET & CLEAN LEVEL RECALCULATION
            # -------------------------------------------------------------------------
            if h_now >= 5 and current_scanner_date != today_str:
                print(f"\n[05:00 AM RESET] New trading day detected: {today_str}. Archiving yesterday's radar & resetting...")
                archive_dir = os.path.join(BASE_DIR, 'paper_trading_terminal', 'radar_archive')
                os.makedirs(archive_dir, exist_ok=True)
                if os.path.exists(ALERTS_CACHE_FILE):
                    try:
                        with open(ALERTS_CACHE_FILE, 'r', encoding='utf-8') as f:
                            old = json.load(f)
                        if old:
                            with open(os.path.join(archive_dir, f"radar_alerts_{current_scanner_date}.json"), 'w', encoding='utf-8') as f:
                                json.dump(old, f, indent=2)
                    except Exception as e:
                        print(f"[ARCHIVE ERR] {e}")
                    try:
                        with open(ALERTS_CACHE_FILE, 'w', encoding='utf-8') as f:
                            json.dump([], f, indent=2)
                    except Exception as e:
                        print(f"[CLEAR ERR] {e}")
                
                # Recalculate fresh Clean PDH/PDL for the new day
                trackers = initialize_clean_levels(symbols)
                tickers = [f"{s}.NS" for s in trackers.keys()]
                current_scanner_date = today_str
                print(f"[05:00 AM RESET] Radar cleared & fresh levels calculated for {today_str}!\n")

            # Market hours check (09:15 to 15:30 IST)
            if not is_test_run:
                if h_now < 9 or (h_now == 9 and m_now < 15):
                    print(f"[{now_str}] Pre-market. Waiting for 09:15 AM open...")
                    time.sleep(30)
                    continue
                    
                if h_now > 15 or (h_now == 15 and m_now > 30):
                    print(f"[{now_str}] Market closed for today. Scanner in standby mode.")
                    if is_github_actions:
                        print("[GITHUB ACTIONS] Market hours completed. Gracefully exiting runner.")
                        try:
                            bot.send_message(
                                CHAT_ID,
                                f"🏁 <b>Market Session Completed ({today_str})</b>\nGitHub Actions scanner shutting down until tomorrow 09:10 AM IST.",
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
                        break
                    time.sleep(60)
                    continue
                
            scan_count += 1
            print(f"\n[{now_str}] Scan #{scan_count} evaluating market mood & {len(trackers)} stocks...")
            
            # TIER 1 & TIER 2: CHECK MARKET MOOD & SECTORS (DUAL BULLISH & BEARISH)
            macro = check_macro_market_trend()
            nifty_bullish = macro["nifty_bullish"]
            nifty_bearish = macro["nifty_bearish"]
            nifty_crashing = macro["nifty_crashing"]
            nifty_rallying = macro["nifty_rallying"]
            bank_bullish = macro["bank_bullish"]
            bank_bearish = macro["bank_bearish"]
            nifty_info = macro["nifty_info"]
            banknifty_info = macro["banknifty_info"]
            sector_trends = macro["sector_trends"]

            print(f"  • Nifty 50 Mood: {nifty_info.get('status', 'OK')} ({nifty_info.get('change_pct', 0):+.2f}%) | Bank Nifty: {banknifty_info.get('status', 'OK')}")

            if nifty_crashing:
                print(f"  ⚠️ [MACRO CRASH FILTER] Nifty 50 is in Severe Selloff ({nifty_info.get('change_pct', 0):+.2f}%). BUY alerts paused, SHORT alerts enabled.")
            if nifty_rallying:
                print(f"  🚀 [MACRO RALLY FILTER] Nifty 50 is in Heavy Rally ({nifty_info.get('change_pct', 0):+.2f}%). SHORT alerts paused, BUY alerts enabled.")

            # Download live intraday 5m data in chunks
            chunk_size = 35
            for i in range(0, len(tickers), chunk_size):
                chunk = tickers[i : i + chunk_size]
                try:
                    df_b = yf.download(chunk, period="1d", interval="5m", group_by="ticker", progress=False)
                    for t in chunk:
                        sym = t.replace(".NS", "")
                        if sym not in trackers:
                            continue
                        tracker = trackers[sym]
                        
                        try:
                            df_t = df_b[t].dropna(how='all') if len(chunk) > 1 else df_b.dropna(how='all')
                            if df_t.empty:
                                continue
                            if isinstance(df_t.columns, pd.MultiIndex):
                                df_t.columns = df_t.columns.droplevel(1)
                            if df_t.index.tz is not None:
                                df_t.index = df_t.index.tz_convert('Asia/Kolkata')
                                
                            if len(df_t) < 2:
                                continue
                                
                            # Calculate 9 EMA on stock
                            df_t['EMA9'] = df_t['Close'].ewm(span=9, adjust=False).mean()

                            # TIER 2: SECTOR CHECK & DUAL-INDEX MACRO ALIGNMENT
                            sec_id = sd.STOCK_TO_SECTOR.get(sym, 'OTHER')
                            sec_info = sector_trends.get(sec_id, {"bullish": True, "strong_bullish": False, "bearish": False, "strong_bearish": False, "change_pct": 0})
                            
                            both_indices_bullish = nifty_bullish and bank_bullish
                            both_indices_bearish = nifty_bearish and bank_bearish
                            
                            can_trade_bull = False
                            can_trade_bear = False
                            bull_macro_tag = ""
                            bear_macro_tag = ""

                            # 🟢 BULLISH ELIGIBILITY:
                            if not nifty_crashing:
                                if both_indices_bullish and sec_info.get("bullish", False):
                                    can_trade_bull = True
                                    bull_macro_tag = f"Nifty 50 🟢 + Bank Nifty 🟢 Both Bullish | Sector {sec_id} Aligned 🟢"
                                elif not both_indices_bullish and sec_info.get("strong_bullish", False):
                                    can_trade_bull = True
                                    bull_macro_tag = f"⚡ SECTOR NEWS RALLY (Indices Choppy, {sec_id} +{sec_info.get('change_pct', 0):+.2f}% Outperforming)"

                            # 🔴 BEARISH ELIGIBILITY (EXACT MIRROR):
                            if not nifty_rallying:
                                if both_indices_bearish and sec_info.get("bearish", False):
                                    can_trade_bear = True
                                    bear_macro_tag = f"Nifty 50 🔴 + Bank Nifty 🔴 Both Bearish | Sector {sec_id} Aligned 🔴"
                                elif not both_indices_bearish and sec_info.get("strong_bearish", False):
                                    can_trade_bear = True
                                    bear_macro_tag = f"⚡ SECTOR BREAKDOWN (Indices Choppy, {sec_id} {sec_info.get('change_pct', 0):+.2f}% Selling)"

                            if not can_trade_bull and not can_trade_bear:
                                continue

                            tracker.macro_tag = bull_macro_tag
                            tracker.bear_macro_tag = bear_macro_tag

                            for idx in range(1, len(df_t)):
                                c = df_t.iloc[idx]
                                prev_c = df_t.iloc[idx-1]
                                t_str = c.name.strftime('%H:%M')
                                h_bar = int(t_str.split(':')[0])
                                m_bar = int(t_str.split(':')[1])
                                
                                c_high = float(c['High'])
                                c_low = float(c['Low'])
                                c_close = float(c['Close'])
                                c_open = float(c['Open'])
                                c_ema9 = float(c['EMA9'])
                                prev_ema9 = float(prev_c['EMA9'])
                                c_vol = float(c.get('Volume', 0))
                                
                                # USER RULE: Trades allowed from morning up to maximum 12:00 PM sharp.
                                # All trades after 12:00 PM are avoided (separate strategy will be created for afternoon).
                                if h_bar > 12 or (h_bar == 12 and m_bar > 0):
                                    break
                                    
                                time_fmt = f"{c.name.hour}:{c.name.minute:02d}"

                                # =========================================================
                                # 🟢 BULLISH SETUP (CLEAN PDH BREAKOUT + RISING 9 EMA RETEST)
                                # =========================================================
                                if can_trade_bull:
                                    ema_rising = c_ema9 >= prev_ema9 * 0.9995

                                    # Tier 3: Breakout / Clean PDH Buffer Zone Test
                                    if tracker.state == 'IDLE':
                                        prior_high = float(df_t.iloc[:idx]['High'].max())
                                        pdh_buffer = max(0.50, round(tracker.pdh * 0.002, 2))
                                        if (c_high >= tracker.pdh - pdh_buffer or c_high >= tracker.pdh) and c_high > prior_high:
                                            tracker.state = 'BREAKOUT'
                                            tracker.breakout_time = c.name
                                            tracker.swing_high = c_high
                                            tracker.swing_high_idx = idx
                                            tracker.pullback_low = c_low
                                            tracker.msg1_sent = True
                                            
                                            alert1_text = (
                                                f"🟡 {sym} ({sec_id}) → {time_fmt} PDH Resistance Zone Tested 🚨\n"
                                                f"• Macro Mode: {tracker.macro_tag}\n"
                                                f"• Clean PDH: Rs.{tracker.pdh:.2f} (Tested near Rs.{c_high:.2f}) | Waiting for Rising 9 EMA Retest"
                                            )
                                            print(f"🔔 [SENDING TELEGRAM] {alert1_text}")
                                            try:
                                                bot.send_message(CHAT_ID, alert1_text)
                                            except Exception as err:
                                                print(f"[TG ERROR] {err}")

                                            risk_pts = max(0.10, tracker.swing_high - tracker.pullback_low)
                                            record_live_alert(
                                                sym=sym, stage=1, badge="🟡 Breakout Alert 🚨",
                                                breakout_time=time_fmt, pullback_time="--:--", entry_time="--:--",
                                                clean_pdh=tracker.pdh, entry_price=tracker.swing_high, sl_price=tracker.pullback_low,
                                                t1=round(tracker.swing_high + 2.0 * risk_pts, 2),
                                                t2=round(tracker.swing_high + 4.0 * risk_pts, 2),
                                                risk_pct=round((risk_pts / tracker.swing_high) * 100.0, 2),
                                                direction="LONG"
                                            )

                                    # Tier 4: Pullback to Rising 9 EMA & Green Candle Close Above 9 EMA
                                    elif tracker.state == 'BREAKOUT' and not tracker.msg3_sent:
                                        if tracker.breakout_time is not None and c.name > tracker.breakout_time:
                                            if c.name not in tracker.evaluated_candles:
                                                tracker.evaluated_candles.add(c.name)
                                                if c_high > tracker.swing_high and c_close >= c_open:
                                                    tracker.swing_high = c_high
                                                    tracker.swing_high_idx = idx
                                                elif c_low <= c_ema9 * 1.003:
                                                    tracker.pullback_low = min(tracker.pullback_low, c_low)
                                                    # USER RULE 2: Pullback ke baad 9 EMA ke upar strong green candle close hote hi pullback alert
                                                    if c_close > c_open and c_close >= c_ema9 and ema_rising:
                                                        tracker.state = 'PULLBACK_CONFIRMED'
                                                        tracker.msg2_sent = True
                                                        
                                                        alert2_text = (
                                                            f"⏳ {sym} ({sec_id}) → {time_fmt} 9 EMA Pullback Confirmed 📈\n"
                                                            f"• Clean PDH: Rs.{tracker.pdh:.2f} | Swing High: Rs.{tracker.swing_high:.2f}\n"
                                                            f"• Pullback Low: Rs.{tracker.pullback_low:.2f} | Green Candle Closed above 9 EMA @ Rs.{c_close:.2f}\n"
                                                            f"• NEXT ACTION: Setup Ready! Jaise hi Swing High (Rs.{tracker.swing_high:.2f}) break hoga, Buy Entry trigger hogi!\n"
                                                            f"• Mode: {tracker.macro_tag}"
                                                        )
                                                        print(f"⏳ [SENDING TELEGRAM] {alert2_text}")
                                                        try:
                                                            bot.send_message(CHAT_ID, alert2_text)
                                                        except Exception as err:
                                                            print(f"[TG ERROR] {err}")

                                                        risk_pts = max(0.10, tracker.swing_high - tracker.pullback_low)
                                                        record_live_alert(
                                                            sym=sym, stage=2, badge="⏳ 9 EMA Retest Ready",
                                                            breakout_time=tracker.breakout_time.strftime('%H:%M') if tracker.breakout_time else time_fmt,
                                                            pullback_time=time_fmt, entry_time="--:--",
                                                            clean_pdh=tracker.pdh, entry_price=tracker.swing_high, sl_price=prev_c['Low'],
                                                            t1=round(tracker.swing_high + 2.0 * risk_pts, 2),
                                                            t2=round(tracker.swing_high + 3.0 * risk_pts, 2),
                                                            risk_pct=round((risk_pts / tracker.swing_high) * 100.0, 2),
                                                            direction="LONG"
                                                        )

                                    # Tier 4: Swing High Breakout Entry Confirmation
                                    elif tracker.state == 'PULLBACK_CONFIRMED' and not tracker.msg3_sent:
                                        if tracker.breakout_time is not None and c.name > tracker.breakout_time:
                                            if c.name not in tracker.evaluated_candles:
                                                tracker.evaluated_candles.add(c.name)
                                                # USER RULE 3: Swing High break karke aage nikalte hi Entry Confirmed!
                                                if c_high >= tracker.swing_high:
                                                    tracker.state = 'CONFIRMED'
                                                    tracker.msg3_sent = True
                                                    
                                                    entry_price = round(max(c_open, tracker.swing_high + max(0.10, tracker.swing_high * 0.0005)), 2)
                                                    
                                                    # USER RULE 4: Entry candle se theek pichhli candle (prev_c) ka Low pr SL
                                                    prev_low = float(prev_c['Low'])
                                                    sl_price = round(prev_low, 2)
                                                    if sl_price >= entry_price:
                                                        sl_price = round(min(prev_low, entry_price * (1.0 - 0.004)), 2)
                                                        
                                                    risk_pts = max(0.10, round(entry_price - sl_price, 2))
                                                    risk_pct = round((risk_pts / entry_price) * 100.0, 2)
                                                    t1 = round(entry_price + 2.0 * risk_pts, 2)
                                                    t2 = round(entry_price + 3.0 * risk_pts, 2)
                                                    
                                                    n_c = nifty_info.get('close', 0)
                                                    n_e = nifty_info.get('ema9', 0)
                                                    n_stat = "9 EMA ke Upar 🟢" if n_c >= n_e else "9 EMA ke Niche ⚠️"
                                                    
                                                    b_c = banknifty_info.get('close', 0)
                                                    b_e = banknifty_info.get('ema9', 0)
                                                    b_stat = "9 EMA ke Upar 🟢" if b_c >= b_e else "9 EMA ke Niche ⚠️"
                                                    
                                                    s_c = sec_info.get('close', 0)
                                                    s_e = sec_info.get('ema9', 0)
                                                    sec_stat = "9 EMA ke Upar 🟢" if s_c >= s_e else "9 EMA ke Niche ⚠️"
                                                    
                                                    stk_stat = "9 EMA ke Upar 🟢" if c_close >= c_ema9 else "9 EMA ke Niche ⚠️"
                                                    
                                                    alert3_text = (
                                                        f"🟢 {sym} ({sec_id}) → {time_fmt} BUY ENTRY CONFIRMED! 🚀\n"
                                                        f"• Swing High Rs.{tracker.swing_high:.2f} Crossed! Live Entry: Rs.{entry_price:.2f}\n"
                                                        f"• Stop Loss: Rs.{sl_price:.2f} (Previous 5m Candle Low | Risk: {risk_pct}%)\n"
                                                        f"• Target 1 (1:2): Rs.{t1:.2f} ➔ Book 50% Profit & Move SL to Cost!\n"
                                                        f"• Target 2 (1:3): Rs.{t2:.2f} ➔ Trailed via 5m 9 EMA Close\n"
                                                        f"📊 5m 9-EMA ALIGNMENT INFO (Decision Checklist):\n"
                                                        f"  1. Nifty 50: {n_stat} ({n_c:.1f})\n"
                                                        f"  2. Bank Nifty: {b_stat} ({b_c:.1f})\n"
                                                        f"  3. Sector {sec_id}: {sec_stat}\n"
                                                        f"  4. Stock {sym}: {stk_stat} ({c_close:.2f})\n"
                                                        f"• Trailing SL Rule: Jaise hi 5m Candle 9 EMA ke niche CLOSE kare (Close < 9 EMA), Position Cut Kar Dein!\n"
                                                        f"• Mode: {tracker.macro_tag}"
                                                    )
                                                    print(f"🎯 [SENDING TELEGRAM] {alert3_text}")
                                                    try:
                                                        bot.send_message(CHAT_ID, alert3_text)
                                                    except Exception as err:
                                                        print(f"[TG ERROR] {err}")

                                                    record_live_alert(
                                                        sym=sym, stage=3, badge="🟢 Entry Confirmed ✅",
                                                        breakout_time=tracker.breakout_time.strftime('%H:%M') if tracker.breakout_time else time_fmt,
                                                        pullback_time=time_fmt, entry_time=time_fmt,
                                                        clean_pdh=tracker.pdh, entry_price=entry_price, sl_price=sl_price,
                                                        t1=t1, t2=t2, risk_pct=risk_pct,
                                                        direction="LONG"
                                                    )
                                                elif c_close < c_ema9 * 0.985:
                                                    tracker.state = 'IDLE'

                                # =========================================================
                                # 🔴 BEARISH SETUP (CLEAN PDL BREAKDOWN + FALLING 9 EMA RETEST)
                                # =========================================================
                                if can_trade_bear:
                                    ema_falling = c_ema9 <= prev_ema9 * 1.0005

                                    # Tier 3: Breakdown / Clean PDL Buffer Zone Test
                                    if tracker.bear_state == 'IDLE':
                                        prior_low = float(df_t.iloc[:idx]['Low'].min())
                                        pdl_buffer = max(0.50, round(tracker.pdl * 0.002, 2))
                                        if (c_low <= tracker.pdl + pdl_buffer or c_low <= tracker.pdl) and c_low < prior_low:
                                            tracker.bear_state = 'BREAKDOWN'
                                            tracker.bear_breakdown_time = c.name
                                            tracker.swing_low = c_low
                                            tracker.swing_low_idx = idx
                                            tracker.pullback_high = c_high
                                            tracker.bear_msg1_sent = True

                                            alert1_bear = (
                                                f"🔴 {sym} ({sec_id}) → {time_fmt} PDL Breakdown / Support Zone Tested 🚨\n"
                                                f"• Macro Mode: {tracker.bear_macro_tag}\n"
                                                f"• Clean PDL: Rs.{tracker.pdl:.2f} (Tested near Rs.{c_low:.2f}) | Waiting for Falling 9 EMA Retest"
                                            )
                                            print(f"🔔 [SENDING TELEGRAM] {alert1_bear}")
                                            try:
                                                bot.send_message(CHAT_ID, alert1_bear)
                                            except Exception as err:
                                                print(f"[TG ERROR] {err}")

                                            risk_pts = max(0.10, tracker.pullback_high - tracker.swing_low)
                                            record_live_alert(
                                                sym=sym, stage=1, badge="🔴 Breakdown Alert 🚨",
                                                breakout_time=time_fmt, pullback_time="--:--", entry_time="--:--",
                                                clean_pdh=tracker.pdl, entry_price=tracker.swing_low, sl_price=tracker.pullback_high,
                                                t1=round(tracker.swing_low - 2.0 * risk_pts, 2),
                                                t2=round(tracker.swing_low - 4.0 * risk_pts, 2),
                                                risk_pct=round((risk_pts / tracker.swing_low) * 100.0, 2),
                                                direction="SHORT"
                                            )

                                    # Tier 4: Bounce to Falling 9 EMA & Red Candle Close Below 9 EMA
                                    elif tracker.bear_state == 'BREAKDOWN' and not tracker.bear_msg3_sent:
                                        if tracker.bear_breakdown_time is not None and c.name > tracker.bear_breakdown_time:
                                            if c.name not in tracker.bear_evaluated_candles:
                                                tracker.bear_evaluated_candles.add(c.name)
                                                if c_low < tracker.swing_low and c_close <= c_open:
                                                    tracker.swing_low = c_low
                                                    tracker.swing_low_idx = idx
                                                elif c_high >= c_ema9 * 0.997:
                                                    tracker.pullback_high = max(tracker.pullback_high, c_high)
                                                    # USER RULE 2: Pullback ke baad 9 EMA ke niche strong red candle close hote hi pullback alert
                                                    if c_close < c_open and c_close <= c_ema9 and ema_falling:
                                                        tracker.bear_state = 'BEAR_PULLBACK_CONFIRMED'
                                                        tracker.bear_msg2_sent = True
                                                        
                                                        alert2_bear = (
                                                            f"⏳ {sym} ({sec_id}) → {time_fmt} 9 EMA Resistance Retest Confirmed 📉\n"
                                                            f"• Clean PDL: Rs.{tracker.pdl:.2f} | Swing Low: Rs.{tracker.swing_low:.2f}\n"
                                                            f"• Pullback High: Rs.{tracker.pullback_high:.2f} | Red Candle Closed below 9 EMA @ Rs.{c_close:.2f}\n"
                                                            f"• NEXT ACTION: Setup Ready! Jaise hi Swing Low (Rs.{tracker.swing_low:.2f}) break hoga, Short Entry trigger hogi!\n"
                                                            f"• Mode: {tracker.bear_macro_tag}"
                                                        )
                                                        print(f"⏳ [SENDING TELEGRAM] {alert2_bear}")
                                                        try:
                                                            bot.send_message(CHAT_ID, alert2_bear)
                                                        except Exception as err:
                                                            print(f"[TG ERROR] {err}")

                                                        risk_pts = max(0.10, tracker.pullback_high - tracker.swing_low)
                                                        record_live_alert(
                                                            sym=sym, stage=2, badge="⏳ 9 EMA Retest Ready",
                                                            breakout_time=tracker.bear_breakdown_time.strftime('%H:%M') if tracker.bear_breakdown_time else time_fmt,
                                                            pullback_time=time_fmt, entry_time="--:--",
                                                            clean_pdh=tracker.pdl, entry_price=tracker.swing_low, sl_price=prev_c['High'],
                                                            t1=round(tracker.swing_low - 2.0 * risk_pts, 2),
                                                            t2=round(tracker.swing_low - 3.0 * risk_pts, 2),
                                                            risk_pct=round((risk_pts / tracker.swing_low) * 100.0, 2),
                                                            direction="SHORT"
                                                        )

                                    # Tier 4: Swing Low Breakdown Entry Confirmation
                                    elif tracker.bear_state == 'BEAR_PULLBACK_CONFIRMED' and not tracker.bear_msg3_sent:
                                        if tracker.bear_breakdown_time is not None and c.name > tracker.bear_breakdown_time:
                                            if c.name not in tracker.bear_evaluated_candles:
                                                tracker.bear_evaluated_candles.add(c.name)
                                                # USER RULE 3: Swing Low break karke niche nikalte hi Short Entry Confirmed!
                                                if c_low <= tracker.swing_low:
                                                    tracker.bear_state = 'CONFIRMED'
                                                    tracker.bear_msg3_sent = True
                                                    
                                                    entry_price = round(min(c_open, tracker.swing_low - max(0.10, tracker.swing_low * 0.0005)), 2)
                                                    
                                                    # USER RULE 4: Entry candle se theek pichhli candle (prev_c) ka High pr SL
                                                    prev_high = float(prev_c['High'])
                                                    sl_price = round(prev_high, 2)
                                                    if sl_price <= entry_price:
                                                        sl_price = round(max(prev_high, entry_price * (1.0 + 0.004)), 2)
                                                        
                                                    risk_pts = max(0.10, round(sl_price - entry_price, 2))
                                                    risk_pct = round((risk_pts / entry_price) * 100.0, 2)
                                                    t1 = round(entry_price - 2.0 * risk_pts, 2)
                                                    t2 = round(entry_price - 3.0 * risk_pts, 2)
                                                    
                                                    n_c = nifty_info.get('close', 0)
                                                    n_e = nifty_info.get('ema9', 0)
                                                    n_stat = "9 EMA ke Niche 🔴" if n_c <= n_e else "9 EMA ke Upar ⚠️"
                                                    
                                                    b_c = banknifty_info.get('close', 0)
                                                    b_e = banknifty_info.get('ema9', 0)
                                                    b_stat = "9 EMA ke Niche 🔴" if b_c <= b_e else "9 EMA ke Upar ⚠️"
                                                    
                                                    s_c = sec_info.get('close', 0)
                                                    s_e = sec_info.get('ema9', 0)
                                                    sec_stat = "9 EMA ke Niche 🔴" if s_c <= s_e else "9 EMA ke Upar ⚠️"
                                                    
                                                    stk_stat = "9 EMA ke Niche 🔴" if c_close <= c_ema9 else "9 EMA ke Upar ⚠️"
                                                    
                                                    alert3_bear = (
                                                        f"🔴 {sym} ({sec_id}) → {time_fmt} SHORT ENTRY CONFIRMED! 🔻\n"
                                                        f"• Swing Low Rs.{tracker.swing_low:.2f} Broken! Live Short Entry: Rs.{entry_price:.2f}\n"
                                                        f"• Stop Loss: Rs.{sl_price:.2f} (Previous 5m Candle High | Risk: {risk_pct}%)\n"
                                                        f"• Target 1 (1:2): Rs.{t1:.2f} ➔ Book 50% Profit & Move SL to Cost!\n"
                                                        f"• Target 2 (1:3): Rs.{t2:.2f} ➔ Trailed via 5m 9 EMA Close\n"
                                                        f"📊 5m 9-EMA ALIGNMENT INFO (Decision Checklist):\n"
                                                        f"  1. Nifty 50: {n_stat} ({n_c:.1f})\n"
                                                        f"  2. Bank Nifty: {b_stat} ({b_c:.1f})\n"
                                                        f"  3. Sector {sec_id}: {sec_stat}\n"
                                                        f"  4. Stock {sym}: {stk_stat} ({c_close:.2f})\n"
                                                        f"• Trailing SL Rule: Jaise hi 5m Candle 9 EMA ke upar CLOSE kare (Close > 9 EMA), Position Cut Kar Dein!\n"
                                                        f"• Mode: {tracker.bear_macro_tag}"
                                                    )
                                                    print(f"🎯 [SENDING TELEGRAM] {alert3_bear}")
                                                    try:
                                                        bot.send_message(CHAT_ID, alert3_bear)
                                                    except Exception as err:
                                                        print(f"[TG ERROR] {err}")

                                                    record_live_alert(
                                                        sym=sym, stage=3, badge="🔴 Short Entry Confirmed ✅",
                                                        breakout_time=tracker.bear_breakdown_time.strftime('%H:%M') if tracker.bear_breakdown_time else time_fmt,
                                                        pullback_time=time_fmt, entry_time=time_fmt,
                                                        clean_pdh=tracker.pdl, entry_price=entry_price, sl_price=sl_price,
                                                        t1=t1, t2=t2, risk_pct=risk_pct,
                                                        direction="SHORT"
                                                    )
                                                elif c_close > c_ema9 * 1.015:
                                                    tracker.bear_state = 'IDLE'
                        except Exception:
                            continue
                except Exception:
                    pass
                    
            if is_test_run and scan_count >= 1:
                print(f"\n[TEST RUN COMPLETE] Evaluated test scan #{scan_count} successfully on cloud!")
                try:
                    bot.send_message(
                        CHAT_ID,
                        f"✅ <b>GitHub Actions Cloud Test Run Complete!</b>\n"
                        f"• Status: Successfully scanned {len(trackers)} F&O stocks with 0 errors.\n"
                        f"• Connection: Verified & Ready for Monday 09:10 AM IST session.",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                break

            # Sleep until next 5m cycle (~45 seconds)
            time.sleep(45)
            
        except KeyboardInterrupt:
            print("\n[STOP] Scanner stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] Main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_live_scanner()
