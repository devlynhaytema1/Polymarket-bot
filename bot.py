"""
 Polymarket Copy Trading Bot
 Copies trades from multiple target wallets.
 Fixed trade size: $0.25 per trade.
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
    "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82",
    "0x89b5cdaaa4866c1e738406712012a630b4078beb",
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
)
    key=PRIVATE_KEY,
    chain_id=POLYGON,
 seen_trade_ids: set = set()
 consecutive_errors: dict = {w: 0 for w in TARGET_WALLETS}
 def get_recent_trades(wallet: str) -> list:
    """Fetch recent trades for a wallet using the Polymarket data API."""
    endpoints = [
        f"{DATA_API}/trades?maker={wallet}&limit=10",
        f"{DATA_API}/activity?user={wallet}&limit=10",
    ]
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                log.warning("Rate limited. Backing off 10s...")
                time.sleep(10)
                return []
            if resp.status_code != 200:
                continue
            data = resp.json()
            if isinstance(data, list):
                consecutive_errors[wallet] = 0
                return data
            elif isinstance(data, dict):
                for key in ("trades", "activity", "data", "results"):
                    if key in data and isinstance(data[key], list):
                        consecutive_errors[wallet] = 0
                        return data[key]
        except requests.exceptions.Timeout:
            consecutive_errors[wallet] += 1
            log.warning(f"Timeout for {wallet[:8]}... (error #{consecutive_errors[wallet]})")
        except Exception as e:
            consecutive_errors[wallet] += 1
            log.error(f"Error fetching trades for {wallet[:8]}...: {e}")
    return []
def extract_trade_fields(trade: dict):
    """Normalize a trade object into the fields we need."""
    token_id = (
        trade.get("asset_id")
        or trade.get("tokenId")
        or trade.get("token_id")
        or trade.get("outcomeIndex")
    )
    side = (
        trade.get("side")
        or trade.get("type")
        or trade.get("tradeType")
        or trade.get("outcome")
    )
    if side:
        side = str(side).upper()
        if side in ("BUY", "LONG", "YES"):
            side = "BUY"
        elif side in ("SELL", "SHORT", "NO"):
            side = "SELL"
        else:
            side = None
    price = (
        trade.get("price")
        or trade.get("avgPrice")
        or trade.get("executionPrice")
    )
    market = (
        trade.get("market")
        or trade.get("conditionId")
        or trade.get("marketId")
        or trade.get("condition_id")
        or "unknown"
    )
    trade_id = (
        trade.get("id")
        or trade.get("tradeId")
        or trade.get("transactionHash")
        or trade.get("txHash")
    )
    if not all([token_id, side, price, trade_id]):
        return None
    try:
        price = float(price)
    except (ValueError, TypeError):
        return None
    return {
        "trade_id": str(trade_id),
        "market":   market,
        "token_id": str(token_id),
        "side":     side,
        "price":    price,
    }
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
    """Evaluate and copy a single trade if it is new."""
    fields = extract_trade_fields(trade)
    if not fields:
        return
    trade_id = fields["trade_id"]
    if trade_id in seen_trade_ids:
        return
    seen_trade_ids.add(trade_id)
    log.info(
        f"New trade from {source_wallet[:8]}... "
        f"| Market: {fields['market']} "
        f"| Side: {fields['side']} "
        f"| Price: {fields['price']}"
    )
    place_order(fields["market"], fields["token_id"], fields["side"], fields["price"])
 def copy_trades():
    """Poll each target wallet and copy any new trades."""
    for wallet in TARGET_WALLETS:
        if consecutive_errors[wallet] >= MAX_CONSECUTIVE_ERRORS:
            log.warning(f"{wallet[:8]}... has too many errors — skipping cycle.")
            continue
        trades = get_recent_trades(wallet)
        for trade in trades:
            fields = extract_trade_fields(trade)
            if fields and fields["trade_id"] not in seen_trade_ids:
                process_trade(trade, wallet)
        time.sleep(WALLET_DELAY)
 def seed_seen_trades():
    """Mark all existing trades as seen on startup."""
    log.info("Seeding seen trades to avoid copying historical activity...")
    for wallet in TARGET_WALLETS:
        trades = get_recent_trades(wallet)
        for trade in trades:
            fields = extract_trade_fields(trade)
            if fields:
                seen_trade_ids.add(fields["trade_id"])
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
