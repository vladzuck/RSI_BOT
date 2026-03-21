"""
Account checker — run from your Mac anytime.
Usage: python3 check_account.py
"""

import requests, time, hmac, hashlib, json
from datetime import datetime, timezone

KEY = "p2cZ7IzInbWjIdGIMC7pV5C7tMf2IAd53XU01R2sLBdJ5b4fzTZdSXxFzKBmrNpL"
SEC = "1auQZH6CyibwRT1qoW5QX0m9aTPdUyMchcnfH5mYEkwc4l9jalQTbFwdrcCe3do4"
URL = "https://mock-api.roostoo.com"

def sign(params=""):
    ts = str(int(time.time() * 1000))
    p = f"timestamp={ts}" if not params else f"{params}&timestamp={ts}"
    s = hmac.new(SEC.encode(), p.encode(), hashlib.sha256).hexdigest()
    return {"RST-API-KEY": KEY, "MSG-SIGNATURE": s}, p

# ── Server Check ──
print("=" * 55)
print("  ACCOUNT STATUS CHECK")
print("=" * 55)
try:
    st = requests.get(f"{URL}/v3/serverTime", timeout=5).json()
    print(f"  Server:       ONLINE")
except:
    print(f"  Server:       OFFLINE")
    exit()

# ── Market Data ──
ts = str(int(time.time() * 1000))
ticker = requests.get(f"{URL}/v3/ticker", params={"timestamp": ts, "pair": "BTC/USD"}, timeout=5).json()
btc = ticker.get("Data", {}).get("BTC/USD", {})
btc_price = btc.get("LastPrice", 0)
btc_change = btc.get("Change", 0)
btc_bid = btc.get("MaxBid", 0)
btc_ask = btc.get("MinAsk", 0)

print(f"  BTC Price:    ${btc_price:,.2f}")
print(f"  24h Change:   {btc_change * 100:+.2f}%")
print(f"  Bid/Ask:      ${btc_bid:,.2f} / ${btc_ask:,.2f}")

# ── Balance ──
print("\n" + "=" * 55)
print("  WALLET")
print("=" * 55)
h, p = sign()
bal = requests.get(f"{URL}/v3/balance", headers=h, params=dict(x.split("=") for x in p.split("&")), timeout=5).json()
wallet = bal.get("SpotWallet", bal.get("Wallet", {}))
total_equity = 0
has_btc = False

for coin, amounts in wallet.items():
    free = amounts.get("Free", 0)
    lock = amounts.get("Lock", 0)
    total = free + lock
    if total > 0:
        if coin == "USD":
            total_equity += total
            print(f"  USD:          ${free:,.2f} free | ${lock:,.2f} locked")
        else:
            value = total * btc_price if coin == "BTC" else 0
            total_equity += value
            if coin == "BTC":
                has_btc = total > 0.000001
            print(f"  {coin}:          {free:.6f} free | {lock:.6f} locked (${value:,.2f})")

print(f"  ─────────────────────────────────")
print(f"  Total Equity: ${total_equity:,.2f}")

# ── Position ──
print("\n" + "=" * 55)
print("  POSITION")
print("=" * 55)
if has_btc:
    btc_total = wallet.get("BTC", {}).get("Free", 0) + wallet.get("BTC", {}).get("Lock", 0)
    print(f"  Status:       LONG {btc_total:.6f} BTC (${btc_total * btc_price:,.2f})")
else:
    print(f"  Status:       FLAT (no position)")

# ── Pending Orders ──
h, p = sign()
pending = requests.get(f"{URL}/v3/pending_count", headers=h, params=dict(x.split("=") for x in p.split("&")), timeout=5).json()
total_pending = pending.get("TotalPending", 0)
if total_pending > 0:
    print(f"  Pending:      {total_pending} orders")
else:
    print(f"  Pending:      None")

# ── Current Conditions ──
print("\n" + "=" * 55)
print("  STRATEGY CONDITIONS")
print("=" * 55)
utc_now = datetime.now(timezone.utc)
utc_hour = utc_now.hour
in_window = 0 <= utc_hour < 8

print(f"  UTC Time:     {utc_now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  Trade Window: {'OPEN  (00:00-08:00)' if in_window else 'CLOSED (need 00:00-08:00)'}")
print(f"  Entry Needs:  RSI(21) < 22 + window OPEN")
print(f"  Exit Needs:   RSI >= 40 OR price -3% OR 700 ticks")
print(f"")
if not in_window and not has_btc:
    print(f"  >> Window CLOSED. Bot scanning but won't enter until 00:00 UTC.")
elif in_window and not has_btc:
    print(f"  >> Window OPEN. Bot scanning for RSI < 22 dip.")
elif has_btc:
    print(f"  >> Bot is HOLDING. Watching for exit signals.")

# ── Recent Trades ──
print("\n" + "=" * 55)
print("  RECENT TRADES (last 10)")
print("=" * 55)
h, p = sign("limit=10")
h["Content-Type"] = "application/x-www-form-urlencoded"
orders = requests.post(f"{URL}/v3/query_order", headers=h, data=p, timeout=5).json()

if orders.get("Success") and orders.get("OrderMatched"):
    trades = orders["OrderMatched"]
    print(f"  {'#':>3} | {'Pair':<10} | {'Side':<5} | {'Status':<9} | {'Price':>11} | {'Qty':>10}")
    print("  " + "-" * 58)
    for i, o in enumerate(trades, 1):
        print(f"  {i:>3} | {o['Pair']:<10} | {o['Side']:<5} | {o['Status']:<9} | "
              f"${o.get('FilledAverPrice', o['Price']):>10,.2f} | {o['Quantity']:>10}")

    buys = len([o for o in trades if o["Side"] == "BUY"])
    sells = len([o for o in trades if o["Side"] == "SELL"])
    print(f"\n  Buys: {buys} | Sells: {sells}")
else:
    print("  No trades yet")

print("\n" + "=" * 55)
print("  Bot runs independently on EC2.")
print("=" * 55)

'''
tail -50 ~/RSI_BOT/rsi_bot.log
to check last logs
'''