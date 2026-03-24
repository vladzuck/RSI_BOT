# Fixed Parameter Backtest for Crypto Trading Strategy
import pandas as pd
import numpy as np
import ta
import os

# --- Fixed Parameters ---
INITIAL_CAPITAL = 1000000
EXECUTION_MODE = "next_open"         # options: next_open, next_close
BB_DEVIATION = 1.5                   # Fixed BB deviation
STOP_LOSS_PCT = 0.0272                 # Fixed stop loss: 2.00 (best fit according to backtesting)
TAKER_FEE_RATE = 0.001               # 0.1% per side
# -------------------------

def load_and_prep_data():
    print("Loading BTC data from CSV...")
    df = pd.read_csv("btc5m.csv", parse_dates=['open_time'])
    df.set_index('open_time', inplace=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'}, inplace=True)

    # Date range filter: 2026-02-01 through 2026-02-28
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    start_date = pd.Timestamp('2026-03-01 00:00:00', tz='UTC')
    end_date = pd.Timestamp('2026-03-28 23:59:59', tz='UTC')
    df = df[(df.index >= start_date) & (df.index <= end_date)].copy()

    # Pre-calculate market returns
    df['Market_Returns'] = df['close'].pct_change()
    return df


def audit_no_lookahead(df):
    checks = {
        "has_shifted_signal_columns": all(
            c in df.columns for c in ["BB_lower_sig", "BB_mid_sig", "RSI_7_sig", "Buy_Signal_sig"]
        ),
        "signal_time_before_execution_time": bool((df["signal_time"] < df.index).all()),
    }
    ok = all(checks.values())
    print("\nNo-lookahead scan:")
    for k, v in checks.items():
        print(f"- {k}: {'PASS' if v else 'FAIL'}")
    if not ok:
        raise ValueError("Look-ahead scan failed. Fix signal shifting before continuing.")


def run_backtest(base_df, bb_dev, stop_loss_pct):
    df = base_df.copy()

    # 1. Dynamic Volatility Channels (Bollinger Bands)
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=bb_dev)
    df['BB_lower'] = indicator_bb.bollinger_lband()
    df['BB_mid'] = indicator_bb.bollinger_mavg()

    # 2. Fast Momentum Filter
    df['RSI_7'] = ta.momentum.rsi(df['close'], window=7)

    # Signal on bar t (raw, not yet shifted)
    df['Buy_Signal'] = (df['close'] < df['BB_lower']) & (df['RSI_7'] < 30)

    # Shift all signal features by 1 bar: signal at t, execution at t+1
    df['BB_lower_sig'] = df['BB_lower'].shift(1)
    df['BB_mid_sig'] = df['BB_mid'].shift(1)
    df['RSI_7_sig'] = df['RSI_7'].shift(1)
    df['Buy_Signal_sig'] = df['Buy_Signal'].shift(1)
    df['signal_time'] = pd.Series(df.index, index=df.index).shift(1)

    df = df.dropna().copy()
    #audit_no_lookahead(df)

    positions = np.zeros(len(df))
    in_position = False

    buy_signals = df['Buy_Signal_sig'].values
    close_prices = df['close'].values
    open_prices = df['open'].values
    bb_mid_prices = df['BB_mid_sig'].values

    trade_returns = []
    entry_price = 0
    hold = np.zeros(len(df))

    if EXECUTION_MODE not in {"next_open", "next_close"}:
        raise ValueError("EXECUTION_MODE must be 'next_open' or 'next_close'.")

    exec_prices = open_prices if EXECUTION_MODE == "next_open" else close_prices

    for i in range(len(df)):
        px = exec_prices[i]
        if not in_position and buy_signals[i]:
            positions[i] = 1
            in_position = True
            entry_price = px

        elif in_position:
            # EXIT LOGIC: Hit the mean (Take Profit) OR hit the Stop Loss
            if px >= bb_mid_prices[i]:  # TP at shifted mean
                positions[i] = -1
                in_position = False
                raw_return = (px - entry_price) / entry_price
                trade_returns.append(raw_return - 2 * TAKER_FEE_RATE)

            elif px <= entry_price * (1 - stop_loss_pct):  # Hard Stop-Loss
                positions[i] = -1
                in_position = False
                raw_return = (px - entry_price) / entry_price
                trade_returns.append(raw_return - 2 * TAKER_FEE_RATE)

        if in_position:
            hold[i] = 1

    df['Position'] = positions
    df['Holding'] = hold

    # Calculate Returns
    # Use executed prices directly to avoid mismatch between signal and execution paths.
    df['Exec_Price'] = exec_prices
    df['Exec_Returns'] = df['Exec_Price'].pct_change()
    # Deduct fees on entry/exit bars
    fee_cost = abs(df['Position']) * TAKER_FEE_RATE
    df['Strategy_Returns'] = df['Holding'].shift(1) * df['Exec_Returns'] - fee_cost
    df_clean = df.dropna(subset=['Strategy_Returns'])

    if len(df_clean) == 0:
        return 0.0, 0, 0.0, 0.0, INITIAL_CAPITAL, 0.0

    cumulative = (1 + df_clean['Strategy_Returns']).cumprod()
    total_return_pct = cumulative.iloc[-1] - 1

    # Calculate Detailed Metrics
    final_equity = INITIAL_CAPITAL * (1 + total_return_pct)
    net_profit = final_equity - INITIAL_CAPITAL

    total_trades = len(trade_returns)
    winning_trades = sum(1 for r in trade_returns if r > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    mean_return = df_clean['Strategy_Returns'].mean()
    std_return = df_clean['Strategy_Returns'].std()
    sharpe_ratio = (mean_return / std_return * np.sqrt(105120)) if std_return > 0 else 0.0

    return total_return_pct * 100, total_trades, win_rate, net_profit, final_equity, sharpe_ratio


def run_fixed_backtest():
    df = load_and_prep_data()

    print(f"\nRunning backtest on full period: {len(df):,} bars")
    print(f"Date range: {df.index.min()} to {df.index.max()}")

    ret, trades, win_rate, net_profit, final_equity, sharpe = run_backtest(df, BB_DEVIATION, STOP_LOSS_PCT)

    print("\n" + "="*50)
    print("BACKTEST RESULT WITH FIXED PARAMETERS")
    print("="*50)
    print(f"Execution Mode:    {EXECUTION_MODE}")
    print(f"BB Deviation:      {BB_DEVIATION}")
    print(f"Stop Loss:         {STOP_LOSS_PCT*100:.2f}%")
    print(f"Sharpe Ratio:      {sharpe:.2f}")
    print(f"Total Trades:      {trades}")
    print(f"Win Rate:          {win_rate:.2f}%")
    print(f"Net Profit:        ${net_profit:,.2f}")
    print(f"Final Equity:      ${final_equity:,.2f}")
    print(f"Total Return:      {ret:.2f}%")
    print("="*50)


if __name__ == "__main__":
    run_fixed_backtest()
