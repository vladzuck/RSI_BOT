# BB + RSI Mean Reversion Backtest (with fees)
import pandas as pd
import numpy as np
import ta

# ==================== SETTINGS ====================
CSV_PATH = "btc5m.csv"
INITIAL_CAPITAL = 1000000
TAKER_FEE_RATE = 0.001          # 0.1% per side

# Strategy Parameters
BB_WINDOW = 20
BB_DEVIATION = 2.0
RSI_PERIOD = 7
RSI_THRESHOLD = 20
TAKE_PROFIT_PCT = 0.04
STOP_LOSS_PCT = 0.04
POSITION_SIZE_PCT = 0.95

# Backtest Dates
START_DATE = "2026-03-01"
END_DATE = "2026-03-23"

# ==================== FUNCTIONS ====================

def load_data():
    print(f"Loading {CSV_PATH}...")
    df = pd.read_csv(CSV_PATH, parse_dates=['open_time'])
    df.set_index('open_time', inplace=True)

    bb = ta.volatility.BollingerBands(close=df['close'], window=BB_WINDOW, window_dev=BB_DEVIATION)
    df['BB_lower'] = bb.bollinger_lband()
    df['BB_mid'] = bb.bollinger_mavg()
    df['RSI'] = ta.momentum.rsi(df['close'], window=RSI_PERIOD)

    df['Buy_Signal'] = (df['close'] < df['BB_lower']) & (df['RSI'] < RSI_THRESHOLD)
    df['Buy_Signal_shifted'] = df['Buy_Signal'].shift(1)

    df = df[(df.index >= START_DATE) & (df.index <= END_DATE)].copy()
    df = df.dropna()

    print(f"Bars: {len(df):,}")
    print(f"Date range: {df.index.min()} to {df.index.max()}")
    return df


def run_backtest(df):
    cash = INITIAL_CAPITAL
    in_pos = False
    entry_price = 0
    units = 0
    entry_idx = 0
    cost_basis = 0          # Track actual USD spent (including entry fee)
    trades = []
    equity_curve = []

    for i in range(len(df)):
        row = df.iloc[i]
        px = row['open']

        if not in_pos:
            if row['Buy_Signal_shifted']:
                in_pos = True
                entry_price = px
                entry_idx = i
                alloc = cash * POSITION_SIZE_PCT
                entry_fee = alloc * TAKER_FEE_RATE
                units = (alloc - entry_fee) / px
                cash -= alloc
                cost_basis = alloc    # Total spent including fee

        if in_pos and i > entry_idx:
            tp_price = entry_price * (1 + TAKE_PROFIT_PCT)
            sl_price = entry_price * (1 - STOP_LOSS_PCT)

            if px >= tp_price:
                gross = units * px
                exit_fee = gross * TAKER_FEE_RATE
                cash += gross - exit_fee
                pnl = (gross - exit_fee) - cost_basis
                pnl_pct = (px - entry_price) / entry_price * 100
                trades.append({
                    'entry_time': df.index[entry_idx],
                    'exit_time': df.index[i],
                    'entry_price': entry_price,
                    'exit_price': px,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'bars_held': i - entry_idx,
                    'reason': 'TAKE_PROFIT',
                    'won': pnl > 0
                })
                in_pos = False

            elif px <= sl_price:
                gross = units * px
                exit_fee = gross * TAKER_FEE_RATE
                cash += gross - exit_fee
                pnl = (gross - exit_fee) - cost_basis
                pnl_pct = (px - entry_price) / entry_price * 100
                trades.append({
                    'entry_time': df.index[entry_idx],
                    'exit_time': df.index[i],
                    'entry_price': entry_price,
                    'exit_price': px,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'bars_held': i - entry_idx,
                    'reason': 'STOP_LOSS',
                    'won': pnl > 0
                })
                in_pos = False

        # Equity: cash + market value of BTC held
        if in_pos:
            equity_curve.append(cash + units * row['close'])
        else:
            equity_curve.append(cash)

    # Close any open position at end
    if in_pos:
        final_price = df['close'].iloc[-1]
        gross = units * final_price
        exit_fee = gross * TAKER_FEE_RATE
        cash += gross - exit_fee

    return trades, equity_curve, cash


