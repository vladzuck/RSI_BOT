"""
Manual trade tester — run from your Mac.
Usage:
  python3 manual_trade.py buy 0.001
  python3 manual_trade.py sell 0.001
  python3 manual_trade.py balance
"""

import requests, time, hmac, hashlib, json, sys

KEY = "p2cZ7IzInbWjIdGIMC7pV5C7tMf2IAd53XU01R2sLBdJ5b4fzTZdSXxFzKBmrNpL"
SEC = "1auQZH6CyibwRT1qoW5QX0m9aTPdUyMchcnfH5mYEkwc4l9jalQTbFwdrcCe3do4"
URL = "https://mock-api.roostoo.com"
PAIR = "BTC/USD"

def sign(params=""):
    ts = str(int(time.time() * 1000))
    p = f"timestamp={ts}" if not params else f"{params}&timestamp={ts}"
    s = hmac.new(SEC.encode(), p.encode(), hashlib.sha256).hexdigest()
    return {"RST-API-KEY": KEY, "MSG-SIGNATURE": s}, p

def balance():
    h, p = sign()
    r = requests.get(f"{URL}/v3/balance", headers=h, params=dict(x.split("=") for x in p.split("&")))
    data = r.json()
    print(json.dumps(data, indent=2))

def trade(side, quantity):
    ts = str(int(time.time() * 1000))
    params = f"pair={PAIR}&quantity={quantity}&side={side.upper()}&timestamp={ts}&type=MARKET"
    sig = hmac.new(SEC.encode(), params.encode(), hashlib.sha256).hexdigest()
    h = {"RST-API-KEY": KEY, "MSG-SIGNATURE": sig, "Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(f"{URL}/v3/place_order", headers=h, data=params)
    data = r.json()
    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 manual_trade.py balance")
        print("  python3 manual_trade.py buy 0.001")
        print("  python3 manual_trade.py sell 0.001")
        sys.exit()

    cmd = sys.argv[1].lower()

    if cmd == "balance":
        balance()
    elif cmd in ("buy", "sell"):
        qty = sys.argv[2] if len(sys.argv) > 2 else "0.001"
        trade(cmd, qty)
    else:
        print(f"Unknown command: {cmd}")