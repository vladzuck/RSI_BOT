"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ROOSTOO TRADING BOT — BB + RSI MEAN REVERSION (OPTIMIZED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STRATEGY:
  ENTRY:   price < BB_lower(20, 2.0)  AND  RSI(7) < 14
  EXIT:    +5% take profit  OR  -3% stop loss  OR  48h timeout
  BACKUP:  Daily forced trade at 12:00 UTC if no trade today

OPTIMIZED PARAMETERS (from 1176 combos across 7 periods):
  BB(20, 2.0) + RSI(7) < 14 | TP=5% | SL=3% | Timeout=48h
  Backtest: +63.5% Dec-Mar, +26.3% full year, 0.7 trades/day
"""

import requests
import time
import hmac
import hashlib
import json
import logging
import math
import functools
from datetime import datetime, timezone
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  API CREDENTIALS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API_KEY = "pZZ9zVTcKdin9QtSpq4slzYtUDFDf1j1OSCh503YO2UyADi1uKl2y5zAyvFmKAkf"
SECRET_KEY = "l8Zb6ebZi2RZcXZE6XCJUg8qOdHgb3sStveWr7Nj96MS8MteMyWSWG5Cku570Qk2"
BASE_URL = "https://mock-api.roostoo.com"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STRATEGY PARAMETERS (optimized)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PAIR = "BTC/USD"
COIN = "BTC"

BB_WINDOW = 20
BB_DEVIATION = 2.0
RSI_PERIOD = 7
RSI_THRESHOLD = 14

TAKE_PROFIT_PCT = 0.05  # +5% take profit
STOP_LOSS_PCT = 0.03  # -3% stop loss
TIMEOUT_TICKS = 2880  # 48 hours at 60s polling (48*60)

POSITION_SIZE_PCT = 0.95
TAKER_FEE_RATE = 0.001
QTY_DECIMALS = 2

POLL_INTERVAL = 60
WARMUP_POLL = 5

# Daily forced trade backup
DAILY_TRADE_HOUR = 12
DAILY_TRADE_SIZE_PCT = 0.50

LOG_FILE = "bot.log"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger("Bot")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RETRY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def retry(max_attempts=3, delay=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_attempts:
                        log.warning(f"Retry {attempt}/{max_attempts} {func.__name__}: {e}")
                        time.sleep(delay)
                    else:
                        log.error(f"Failed {func.__name__} after {max_attempts} attempts: {e}")
                        raise

        return wrapper

    return decorator


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DATA CLASSES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@dataclass
class Position:
    entry_price: float
    entry_tick: int
    units: float
    entry_time: str
    cost_basis: float
    trade_type: str = "BB"


@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    ticks_held: int
    exit_reason: str
    trade_type: str


@dataclass
class BotState:
    prices: deque = field(default_factory=lambda: deque(maxlen=500))
    position: Optional[Position] = None
    tick_count: int = 0
    trades: List[TradeRecord] = field(default_factory=list)
    initial_capital: float = 0.0
    last_daily_trade_date: Optional[str] = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  API CLIENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class RoostooClient:
    def __init__(self):
        self.session = requests.Session()

    def _ts(self):
        return str(int(time.time() * 1000))

    def _sign(self, payload):
        payload["timestamp"] = self._ts()
        sp = "&".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
        sig = hmac.new(SECRET_KEY.encode(), sp.encode(), hashlib.sha256).hexdigest()
        return {"RST-API-KEY": API_KEY, "MSG-SIGNATURE": sig}, payload, sp

    @retry()
    def server_time(self):
        return self.session.get(f"{BASE_URL}/v3/serverTime", timeout=10).json()

    @retry()
    def exchange_info(self):
        return self.session.get(f"{BASE_URL}/v3/exchangeInfo", timeout=10).json()

    @retry()
    def ticker(self, pair):
        p = {"timestamp": self._ts(), "pair": pair}
        return self.session.get(f"{BASE_URL}/v3/ticker", params=p, timeout=10).json()

    @retry()
    def balance(self):
        h, p, _ = self._sign({})
        return self.session.get(f"{BASE_URL}/v3/balance", headers=h, params=p, timeout=10).json()

    @retry(max_attempts=5, delay=5)
    def place_order(self, pair, side, quantity):
        payload = {"pair": pair, "side": side.upper(), "type": "MARKET", "quantity": str(quantity)}
        h, _, tp = self._sign(payload)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        return self.session.post(f"{BASE_URL}/v3/place_order", headers=h, data=tp, timeout=10).json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  INDICATORS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class Indicators:
    @staticmethod
    def bollinger_bands(prices, window=20, num_std=2.0):
        if len(prices) < window:
            return None, None, None
        data = list(prices)[-window:]
        mean = sum(data) / window
        var = sum((x - mean) ** 2 for x in data) / window
        std = math.sqrt(var)
        return mean - num_std * std, mean, mean + num_std * std

    @staticmethod
    def rsi(prices, period=7):
        """Wilder's RSI matching ta library (EMA smoothing)."""
        if len(prices) < period + 1:
            return None
        data = list(prices)

        # Calculate initial averages using first 'period' changes
        gains = []
        losses = []
        for i in range(1, len(data)):
            d = data[i] - data[i - 1]
            gains.append(max(0, d))
            losses.append(max(0, -d))

        if len(gains) < period:
            return None

        # First average: simple average of first 'period' values
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Wilder's smoothing for remaining values
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TRADING BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TradingBot:
    def __init__(self):
        self.client = RoostooClient()
        self.state = BotState()

    def get_wallet(self):
        try:
            bal = self.client.balance()
            w = bal.get("SpotWallet", bal.get("Wallet", {}))
            return (
                w.get("USD", {}).get("Free", 0),
                w.get("USD", {}).get("Lock", 0),
                w.get(COIN, {}).get("Free", 0),
                w.get(COIN, {}).get("Lock", 0),
            )
        except:
            return 0, 0, 0, 0

    def preflight(self):
        log.info("Preflight checks...")
        try:
            self.client.server_time()
            log.info("  Server: OK")
        except:
            log.error("  Server: FAIL")
            return False

        try:
            info = self.client.exchange_info()
            if PAIR not in info.get("TradePairs", {}):
                log.error(f"  {PAIR} not available")
                return False
            log.info(f"  {PAIR}: OK")
        except:
            log.error("  Exchange info: FAIL")
            return False

        usd_f, usd_l, btc_f, btc_l = self.get_wallet()
        self.state.initial_capital = usd_f + usd_l
        log.info(f"  Wallet: ${usd_f:,.2f} USD | {btc_f:.6f} {COIN}")

        log.info("-" * 65)
        log.info(f"  BB({BB_WINDOW},{BB_DEVIATION}) + RSI({RSI_PERIOD})<{RSI_THRESHOLD}")
        log.info(f"  TP=+{TAKE_PROFIT_PCT * 100:.0f}% | SL=-{STOP_LOSS_PCT * 100:.0f}% | Timeout={TIMEOUT_TICKS} ticks")
        log.info(f"  Daily backup: {DAILY_TRADE_HOUR:02d}:00 UTC | Poll: {POLL_INTERVAL}s")
        log.info(f"  Warmup: {max(BB_WINDOW, RSI_PERIOD + 1)} ticks")
        log.info("-" * 65)
        return True

    def recover_position(self):
        usd_f, usd_l, btc_f, btc_l = self.get_wallet()
        total_btc = btc_f + btc_l
        if total_btc > 0.001:
            price = self.fetch_price()
            if price:
                self.state.position = Position(
                    entry_price=price,
                    entry_tick=self.state.tick_count,
                    units=total_btc,
                    entry_time=datetime.now(timezone.utc).isoformat(),
                    cost_basis=total_btc * price,
                    trade_type="RECOVERED",
                )
                log.info(f"  RECOVERED: {total_btc:.6f} {COIN} @ ~${price:,.2f}")
                return True
        log.info("  No position to recover.")
        return False

    def fetch_price(self):
        try:
            d = self.client.ticker(PAIR)
            if d.get("Success"):
                return d["Data"][PAIR]["LastPrice"]
        except Exception as e:
            log.error(f"Price error: {e}")
        return None

    def print_tick(self, price, rsi, bb_lower, bb_mid, signal, detail):
        tick = self.state.tick_count
        utc = datetime.now(timezone.utc).strftime("%H:%M")

        # Only fetch wallet every 5 ticks to reduce API calls
        if tick % 5 == 1 or signal == "EXIT":
            usd_f, _, btc_f, _ = self.get_wallet()
            self._cached_wallet = (usd_f, btc_f)
        else:
            usd_f, btc_f = getattr(self, '_cached_wallet', (0, 0))
        equity = usd_f + btc_f * price

        if self.state.position:
            p = self.state.position
            pnl = (price / p.entry_price - 1) * 100
            held = tick - p.entry_tick
            tp_price = p.entry_price * (1 + TAKE_PROFIT_PCT)
            sl_price = p.entry_price * (1 - STOP_LOSS_PCT)
            pos_line = (
                f"  POS: [{p.trade_type}] {p.units:.2f} {COIN} @ ${p.entry_price:,.2f} | "
                f"P&L={pnl:+.2f}% | Held={held} | TP=${tp_price:,.0f} SL=${sl_price:,.0f}"
            )
        else:
            pos_line = "  POS: FLAT"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily = "YES" if self.state.last_daily_trade_date == today else "NO"

        bb_str = f"${bb_lower:,.0f}" if bb_lower else "warming"
        rsi_str = f"{rsi:.1f}" if rsi else "warming"

        log.info("")
        log.info(f"=== T{tick} | {utc} UTC | ${price:,.2f} | RSI={rsi_str} | BB_low={bb_str} ===")
        log.info(f"  USD: ${usd_f:,.2f} | BTC: {btc_f:.6f} | Equity: ${equity:,.2f}")
        log.info(pos_line)
        log.info(f"  [{signal}] {detail}")
        log.info(f"  Daily: {daily} | Trades: {len(self.state.trades)}")

    def open_position(self, price, trade_type="BB", size_pct=None):
        if size_pct is None:
            size_pct = POSITION_SIZE_PCT

        usd_f, _, _, _ = self.get_wallet()
        alloc = usd_f * size_pct

        if alloc < 10:
            log.warning(f"  Insufficient USD (${usd_f:.2f})")
            return False

        qty = round((alloc * (1 - TAKER_FEE_RATE)) / price, QTY_DECIMALS)
        if qty <= 0:
            log.warning("  Quantity too small")
            return False

        log.info(f"  >>> BUY [{trade_type}] {qty} {COIN} @ ~${price:,.2f} <<<")

        try:
            r = self.client.place_order(PAIR, "BUY", qty)
            if r.get("Success"):
                d = r["OrderDetail"]
                fp = d.get("FilledAverPrice", price)
                fq = d.get("FilledQuantity", qty)
                oid = d.get("OrderID", 0)

                self.state.position = Position(
                    entry_price=fp, entry_tick=self.state.tick_count,
                    units=fq, entry_time=datetime.now(timezone.utc).isoformat(),
                    cost_basis=alloc, trade_type=trade_type,
                )
                log.info(f"  FILLED #{oid} @ ${fp:,.2f} ({fq} {COIN})")
                return True
            else:
                log.warning(f"  Rejected: {r.get('ErrMsg')}")
                return False
        except Exception as e:
            log.error(f"  Order failed: {e}")
            return False

    def close_position(self, price, exit_reason):
        pos = self.state.position
        if not pos:
            return False

        # Round DOWN to avoid selling more than we own
        factor = 10 ** QTY_DECIMALS
        qty = math.floor(pos.units * factor) / factor
        if qty <= 0:
            log.error(f"  Sell qty is 0 after rounding (units={pos.units})")
            return False
        log.info(f"  >>> SELL [{pos.trade_type}] {qty} {COIN} | {exit_reason} <<<")

        try:
            r = self.client.place_order(PAIR, "SELL", qty)
            if r.get("Success"):
                d = r["OrderDetail"]
                ep = d.get("FilledAverPrice", price)
                oid = d.get("OrderID", 0)

                gross = pos.units * ep
                net = gross * (1 - TAKER_FEE_RATE)
                pnl = net - pos.cost_basis
                pnl_pct = (ep / pos.entry_price - 1) * 100
                held = self.state.tick_count - pos.entry_tick

                self.state.trades.append(TradeRecord(
                    entry_time=pos.entry_time,
                    exit_time=datetime.now(timezone.utc).isoformat(),
                    entry_price=pos.entry_price, exit_price=ep,
                    pnl=pnl, pnl_pct=pnl_pct, ticks_held=held,
                    exit_reason=exit_reason, trade_type=pos.trade_type,
                ))
                self.state.position = None

                w = "WIN" if pnl > 0 else "LOSS"
                log.info(f"  SOLD #{oid} @ ${ep:,.2f}")
                log.info(f"  [{w}] ${pnl:+,.2f} ({pnl_pct:+.2f}%) | {exit_reason} | Held {held} ticks")
                return True
            else:
                log.warning(f"  Sell rejected: {r.get('ErrMsg')}")
                return False
        except Exception as e:
            log.error(f"  Sell failed: {e}")
            return False

    def check_entry(self, price):
        prices = self.state.prices
        bb_lower, bb_mid, _ = Indicators.bollinger_bands(prices, BB_WINDOW, BB_DEVIATION)
        rsi = Indicators.rsi(prices, RSI_PERIOD)

        if bb_lower is None or rsi is None:
            return False, rsi, bb_lower, bb_mid, f"Warmup ({len(prices)}/{max(BB_WINDOW, RSI_PERIOD + 1)})"

        if price < bb_lower and rsi < RSI_THRESHOLD:
            return True, rsi, bb_lower, bb_mid, (
                f"ENTRY SIGNAL! Price ${price:,.2f} < BB ${bb_lower:,.2f} AND RSI {rsi:.1f} < {RSI_THRESHOLD}")

        return False, rsi, bb_lower, bb_mid, (
            f"Price ${price:,.2f} | BB_low ${bb_lower:,.2f} | RSI {rsi:.1f}")

    def check_exit(self, price):
        pos = self.state.position
        held = self.state.tick_count - pos.entry_tick
        tp_price = pos.entry_price * (1 + TAKE_PROFIT_PCT)
        sl_price = pos.entry_price * (1 - STOP_LOSS_PCT)

        if price >= tp_price:
            return True, "TAKE_PROFIT"
        if price <= sl_price:
            return True, "STOP_LOSS"
        if held >= TIMEOUT_TICKS:
            return True, "TIMEOUT_48H"

        pnl = (price / pos.entry_price - 1) * 100
        return False, (
            f"P&L={pnl:+.2f}% | TP=${tp_price:,.0f} | SL=${sl_price:,.0f} | "
            f"Timeout in {TIMEOUT_TICKS - held} ticks")

    def check_daily_trade(self, price):
        utc = datetime.now(timezone.utc)
        today = utc.strftime("%Y-%m-%d")

        if self.state.last_daily_trade_date == today:
            return
        if utc.hour != DAILY_TRADE_HOUR:
            return
        if self.state.position is not None:
            return

        log.info(f"  *** DAILY FORCED TRADE — {today} ***")
        if self.open_position(price, trade_type="DAILY", size_pct=DAILY_TRADE_SIZE_PCT):
            self.state.last_daily_trade_date = today

    def run(self):
        log.info("=" * 65)
        log.info("  BB + RSI BOT — OPTIMIZED FOR COMPETITION")
        log.info("=" * 65)

        if not self.preflight():
            log.error("Preflight failed.")
            return

        self.recover_position()
        log.info("Bot running. Ctrl+C to stop.\n")

        try:
            while True:
                price = self.fetch_price()
                if price is None:
                    time.sleep(POLL_INTERVAL)
                    continue

                self.state.tick_count += 1
                self.state.prices.append(price)

                if self.state.position is None:
                    should_enter, rsi, bb_lower, bb_mid, detail = self.check_entry(price)
                    self.print_tick(price, rsi, bb_lower, bb_mid, "SCAN", detail)

                    if should_enter:
                        if self.open_position(price, trade_type="BB"):
                            self.state.last_daily_trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    else:
                        self.check_daily_trade(price)

                else:
                    rsi = Indicators.rsi(self.state.prices, RSI_PERIOD)
                    bb_lower, bb_mid, _ = Indicators.bollinger_bands(self.state.prices, BB_WINDOW, BB_DEVIATION)

                    should_exit, detail = self.check_exit(price)
                    signal = detail if not should_exit else "EXIT"
                    self.print_tick(price, rsi, bb_lower, bb_mid, signal, detail)

                    if should_exit:
                        self.close_position(price, detail)

                if len(self.state.prices) < max(BB_WINDOW, RSI_PERIOD + 1):
                    time.sleep(WARMUP_POLL)
                else:
                    time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        trades = self.state.trades
        total = len(trades)
        wins = len([t for t in trades if t.pnl > 0])
        total_pnl = sum(t.pnl for t in trades)
        tp_c = len([t for t in trades if t.exit_reason == "TAKE_PROFIT"])
        sl_c = len([t for t in trades if t.exit_reason == "STOP_LOSS"])
        to_c = len([t for t in trades if t.exit_reason == "TIMEOUT_48H"])
        daily_c = len([t for t in trades if t.trade_type == "DAILY"])

        usd_f, _, btc_f, _ = self.get_wallet()
        last_p = list(self.state.prices)[-1] if self.state.prices else 0
        equity = usd_f + btc_f * last_p

        log.info("")
        log.info("=" * 65)
        log.info("  SESSION SUMMARY")
        log.info("=" * 65)
        log.info(f"  Ticks:       {self.state.tick_count}")
        log.info(f"  Trades:      {total} (BB: {total - daily_c}, Daily: {daily_c})")
        log.info(f"  Wins:        {wins}/{total}")
        log.info(f"  TP/SL/TO:    {tp_c}/{sl_c}/{to_c}")
        log.info(f"  Net P&L:     ${total_pnl:+,.2f}")
        log.info(f"  Equity:      ${equity:,.2f}")

        if trades:
            log.info("")
            for i, t in enumerate(trades, 1):
                w = "W" if t.pnl > 0 else "L"
                log.info(f"  {i}. [{t.trade_type}] ${t.entry_price:,.2f}->${t.exit_price:,.2f} "
                         f"${t.pnl:+,.2f} ({t.pnl_pct:+.1f}%) {t.exit_reason} [{w}]")

        if self.state.position:
            p = self.state.position
            pnl = (last_p / p.entry_price - 1) * 100
            log.info(f"\n  OPEN: {p.units:.2f} {COIN} @ ${p.entry_price:,.2f} ({pnl:+.1f}%)")

        log.info("=" * 65)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()