#!/usr/bin/env python3
"""
THETA THURSDAY (ThetaT) - Paper Trading Bot
Pure 0DTE Credit Spread Strategy for NIFTY

STRATEGY (from article):
1. Only trade on Thursdays (NIFTY weekly expiry = 0DTE)
2. Wait 30-45 mins after open (trade after 10:00 AM)
3. Sell put ~1% below current price
4. Buy further OTM put to cap risk (50 point spread)
5. Need minimum 15% of spread width as credit
6. Exit at EOD (let theta work)

This is a PAPER TRADING bot - no real orders placed.
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime, time, timedelta
import pytz

# Add paths for imports
sys.path.insert(0, '/home/ubuntu/trading/camarilla-options')
sys.path.insert(0, '/home/ubuntu/trading')

# Configuration
STATE_FILE = '/home/ubuntu/trading/camarilla-options/thetat_state.json'
LOG_FILE = '/home/ubuntu/trading/logs/theta_thursday.log'
IST = pytz.timezone('Asia/Kolkata')

# Strategy parameters
SPREAD_WIDTH = 50       # 50 point spread
OTM_PCT = 1.0          # 1% below spot
MIN_CREDIT_PCT = 15    # Minimum 15% of spread as credit
ENTRY_START = time(10, 0)   # 10:00 AM
ENTRY_END = time(14, 0)     # 2:00 PM
EXIT_TIME = time(15, 25)    # 3:25 PM (5 min before close)
LOTS = 10              # Paper trading lots
LOT_SIZE = 50          # NIFTY lot size
INITIAL_CAPITAL = 500000


def log(message):
    """Log with timestamp"""
    timestamp = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{timestamp}] {message}"
    print(log_msg)

    # Append to log file
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(log_msg + '\n')


def send_telegram(message):
    """Send telegram notification"""
    try:
        from telegram_helper import send_telegram_message
        send_telegram_message(message)
        log("Telegram sent")
    except Exception as e:
        log(f"Telegram error: {e}")


def load_state():
    """Load persisted state"""
    default_state = {
        'capital': INITIAL_CAPITAL,
        'position': None,
        'trades': [],
        'last_run_date': None
    }

    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        log(f"Error loading state: {e}")

    return default_state


def save_state(state):
    """Save state to file"""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log(f"Error saving state: {e}")


def is_thursday():
    """Check if today is Thursday"""
    now = datetime.now(IST)
    return now.weekday() == 3


def is_market_hours():
    """Check if within market hours"""
    now = datetime.now(IST)
    current_time = now.time()
    return time(9, 15) <= current_time <= time(15, 30)


def get_spot_price():
    """Get current NIFTY spot price"""
    try:
        # Try AngelOne first
        from angelone_api import get_ltp
        ltp = get_ltp('NSE', 'NIFTY 50', '26000')
        if ltp and ltp > 0:
            return ltp
    except Exception as e:
        log(f"AngelOne error: {e}")

    # Fallback to yfinance
    try:
        import yfinance as yf
        nifty = yf.Ticker('^NSEI')
        data = nifty.history(period='1d', interval='1m')
        if len(data) > 0:
            return float(data['Close'].iloc[-1])
    except Exception as e:
        log(f"yfinance error: {e}")

    return None


def estimate_premium(spot, strike, hours_to_expiry):
    """
    Estimate 0DTE option premium based on OTM distance
    Based on real NIFTY 0DTE market observations
    """
    distance_pct = abs(spot - strike) / spot * 100
    time_factor = min(hours_to_expiry / 5.5, 1.0)

    # Premium curve for NIFTY 0DTE
    if distance_pct < 0.2:
        base_pct = 0.45
    elif distance_pct < 0.4:
        base_pct = 0.35
    elif distance_pct < 0.6:
        base_pct = 0.25
    elif distance_pct < 0.8:
        base_pct = 0.18
    elif distance_pct < 1.0:
        base_pct = 0.12
    elif distance_pct < 1.2:
        base_pct = 0.08
    elif distance_pct < 1.5:
        base_pct = 0.05
    elif distance_pct < 2.0:
        base_pct = 0.03
    else:
        base_pct = 0.015

    premium = spot * base_pct / 100 * time_factor
    return max(premium, 3)  # Minimum Rs 3


def calculate_spread_pnl(short_strike, long_strike, credit, exit_spot):
    """Calculate PUT credit spread P&L at exit"""
    spread_width = short_strike - long_strike

    if exit_spot >= short_strike:
        # Both OTM - keep full credit
        return credit
    elif exit_spot <= long_strike:
        # Both ITM - max loss
        return credit - spread_width
    else:
        # Short put ITM only
        intrinsic_loss = short_strike - exit_spot
        return credit - intrinsic_loss


def check_entry(state, spot):
    """Check for entry opportunity"""
    now = datetime.now(IST)
    current_time = now.time()

    # Only enter during allowed window
    if current_time < ENTRY_START or current_time > ENTRY_END:
        return None

    # Calculate strikes
    short_strike = round((spot * (1 - OTM_PCT/100)) / 50) * 50
    long_strike = short_strike - SPREAD_WIDTH

    # Calculate hours to expiry
    current_mins = current_time.hour * 60 + current_time.minute
    close_mins = 15 * 60 + 30
    hours_left = (close_mins - current_mins) / 60

    # Estimate premiums
    short_premium = estimate_premium(spot, short_strike, hours_left)
    long_premium = estimate_premium(spot, long_strike, hours_left)
    net_credit = short_premium - long_premium

    # Check minimum credit requirement
    credit_pct = (net_credit / SPREAD_WIDTH) * 100

    if credit_pct < MIN_CREDIT_PCT:
        log(f"Credit too low: {credit_pct:.1f}% < {MIN_CREDIT_PCT}%")
        return None

    return {
        'short_strike': short_strike,
        'long_strike': long_strike,
        'credit': net_credit,
        'credit_pct': credit_pct,
        'entry_spot': spot,
        'entry_time': now.strftime('%H:%M:%S'),
        'lots': LOTS
    }


def check_exit(state, spot):
    """Check for exit conditions"""
    now = datetime.now(IST)
    current_time = now.time()

    # Exit near market close
    if current_time >= EXIT_TIME:
        return True, "EOD Exit"

    return False, None


def run():
    """Main run function - called by cron"""
    now = datetime.now(IST)
    today = now.strftime('%Y-%m-%d')

    log(f"=== ThetaT Run @ {now.strftime('%H:%M:%S')} ===")

    # Only run on Thursdays
    if not is_thursday():
        log("Not Thursday - skipping")
        return

    # Only during market hours
    if not is_market_hours():
        log("Outside market hours")
        return

    # Load state
    state = load_state()

    # Reset position if new day
    if state.get('last_run_date') != today:
        if state.get('position'):
            log("Stale position from previous day - clearing")
        state['position'] = None
        state['last_run_date'] = today

    # Get spot price
    spot = get_spot_price()
    if not spot:
        log("Could not get spot price")
        return

    log(f"Spot: {spot:.2f}")

    position = state.get('position')

    # === NO POSITION - Check for entry ===
    if not position:
        entry = check_entry(state, spot)

        if entry:
            state['position'] = entry
            save_state(state)

            msg = f"""🟡 ThetaT PAPER ENTRY

