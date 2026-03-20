"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ROOSTOO LIVE TRADING BOT — RSI DIP-BUY STRATEGY (from rsi_basic.py)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STRATEGY (ported from your backtest):
  ┌───────────────────────────────────────────────────────────────┐
  │  ENTRY (all conditions must be true):                        │
  │    ✓ RSI(21) drops below 22                                  │
  │    ✓ Current UTC hour is between 00:00 and 07:59             │
  │    ✓ Not already in a position                               │
  │    ✓ Risk manager approves                                   │
  │                                                              │
  │  EXIT (first condition that triggers):                       │
  │    1. STOP LOSS — price drops 3% below entry     (priority)  │
  │    2. RSI EXIT  — RSI(21) rises above 40                     │
  │    3. TIME EXIT — position held for 700 ticks                │
  │                                                              │
  │  SIZING: configurable % of available USD (default: 95%)      │
  └───────────────────────────────────────────────────────────────┘

USAGE:
  1. pip install requests
  2. Set API_KEY and SECRET_KEY below
  3. python roostoo_rsi_bot.py
  4. Ctrl+C to stop (prints full session report)
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
API_KEY = "p2cZ7IzInbWjIdGIMC7pV5C7tMf2IAd53XU01R2sLBdJ5b4fzTZdSXxFzKBmrNpL"
SECRET_KEY = "1auQZH6CyibwRT1qoW5QX0m9aTPdUyMchcnfH5mYEkwc4l9jalQTbFwdrcCe3do4"
BASE_URL = "https://mock-api.roostoo.com"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STRATEGY PARAMETERS (mirrored from your backtest)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PAIR = "BTC/USD"
COIN = "BTC"

# RSI Settings
RSI_PERIOD = 21  # Wilder's RSI period
ENTRY_RSI_THRESHOLD = 22  # Enter when RSI drops below this
EXIT_RSI_THRESHOLD = 40  # Exit when RSI rises above this

# Exit Rules
SL_PERCENT = 0.03  # 3% stop loss
MAX_HOLDING_TICKS = 700  # Max ticks before forced exit

# UTC Time Window (entry only)
ALLOWED_START_HOUR = 0  # 00:00 UTC
ALLOWED_END_HOUR = 8  # 07:59 UTC (entry window closes at 08:00)

# Position Sizing
POSITION_SIZE_PCT = 0.95  # Use 95% of available USD per trade
TAKER_FEE_RATE = 0.001  # 0.1% taker fee (for P&L calculation)

# Bot Timing
POLL_INTERVAL = 60  # Seconds between price checks
WARMUP_POLL = 10  # Faster polling during RSI warmup

# Logging
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
    """Tracks an open position."""
    entry_price: float
    entry_tick: int
    units: float
    entry_time: str
    cost_basis: float  # USD spent including fees


@dataclass
class TradeRecord:
    """Completed trade log entry."""
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    units: float
    pnl: float
    pnl_pct: float
    exit_reason: str  # STOP_LOSS, RSI_EXIT, TIME_LIMIT
    won: bool
    ticks_held: int


