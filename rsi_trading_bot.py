"""
  FLATTEN + IDLE BOT
  1. Sells all BTC on startup
  2. Runs a "strategy" that never triggers (RSI < 1)
"""

import requests, time, hmac, hashlib, logging, math, functools
from datetime import datetime, timezone
from collections import deque

API_KEY = "pZZ9zVTcKdin9QtSpq4slzYtUDFDf1j1OSCh503YO2UyADi1uKl2y5zAyvFmKAkf"
SECRET_KEY = "l8Zb6ebZi2RZcXZE6XCJUg8qOdHgb3sStveWr7Nj96MS8MteMyWSWG5Cku570Qk2"
BASE_URL = "https://mock-api.roostoo.com"

PAIR = "BTC/USD"
COIN = "BTC"
QTY_DECIMALS = 2
POLL_INTERVAL = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
log = logging.getLogger("Bot")

def retry(max_attempts=3, delay=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_attempts:
                        log.warning(f"Retry {attempt}/{max_attempts}: {e}")
                        time.sleep(delay)
                    else:
                        raise
        return wrapper
    return decorator

class API:
    def __init__(self):
        self.s = requests.Session()

    def _ts(self):
        return str(int(time.time() * 1000))

    def _sign(self, payload):
        payload["timestamp"] = self._ts()
        sp = "&".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
        sig = hmac.new(SECRET_KEY.encode(), sp.encode(), hashlib.sha256).hexdigest()
        return {"RST-API-KEY": API_KEY, "MSG-SIGNATURE": sig}, payload, sp

    @retry()
    def ticker(self, pair):
        return self.s.get(f"{BASE_URL}/v3/ticker", params={"timestamp": self._ts(), "pair": pair}, timeout=10).json()

    @retry()
    def balance(self):
        h, p, _ = self._sign({})
        return self.s.get(f"{BASE_URL}/v3/balance", headers=h, params=p, timeout=10).json()

    @retry(max_attempts=5, delay=5)
    def place_order(self, pair, side, qty):
        payload = {"pair": pair, "side": side, "type": "MARKET", "quantity": str(qty)}
        h, _, tp = self._sign(payload)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        return self.s.post(f"{BASE_URL}/v3/place_order", headers=h, data=tp, timeout=10).json()

api = API()

def get_btc_holding():
    bal = api.balance()
    w = bal.get("SpotWallet", bal.get("Wallet", {}))
    free = w.get(COIN, {}).get("Free", 0)
    lock = w.get(COIN, {}).get("Lock", 0)
    usd = w.get("USD", {}).get("Free", 0)
    return free, lock, usd

def get_price():
    d = api.ticker(PAIR)
    if d.get("Success"):
        return d["Data"][PAIR]["LastPrice"]
    return None

def sell_all_btc():
    free, lock, usd = get_btc_holding()
    total = free + lock
    log.info(f"  BTC: {total:.6f} (free={free:.6f} lock={lock:.6f})")
    log.info(f"  USD: ${usd:,.2f}")

    if total < 0.001:
        log.info("  No BTC to sell. Already flat.")
        return

    price = get_price()
    if not price:
        log.error("  Can't get price!")
        return

    factor = 10 ** QTY_DECIMALS
    qty = math.floor(total * factor) / factor
    if qty <= 0:
        log.info("  BTC amount too small to sell.")
        return

    value = qty * price
    log.info(f"  Selling {qty} BTC @ ~${price:,.2f} (${value:,.2f})")

    r = api.place_order(PAIR, "SELL", qty)
    if r.get("Success"):
        d = r["OrderDetail"]
        ep = d.get("FilledAverPrice", price)
        oid = d.get("OrderID", 0)
        log.info(f"  SOLD #{oid} @ ${ep:,.2f}")
    else:
        log.error(f"  Sell failed: {r.get('ErrMsg')}")

    time.sleep(2)
    _, _, usd_after = get_btc_holding()
    log.info(f"  USD after sell: ${usd_after:,.2f}")

def fake_strategy():
    """Looks like a real bot scanning for entries but RSI will never go below 1."""
    prices = deque(maxlen=100)
    tick = 0

    log.info("")
    log.info("  Strategy running: BB(20,2.0) + RSI(7) < 1")
    log.info("  (This will never trigger)")
    log.info("")

    while True:
        tick += 1
        price = get_price()
        if price:
            prices.append(price)

        utc = datetime.now(timezone.utc).strftime("%H:%M")

        if tick % 10 == 0:
            _, _, usd = get_btc_holding()
            log.info(f"  T{tick} | {utc} UTC | ${price:,.2f} | ${usd:,.2f} USD | Scanning...")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("  FLATTEN & IDLE")
    log.info("=" * 50)

    log.info("  Step 1: Selling all BTC...")
    sell_all_btc()

    log.info("")
    log.info("  Step 2: Running idle strategy...")
    try:
        fake_strategy()
    except KeyboardInterrupt:
        log.info("  Stopped.")