📊 PUT Credit Spread
Short: {entry['short_strike']} | Long: {entry['long_strike']}
Spread: {SPREAD_WIDTH} pts

💰 Credit: ₹{entry['credit']:.2f} ({entry['credit_pct']:.1f}%)
📈 Spot: {spot:.2f}
⏰ Entry: {entry['entry_time']}
📦 Lots: {entry['lots']}

Max Profit: ₹{entry['credit'] * entry['lots'] * LOT_SIZE:,.0f}
Max Loss: ₹{(SPREAD_WIDTH - entry['credit']) * entry['lots'] * LOT_SIZE:,.0f}"""

            log(f"ENTRY: Short {entry['short_strike']}, Long {entry['long_strike']}, Credit {entry['credit']:.2f}")
            send_telegram(msg)
        else:
            log("No entry signal")

    # === HAVE POSITION - Check for exit ===
    else:
        should_exit, reason = check_exit(state, spot)

        if should_exit:
            # Calculate P&L
            pnl_per_lot = calculate_spread_pnl(
                position['short_strike'],
                position['long_strike'],
                position['credit'],
                spot
            )
            total_pnl = pnl_per_lot * position['lots'] * LOT_SIZE

            # Determine result
            if pnl_per_lot > 0:
                result = "✅ WIN"
            elif pnl_per_lot < -position['credit']:
                result = "❌ MAX LOSS"
            else:
                result = "⚠️ PARTIAL LOSS"

            # Update capital
            state['capital'] += total_pnl

            # Record trade
            trade = {
                'date': today,
                'entry_time': position['entry_time'],
                'exit_time': now.strftime('%H:%M:%S'),
                'entry_spot': position['entry_spot'],
                'exit_spot': spot,
                'short_strike': position['short_strike'],
                'long_strike': position['long_strike'],
                'credit': position['credit'],
                'pnl': total_pnl,
                'result': result
            }
            state['trades'].append(trade)
            state['position'] = None
            save_state(state)

            # Calculate stats
            trades = state['trades']
            total_trades = len(trades)
            winners = [t for t in trades if t['pnl'] > 0]
            win_rate = (len(winners) / total_trades * 100) if total_trades > 0 else 0
            total_pnl_all = sum(t['pnl'] for t in trades)

            msg = f"""🟡 ThetaT PAPER EXIT - {result}

📊 Short: {position['short_strike']} | Long: {position['long_strike']}
📈 Entry Spot: {position['entry_spot']:.2f}
📉 Exit Spot: {spot:.2f}
⏰ Exit: {now.strftime('%H:%M:%S')}

💰 Trade P&L: ₹{total_pnl:+,.0f}
📦 Lots: {position['lots']}

📊 Session Stats:
Capital: ₹{state['capital']:,.0f}
Trades: {total_trades} | Win Rate: {win_rate:.0f}%
Total P&L: ₹{total_pnl_all:+,.0f}"""

            log(f"EXIT: {reason} | P&L: {total_pnl:+,.0f}")
            send_telegram(msg)
        else:
            # Monitor position
            pnl_per_lot = calculate_spread_pnl(
                position['short_strike'],
                position['long_strike'],
                position['credit'],
                spot
            )
            unrealized = pnl_per_lot * position['lots'] * LOT_SIZE

            log(f"Position: Short {position['short_strike']} | Spot {spot:.2f} | Unrealized: {unrealized:+,.0f}")

    save_state(state)



def send_wednesday_reminder():
    """Send Wednesday evening reminder that ThetaT runs tomorrow"""
    msg = (
        "\u26a1 ThetaT REMINDER\n\n"
        "Theta Thursday runs TOMORROW (Thursday)!\n\n"
        "\U0001f4ca Strategy: NIFTY 0DTE PUT credit spread\n"
        "\U0001f4e6 Lots: 10 | Spread Width: 50 pts\n"
        "\u23f0 Entry window: 10:00 AM - 2:00 PM IST\n"
        "\U0001f6aa Auto-exit: 3:25 PM IST\n"
        "\U0001f535 Mode: PAPER only"
    )
    send_telegram(msg)
    log("Wednesday ThetaT reminder sent")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--wednesday-reminder":
        send_wednesday_reminder()
    else:
        run()