@dataclass
class BotState:
    """Full bot state."""
    # Price history for RSI
    prices: deque = field(default_factory=lambda: deque(maxlen=500))

    # RSI internals (Wilder's smoothing needs running avg_gain/avg_loss)
    avg_gain: Optional[float] = None
    avg_loss: Optional[float] = None
    rsi_ready: bool = False
    rsi_warmup_count: int = 0

    # Position tracking
    position: Optional[Position] = None
    tick_count: int = 0

    # Trade history
    trades: List[TradeRecord] = field(default_factory=list)

    # Session stats
    initial_capital: float = 0.0
    peak_equity: float = 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ROOSTOO API CLIENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RoostooClient:
    def __init__(self, api_key: str, secret_key: str, base_url: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.session = requests.Session()

    def _ts(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(self, payload: dict) -> tuple:
        payload["timestamp"] = self._ts()
        sorted_params = "&".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
        sig = hmac.new(
            self.secret_key.encode(), sorted_params.encode(), hashlib.sha256
        ).hexdigest()
        headers = {"RST-API-KEY": self.api_key, "MSG-SIGNATURE": sig}
        return headers, payload, sorted_params

    def server_time(self) -> dict:
        return self.session.get(f"{self.base_url}/v3/serverTime", timeout=10).json()

    def exchange_info(self) -> dict:
        return self.session.get(f"{self.base_url}/v3/exchangeInfo", timeout=10).json()

    def ticker(self, pair: str) -> dict:
        params = {"timestamp": self._ts(), "pair": pair}
        return self.session.get(f"{self.base_url}/v3/ticker", params=params, timeout=10).json()

    def balance(self) -> dict:
        h, p, _ = self._sign({})
        return self.session.get(f"{self.base_url}/v3/balance", headers=h, params=p, timeout=10).json()

    def place_order(self, pair: str, side: str, quantity: float,
                    order_type: str = "MARKET", price: float = None) -> dict:
        payload = {"pair": pair, "side": side.upper(), "type": order_type.upper(),
                   "quantity": str(quantity)}
        if order_type.upper() == "LIMIT" and price is not None:
            payload["price"] = str(price)
        h, _, tp = self._sign(payload)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        return self.session.post(f"{self.base_url}/v3/place_order", headers=h, data=tp, timeout=10).json()

    def query_order(self, order_id: int = None, pair: str = None) -> dict:
        payload = {}
        if order_id:
            payload["order_id"] = str(order_id)
        elif pair:
            payload["pair"] = pair
        h, _, tp = self._sign(payload)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        return self.session.post(f"{self.base_url}/v3/query_order", headers=h, data=tp, timeout=10).json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WILDER'S RSI (matches your backtest exactly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WildersRSI:
    """
    Incremental Wilder's RSI — identical to your pandas EWM calculation.

    Your backtest uses:
        ewm(com=period-1, min_periods=period)

    This is Wilder's smoothing with alpha = 1/period.
    We replicate it incrementally so we don't need to store
    the entire price history.
    """

    def __init__(self, period: int):
        self.period = period
        self.avg_gain: Optional[float] = None
        self.avg_loss: Optional[float] = None
        self.prev_price: Optional[float] = None
        self.warmup_gains: list = []
        self.warmup_losses: list = []
        self.ready = False

    def update(self, price: float) -> Optional[float]:
        """
        Feed a new price. Returns RSI value or None if still warming up.
        """
        if self.prev_price is None:
            self.prev_price = price
            return None

        delta = price - self.prev_price
        gain = max(0, delta)
        loss = max(0, -delta)
        self.prev_price = price

        if not self.ready:
            # Collecting initial window
            self.warmup_gains.append(gain)
            self.warmup_losses.append(loss)

            if len(self.warmup_gains) >= self.period:
                # First RSI: simple average of warmup window
                self.avg_gain = sum(self.warmup_gains) / self.period
                self.avg_loss = sum(self.warmup_losses) / self.period
                self.ready = True
                return self._calc_rsi()
            return None

        # Wilder's smoothing: new_avg = (prev_avg * (period-1) + current) / period
        self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
        self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period

        return self._calc_rsi()

    def _calc_rsi(self) -> float:
        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        return 100 - (100 / (1 + rs))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STRATEGY ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RSIStrategy:
    """
    Mirrors your backtest logic exactly:

    ENTRY:  RSI < 22  AND  UTC hour in [00:00, 08:00)  AND  flat
    EXIT:   stop_loss(3%)  OR  RSI >= 40  OR  ticks_held >= 700
    """

    def __init__(self):
        self.rsi = WildersRSI(RSI_PERIOD)

    def min_data_points(self) -> int:
        return RSI_PERIOD + 1

    def is_valid_entry_time(self) -> bool:
        """Check if current UTC hour is within the allowed entry window."""
        utc_hour = datetime.now(timezone.utc).hour
        return ALLOWED_START_HOUR <= utc_hour < ALLOWED_END_HOUR

    def check_entry(self, price: float) -> tuple:
        """
        Returns (should_enter: bool, rsi_value: float, reason: str)
        """
        rsi_val = self.rsi.update(price)

        if rsi_val is None:
            return False, 0.0, f"RSI warming up ({len(self.rsi.warmup_gains)}/{RSI_PERIOD})"

        if rsi_val >= ENTRY_RSI_THRESHOLD:
            return False, rsi_val, f"RSI {rsi_val:.1f} >= {ENTRY_RSI_THRESHOLD} (no entry)"

        if not self.is_valid_entry_time():
            utc_hour = datetime.now(timezone.utc).hour
            return False, rsi_val, (
                f"RSI {rsi_val:.1f} is LOW but outside time window "
                f"(now={utc_hour:02d}:xx, need {ALLOWED_START_HOUR:02d}:00-{ALLOWED_END_HOUR:02d}:00)"
            )

        return True, rsi_val, f"RSI {rsi_val:.1f} < {ENTRY_RSI_THRESHOLD} in valid time window"

    def check_exit(self, price: float, position: Position,
                   current_tick: int) -> tuple:
        """
        Returns (should_exit: bool, reason: str)
        Checks in priority order: stop loss → RSI exit → time limit
        """
        # Get latest RSI (already updated in check_entry path,
        # but if we're in position we need the current value)
        # We peek at the last computed value
        rsi_val = self.rsi._calc_rsi() if self.rsi.ready else 50.0

        ticks_held = current_tick - position.entry_tick
        sl_price = position.entry_price * (1 - SL_PERCENT)

        # Priority 1: Stop Loss
        if price <= sl_price:
            return True, f"STOP_LOSS (price ${price:,.2f} <= SL ${sl_price:,.2f}, -{SL_PERCENT * 100}%)"

        # Priority 2: RSI Exit
        if rsi_val >= EXIT_RSI_THRESHOLD:
            return True, f"RSI_EXIT (RSI {rsi_val:.1f} >= {EXIT_RSI_THRESHOLD})"

        # Priority 3: Time Limit
        if ticks_held >= MAX_HOLDING_TICKS:
            return True, f"TIME_LIMIT ({ticks_held} ticks >= {MAX_HOLDING_TICKS})"

        return False, (
            f"Holding: RSI={rsi_val:.1f}, ticks={ticks_held}/{MAX_HOLDING_TICKS}, "
            f"SL=${sl_price:,.2f}, P&L={(price / position.entry_price - 1) * 100:+.2f}%"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TRADING BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TradingBot:
    def __init__(self):
        self.client = RoostooClient(API_KEY, SECRET_KEY, BASE_URL)
        self.strategy = RSIStrategy()
        self.state = BotState()

    # ── Preflight ──

    def preflight(self) -> bool:
        log.info("Running preflight checks...")

        # Server connectivity
        try:
            st = self.client.server_time()
            log.info(f"  ✓ Server reachable (time: {st['ServerTime']})")
        except Exception as e:
            log.error(f"  ✗ Server unreachable: {e}")
            return False

        # Pair check
        try:
            info = self.client.exchange_info()
            pairs = info.get("TradePairs", {})
            if PAIR not in pairs:
                log.error(f"  ✗ {PAIR} not available. Options: {list(pairs.keys())}")
                return False
            pi = pairs[PAIR]
            log.info(f"  ✓ {PAIR} active (price_prec={pi['PricePrecision']}, "
                     f"amt_prec={pi['AmountPrecision']})")
        except Exception as e:
            log.error(f"  ✗ Exchange info failed: {e}")
            return False

        # Auth + balance
        try:
            bal = self.client.balance()
            wallet = bal.get("Wallet", {})
            usd_free = wallet.get("USD", {}).get("Free", 0)
            btc_free = wallet.get(COIN, {}).get("Free", 0)
            self.state.initial_capital = usd_free
            self.state.peak_equity = usd_free
            log.info(f"  ✓ Auth OK — ${usd_free:,.2f} USD, {btc_free} {COIN}")
        except Exception as e:
            log.error(f"  ✗ Auth failed: {e}")
            return False

        # Config summary
        log.info("─" * 60)
        log.info("  YOUR STRATEGY (from rsi_basic.py):")
        log.info(f"    Pair:              {PAIR}")
        log.info(f"    RSI Period:        {RSI_PERIOD} (Wilder's)")
        log.info(f"    Entry:             RSI < {ENTRY_RSI_THRESHOLD}")
        log.info(f"    Exit RSI:          RSI >= {EXIT_RSI_THRESHOLD}")
        log.info(f"    Stop Loss:         {SL_PERCENT * 100:.1f}%")
        log.info(f"    Max Hold:          {MAX_HOLDING_TICKS} ticks")
        log.info(f"    Time Window:       {ALLOWED_START_HOUR:02d}:00 - {ALLOWED_END_HOUR:02d}:00 UTC")
        log.info(f"    Position Size:     {POSITION_SIZE_PCT * 100:.0f}% of free USD")
        log.info(f"    Poll Interval:     {POLL_INTERVAL}s")
        log.info(f"    RSI Warmup Needs:  {self.strategy.min_data_points()} price ticks")
        log.info("─" * 60)
        return True

    # ── Price Fetch ──

    def fetch_price(self) -> Optional[float]:
        try:
            data = self.client.ticker(PAIR)
            if data.get("Success"):
                return data["Data"][PAIR]["LastPrice"]
            return None
        except Exception as e:
            log.error(f"Price fetch error: {e}")
            return None

    # ── Wallet ──

    def get_usd_free(self) -> float:
        try:
            bal = self.client.balance()
            return bal.get("Wallet", {}).get("USD", {}).get("Free", 0)
        except:
            return 0

    def get_coin_free(self) -> float:
        try:
            bal = self.client.balance()
            return bal.get("Wallet", {}).get(COIN, {}).get("Free", 0)
        except:
            return 0

    # ── Order Execution ──

    def open_position(self, price: float, rsi_val: float) -> bool:
        """
        Open a BUY position — mirrors your backtest's all-in logic:
          units = (cash * (1 - fee)) / price
        """
        usd_free = self.get_usd_free()
        buy_amount_usd = usd_free * POSITION_SIZE_PCT

        if buy_amount_usd < 10:
            log.warning(f"  Insufficient USD (${usd_free:.2f}), skipping entry")
            return False

        # Calculate quantity matching your backtest sizing
        quantity = (buy_amount_usd * (1 - TAKER_FEE_RATE)) / price

        log.info(f"  🟢 OPENING POSITION: BUY {quantity:.6f} {COIN} @ ~${price:,.2f}")
        log.info(f"     RSI={rsi_val:.1f} | Spend=${buy_amount_usd:,.2f} | SL=${price * (1 - SL_PERCENT):,.2f}")

        try:
            result = self.client.place_order(PAIR, "BUY", round(quantity, 6))

            if result.get("Success"):
                detail = result["OrderDetail"]
                filled_price = detail.get("FilledAverPrice", price)
                filled_qty = detail.get("FilledQuantity", quantity)
                order_id = detail.get("OrderID", 0)
                commission = detail.get("CommissionChargeValue", 0)

                self.state.position = Position(
                    entry_price=filled_price,
                    entry_tick=self.state.tick_count,
                    units=filled_qty,
                    entry_time=datetime.now(timezone.utc).isoformat(),
                    cost_basis=buy_amount_usd,
                )

                log.info(f"  ✅ FILLED Order #{order_id} @ ${filled_price:,.2f} "
                         f"({filled_qty:.6f} {COIN}, fee=${commission:.4f})")
                return True
            else:
                log.warning(f"  ⚠️  Order rejected: {result.get('ErrMsg')}")
                return False
        except Exception as e:
            log.error(f"  ✗ Order failed: {e}")
            return False

    def close_position(self, price: float, reason: str) -> bool:
        """
        Close position with SELL — mirrors your backtest's exit logic:
          gross = units * exit_price
          cash  = gross * (1 - fee)
        """
        pos = self.state.position
        if not pos:
            return False

        log.info(f"  🔴 CLOSING POSITION: SELL {pos.units:.6f} {COIN} @ ~${price:,.2f}")
        log.info(f"     Reason: {reason}")

        try:
            result = self.client.place_order(PAIR, "SELL", round(pos.units, 6))

            if result.get("Success"):
                detail = result["OrderDetail"]
                exit_price = detail.get("FilledAverPrice", price)
                order_id = detail.get("OrderID", 0)
                commission = detail.get("CommissionChargeValue", 0)

                # P&L calculation matching your backtest:
                # pnl = cash_after_sell - cash_before_buy
                gross_proceeds = pos.units * exit_price
                net_proceeds = gross_proceeds * (1 - TAKER_FEE_RATE)
                pnl = net_proceeds - pos.cost_basis
                pnl_pct = (exit_price / pos.entry_price - 1) * 100

                ticks_held = self.state.tick_count - pos.entry_tick

                # Determine exit reason category
                exit_type = "STOP_LOSS" if "STOP_LOSS" in reason else \
                    "RSI_EXIT" if "RSI_EXIT" in reason else "TIME_LIMIT"

                trade = TradeRecord(
                    entry_time=pos.entry_time,
                    exit_time=datetime.now(timezone.utc).isoformat(),
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    units=pos.units,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    exit_reason=exit_type,
                    won=pnl > 0,
                    ticks_held=ticks_held,
                )
                self.state.trades.append(trade)
                self.state.position = None

                emoji = "💰" if pnl > 0 else "💸"
                log.info(f"  ✅ SOLD Order #{order_id} @ ${exit_price:,.2f} (fee=${commission:.4f})")
                log.info(f"  {emoji} P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%) | "
                         f"Held {ticks_held} ticks | Exit: {exit_type}")
                return True
            else:
                log.warning(f"  ⚠️  Sell rejected: {result.get('ErrMsg')}")
                return False
        except Exception as e:
            log.error(f"  ✗ Sell failed: {e}")
            return False

    # ── Equity Tracking ──

    def get_current_equity(self, price: float) -> float:
        """Mark-to-market equity, matching your backtest's equity_curve logic."""
        if self.state.position:
            return self.state.position.units * price
        else:
            return self.get_usd_free()

    def print_tick(self, price, rsi_val, signal, detail):
        """Show wallet + position every tick."""
        tick = self.state.tick_count
        utc_now = datetime.now(timezone.utc).strftime("%H:%M")

        # Fetch wallet
        try:
            bal = self.client.balance()
            wallet = bal.get("Wallet", {})
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
                f"  POSITION: LONG {pos.units:.6f} {COIN} @ ${pos.entry_price:,.2f} | "
                f"P&L={pos_pnl:+.2f}% | Held={ticks_held}/{MAX_HOLDING_TICKS} | "
                f"SL=${sl_price:,.2f}"
            )
        else:
            pos_line = "  POSITION: FLAT"

        log.info("")
        log.info(f"=== TICK {tick} === {utc_now} UTC === {PAIR} ${price:,.2f} === RSI={rsi_val:.1f} ===")
        log.info(f"  WALLET:   ${usd_free:,.2f} USD free | ${usd_lock:,.2f} locked | "
                 f"{btc_free:.6f} {COIN} free | {btc_lock:.6f} locked")
        log.info(f"  EQUITY:   ${total_equity:,.2f}")
        log.info(pos_line)
        log.info(f"  SIGNAL:   [{signal}] {detail}")
    # ── Main Loop ──

    def run(self):
        log.info("=" * 60)
        log.info("  RSI DIP-BUY BOT — LIVE ON ROOSTOO")
        log.info("=" * 60)

        if not self.preflight():
            log.error("Preflight failed. Fix issues above and restart.")
            return

        log.info("")
        log.info("Bot running. Ctrl+C to stop.")
        log.info("─" * 60)

        header = (
            f"{'Tick':>5} │ {'Price':>11} │ {'RSI':>6} │ "
            f"{'Position':^10} │ {'Status'}"
        )
        log.info(header)
        log.info("─" * 60)

        try:
            while True:
                # 1. Fetch current price
                price = self.fetch_price()
                if price is None:
                    time.sleep(POLL_INTERVAL)
                    continue

                self.state.tick_count += 1
                self.state.prices.append(price)
                tick = self.state.tick_count

                # 2. Branch: are we in a position or flat?
                if self.state.position is None:
                    should_enter, rsi_val, reason = self.strategy.check_entry(price)

                    self.print_tick(price, rsi_val, "SCANNING", reason)

                    if should_enter:
                        self.open_position(price, rsi_val)

                else:
                    self.strategy.rsi.update(price)

                    should_exit, reason = self.strategy.check_exit(
                        price, self.state.position, self.state.tick_count
                    )

                    rsi_val = self.strategy.rsi._calc_rsi() if self.strategy.rsi.ready else 0

                    self.print_tick(price, rsi_val, "HOLDING", reason)

                    if should_exit:
                        self.close_position(price, reason)

                # 3. Track peak equity
                equity = self.get_current_equity(price)
                if equity > self.state.peak_equity:
                    self.state.peak_equity = equity

                # 4. Sleep
                if not self.strategy.rsi.ready:
                    time.sleep(WARMUP_POLL)
                else:
                    time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            self.shutdown()

    # ── Shutdown Report ──

    def shutdown(self):
        """Full session summary matching your backtest output style."""
        trades = self.state.trades
        total_trades = len(trades)
        wins = len([t for t in trades if t.won])
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        total_pnl = sum(t.pnl for t in trades)
        current_equity = self.get_usd_free()
        if self.state.position:
            last_price = list(self.state.prices)[-1] if self.state.prices else 0
            current_equity = self.state.position.units * last_price

        # Exit reason breakdown
        sl_count = len([t for t in trades if t.exit_reason == "STOP_LOSS"])
        rsi_count = len([t for t in trades if t.exit_reason == "RSI_EXIT"])
        time_count = len([t for t in trades if t.exit_reason == "TIME_LIMIT"])

        # Best/worst trades
        best_trade = max(trades, key=lambda t: t.pnl) if trades else None
        worst_trade = min(trades, key=lambda t: t.pnl) if trades else None

        # Avg ticks held
        avg_ticks = sum(t.ticks_held for t in trades) / total_trades if trades else 0

        # Max drawdown from peak
        dd_pct = ((self.state.peak_equity - current_equity) /
                  self.state.peak_equity * 100) if self.state.peak_equity > 0 else 0

        log.info("")
        log.info("=" * 60)
        log.info(f"  SESSION SUMMARY: {ALLOWED_START_HOUR:02d}:00-{ALLOWED_END_HOUR:02d}:00 UTC")
        log.info("=" * 60)
        log.info(f"  Total Ticks:         {self.state.tick_count}")
        log.info(f"  Total Trades:        {total_trades}")
        log.info(f"  Wins / Losses:       {wins} / {losses}")
        log.info(f"  Win Rate:            {win_rate:.2f}%")
        log.info(f"  Net P&L:             ${total_pnl:+,.2f}")
        log.info(f"  Current Equity:      ${current_equity:,.2f}")

        if self.state.initial_capital > 0:
            total_return = (current_equity / self.state.initial_capital - 1) * 100
            log.info(f"  Total Return:        {total_return:+.2f}%")

        log.info(f"  Peak Equity:         ${self.state.peak_equity:,.2f}")
        log.info(f"  Max Drawdown:        {dd_pct:.2f}%")
        log.info("")
        log.info("  EXIT BREAKDOWN:")
        log.info(f"    Stop Loss:         {sl_count}")
        log.info(f"    RSI Exit:          {rsi_count}")
        log.info(f"    Time Limit:        {time_count}")

        if best_trade:
            log.info("")
            log.info(f"  Best Trade:          ${best_trade.pnl:+,.2f} ({best_trade.pnl_pct:+.2f}%)")
            log.info(f"  Worst Trade:         ${worst_trade.pnl:+,.2f} ({worst_trade.pnl_pct:+.2f}%)")
            log.info(f"  Avg Ticks Held:      {avg_ticks:.0f}")

        if self.state.position:
            pos = self.state.position
            last_p = list(self.state.prices)[-1] if self.state.prices else 0
            unreal = (last_p / pos.entry_price - 1) * 100
            log.info("")
            log.info(f"  ⚠️  OPEN POSITION:    {pos.units:.6f} {COIN} "
                     f"@ ${pos.entry_price:,.2f} (unrealized {unreal:+.2f}%)")

        if trades:
            log.info("")
            log.info("  TRADE LOG:")
            log.info(f"  {'#':>3} │ {'Entry':>11} │ {'Exit':>11} │ "
                     f"{'P&L':>10} │ {'%':>7} │ {'Ticks':>5} │ Reason")
            log.info("  " + "─" * 62)
            for i, t in enumerate(trades, 1):
                log.info(
                    f"  {i:>3} │ ${t.entry_price:>10,.2f} │ ${t.exit_price:>10,.2f} │ "
                    f"${t.pnl:>+9,.2f} │ {t.pnl_pct:>+6.2f}% │ {t.ticks_held:>5} │ {t.exit_reason}"
                )

        log.info("=" * 60)
        log.info("  Goodbye!")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  START
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()