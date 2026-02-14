#!/usr/bin/env python3
"""
THETA WEDNESDAY (ThetaW) - Paper Trading Bot
PUT Credit Spread Strategy for BANK NIFTY (0DTE, every Wednesday)

BACKTEST RESULTS (94 Wednesdays, 2y):
  Best sweet spot: OTM=0.75%, Spread=100pts, MinCredit=15%
  → WinRate: 92.9% | ProfitFactor: 32.79 | MaxDD: ₹5,621 (5 lots)
  → Trades: 85/94 Wednesdays (almost never skips)

Parameters tested vs ThetaT (NIFTY Thursday):
  - Bank Nifty lot size = 15 (vs 50 for NIFTY)
  - Strike gap = 100pts (vs 50 for NIFTY)
  - Slightly higher IV → more credit per spread

This is PAPER TRADING only — no real orders placed.
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime, time, timedelta
import pytz

sys.path.insert(0, '/home/ubuntu/trading/camarilla-options')
sys.path.insert(0, '/home/ubuntu/trading')

# ── Config ──────────────────────────────────────────────────────────────────
STATE_FILE = '/home/ubuntu/trading/camarilla-options/thetaw_state.json'
LOG_FILE   = '/home/ubuntu/trading/logs/theta_wednesday.log'
IST        = pytz.timezone('Asia/Kolkata')

# ── Strategy parameters (tuned from backtest) ────────────────────────────────
SPREAD_WIDTH    = 100      # 100pt spread (Bank Nifty strike gap)
OTM_PCT         = 0.75     # 0.75% below spot  ← sweet spot from backtest
MIN_CREDIT_PCT  = 15       # Min 15% of spread as credit
ENTRY_START     = time(10, 0)
ENTRY_END       = time(14, 0)
EXIT_TIME       = time(15, 25)
LOTS            = 5        # Conservative start (can raise to 10)
LOT_SIZE        = 15       # Bank Nifty lot size
STRIKE_STEP     = 100      # Bank Nifty strike intervals
INITIAL_CAPITAL = 500000


def log(message):
    timestamp = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {message}"
    print(line)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def send_telegram(message):
    try:
        from telegram_helper import send_telegram_message
        send_telegram_message(message)
        log("Telegram sent")
    except Exception as e:
        log(f"Telegram error: {e}")


def load_state():
    default = {'capital': INITIAL_CAPITAL, 'position': None,
               'trades': [], 'last_run_date': None}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception as e:
        log(f"State load error: {e}")
    return default


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log(f"State save error: {e}")


def is_wednesday():
    return datetime.now(IST).weekday() == 2


def is_market_hours():
    t = datetime.now(IST).time()
    return time(9, 15) <= t <= time(15, 30)


def get_spot_price():
    """Get current Bank Nifty spot price"""
    try:
        import yfinance as yf
        bnf = yf.Ticker('^NSEBANK')
        data = bnf.history(period='1d', interval='1m')
        if len(data) > 0:
            return float(data['Close'].iloc[-1])
    except Exception as e:
        log(f"yfinance error: {e}")
    return None


def estimate_premium(spot, strike, hours_to_expiry):
    """
    Estimate Bank Nifty 0DTE put premium.
    ~1.4x higher base_pct than NIFTY reflecting BNF's higher implied vol.
    """
    distance_pct = abs(spot - strike) / spot * 100
    time_factor  = min(hours_to_expiry / 5.5, 1.0)

    if   distance_pct < 0.2:  base_pct = 0.65
    elif distance_pct < 0.4:  base_pct = 0.50
    elif distance_pct < 0.6:  base_pct = 0.38
    elif distance_pct < 0.8:  base_pct = 0.27
    elif distance_pct < 1.0:  base_pct = 0.18
    elif distance_pct < 1.25: base_pct = 0.12
    elif distance_pct < 1.5:  base_pct = 0.08
    elif distance_pct < 2.0:  base_pct = 0.05
    elif distance_pct < 2.5:  base_pct = 0.03
    else:                      base_pct = 0.015

    return max(spot * base_pct / 100 * time_factor, 5.0)


def calculate_spread_pnl(short_k, long_k, credit, exit_spot):
    if exit_spot >= short_k:
        return credit
    elif exit_spot <= long_k:
        return credit - (short_k - long_k)
    else:
        return credit - (short_k - exit_spot)


def check_entry(state, spot):
    now = datetime.now(IST)
    t = now.time()
    if t < ENTRY_START or t > ENTRY_END:
        return None

    short_k = round((spot * (1 - OTM_PCT / 100)) / STRIKE_STEP) * STRIKE_STEP
    long_k  = short_k - SPREAD_WIDTH

    close_mins   = 15 * 60 + 30
    current_mins = t.hour * 60 + t.minute
    hours_left   = (close_mins - current_mins) / 60

    short_prem = estimate_premium(spot, short_k, hours_left)
    long_prem  = estimate_premium(spot, long_k,  hours_left)
    net_credit = short_prem - long_prem
    credit_pct = (net_credit / SPREAD_WIDTH) * 100

    if credit_pct < MIN_CREDIT_PCT:
        log(f"Credit too low: {credit_pct:.1f}% < {MIN_CREDIT_PCT}% — no entry")
        return None

    return {
        'short_strike': short_k,
        'long_strike':  long_k,
        'credit':       net_credit,
        'credit_pct':   credit_pct,
        'entry_spot':   spot,
        'entry_time':   now.strftime('%H:%M:%S'),
        'lots':         LOTS,
    }


def check_exit(state, spot):
    if datetime.now(IST).time() >= EXIT_TIME:
        return True, "EOD Exit"
    return False, None


def run():
    now   = datetime.now(IST)
    today = now.strftime('%Y-%m-%d')
    log(f"=== ThetaW Run @ {now.strftime('%H:%M:%S')} ===")

    if not is_wednesday():
        log("Not Wednesday — skipping")
        return
    if not is_market_hours():
        log("Outside market hours")
        return

    state = load_state()

    if state.get('last_run_date') != today:
        if state.get('position'):
            log("Stale position from previous session — clearing")
        state['position'] = None
        state['last_run_date'] = today

    spot = get_spot_price()
    if not spot:
        log("Could not get Bank Nifty spot price")
        return

    log(f"Bank Nifty Spot: {spot:.2f}")
    position = state.get('position')

    # ── No position: check entry ──────────────────────────────────────────
    if not position:
        entry = check_entry(state, spot)
        if entry:
            state['position'] = entry
            save_state(state)

            msg = (
                f"\U0001f7e1 ThetaW PAPER ENTRY\n\n"
                f"\U0001f4ca Bank Nifty PUT Credit Spread\n"
                f"Short: {entry['short_strike']} | Long: {entry['long_strike']}\n"
                f"Spread: {SPREAD_WIDTH} pts\n\n"
                f"\U0001f4b0 Credit: \u20b9{entry['credit']:.2f} ({entry['credit_pct']:.1f}%)\n"
                f"\U0001f4c8 Spot: {spot:.2f}\n"
                f"\u23f0 Entry: {entry['entry_time']}\n"
                f"\U0001f4e6 Lots: {entry['lots']}\n\n"
                f"Max Profit: \u20b9{entry['credit'] * entry['lots'] * LOT_SIZE:,.0f}\n"
                f"Max Loss:   \u20b9{(SPREAD_WIDTH - entry['credit']) * entry['lots'] * LOT_SIZE:,.0f}"
            )
            log(f"ENTRY: Short {entry['short_strike']}, Long {entry['long_strike']}, Credit {entry['credit']:.2f}")
            send_telegram(msg)
        else:
            log("No entry signal")

    # ── Have position: check exit ──────────────────────────────────────────
    else:
        should_exit, reason = check_exit(state, spot)
        if should_exit:
            pnl_per_unit = calculate_spread_pnl(
                position['short_strike'], position['long_strike'],
                position['credit'], spot)
            total_pnl = pnl_per_unit * position['lots'] * LOT_SIZE

            if pnl_per_unit > 0:
                result = "\u2705 WIN"
            elif pnl_per_unit < -position['credit']:
                result = "\u274c MAX LOSS"
            else:
                result = "\u26a0\ufe0f PARTIAL LOSS"

            state['capital'] += total_pnl

            trade = {
                'date':         today,
                'entry_time':   position['entry_time'],
                'exit_time':    now.strftime('%H:%M:%S'),
                'entry_spot':   position['entry_spot'],
                'exit_spot':    spot,
                'short_strike': position['short_strike'],
                'long_strike':  position['long_strike'],
                'credit':       position['credit'],
                'pnl':          total_pnl,
                'result':       result,
            }
            state['trades'].append(trade)
            state['position'] = None
            save_state(state)

            trades     = state['trades']
            n          = len(trades)
            win_rate   = sum(1 for t in trades if t['pnl'] > 0) / n * 100
            total_all  = sum(t['pnl'] for t in trades)

            msg = (
                f"\U0001f7e1 ThetaW PAPER EXIT — {result}\n\n"
                f"\U0001f4ca Short: {position['short_strike']} | Long: {position['long_strike']}\n"
                f"\U0001f4c8 Entry Spot: {position['entry_spot']:.2f}\n"
                f"\U0001f4c9 Exit Spot: {spot:.2f}\n"
                f"\u23f0 Exit: {now.strftime('%H:%M:%S')}\n\n"
                f"\U0001f4b0 Trade P&L: \u20b9{total_pnl:+,.0f}\n"
                f"\U0001f4e6 Lots: {position['lots']}\n\n"
                f"\U0001f4ca Session Stats:\n"
                f"Capital: \u20b9{state['capital']:,.0f}\n"
                f"Trades: {n} | Win Rate: {win_rate:.0f}%\n"
                f"Total P&L: \u20b9{total_all:+,.0f}"
            )
            log(f"EXIT: {reason} | P&L: {total_pnl:+,.0f}")
            send_telegram(msg)

        else:
            pnl_per_unit = calculate_spread_pnl(
                position['short_strike'], position['long_strike'],
                position['credit'], spot)
            unrealized = pnl_per_unit * position['lots'] * LOT_SIZE
            log(f"Position: Short {position['short_strike']} | Spot {spot:.2f} | Unrealized: {unrealized:+,.0f}")

    save_state(state)


def send_tuesday_reminder():
    """Send Tuesday evening reminder that ThetaW runs tomorrow (Wednesday)"""
    msg = (
        "\U0001f3e6 ThetaW REMINDER\n\n"
        "Theta Wednesday runs TOMORROW (Wednesday)!\n\n"
        "\U0001f4ca Strategy: Bank Nifty 0DTE PUT credit spread\n"
        f"\U0001f4e6 Lots: {LOTS} | Spread Width: {SPREAD_WIDTH} pts\n"
        f"\U0001f4cd OTM: {OTM_PCT}% below spot\n"
        "\u23f0 Entry window: 10:00 AM - 2:00 PM IST\n"
        "\U0001f6aa Auto-exit: 3:25 PM IST\n"
        "\U0001f535 Mode: PAPER only\n\n"
        "\U0001f4ca Backtest (2y): 92.9% win rate | PF 32.79"
    )
    send_telegram(msg)
    log("Tuesday ThetaW reminder sent")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--tuesday-reminder":
        send_tuesday_reminder()
    else:
        run()
