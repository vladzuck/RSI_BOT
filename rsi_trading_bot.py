"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ROOSTOO LIVE TRADING BOT — RSI + DAILY FORCED TRADE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STRATEGY:
  1. RSI dip-buy (same as before)
  2. Daily forced trade at DAILY_TRADE_HOUR if no trade happened today
"""

import requests
import time
import hmac
import hashlib
import json
import logging
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
#  STRATEGY PARAMETERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PAIR = "BTC/USD"
COIN = "BTC"

RSI_PERIOD = 21
ENTRY_RSI_THRESHOLD = 22
EXIT_RSI_THRESHOLD = 40

SL_PERCENT = 0.03
MAX_HOLDING_TICKS = 700

ALLOWED_START_HOUR = 0
ALLOWED_END_HOUR = 8

POSITION_SIZE_PCT = 0.95
TAKER_FEE_RATE = 0.001

POLL_INTERVAL = 60
WARMUP_POLL = 10

# ── Daily Forced Trade ──
DAILY_TRADE_HOUR = 2       # UTC hour to force a trade (2 = 02:00 UTC)
DAILY_TRADE_SIZE_PCT = 0.5 # Use 50% of USD for forced trade

LOG_FILE = "rsi_bot.log"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger("RSI-Bot")


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
    trade_type: str = "RSI"


@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    units: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    won: bool
    ticks_held: int
    trade_type: str = "RSI"


@dataclass
class BotState:
    prices: deque = field(default_factory=lambda: deque(maxlen=500))
    position: Optional[Position] = None
    tick_count: int = 0
    trades: List[TradeRecord] = field(default_factory=list)
    initial_capital: float = 0.0
    peak_equity: float = 0.0
    last_daily_trade_date: Optional[str] = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ROOSTOO API CLIENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RoostooClient:
    def __init__(self, api_key, secret_key, base_url):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.session = requests.Session()

    def _ts(self):
        return str(int(time.time() * 1000))

    def _sign(self, payload):
        payload["timestamp"] = self._ts()
        sorted_params = "&".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
        sig = hmac.new(self.secret_key.encode(), sorted_params.encode(), hashlib.sha256).hexdigest()
        return {"RST-API-KEY": self.api_key, "MSG-SIGNATURE": sig}, payload, sorted_params

    def server_time(self):
        return self.session.get(f"{self.base_url}/v3/serverTime", timeout=10).json()

    def exchange_info(self):
        return self.session.get(f"{self.base_url}/v3/exchangeInfo", timeout=10).json()

    def ticker(self, pair):
        params = {"timestamp": self._ts(), "pair": pair}
        return self.session.get(f"{self.base_url}/v3/ticker", params=params, timeout=10).json()

    def balance(self):
        h, p, _ = self._sign({})
        return self.session.get(f"{self.base_url}/v3/balance", headers=h, params=p, timeout=10).json()

    def place_order(self, pair, side, quantity, order_type="MARKET", price=None):
        payload = {"pair": pair, "side": side.upper(), "type": order_type.upper(),
                   "quantity": str(quantity)}
        if order_type.upper() == "LIMIT" and price is not None:
            payload["price"] = str(price)
        h, _, tp = self._sign(payload)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        return self.session.post(f"{self.base_url}/v3/place_order", headers=h, data=tp, timeout=10).json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WILDER'S RSI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WildersRSI:
    def __init__(self, period):
        self.period = period
        self.avg_gain = None
        self.avg_loss = None
        self.prev_price = None
        self.warmup_gains = []
        self.warmup_losses = []
        self.ready = False

    def update(self, price):
        if self.prev_price is None:
            self.prev_price = price
            return None
        delta = price - self.prev_price
        gain = max(0, delta)
        loss = max(0, -delta)
        self.prev_price = price
        if not self.ready:
            self.warmup_gains.append(gain)
            self.warmup_losses.append(loss)
            if len(self.warmup_gains) >= self.period:
                self.avg_gain = sum(self.warmup_gains) / self.period
                self.avg_loss = sum(self.warmup_losses) / self.period
                self.ready = True
                return self._calc_rsi()
            return None
        self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
        self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period
        return self._calc_rsi()

    def _calc_rsi(self):
        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        return 100 - (100 / (1 + rs))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STRATEGY ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RSIStrategy:
    def __init__(self):
        self.rsi = WildersRSI(RSI_PERIOD)

    def min_data_points(self):
        return RSI_PERIOD + 1

    def is_valid_entry_time(self):
        utc_hour = datetime.now(timezone.utc).hour
        return ALLOWED_START_HOUR <= utc_hour < ALLOWED_END_HOUR

    def check_entry(self, price):
        rsi_val = self.rsi.update(price)
        if rsi_val is None:
            return False, 0.0, f"RSI warming up ({len(self.rsi.warmup_gains)}/{RSI_PERIOD})"
        if rsi_val >= ENTRY_RSI_THRESHOLD:
            return False, rsi_val, f"RSI {rsi_val:.1f} >= {ENTRY_RSI_THRESHOLD}"
        if not self.is_valid_entry_time():
            utc_hour = datetime.now(timezone.utc).hour
            return False, rsi_val, (
                f"RSI {rsi_val:.1f} LOW but outside window "
                f"({utc_hour:02d}:xx, need {ALLOWED_START_HOUR:02d}-{ALLOWED_END_HOUR:02d})")
        return True, rsi_val, f"RSI {rsi_val:.1f} < {ENTRY_RSI_THRESHOLD} — ENTRY SIGNAL"

    def check_exit(self, price, position, current_tick):
        rsi_val = self.rsi._calc_rsi() if self.rsi.ready else 50.0
        ticks_held = current_tick - position.entry_tick
        sl_price = position.entry_price * (1 - SL_PERCENT)

        if price <= sl_price:
            return True, f"STOP_LOSS (${price:,.2f} <= ${sl_price:,.2f})"
        if rsi_val >= EXIT_RSI_THRESHOLD:
            return True, f"RSI_EXIT (RSI {rsi_val:.1f} >= {EXIT_RSI_THRESHOLD})"
        if ticks_held >= MAX_HOLDING_TICKS:
            return True, f"TIME_LIMIT ({ticks_held} >= {MAX_HOLDING_TICKS})"

        return False, (
            f"RSI={rsi_val:.1f} | ticks={ticks_held}/{MAX_HOLDING_TICKS} | "
            f"SL=${sl_price:,.2f} | P&L={(price / position.entry_price - 1) * 100:+.2f}%")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TRADING BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TradingBot:
    def __init__(self):
        self.client = RoostooClient(API_KEY, SECRET_KEY, BASE_URL)
        self.strategy = RSIStrategy()
        self.state = BotState()

    def preflight(self):
        log.info("Running preflight checks...")
        try:
            st = self.client.server_time()
            log.info(f"  Server reachable (time: {st['ServerTime']})")
        except Exception as e:
            log.error(f"  Server unreachable: {e}")
            return False
        try:
            info = self.client.exchange_info()
            pairs = info.get("TradePairs", {})
            if PAIR not in pairs:
                log.error(f"  {PAIR} not available")
                return False
            log.info(f"  {PAIR} active")
        except Exception as e:
            log.error(f"  Exchange info failed: {e}")
            return False
        try:
            bal = self.client.balance()
            wallet = bal.get("SpotWallet", bal.get("Wallet", {}))
            usd_free = wallet.get("USD", {}).get("Free", 0)
            self.state.initial_capital = usd_free
            self.state.peak_equity = usd_free
            log.info(f"  Auth OK — ${usd_free:,.2f} USD")
        except Exception as e:
            log.error(f"  Auth failed: {e}")
            return False

        log.info("─" * 60)
        log.info(f"  RSI Strategy: Entry<{ENTRY_RSI_THRESHOLD} Exit>={EXIT_RSI_THRESHOLD} SL={SL_PERCENT*100}%")
        log.info(f"  Daily Trade:  {DAILY_TRADE_HOUR:02d}:00 UTC, {DAILY_TRADE_SIZE_PCT*100:.0f}% size")
        log.info(f"  Poll: {POLL_INTERVAL}s | Window: {ALLOWED_START_HOUR:02d}-{ALLOWED_END_HOUR:02d} UTC")
        log.info("─" * 60)
        return True

    def fetch_price(self):
        try:
            data = self.client.ticker(PAIR)
            if data.get("Success"):
                return data["Data"][PAIR]["LastPrice"]
            return None
        except Exception as e:
            log.error(f"Price fetch error: {e}")
            return None

    def get_usd_free(self):
        try:
            bal = self.client.balance()
            wallet = bal.get("SpotWallet", bal.get("Wallet", {}))
            return wallet.get("USD", {}).get("Free", 0)
        except:
            return 0

    def print_tick(self, price, rsi_val, signal, detail):
        tick = self.state.tick_count
        utc_now = datetime.now(timezone.utc).strftime("%H:%M")
        try:
            bal = self.client.balance()
            wallet = bal.get("SpotWallet", bal.get("Wallet", {}))
            usd_free = wallet.get("USD", {}).get("Free", 0)
            usd_lock = wallet.get("USD", {}).get("Lock", 0)
            btc_free = wallet.get(COIN, {}).get("Free", 0)
            btc_lock = wallet.get(COIN, {}).get("Lock", 0)
        except:
            usd_free = usd_lock = btc_free = btc_lock = 0

        btc_value = (btc_free + btc_lock) * price
        total_equity = usd_free + usd_lock + btc_value

        if self.state.position:
            pos = self.state.position
            pos_pnl = (price / pos.entry_price - 1) * 100
            ticks_held = tick - pos.entry_tick
            sl_price = pos.entry_price * (1 - SL_PERCENT)
            pos_line = (
                f"  POSITION: LONG [{pos.trade_type}] {pos.units:.6f} {COIN} @ ${pos.entry_price:,.2f} | "
                f"P&L={pos_pnl:+.2f}% | Held={ticks_held}/{MAX_HOLDING_TICKS} | "
                f"SL=${sl_price:,.2f}")
        else:
            pos_line = "  POSITION: FLAT"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_done = "YES" if self.state.last_daily_trade_date == today else "NO"

        log.info("")
        log.info(f"=== TICK {tick} === {utc_now} UTC === {PAIR} ${price:,.2f} === RSI={rsi_val:.1f} ===")
        log.info(f"  WALLET:   ${usd_free:,.2f} USD | {btc_free:.6f} {COIN} | Equity: ${total_equity:,.2f}")
        log.info(pos_line)
        log.info(f"  SIGNAL:   [{signal}] {detail}")
        log.info(f"  DAILY:    Trade today: {daily_done} | Next: {DAILY_TRADE_HOUR:02d}:00 UTC")

    def open_position(self, price, rsi_val, trade_type="RSI", size_pct=None):
        if size_pct is None:
            size_pct = POSITION_SIZE_PCT

        usd_free = self.get_usd_free()
        buy_amount = usd_free * size_pct

        if buy_amount < 10:
            log.warning(f"  Insufficient USD (${usd_free:.2f})")
            return False

        quantity = (buy_amount * (1 - TAKER_FEE_RATE)) / price

        log.info("")
        log.info(f"  >>> OPENING [{trade_type}] POSITION <<<")
        log.info(f"  BUY {quantity:.6f} {COIN} @ ~${price:,.2f} (${buy_amount:,.2f})")

        try:
            result = self.client.place_order(PAIR, "BUY", round(quantity, 6))
            if result.get("Success"):
                detail = result["OrderDetail"]
                filled_price = detail.get("FilledAverPrice", price)
                filled_qty = detail.get("FilledQuantity", quantity)
                order_id = detail.get("OrderID", 0)

                self.state.position = Position(
                    entry_price=filled_price,
                    entry_tick=self.state.tick_count,
                    units=filled_qty,
                    entry_time=datetime.now(timezone.utc).isoformat(),
                    cost_basis=buy_amount,
                    trade_type=trade_type,
                )

                log.info(f"  FILLED #{order_id} @ ${filled_price:,.2f} ({filled_qty:.6f} {COIN})")
                return True
            else:
                log.warning(f"  Rejected: {result.get('ErrMsg')}")
                return False
        except Exception as e:
            log.error(f"  Failed: {e}")
            return False

    def close_position(self, price, reason):
        pos = self.state.position
        if not pos:
            return False

        log.info("")
        log.info(f"  >>> CLOSING [{pos.trade_type}] POSITION <<<")
        log.info(f"  SELL {pos.units:.6f} {COIN} @ ~${price:,.2f} | {reason}")

        try:
            result = self.client.place_order(PAIR, "SELL", round(pos.units, 6))
            if result.get("Success"):
                detail = result["OrderDetail"]
                exit_price = detail.get("FilledAverPrice", price)
                order_id = detail.get("OrderID", 0)

                gross = pos.units * exit_price
                net = gross * (1 - TAKER_FEE_RATE)
                pnl = net - pos.cost_basis
                pnl_pct = (exit_price / pos.entry_price - 1) * 100
                ticks_held = self.state.tick_count - pos.entry_tick

                exit_type = "STOP_LOSS" if "STOP_LOSS" in reason else \
                            "RSI_EXIT" if "RSI_EXIT" in reason else "TIME_LIMIT"

                self.state.trades.append(TradeRecord(
                    entry_time=pos.entry_time,
                    exit_time=datetime.now(timezone.utc).isoformat(),
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    units=pos.units,
                    pnl=pnl, pnl_pct=pnl_pct,
                    exit_reason=exit_type,
                    won=pnl > 0,
                    ticks_held=ticks_held,
                    trade_type=pos.trade_type,
                ))
                self.state.position = None

                emoji = "WIN" if pnl > 0 else "LOSS"
                log.info(f"  SOLD #{order_id} @ ${exit_price:,.2f}")
                log.info(f"  [{emoji}] P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%) | {exit_type} | {pos.trade_type}")
                return True
            else:
                log.warning(f"  Rejected: {result.get('ErrMsg')}")
                return False
        except Exception as e:
            log.error(f"  Failed: {e}")
            return False

    def check_daily_trade(self, price):
        utc_now = datetime.now(timezone.utc)
        today = utc_now.strftime("%Y-%m-%d")

        if self.state.last_daily_trade_date == today:
            return
        if utc_now.hour != DAILY_TRADE_HOUR:
            return
        if self.state.position is not None:
            return

        log.info("")
        log.info(f"  *** DAILY FORCED TRADE — {today} {DAILY_TRADE_HOUR:02d}:00 UTC ***")
        if self.open_position(price, 0, trade_type="DAILY", size_pct=DAILY_TRADE_SIZE_PCT):
            self.state.last_daily_trade_date = today

    def run(self):
        log.info("=" * 60)
        log.info("  RSI + DAILY TRADE BOT — LIVE ON ROOSTOO")
        log.info("=" * 60)

        if not self.preflight():
            log.error("Preflight failed.")
            return

        log.info("Bot running. Ctrl+C to stop.")

        try:
            while True:
                price = self.fetch_price()
                if price is None:
                    time.sleep(POLL_INTERVAL)
                    continue

                self.state.tick_count += 1
                self.state.prices.append(price)

                if self.state.position is None:
                    should_enter, rsi_val, reason = self.strategy.check_entry(price)
                    self.print_tick(price, rsi_val, "SCANNING", reason)

                    if should_enter:
                        self.open_position(price, rsi_val, trade_type="RSI")
                        self.state.last_daily_trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    else:
                        self.check_daily_trade(price)

                else:
                    self.strategy.rsi.update(price)
                    should_exit, reason = self.strategy.check_exit(
                        price, self.state.position, self.state.tick_count)
                    rsi_val = self.strategy.rsi._calc_rsi() if self.strategy.rsi.ready else 0
                    self.print_tick(price, rsi_val, "HOLDING", reason)

                    if should_exit:
                        self.close_position(price, reason)

                if not self.strategy.rsi.ready:
                    time.sleep(WARMUP_POLL)
                else:
                    time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        trades = self.state.trades
        total = len(trades)
        wins = len([t for t in trades if t.won])
        rsi_trades = len([t for t in trades if t.trade_type == "RSI"])
        daily_trades = len([t for t in trades if t.trade_type == "DAILY"])
        total_pnl = sum(t.pnl for t in trades)

        log.info("")
        log.info("=" * 60)
        log.info("  SESSION SUMMARY")
        log.info("=" * 60)
        log.info(f"  Ticks:       {self.state.tick_count}")
        log.info(f"  Trades:      {total} (RSI: {rsi_trades}, Daily: {daily_trades})")
        log.info(f"  Wins:        {wins}/{total}")
        log.info(f"  Net P&L:     ${total_pnl:+,.2f}")

        if trades:
            log.info("")
            for i, t in enumerate(trades, 1):
                log.info(f"  {i}. [{t.trade_type}] ${t.entry_price:,.2f} -> ${t.exit_price:,.2f} "
                         f"P&L=${t.pnl:+,.2f} ({t.pnl_pct:+.2f}%) {t.exit_reason}")

        log.info("=" * 60)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()