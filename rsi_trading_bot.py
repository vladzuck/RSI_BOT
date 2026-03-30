import requests, time, hmac, hashlib, logging, functools
from datetime import datetime, timezone
from collections import deque

API_KEY = "pZZ9zVTcKdin9QtSpq4slzYtUDFDf1j1OSCh503YO2UyADi1uKl2y5zAyvFmKAkf"
SECRET_KEY = "l8Zb6ebZi2RZcXZE6XCJUg8qOdHgb3sStveWr7Nj96MS8MteMyWSWG5Cku570Qk2"
BASE_URL = "https://mock-api.roostoo.com"

PAIR = "BTC/USD"
RSI_PERIOD = 7
RSI_THRESHOLD = 0
POLL_INTERVAL = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")],
)
log = logging.getLogger("Bot")

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
    def ticker(self, pair):
        return self.s.get(f"{BASE_URL}/v3/ticker", params={"timestamp": self._ts(), "pair": pair}, timeout=10).json()
    def balance(self):
        h, p, _ = self._sign({})
        return self.s.get(f"{BASE_URL}/v3/balance", headers=h, params=p, timeout=10).json()

api = API()

def calc_rsi(prices, period=7):
    if len(prices) < period + 1:
        return None
    data = list(prices)
    gains = []; losses = []
    for i in range(1, len(data)):
        d = data[i] - data[i - 1]
        gains.append(max(0, d)); losses.append(max(0, -d))
    if len(gains) < period:
        return None
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

if __name__ == "__main__":
    log.info("=" * 55)
    log.info("  BTC RSI BOT — BB(20,2.0) + RSI(7) < 2")
    log.info("=" * 55)

    prices = deque(maxlen=500)
    tick = 0

    try:
        while True:
            tick += 1
            try:
                d = api.ticker(PAIR)
                price = d["Data"][PAIR]["LastPrice"] if d.get("Success") else None
            except:
                price = None

            if price is None:
                time.sleep(POLL_INTERVAL)
                continue

            prices.append(price)
            rsi = calc_rsi(prices, RSI_PERIOD)
            utc = datetime.now(timezone.utc).strftime("%H:%M")
            rsi_str = f"{rsi:.1f}" if rsi else "warmup"

            log.info(f"  T{tick} | {utc} UTC | BTC ${price:,.2f} | RSI={rsi_str} | Scanning...")

            if tick <= RSI_PERIOD + 1:
                time.sleep(5)
            else:
                time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info(" Stopped.")