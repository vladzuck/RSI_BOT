"""
Quick account checker — run anytime from your Mac to see your status.
Usage: python3 check_account.py
"""

import requests, time, hmac, hashlib, json

KEY = "p2cZ7IzInbWjIdGIMC7pV5C7tMf2IAd53XU01R2sLBdJ5b4fzTZdSXxFzKBmrNpL"
SEC = "1auQZH6CyibwRT1qoW5QX0m9aTPdUyMchcnfH5mYEkwc4l9jalQTbFwdrcCe3do4"
URL = "https://mock-api.roostoo.com"

def sign(params=""):
    ts = str(int(time.time() * 1000))
    p = f"timestamp={ts}" if not params else f"{params}&timestamp={ts}"
    s = hmac.new(SEC.encode(), p.encode(), hashlib.sha256).hexdigest()
    return {"RST-API-KEY": KEY, "MSG-SIGNATURE": s}, p

# ── Balance ──
print("=" * 50)
print("  WALLET BALANCE")
print("=" * 50)
h, p = sign()
bal = requests.get(f"{URL}/v3/balance", headers=h, params=dict(x.split("=") for x in p.split("&"))).json()
total_usd_value = 0

# Get BTC price for value calculation
ts = str(int(time.time() * 1000))
ticker = requests.get(f"{URL}/v3/ticker", params={"timestamp": ts, "pair": "BTC/USD"}).json()
btc_price = ticker.get("Data", {}).get("BTC/USD", {}).get("LastPrice", 0)

for coin, amounts in bal.get("SpotWallet", {}).items():
    free = amounts.get("Free", 0)
    lock = amounts.get("Lock", 0)
    total = free + lock
    if total > 0:
        if coin == "USD":
            total_usd_value += total
            print(f"  {coin}: ${free:,.2f} free | ${lock:,.2f} locked | Total: ${total:,.2f}")
        else:
            value = total * btc_price if coin == "BTC" else 0
            total_usd_value += value
            print(f"  {coin}: {free:.6f} free | {lock:.6f} locked | Value: ${value:,.2f}")

print(f"\n  BTC Price: ${btc_price:,.2f}")
print(f"  Total Equity: ${total_usd_value:,.2f}")

# ── Pending Orders ──
print("\n" + "=" * 50)
print("  PENDING ORDERS")
print("=" * 50)
h, p = sign()
pending = requests.get(f"{URL}/v3/pending_count", headers=h, params=dict(x.split("=") for x in p.split("&"))).json()
total_pending = pending.get("TotalPending", 0)
if total_pending > 0:
    print(f"  Total: {total_pending}")
    for pair, count in pending.get("OrderPairs", {}).items():
        print(f"    {pair}: {count}")
else:
    print("  None")

# ── Recent Trades ──
print("\n" + "=" * 50)
print("  RECENT TRADES (last 20)")
print("=" * 50)
h, p = sign("limit=20")
h["Content-Type"] = "application/x-www-form-urlencoded"
orders = requests.post(f"{URL}/v3/query_order", headers=h, data=p).json()

if orders.get("Success") and orders.get("OrderMatched"):
    wins = 0
    losses = 0
    total_pnl = 0
    print(f"  {'#':>3} | {'Pair':<10} | {'Side':<5} | {'Status':<9} | {'Price':>11} | {'Qty':>10}")
    print("  " + "-" * 60)
    for i, o in enumerate(orders["OrderMatched"], 1):
        print(f"  {i:>3} | {o['Pair']:<10} | {o['Side']:<5} | {o['Status']:<9} | "
              f"${o.get('FilledAverPrice', o['Price']):>10,.2f} | {o['Quantity']:>10}")
else:
    print("  No trades found")

print("\n" + "=" * 50)