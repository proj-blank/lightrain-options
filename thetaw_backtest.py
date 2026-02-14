#!/usr/bin/env python3
"""
ThetaW Backtest - Bank Nifty Wednesday PUT Credit Spread
Grid search over OTM%, spread width, min credit%, entry hour, lots
Uses historical Bank Nifty daily OHLC: Open = entry proxy, Close = exit proxy
"""

import yfinance as yf
import sys
from datetime import date
from itertools import product

# --- Parameter grid ---
OTM_PCT_RANGE      = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
SPREAD_WIDTH_RANGE = [100, 150, 200]
MIN_CREDIT_RANGE   = [10, 15, 20]
LOTS_RANGE         = [5, 10]

LOT_SIZE       = 15
INITIAL_CAPITAL = 500000
STRIKE_STEP     = 100   # Bank Nifty strike gap

HISTORY_YEARS = 2


def estimate_premium_bnf(spot, strike, hours_to_expiry):
    """
    Estimate Bank Nifty 0DTE put premium.
    Slightly higher base_pct than NIFTY (~1.4x) reflecting higher IV.
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


def spread_pnl(short_k, long_k, credit, exit_spot):
    if exit_spot >= short_k:
        return credit
    elif exit_spot <= long_k:
        return credit - (short_k - long_k)
    else:
        return credit - (short_k - exit_spot)


def run_backtest():
    print(f"Downloading Bank Nifty data ({HISTORY_YEARS}y daily)...")
    df = yf.download("^NSEBANK", period=f"{HISTORY_YEARS}y", interval="1d",
                     auto_adjust=True, progress=False)

    if df.empty:
        print("ERROR: No data downloaded")
        sys.exit(1)

    # Filter Wednesdays (weekday == 2)
    df = df[df.index.weekday == 2].copy()

    # Flatten MultiIndex columns if present
    if isinstance(df.columns, __import__('pandas').MultiIndex):
        df.columns = df.columns.get_level_values(0)

    wednesdays = []
    for idx, row in df.iterrows():
        o = float(row['Open'])
        c = float(row['Close'])
        if o > 0 and c > 0:
            wednesdays.append({'date': idx.date(), 'entry_spot': o, 'exit_spot': c})

    print(f"Found {len(wednesdays)} Wednesdays with data\n")

    # Entry at ~10:30 AM means ~5h to 3:30 close
    # Open proxy = just after open; use 4.5h as conservative estimate
    ENTRY_HOURS_TO_EXPIRY = 4.5

    results = []
    total_combos = len(OTM_PCT_RANGE) * len(SPREAD_WIDTH_RANGE) * len(MIN_CREDIT_RANGE) * len(LOTS_RANGE)
    print(f"Testing {total_combos} parameter combinations...")

    for otm_pct, spread_width, min_credit_pct, lots in product(
            OTM_PCT_RANGE, SPREAD_WIDTH_RANGE, MIN_CREDIT_RANGE, LOTS_RANGE):

        trades = []
        capital = INITIAL_CAPITAL
        peak_capital = capital
        max_drawdown = 0
        skipped = 0

        for w in wednesdays:
            entry_spot = w['entry_spot']
            exit_spot  = w['exit_spot']

            short_k = round((entry_spot * (1 - otm_pct / 100)) / STRIKE_STEP) * STRIKE_STEP
            long_k  = short_k - spread_width

            short_prem = estimate_premium_bnf(entry_spot, short_k, ENTRY_HOURS_TO_EXPIRY)
            long_prem  = estimate_premium_bnf(entry_spot, long_k,  ENTRY_HOURS_TO_EXPIRY)
            net_credit = short_prem - long_prem

            credit_pct = (net_credit / spread_width) * 100
            if credit_pct < min_credit_pct:
                skipped += 1
                continue

            pnl_unit = spread_pnl(short_k, long_k, net_credit, exit_spot)
            total_pnl = pnl_unit * lots * LOT_SIZE

            capital += total_pnl
            if capital > peak_capital:
                peak_capital = capital
            dd = peak_capital - capital
            if dd > max_drawdown:
                max_drawdown = dd

            trades.append({
                'win': total_pnl > 0,
                'pnl': total_pnl,
                'credit_pct': credit_pct,
            })

        if len(trades) < 5:
            continue

        n = len(trades)
        win_rate  = sum(1 for t in trades if t['win']) / n * 100
        total_pnl = sum(t['pnl'] for t in trades)
        avg_pnl   = total_pnl / n
        avg_credit = sum(t['credit_pct'] for t in trades) / n
        pf = total_pnl / max_drawdown if max_drawdown > 0 else 99.0

        results.append({
            'otm_pct':        otm_pct,
            'spread_width':   spread_width,
            'min_credit_pct': min_credit_pct,
            'lots':           lots,
            'trades':         n,
            'skipped':        skipped,
            'win_rate':       win_rate,
            'avg_credit_pct': avg_credit,
            'avg_pnl':        avg_pnl,
            'total_pnl':      total_pnl,
            'max_drawdown':   max_drawdown,
            'profit_factor':  pf,
        })

    if not results:
        print("No results generated")
        return

    HDR = f"{'OTM%':>6} {'Sprd':>5} {'MinCr':>6} {'Lots':>5} {'Trades':>7} {'Skip':>5} {'Win%':>6} {'AvgCr%':>7} {'AvgPnL':>9} {'TotalPnL':>11} {'MaxDD':>10} {'PF':>6}"
    SEP = "-" * len(HDR)

    def row(r):
        return (f"{r['otm_pct']:>6.2f} {r['spread_width']:>5.0f} {r['min_credit_pct']:>6.0f}"
                f" {r['lots']:>5.0f} {r['trades']:>7} {r['skipped']:>5}"
                f" {r['win_rate']:>5.1f}% {r['avg_credit_pct']:>6.1f}%"
                f" {r['avg_pnl']:>+9,.0f} {r['total_pnl']:>+11,.0f}"
                f" {r['max_drawdown']:>10,.0f} {r['profit_factor']:>6.2f}")

    # ── By total P&L ──
    by_pnl = sorted(results, key=lambda x: x['total_pnl'], reverse=True)
    print(f"\n{'='*len(HDR)}")
    print(f"ThetaW BACKTEST — Bank Nifty Wednesday PUT Credit Spread ({HISTORY_YEARS}y)")
    print(f"{'='*len(HDR)}")
    print(f"\nTOP 20 BY TOTAL P&L:")
    print(HDR); print(SEP)
    for r in by_pnl[:20]:
        print(row(r))

    # ── By win rate (min 10 trades) ──
    by_wr = sorted([r for r in results if r['trades'] >= 10],
                   key=lambda x: x['win_rate'], reverse=True)
    print(f"\nTOP 15 BY WIN RATE (min 10 trades):")
    print(HDR); print(SEP)
    for r in by_wr[:15]:
        print(row(r))

    # ── By profit factor (min 10 trades, PF <= 50 to exclude edge cases) ──
    by_pf = sorted([r for r in results if r['trades'] >= 10 and r['profit_factor'] <= 50],
                   key=lambda x: x['profit_factor'], reverse=True)
    print(f"\nTOP 15 BY PROFIT FACTOR:")
    print(HDR); print(SEP)
    for r in by_pf[:15]:
        print(row(r))

    print(f"\n{'='*len(HDR)}")
    print(f"Total combinations tested: {len(results)}")
    print(f"Best config: OTM={by_pnl[0]['otm_pct']}%  Spread={by_pnl[0]['spread_width']}pts"
          f"  MinCredit={by_pnl[0]['min_credit_pct']}%  Lots={by_pnl[0]['lots']}"
          f"  WinRate={by_pnl[0]['win_rate']:.1f}%  TotalPnL=₹{by_pnl[0]['total_pnl']:+,.0f}")


if __name__ == "__main__":
    run_backtest()