def print_results(df, trades, equity_curve, final_cash):
    total_trades = len(trades)
    wins = len([t for t in trades if t['won']])
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    tp_trades = len([t for t in trades if t['reason'] == 'TAKE_PROFIT'])
    sl_trades = len([t for t in trades if t['reason'] == 'STOP_LOSS'])

    total_pnl = sum(t['pnl'] for t in trades)
    total_return = (final_cash / INITIAL_CAPITAL - 1) * 100

    avg_win = np.mean([t['pnl_pct'] for t in trades if t['won']]) if wins > 0 else 0
    avg_loss = np.mean([t['pnl_pct'] for t in trades if not t['won']]) if losses > 0 else 0
    avg_bars = np.mean([t['bars_held'] for t in trades]) if trades else 0

    btc_start = df['open'].iloc[0]
    btc_end = df['close'].iloc[-1]
    btc_return = (btc_end / btc_start - 1) * 100

    days = max(1, (df.index[-1] - df.index[0]).days)
    trades_per_day = total_trades / days

    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    drawdown = (peak - eq) / peak * 100
    max_dd = np.max(drawdown)

    print("\n" + "=" * 55)
    print("  BACKTEST RESULTS")
    print("=" * 55)
    print(f"  Period:          {START_DATE} to {END_DATE} ({days} days)")
    print(f"  Fee:             {TAKER_FEE_RATE*100:.1f}% per side")
    print()
    print(f"  PARAMETERS:")
    print(f"    BB({BB_WINDOW}, {BB_DEVIATION}) + RSI({RSI_PERIOD}) < {RSI_THRESHOLD}")
    print(f"    Take Profit:   +{TAKE_PROFIT_PCT*100:.1f}%")
    print(f"    Stop Loss:     -{STOP_LOSS_PCT*100:.1f}%")
    print(f"    Position Size: {POSITION_SIZE_PCT*100:.0f}%")
    print()
    print(f"  PERFORMANCE:")
    print(f"    Initial:       ${INITIAL_CAPITAL:,.2f}")
    print(f"    Final:         ${final_cash:,.2f}")
    print(f"    Net P&L:       ${total_pnl:+,.2f}")
    print(f"    Return:        {total_return:+.2f}%")
    print(f"    BTC Return:    {btc_return:+.2f}%")
    print(f"    vs BTC:        {total_return - btc_return:+.2f}%")
    print(f"    Max Drawdown:  {max_dd:.2f}%")
    print()
    print(f"  TRADES:")
    print(f"    Total:         {total_trades}")
    print(f"    Per Day:       {trades_per_day:.1f}")
    print(f"    Wins:          {wins} ({win_rate:.1f}%)")
    print(f"    Losses:        {losses}")
    print(f"    Take Profits:  {tp_trades}")
    print(f"    Stop Losses:   {sl_trades}")
    print(f"    Avg Win:       {avg_win:+.2f}%")
    print(f"    Avg Loss:      {avg_loss:+.2f}%")
    print(f"    Avg Hold:      {avg_bars:.0f} bars ({avg_bars*5/60:.1f} hours)")
    print("=" * 55)

    if trades:
        print("\n  TRADE LOG:")
        print(f"  {'#':>3} | {'Entry':>11} | {'Exit':>11} | {'P&L':>9} | {'%':>7} | {'Bars':>5} | Reason")
        print("  " + "-" * 65)
        for i, t in enumerate(trades, 1):
            print(f"  {i:>3} | ${t['entry_price']:>10,.2f} | ${t['exit_price']:>10,.2f} | "
                  f"${t['pnl']:>+8,.2f} | {t['pnl_pct']:>+6.2f}% | {t['bars_held']:>5} | {t['reason']}")

    # Estimated trades for next 7 days
    print()
    print(f"  ESTIMATE FOR NEXT 7 DAYS:")
    print(f"    Based on {trades_per_day:.1f} trades/day: ~{trades_per_day * 7:.0f} trades expected")
    print(f"    At {win_rate:.0f}% win rate: ~{trades_per_day * 7 * win_rate/100:.0f} wins, ~{trades_per_day * 7 * (1-win_rate/100):.0f} losses")
    projected = INITIAL_CAPITAL * (1 + total_return/100) ** (7/days)
    print(f"    Projected return: ~{((1 + total_return/100) ** (7/days) - 1) * 100:+.1f}%")


# ==================== RUN ====================

if __name__ == "__main__":
    df = load_data()
    trades, equity_curve, final_cash = run_backtest(df)
    print_results(df, trades, equity_curve, final_cash)