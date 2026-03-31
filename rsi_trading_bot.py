"""
Roostoo PAXG/USDT — One-Shot RSI Bot
======================================
1. Collect 20 price ticks to compute RSI(14)
2. If RSI < 50  -> BUY PAXG with 10% of free USDT (market order)
3. Poll price every 10s; sell via market order when:
     profit >= +0.8%  (take profit)
     loss   >= -1.0%  (stop loss)
4. Script exits.
"""

import time
import hmac
import hashlib
import requests

# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────
API_KEY    = "pZZ9zVTcKdin9QtSpq4slzYtUDFDf1j1OSCh503YO2UyADi1uKl2y5zAyvFmKAkf"
SECRET_KEY = "l8Zb6ebZi2RZcXZE6XCJUg8qOdHgb3sStveWr7Nj96MS8MteMyWSWG5Cku570Qk2"

BASE_URL    = "https://mock-api.roostoo.com"
PAIR        = "PAXG/USDT"
CAPITAL_PCT = 0.10       # 10% of free USDT
TAKE_PROFIT = 0.008      # +0.8%
STOP_LOSS   = 0.010      # -1.0%

RSI_PERIOD    = 14
RSI_THRESHOLD = 50

TICK_COUNT    = 20
TICK_INTERVAL = 10        # seconds between ticks
POLL_INTERVAL = 10        # seconds between PnL checks


# ═══════════════════════════════════════════════════════════════
#  Roostoo API helpers
# ═══════════════════════════════════════════════════════════════

def _timestamp():
    return str(int(time.time() * 1000))


