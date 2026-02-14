#!/usr/bin/env python3
"""
THETA TUESDAY (ThetaT) - Paper Trading Bot
Pure 0DTE Credit Spread Strategy for NIFTY

STRATEGY:
1. Only trade on Tuesdays (NIFTY weekly expiry moved to Tuesday, Sept 2025)
2. Skip morning chop — enter after 11:00 AM
3. Sell put ~1% below current price
4. Buy further OTM put 50 points below (capped risk)
5. Need minimum 15% of spread width as credit
6. STOP LOSS: exit if cost-to-close reaches 2x credit received
7. Exit at EOD if not stopped out

NIFTY lot size updated to 75 (SEBI increased min contract size, Nov 2024)
Conservative sizing: 2 lots paper trading (5% capital risk at max loss)

This is a PAPER TRADING bot — no real orders placed.
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
STATE_FILE = '/home/ubuntu/trading/camarilla-options/thetat_state.json'
LOG_FILE   = '/home/ubuntu/trading/logs/theta_tuesday.log'
IST        = pytz.timezone('Asia/Kolkata')

# ── Strategy parameters ──────────────────────────────────────────────────────
SPREAD_WIDTH   = 50       # 50 point spread
OTM_PCT        = 1.0      # 1% below spot
MIN_CREDIT_PCT = 15       # Min 15% of spread as credit
ENTRY_START    = time(11, 0)   # 11:00 AM — skip morning chop
ENTRY_END      = time(14, 0)   # 2:00 PM
EXIT_TIME      = time(15, 25)  # 3:25 PM hard exit
LOTS           = 2         # Conservative: 2 lots paper (was 10)
LOT_SIZE       = 75        # NIFTY lot size (updated Nov 2024, was 50)
INITIAL_CAPITAL = 500000
STOP_LOSS_MULTIPLIER = 2.0  # Exit if cost-to-close >= 2x credit received


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
    default = {'capital': INITIAL_CAPITAL, 'position': None, 'trades': [], 'last_run_date': None}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        log(f"Error loading state: {e}")
    return default


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log(f"Error saving state: {e}")


def is_tuesday():
    """Check if today is Tuesday (NIFTY weekly expiry since Sept 2025)"""
    return datetime.now(IST).weekday() == 1  # Monday=0, Tuesday=1


def is_market_hours():
    now = datetime.now(IST).time()
    return time(9, 15) <= now <= time(15, 30)


def get_spot_price():
    """Get current NIFTY spot price — AngelOne first, yfinance fallback"""
    try:
        from angelone_api import get_ltp
        ltp = get_ltp('NSE', 'NIFTY 50', '26000')
        if ltp and ltp > 0:
            return ltp
    except Exception as e:
        log(f"AngelOne error: {e}")
    try:
        import yfinance as yf
        data = yf.Ticker('^NSEI').history(period='1d', interval='1m')
        if len(data) > 0:
            return float(data['Close'].iloc[-1])
    except Exception as e:
        log(f"yfinance error: {e}")
    return None


def estimate_premium(spot, strike, hours_to_expiry):
    """
    Estimate 0DTE put premium from OTM distance and time remaining.
    NOTE: This is a model — real AngelOne option chain data needed for live trading.
    """
    distance_pct = abs(spot - strike) / spot * 100
    time_factor = min(hours_to_expiry / 5.5, 1.0)

    if distance_pct < 0.2:   base_pct = 0.45
    elif distance_pct < 0.4: base_pct = 0.35
    elif distance_pct < 0.6: base_pct = 0.25
    elif distance_pct < 0.8: base_pct = 0.18
    elif distance_pct < 1.0: base_pct = 0.12
    elif distance_pct < 1.2: base_pct = 0.08
    elif distance_pct < 1.5: base_pct = 0.05
    elif distance_pct < 2.0: base_pct = 0.03
    else:                     base_pct = 0.015

    return max(spot * base_pct / 100 * time_factor, 3)


def get_hours_left(now=None):
    """Hours remaining until market close"""
    if now is None:
        now = datetime.now(IST)
    current_mins = now.hour * 60 + now.minute
    close_mins   = 15 * 60 + 30
    return max((close_mins - current_mins) / 60, 0.0001)


def calculate_spread_pnl(short_strike, long_strike, credit, exit_spot):
    """P&L for PUT credit spread at exit"""
    if exit_spot >= short_strike:
        return credit                            # Both OTM — keep full credit
    elif exit_spot <= long_strike:
        return credit - (short_strike - long_strike)  # Both ITM — max loss
    else:
        return credit - (short_strike - exit_spot)    # Short ITM only


def check_entry(state, spot, now):
    """Check for entry opportunity"""
    current_time = now.time()
    if current_time < ENTRY_START or current_time > ENTRY_END:
        return None

    short_strike = round((spot * (1 - OTM_PCT / 100)) / 50) * 50
    long_strike  = short_strike - SPREAD_WIDTH
    hours_left   = get_hours_left(now)

    short_premium = estimate_premium(spot, short_strike, hours_left)
    long_premium  = estimate_premium(spot, long_strike, hours_left)
    net_credit    = short_premium - long_premium
    credit_pct    = (net_credit / SPREAD_WIDTH) * 100

    if credit_pct < MIN_CREDIT_PCT:
        log(f"Credit too low: {credit_pct:.1f}% < {MIN_CREDIT_PCT}%")
        return None

    return {
        'short_strike': short_strike,
        'long_strike':  long_strike,
        'credit':       net_credit,
        'credit_pct':   credit_pct,
        'entry_spot':   spot,
        'entry_time':   now.strftime('%H:%M:%S'),
        'lots':         LOTS,
        'stop_trigger': net_credit * STOP_LOSS_MULTIPLIER,  # cost-to-close threshold
    }


def check_exit(position, spot, now):
    """
    Check for exit. Returns (should_exit: bool, reason: str).
    Priority: stop-loss > EOD.
    """
    current_time = now.time()

    # 1. Stop-loss: exit if cost-to-close >= 2x credit
    hours_left     = get_hours_left(now)
    cost_to_close  = (estimate_premium(spot, position['short_strike'], hours_left)
                      - estimate_premium(spot, position['long_strike'],  hours_left))
    stop_trigger   = position['stop_trigger']

    if cost_to_close >= stop_trigger:
        return True, f"STOP LOSS 2× (close={cost_to_close:.2f} >= {stop_trigger:.2f})"

    # 2. EOD hard exit
    if current_time >= EXIT_TIME:
        return True, "EOD Exit"

    return False, None


def run():
    now   = datetime.now(IST)
    today = now.strftime('%Y-%m-%d')
    log(f"=== ThetaT Run @ {now.strftime('%H:%M:%S')} ===")

    if not is_tuesday():
        log("Not Tuesday — skipping")
        return

    if not is_market_hours():
        log("Outside market hours")
        return

    state = load_state()

    if state.get('last_run_date') != today:
        if state.get('position'):
            log("Stale position from previous session — clearing")
        state['position']      = None
        state['last_run_date'] = today

    spot = get_spot_price()
    if not spot:
        log("Could not get spot price")
        return

    log(f"NIFTY Spot: {spot:.2f}")
    position = state.get('position')

    # ── No position: check entry ─────────────────────────────────────────────
    if not position:
        entry = check_entry(state, spot, now)
        if entry:
            state['position'] = entry
            save_state(state)

            max_profit = entry['credit'] * entry['lots'] * LOT_SIZE
            max_loss   = (SPREAD_WIDTH - entry['credit']) * entry['lots'] * LOT_SIZE
            stop_at    = entry['stop_trigger'] * entry['lots'] * LOT_SIZE - max_profit

            msg = (
                f"🟡 ThetaT PAPER ENTRY\n\n"
                f"📊 NIFTY PUT Credit Spread (0DTE Tuesday)\n"
                f"Short: {entry['short_strike']} | Long: {entry['long_strike']}\n"
                f"Spread: {SPREAD_WIDTH} pts | Lots: {entry['lots']} × {LOT_SIZE}\n\n"
                f"💰 Credit: ₹{entry['credit']:.2f} ({entry['credit_pct']:.1f}%)\n"
                f"📈 Spot: {spot:.2f} | ⏰ Entry: {entry['entry_time']}\n\n"
                f"✅ Max Profit: ₹{max_profit:,.0f}\n"
                f"🛑 Stop Loss (2×): ₹{stop_at:,.0f}\n"
                f"❌ Max Loss: ₹{max_loss:,.0f}"
            )
            log(f"ENTRY: Short {entry['short_strike']}, Long {entry['long_strike']}, Credit {entry['credit']:.2f}")
            send_telegram(msg)
        else:
            log("No entry signal")

    # ── Have position: check exit ────────────────────────────────────────────
    else:
        should_exit, reason = check_exit(position, spot, now)

        if should_exit:
            pnl_per_lot = calculate_spread_pnl(
                position['short_strike'], position['long_strike'],
                position['credit'], spot
            )
            total_pnl = pnl_per_lot * position['lots'] * LOT_SIZE

            if total_pnl > 0:
                result = "✅ WIN"
            elif pnl_per_lot < -position['credit']:
                result = "❌ MAX LOSS"
            else:
                result = "⚠️ PARTIAL LOSS"

            state['capital'] += total_pnl
            trade = {
                'date': today, 'exit_reason': reason,
                'entry_time': position['entry_time'], 'exit_time': now.strftime('%H:%M:%S'),
                'entry_spot': position['entry_spot'], 'exit_spot': spot,
                'short_strike': position['short_strike'], 'long_strike': position['long_strike'],
                'credit': position['credit'], 'pnl': total_pnl, 'result': result
            }
            state['trades'].append(trade)
            state['position'] = None
            save_state(state)

            trades    = state['trades']
            win_rate  = len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100 if trades else 0
            total_all = sum(t['pnl'] for t in trades)

            msg = (
                f"🟡 ThetaT PAPER EXIT — {result}\n\n"
                f"📊 Short: {position['short_strike']} | Long: {position['long_strike']}\n"
                f"📈 Entry: {position['entry_spot']:.2f} → Exit: {spot:.2f}\n"
                f"⏰ {position['entry_time']} → {now.strftime('%H:%M:%S')}\n"
                f"🔔 Reason: {reason}\n\n"
                f"💰 Trade P&L: ₹{total_pnl:+,.0f}\n\n"
                f"📊 Session: {len(trades)} trades | {win_rate:.0f}% win\n"
                f"Capital: ₹{state['capital']:,.0f} | Total P&L: ₹{total_all:+,.0f}"
            )
            log(f"EXIT: {reason} | P&L: {total_pnl:+,.0f}")
            send_telegram(msg)

        else:
            # Monitoring log
            hours_left    = get_hours_left(now)
            cost_to_close = (estimate_premium(spot, position['short_strike'], hours_left)
                             - estimate_premium(spot, position['long_strike'], hours_left))
            pnl_per_lot   = calculate_spread_pnl(
                position['short_strike'], position['long_strike'],
                position['credit'], spot
            )
            unrealized = pnl_per_lot * position['lots'] * LOT_SIZE
            log(f"Position: Short {position['short_strike']} | Spot {spot:.2f} | "
                f"Cost-to-close: {cost_to_close:.2f}/{position['stop_trigger']:.2f} | "
                f"Unrealized: {unrealized:+,.0f}")

    save_state(state)


def send_monday_reminder():
    """Monday evening reminder that ThetaT runs tomorrow (Tuesday)"""
    msg = (
        "⚡ ThetaT REMINDER\n\n"
        "Theta Tuesday runs TOMORROW!\n\n"
        "📊 Strategy: NIFTY 0DTE PUT credit spread\n"
        "📦 Lots: 2 × 75 = 150 units | Spread: 50 pts\n"
        "⏰ Entry window: 11:00 AM – 2:00 PM IST\n"
        "🛑 Stop loss: 2× credit | 🚪 Hard exit: 3:25 PM\n"
        "🔵 Mode: PAPER only"
    )
    send_telegram(msg)
    log("Monday ThetaT reminder sent")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--monday-reminder":
        send_monday_reminder()
    else:
        run()
