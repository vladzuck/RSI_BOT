import requests, time, hmac, hashlib, logging, math, functools
from datetime import datetime, timezone
from collections import deque

API_KEY = "pZZ9zVTcKdin9QtSpq4slzYtUDFDf1j1OSCh503YO2UyADi1uKl2y5zAyvFmKAkf"
SECRET_KEY = "l8Zb6ebZi2RZcXZE6XCJUg8qOdHgb3sStveWr7Nj96MS8MteMyWSWG5Cku570Qk2"
BASE_URL = "https://mock-api.roostoo.com"

PAIR = "STO/USD"
COIN = "STO"
QTY_DECIMALS = 0
POLL_INTERVAL = 60
RSI_PERIOD = 7
RSI_THRESHOLD = 50
TAKE_PROFIT_PCT = 0.022      # +2%
STOP_LOSS_PCT = 0.03       # -3%

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
    def exchange_info(self):
        return self.s.get(f"{BASE_URL}/v3/exchangeInfo", timeout=10).json()

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

def calc_rsi(prices, period=7):
    if len(prices) < period + 1:
        return None
    data = list(prices)
    gains = []
    losses = []
    for i in range(1, len(data)):
        d = data[i] - data[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def get_price():
    d = api.ticker(PAIR)
    if d.get("Success"):
        return d["Data"][PAIR]["LastPrice"]
    return None

def get_usd():
    bal = api.balance()
    w = bal.get("SpotWallet", bal.get("Wallet", {}))
    return w.get("USD", {}).get("Free", 0)

def get_qty_decimals():
    """Get quantity precision from exchange info."""
    try:
        info = api.exchange_info()
        pairs = info.get("TradePairs", {})
        if PAIR in pairs:
            step = pairs[PAIR].get("StepSize", None)
            if step:
                step_str = str(step)
                if '.' in step_str:
                    return len(step_str.rstrip('0').split('.')[-1])
        log.info(f"  {PAIR} found, using default {QTY_DECIMALS} decimals")
    except Exception as e:
        log.warning(f"  Could not get step size: {e}")
    return QTY_DECIMALS

def buy_all(price, decimals):
    usd = get_usd()
    alloc = usd * 0.99  # 99% to leave room for rounding
    if alloc < 5:
        log.warning(f"  Not enough USD (${usd:.2f})")
        return False

    factor = 10 ** decimals
    qty = math.floor((alloc * 0.999) / price * factor) / factor
    if qty <= 0:
        log.warning(f"  Qty too small after rounding")
        return False

    log.info(f"  >>> BUY {COIN} {qty} @ ~${price:,.4f} (${alloc:,.2f}) <<<")

    r = api.place_order(PAIR, "BUY", qty)
    if r.get("Success"):
        d = r["OrderDetail"]
        fp = d.get("FilledAverPrice", price)
        fq = d.get("FilledQuantity", qty)
        oid = d.get("OrderID", 0)
        log.info(f"  FILLED #{oid} {COIN} {fq} @ ${fp:,.4f}")
        return True, fp, fq
    else:
        log.warning(f"  Rejected: {r.get('ErrMsg')}")
        return False, 0, 0

def sell_all(price, units, decimals, reason):
    factor = 10 ** decimals
    qty = math.floor(units * factor) / factor
    if qty <= 0:
        log.error(f"  Sell qty 0 (units={units})")
        return False

    log.info(f"  >>> SELL {COIN} {qty} @ ~${price:,.4f} | {reason} <<<")

    r = api.place_order(PAIR, "SELL", qty)
    if r.get("Success"):
        d = r["OrderDetail"]
        ep = d.get("FilledAverPrice", price)
        oid = d.get("OrderID", 0)
        log.info(f"  SOLD #{oid} {COIN} @ ${ep:,.4f} | {reason}")
        return True
    else:
        log.warning(f"  Sell rejected: {r.get('ErrMsg')}")
        return False

if __name__ == "__main__":
    log.info("=" * 50)
    log.info(f"  {COIN} BOT — TP={TAKE_PROFIT_PCT*100:.1f}% SL={STOP_LOSS_PCT*100:.1f}%")
    log.info("=" * 50)

    decimals = get_qty_decimals()
    log.info(f"  Pair: {PAIR} | Qty decimals: {decimals}")

    usd = get_usd()
    log.info(f"  Balance: ${usd:,.2f} USD")

    prices = deque(maxlen=100)
    entry_price = 0
    units = 0
    in_pos = True
    done = False
    entry_price = 0.0872
    units = 11327519
    tick = 0

    log.info(f"  Entry: RSI({RSI_PERIOD}) < {RSI_THRESHOLD}")
    log.info(f"  Exit:  TP=+{TAKE_PROFIT_PCT*100:.1f}% | SL=-{STOP_LOSS_PCT*100:.1f}%")
    log.info("")

    try:
        while True:
            tick += 1
            price = get_price()
            if price is None:
                time.sleep(POLL_INTERVAL)
                continue

            prices.append(price)
            rsi = calc_rsi(prices, RSI_PERIOD)
            utc = datetime.now(timezone.utc).strftime("%H:%M")

            if not in_pos and not done:
                # ── FLAT: wait for RSI entry ──
                if rsi is not None and rsi < RSI_THRESHOLD:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.4f} | RSI={rsi:.1f} < {RSI_THRESHOLD} -> BUY")
                    success, entry_price, units = buy_all(price, decimals)
                    if success:
                        in_pos = True
                        tp_price = entry_price * (1 + TAKE_PROFIT_PCT)
                        sl_price = entry_price * (1 - STOP_LOSS_PCT)
                        log.info(f"  Entry=${entry_price:,.4f} | TP=${tp_price:,.4f} | SL=${sl_price:,.4f}")
                        log.info("")
                else:
                    rsi_str = f"{rsi:.1f}" if rsi else "warmup"
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.4f} | RSI={rsi_str} | Waiting...")

            elif in_pos:
                # ── IN POSITION: check TP / SL ──
                tp_price = entry_price * (1 + TAKE_PROFIT_PCT)
                sl_price = entry_price * (1 - STOP_LOSS_PCT)
                pnl_pct = (price / entry_price - 1) * 100

                if price >= tp_price:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.4f} | P&L={pnl_pct:+.2f}% -> TAKE PROFIT")
                    if sell_all(price, units, decimals, "TAKE_PROFIT"):
                        in_pos = False
                        done = True
                        log.info(f"  Done. Bot will idle now.")
                        log.info("")

                elif price <= sl_price:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.4f} | P&L={pnl_pct:+.2f}% -> STOP LOSS")
                    if sell_all(price, units, decimals, "STOP_LOSS"):
                        in_pos = False
                        done = True
                        log.info(f"  Done. Bot will idle now.")
                        log.info("")

                elif tick % 10 == 0:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.4f} | P&L={pnl_pct:+.2f}% | TP=${tp_price:,.4f} SL=${sl_price:,.4f}")

            elif done:
                if tick % 10 == 0:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.4f} | IDLE — trade complete")

            if tick <= RSI_PERIOD + 1:
                time.sleep(5)
            else:
                time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("  Stopped.")