def _sign(payload: dict):
    # FIX 1: work on a copy so the caller's dict is never mutated
    payload = dict(payload)
    payload["timestamp"] = _timestamp()
    sorted_keys  = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)
    sig = hmac.new(
        SECRET_KEY.encode("utf-8"),
        total_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {"RST-API-KEY": API_KEY, "MSG-SIGNATURE": sig}
    return headers, total_params


def get_ticker(pair: str):
    """Return LastPrice (float) or None on any failure."""
    try:
        r = requests.get(
            f"{BASE_URL}/v3/ticker",
            params={"timestamp": _timestamp(), "pair": pair},
            timeout=10,
        )
        r.raise_for_status()   # FIX 2: was missing — HTTP errors silently returned bad JSON
        data = r.json()
        if data.get("Success") and pair in data.get("Data", {}):
            return float(data["Data"][pair]["LastPrice"])
        print(f"  [ticker] API error: {data.get('ErrMsg')}")
    except Exception as e:
        print(f"  [ticker] Request failed: {e}")
    return None


def get_balance():
    """Return wallet dict or None."""
    try:
        headers, total_params = _sign({})
        # FIX 3: _sign returns an encoded string; GET needs a proper dict for params
        params = dict(item.split("=", 1) for item in total_params.split("&"))
        r = requests.get(
            f"{BASE_URL}/v3/balance",
            headers=headers,
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("Success"):
            return data["Wallet"]
        print(f"  [balance] API error: {data.get('ErrMsg')}")
    except Exception as e:
        print(f"  [balance] Request failed: {e}")
    return None


def place_market_order(pair, side, quantity):
    """Place a MARKET order. Returns OrderDetail dict or None."""
    try:
        url = f"{BASE_URL}/v3/place_order"
        payload = {
            "pair":     pair,
            "side":     side.upper(),
            "type":     "MARKET",
            "quantity": str(quantity),
        }
        headers, total_params = _sign(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        r    = requests.post(url, headers=headers, data=total_params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("Success"):
            return data["OrderDetail"]
        print(f"  [order] API error: {data.get('ErrMsg')}")
    except Exception as e:
        print(f"  [order] Request failed: {e}")
    return None


def sell_with_retry(pair, quantity, retries=3):
    """
    FIX 4: original broke out of the monitor loop even when SELL failed,
    leaving an unprotected open position. Now retries up to 3 times.
    Returns True only on confirmed success.
    """
    for attempt in range(1, retries + 1):
        print(f"  Sell attempt {attempt}/{retries}...")
        result = place_market_order(pair, "SELL", quantity)
        if result is not None:
            return True
        if attempt < retries:
            time.sleep(3)
    return False


# ═══════════════════════════════════════════════════════════════
#  RSI — Wilder's smoothing
# ═══════════════════════════════════════════════════════════════

def compute_rsi(prices: list, period: int = 14):
    if len(prices) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print(f"  Roostoo One-Shot Bot  |  {PAIR}")
    print(f"  RSI < {RSI_THRESHOLD}  |  TP +{TAKE_PROFIT*100:.1f}%  |  SL -{STOP_LOSS*100:.1f}%")
    print("=" * 55)

    # ── Step 1: Collect price ticks for RSI ──────────────────
    print(f"\n[1/4] Collecting {TICK_COUNT} ticks (~{TICK_COUNT * TICK_INTERVAL}s)...")
    prices = []
    for i in range(TICK_COUNT):
        price = get_ticker(PAIR)
        if price is None:
            print("  Failed to get price. Aborting.")
            return
        prices.append(price)
        print(f"  Tick {i+1:02d}/{TICK_COUNT}  price={price:.4f}")
        if i < TICK_COUNT - 1:
            time.sleep(TICK_INTERVAL)

    # ── Step 2: Evaluate RSI ──────────────────────────────────
    rsi = compute_rsi(prices, RSI_PERIOD)
    if rsi is None:
        print("  Not enough data for RSI. Aborting.")
        return

    print(f"\n[2/4] RSI(14) = {rsi:.2f}")
    if rsi >= RSI_THRESHOLD:
        print(f"  RSI {rsi:.2f} >= {RSI_THRESHOLD} — no entry. Exiting.")
        return
    print(f"  RSI {rsi:.2f} < {RSI_THRESHOLD} — entry signal confirmed!")

    # ── Step 3: Size and place the BUY ───────────────────────
    print("\n[3/4] Placing BUY order...")

    wallet = get_balance()
    if wallet is None:
        print("  Could not fetch balance. Aborting.")
        return

    free_usdt = 0.0
    for key in ("USDT", "USD"):
        if key in wallet:
            free_usdt = float(wallet[key].get("Free", 0))
            print(f"  Free balance: {free_usdt:.2f} {key}")
            break

    if free_usdt < 1.0:
        print(f"  Insufficient balance ({free_usdt:.2f}). Aborting.")
        return

    last_price = prices[-1]
    capital    = free_usdt * CAPITAL_PCT
    quantity   = round(capital / last_price, 6)

    if quantity <= 0:
        print("  Computed quantity is zero. Aborting.")
        return

    print(f"  Using {capital:.2f} USDT -> buying {quantity} PAXG at ~{last_price:.4f}")

    buy = place_market_order(PAIR, "BUY", quantity)
    if buy is None:
        print("  BUY order failed. Aborting.")
        return

    # FIX 5: FilledAverPrice can be 0 on the mock even for filled orders
    filled_price = buy.get("FilledAverPrice")
    entry = float(filled_price) if filled_price and float(filled_price) > 0 else last_price
    print(f"  BUY confirmed | entry = {entry:.4f}")

    # ── Step 4: Monitor PnL ──────────────────────────────────
    print(f"\n[4/4] Monitoring PnL every {POLL_INTERVAL}s...")
    print(f"  Entry={entry:.4f}  TP>={entry*(1+TAKE_PROFIT):.4f}  SL<={entry*(1-STOP_LOSS):.4f}")

    consecutive_errors = 0
    MAX_ERRORS = 5

    while True:
        time.sleep(POLL_INTERVAL)

        current_price = get_ticker(PAIR)

        if current_price is None:
            consecutive_errors += 1
            print(f"  Price fetch failed ({consecutive_errors}/{MAX_ERRORS})...")
            if consecutive_errors >= MAX_ERRORS:
                # FIX 6: if price is unreachable we are flying blind — emergency sell
                print("  Too many errors — emergency SELL to protect position!")
                if sell_with_retry(PAIR, quantity):
                    print("  Emergency SELL executed.")
                else:
                    print("  !! Emergency SELL FAILED — close position manually !!")
                break
            continue

        consecutive_errors = 0
        pnl_pct = (current_price - entry) / entry * 100
        print(f"  Price={current_price:.4f}  PnL={pnl_pct:+.3f}%")

        if pnl_pct >= TAKE_PROFIT * 100:
            print(f"\n  TAKE PROFIT hit ({pnl_pct:+.3f}%) — selling...")
            if sell_with_retry(PAIR, quantity):
                print(f"  SELL confirmed. Closed with +{pnl_pct:.3f}% profit.")
            else:
                print("  !! SELL FAILED after retries — close position manually !!")
            break

        if pnl_pct <= -(STOP_LOSS * 100):
            print(f"\n  STOP LOSS hit ({pnl_pct:+.3f}%) — selling...")
            if sell_with_retry(PAIR, quantity):
                print(f"  SELL confirmed. Closed with {pnl_pct:.3f}% loss.")
            else:
                print("  !! SELL FAILED after retries — close position manually !!")
            break

    print("\n" + "=" * 55)
    print("  Bot finished.")
    print("=" * 55)


if __name__ == "__main__":
    main()