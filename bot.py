"""
Polymarket Copy Trading Bot

Copies trades from multiple target wallets.
Fixed trade size: $0.50 per trade.
Risk mode: Copy everything (no filters).
Poll interval: 3 seconds.
"""

import os
import time
import logging
import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.environ["PRIVATE_KEY"]

TARGET_WALLETS = [
    "0x3a847382ad6fff9be1db4e073fd9b869f6884d44",
    "0xe1d6b51521bd4365769199f392f9818661bd907c",
]

TRADE_SIZE_USD         = 0.50
POLL_INTERVAL          = 3
WALLET_DELAY           = 0.5
REQUEST_TIMEOUT        = 5
MAX_CONSECUTIVE_ERRORS = 10
CLOB_HOST              = "https://clob.polymarket.com"
DATA_API               = "https://data-api.polymarket.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

client = ClobClient(
    host=CLOB_HOST,
    key=PRIVATE_KEY,
    chain_id=POLYGON,
)

seen_trade_ids: set = set()
consecutive_errors: dict = {w: 0 for w in TARGET_WALLETS}

def get_recent_trades(wallet: str) -> list:
    """Fetch the 10 most recent trades for a given wallet."""
    try:
        url = f"{DATA_API}/activity"
        params = {"user": wallet, "limit": 10}
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

        if resp.status_code == 429:
            log.warning("Rate limited by Polymarket API. Backing off 10s...")
            time.sleep(10)
            return []

        resp.raise_for_status()
        consecutive_errors[wallet] = 0
        return resp.json()

    except requests.exceptions.Timeout:
        consecutive_errors[wallet] += 1
        log.warning(f"Request timed out for {wallet[:8]}... (error #{consecutive_errors[wallet]})")
        return []
    except Exception as e:
        consecutive_errors[wallet] += 1
        log.error(f"Failed to fetch trades for {wallet[:8]}...: {e} (error #{consecutive_errors[wallet]})")
        return []

def place_order(market: str, token_id: str, side: str, price: float) -> bool:
    """Place a $0.50 order on the given market."""
    try:
        if price <= 0 or price >= 1:
            log.warning(f"Skipping trade — unusual price: {price}")
            return False

        size = round(TRADE_SIZE_USD / price, 4)

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )

        signed_order = client.create_order(order_args)
        response = client.post_order(signed_order, OrderType.GTC)

        log.info(
            f"Order placed | Market: {market} | Side: {side} "
            f"| Price: {price} | Size: {size} shares (${TRADE_SIZE_USD})"
        )
        log.info(f"Response: {response}")
        return True

    except Exception as e:
        log.error(f"Failed to place order on {market}: {e}")
        return False

def process_trade(trade: dict, source_wallet: str):
    """Evaluate a single trade and copy it if its new."""
    trade_id = trade.get("id") or trade.get("transactionHash")

    if not trade_id or trade_id in seen_trade_ids:
        return

    seen_trade_ids.add(trade_id)

    market   = trade.get("market", "unknown")
    token_id = trade.get("asset_id") or trade.get("tokenId")
    side     = trade.get("side", "").upper()
    price    = trade.get("price")

    if not all([token_id, side, price]):
        log.warning(f"Skipping incomplete trade data: {trade}")
        return

    try:
        price = float(price)
    except (ValueError, TypeError):
        log.warning(f"Skipping trade — invalid price: {price}")
        return

    log.info(
        f"New trade from {source_wallet[:8]}... "
        f"| Market: {market} | Side: {side} | Price: {price}"
    )

    place_order(market, token_id, side, price)

def copy_trades():
    """Poll each target wallet and copy any new trades."""
    for wallet in TARGET_WALLETS:

        if consecutive_errors[wallet] >= MAX_CONSECUTIVE_ERRORS:
            log.warning(
                f"{wallet[:8]}... has {MAX_CONSECUTIVE_ERRORS} consecutive errors — "
                f"skipping this cycle."
            )
            continue

        trades = get_recent_trades(wallet)

        for trade in trades:
            trade_id = trade.get("id") or trade.get("transactionHash")
            if trade_id and trade_id not in seen_trade_ids:
                process_trade(trade, wallet)

        time.sleep(WALLET_DELAY)

def seed_seen_trades():
    """Mark all existing trades as seen on startup."""
    log.info("Seeding seen trades to avoid copying historical activity...")
    for wallet in TARGET_WALLETS:
        trades = get_recent_trades(wallet)
        for trade in trades:
            trade_id = trade.get("id") or trade.get("transactionHash")
            if trade_id:
                seen_trade_ids.add(trade_id)
        time.sleep(WALLET_DELAY)
    log.info(f"Seeded {len(seen_trade_ids)} existing trade ID(s).")

if __name__ == "__main__":
    log.info("Polymarket Copy Bot starting up...")
    log.info(f"Watching wallets: {[w[:8] + '...' for w in TARGET_WALLETS]}")
    log.info(f"Fixed trade size: ${TRADE_SIZE_USD}")
    log.info(f"Poll interval: {POLL_INTERVAL}s")

    seed_seen_trades()

    log.info(f"Bot running. Polling every {POLL_INTERVAL} seconds.")

    while True:
        start = time.time()
        copy_trades()
        elapsed = time.time() - start
        sleep_time = max(0, POLL_INTERVAL - elapsed)
        time.sleep(sleep_time)
