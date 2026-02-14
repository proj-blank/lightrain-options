# lightrain-options — Theta Decay Options Strategies

**Status:** Paper trading (no real orders placed)  
**Exchange:** NSE India (via AngelOne API — not yet integrated in these scripts)  
**Analysis request:** Review both strategies and suggest improvements to entry logic, strike selection, and risk management.

---

## Strategies

### ThetaT — Theta Thursday (NIFTY, 0DTE)
**File:** `theta_thursday_paper.py`  
**Runs:** Every Thursday (NIFTY weekly expiry day), every 3 minutes 9AM–3PM via cron

**Core Idea:** Sell a put credit spread on NIFTY on expiry day (0DTE = zero days to expiration). Theta decay is fastest on expiry day, so credit sellers have an edge if the market doesn't crash.

**Parameters (currently hardcoded):**
| Parameter | Value | Notes |
|-----------|-------|-------|
| Underlying | NIFTY | Weekly expiry = Thursday |
| Lot size | 50 | NSE standard |
| Lots traded | 10 | Paper only |
| Short strike | ~1% below spot | `OTM_PCT = 1.0` |
| Spread width | 50 points | Buy put 50pts below short strike |
| Min credit | 15% of spread | e.g. ≥₹7.5 for 50pt spread |
| Entry window | 10:00 AM – 2:00 PM | Wait 45min after open |
| Exit | 3:25 PM | Hard EOD exit |
| Capital | ₹5,00,000 (paper) | |

**Entry Logic:**
1. After 10:00 AM, check if NIFTY spot is available
2. Calculate short strike = round(spot × 0.99 / 50) × 50
3. Buy strike = short strike − 50
4. Fetch option premiums via AngelOne API
5. If (short premium − buy premium) / spread_width ≥ 15% → enter
6. One entry per day only

**Exit Logic:**
- Hard exit at 3:25 PM regardless of P&L
- Logs realized P&L based on premium at exit vs entry

**No stop-loss currently.** If NIFTY drops >1% intraday, the spread goes deep ITM and losses can approach full spread width (₹2,500 per lot × 10 lots = ₹25,000 max loss).

---

### ThetaW — Theta Wednesday (BANK NIFTY, 0DTE)
**File:** `theta_wednesday_paper.py`  
**Runs:** Every Wednesday (Bank Nifty weekly expiry day), every 3 minutes 9AM–3PM via cron

**Core Idea:** Same theta decay play as ThetaT but on Bank Nifty. Bank Nifty has higher IV → more credit per spread. Backtest shows this performs better than ThetaT.

**Parameters (tuned from backtest of 94 Wednesdays):**
| Parameter | Value | Notes |
|-----------|-------|-------|
| Underlying | BANK NIFTY | Weekly expiry = Wednesday |
| Lot size | 15 | NSE standard |
| Lots traded | 5 | Conservative start |
| Short strike | ~0.75% below spot | `OTM_PCT = 0.75` ← sweet spot |
| Spread width | 100 points | Bank Nifty strike step = 100 |
| Min credit | 15% of spread | e.g. ≥₹15 for 100pt spread |
| Entry window | 10:00 AM – 2:00 PM | |
| Exit | 3:25 PM | |
| Capital | ₹5,00,000 (paper) | |

**Backtest Results (94 Wednesdays, 2 years):**
- Win rate: **92.9%**
- Profit factor: **32.79**
- Max drawdown: ₹5,621 (at 5 lots)
- Trades: 85/94 Wednesdays (skips when credit too low)

**Same entry/exit logic as ThetaT**, just different underlying and parameters.

---

## Shared Dependency
**`telegram_helper.py`** — Sends Telegram alerts for entries, exits, P&L updates. Uses the same bot token as the main trading system.

**`thetaw_backtest.py`** — Backtest script used to tune ThetaW parameters. Shows parameter sweep results across OTM%, spread width, and credit thresholds.

---

## State Files
**`thetat_state.json`** — Persists daily state for ThetaT (entry price, position, P&L) so the script can resume if restarted mid-day.

---

## Infrastructure
- Both scripts poll every 3 minutes via cron
- AngelOne API used to fetch live option premiums (no orders placed)
- Hardcoded absolute paths to `/home/ubuntu/trading/` — needs cleanup if deploying elsewhere
- No database integration — state is JSON file only
- No stop-loss — **this is the biggest risk**

---

## Questions for Analysis

1. **Stop-loss:** Should we add a stop-loss? What % of spread width is typical for 0DTE credit spreads? The common approaches are: (a) 2× credit received, (b) 50% of spread width, (c) delta-based (exit if short delta > 0.30).

2. **Strike selection:** Is 1% OTM for NIFTY (ThetaT) optimal? The backtest for ThetaW showed 0.75% was better. Should ThetaT also move to 0.75%?

3. **Entry timing:** Currently enters any time between 10AM–2PM on the first valid opportunity. Would a later entry (e.g., after 11AM) improve win rate since IV crush is faster post-open?

4. **Multiple entries:** The strategy enters once and holds. Would rolling the spread intraday (re-entering after exit if conditions are still good) improve performance?

5. **Transition to LIVE:** What safeguards are needed before switching from paper to live orders via AngelOne?
