
import requests, time, hmac, hashlib, logging, math
from datetime import datetime, timezone
from collections import deque

API_KEY = "pZZ9zVTcKdin9QtSpq4slzYtUDFDf1j1OSCh503YO2UyADi1uKl2y5zAyvFmKAkf"
SECRET_KEY = "l8Zb6ebZi2RZcXZE6XCJUg8qOdHgb3sStveWr7Nj96MS8MteMyWSWG5Cku570Qk2"
BASE_URL = "https://mock-api.roostoo.com"

PAIR = "PAXG/USD"
COIN = "PAXG"
TAKE_PROFIT_PCT = 0.008
STOP_LOSS_PCT = 0.01
RSI_PERIOD = 7
RSI_THRESHOLD = 50
POLL_INTERVAL = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
log = logging.getLogger("Bot")

# ── API ──
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
    def exchange_info(self):
        return self.s.get(f"{BASE_URL}/v3/exchangeInfo", timeout=10).json()
    def ticker(self, pair):
        return self.s.get(f"{BASE_URL}/v3/ticker", params={"timestamp": self._ts(), "pair": pair}, timeout=10).json()
    def balance(self):
        h, p, _ = self._sign({})
        return self.s.get(f"{BASE_URL}/v3/balance", headers=h, params=p, timeout=10).json()
    def place_order(self, pair, side, qty):
        payload = {"pair": pair, "side": side, "type": "MARKET", "quantity": str(qty)}
        h, _, tp = self._sign(payload)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        return self.s.post(f"{BASE_URL}/v3/place_order", headers=h, data=tp, timeout=10).json()

api = API()

# ── RSI (Wilder's) ──
def calc_rsi(prices):
    if len(prices) < RSI_PERIOD + 1:
        return None
    data = list(prices)
    gains, losses = [], []
    for i in range(1, len(data)):
        d = data[i] - data[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    if len(gains) < RSI_PERIOD:
        return None
    ag = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
    al = sum(losses[:RSI_PERIOD]) / RSI_PERIOD
    for i in range(RSI_PERIOD, len(gains)):
        ag = (ag * (RSI_PERIOD - 1) + gains[i]) / RSI_PERIOD
        al = (al * (RSI_PERIOD - 1) + losses[i]) / RSI_PERIOD
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))

# ── Helpers ──
def get_price():
    try:
        d = api.ticker(PAIR)
        if d.get("Success"):
            return d["Data"][PAIR]["LastPrice"]
    except:
        pass
    return None

def get_usd():
    try:
        w = api.balance().get("SpotWallet", api.balance().get("Wallet", {}))
        return w.get("USD", {}).get("Free", 0)
    except:
        return 0

def get_qty_decimals():
    try:
        info = api.exchange_info()
        pair_info = info.get("TradePairs", {}).get(PAIR, {})
        step = pair_info.get("StepSize", None)
        if step and '.' in str(step):
            return len(str(step).rstrip('0').split('.')[-1])
        elif step:
            return 0
    except:
        pass
    return 2

# ── Main ──
if __name__ == "__main__":
    log.info("=" * 55)
    log.info(f"  {COIN} BOT — TP +{TAKE_PROFIT_PCT*100:.1f}% | SL -{STOP_LOSS_PCT*100:.1f}%")
    log.info("=" * 55)

    decimals = get_qty_decimals()
    log.info(f"  Pair: {PAIR} | Qty decimals: {decimals}")
    log.info(f"  USD: ${get_usd():,.2f}")
    log.info("")

    prices = deque(maxlen=500)
    entry_price = 0.0
    units = 0.0
    state = "WAITING"  # WAITING -> HOLDING -> DONE
    tick = 0

    try:
        while True:
            tick += 1
            utc = datetime.now(timezone.utc).strftime("%H:%M")

            try:
                price = get_price()
            except:
                price = None

            if price is None:
                log.warning(f"  T{tick} | {utc} UTC | Price fetch failed")
                time.sleep(POLL_INTERVAL)
                continue

            prices.append(price)
            rsi = calc_rsi(prices)
            rsi_str = f"{rsi:.1f}" if rsi else "warmup"

            # ── STATE: WAITING ──
            if state == "WAITING":
                if rsi is not None and rsi < RSI_THRESHOLD:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.2f} | RSI={rsi_str} < {RSI_THRESHOLD} -> BUY")

                    usd = get_usd()
                    alloc = usd * 0.10
                    factor = 10 ** decimals
                    qty = math.floor((alloc * 0.999) / price * factor) / factor

                    if qty <= 0:
                        log.error(f"  Qty too small (USD=${usd:.2f}, price=${price:.2f})")
                        time.sleep(POLL_INTERVAL)
                        continue

                    log.info(f"  >>> BUY {COIN} {qty} @ ~${price:,.2f} (${alloc:,.2f}) <<<")
                    try:
                        r = api.place_order(PAIR, "BUY", qty)
                        if r.get("Success"):
                            d = r["OrderDetail"]
                            entry_price = d.get("FilledAverPrice", price)
                            units = d.get("FilledQuantity", qty)
                            tp = entry_price * (1 + TAKE_PROFIT_PCT)
                            sl = entry_price * (1 - STOP_LOSS_PCT)
                            log.info(f"  FILLED {units} @ ${entry_price:,.2f}")
                            log.info(f"  TP=${tp:,.2f} | SL=${sl:,.2f}")
                            state = "HOLDING"
                        else:
                            log.warning(f"  Rejected: {r.get('ErrMsg')}")
                    except Exception as e:
                        log.error(f"  Buy failed: {e}")
                else:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.2f} | RSI={rsi_str} | Waiting...")

            # ── STATE: HOLDING ──
            elif state == "HOLDING":
                tp_price = entry_price * (1 + TAKE_PROFIT_PCT)
                sl_price = entry_price * (1 - STOP_LOSS_PCT)
                pnl = (price / entry_price - 1) * 100
                reason = None

                if price >= tp_price:
                    reason = "TAKE_PROFIT"
                elif price <= sl_price:
                    reason = "STOP_LOSS"

                if reason:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.2f} | P&L={pnl:+.2f}% -> {reason}")
                    factor = 10 ** decimals
                    sell_qty = math.floor(units * factor) / factor
                    if sell_qty <= 0:
                        log.error(f"  Sell qty 0 (units={units})")
                        time.sleep(POLL_INTERVAL)
                        continue
                    log.info(f"  >>> SELL {COIN} {sell_qty} @ ~${price:,.2f} | {reason} <<<")
                    try:
                        r = api.place_order(PAIR, "SELL", sell_qty)
                        if r.get("Success"):
                            ep = r["OrderDetail"].get("FilledAverPrice", price)
                            log.info(f"  SOLD @ ${ep:,.2f} | {reason}")
                            state = "DONE"
                        else:
                            log.warning(f"  Sell rejected: {r.get('ErrMsg')}")
                    except Exception as e:
                        log.error(f"  Sell failed: {e}")
                else:
                    log.info(f"  T{tick} | {utc} UTC | ${price:,.2f} | RSI={rsi_str} | P&L={pnl:+.2f}% | TP=${tp_price:,.2f} SL=${sl_price:,.2f}")

            # ── STATE: DONE ──
            elif state == "DONE":
                log.info(f"  T{tick} | {utc} UTC | ${price:,.2f} | RSI={rsi_str} | Trade complete. Scanning...")

            # Sleep
            if tick <= RSI_PERIOD + 1:
                time.sleep(5)
            else:
                time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("  Stopped.")