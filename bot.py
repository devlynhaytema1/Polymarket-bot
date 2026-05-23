"""
Polymarket Copy Trading Bot
Copies trades from multiple target wallets.
Fixed trade size: 5 units per trade.
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
    "0x8c901f67b036b5eebab4e1f2f904b8676743a904",
    
]
PAPER_TRADING = True
TRADE_SIZE_SHARES      = 5
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
    funder=os.environ["POLYMARKET_ADDRESS"],
)
api_creds = client.create_or_derive_api_creds()
client.set_api_creds(api_creds)
seen_trade_ids: set = set()
consecutive_errors: dict = {w: 0 for w in TARGET_WALLETS}
def get_recent_trades(wallet: str) -> list:
    """Fetch recent trades for a wallet using the Polymarket data API."""
    endpoints = [
        f"{DATA_API}/trades?maker={wallet}&limit=25",
        f"{DATA_API}/trades?proxywallet={wallet}&limit=25",
        f"{DATA_API}/activity?user={wallet}&limit=25",
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
            import traceback
            log.error(f"failed to place orderon {market}: {e}")
            log.error(traceback.format_exc())
            return False
            consecutive_errors[wallet] += 1
            log.error(f"Error fetching trades for {wallet[:8]}...: {e}")
    return []
def extract_trade_fields(trade: dict):
    """Normalize a trade object into the fields we need,"""
    log.info(f"processing trade: side={trade.get('side')} asset={trade.get('asset')} price={trade.get('price')} txHash={trade.get('transactionHash')}")
    token_id = trade.get("asset")
    side = trade.get("side")
    side = (
        trade.get("side")
        or trade.get("outcome")
        or trade.get("type")
    )
    if side:
        side = str(side).strip().upper()
        if "BUY" in side or "SHORT" in side or "NO" in side:
            side = "BUY"
        elif "SELL" in side or "SHORT" in side or "NO" in side:
            side = "SELL"
        else:
            log.info(f"Unknown side value: '{side}'")
    else:
        log.info(f"No side field found in trade keys: {list(trade.keys())}")
        side = "BUY"
    price = trade.get("price")
    market = trade.get("conditionId") or "unknown"
    trade_id = trade.get("transactionHash")

    log.info(f"validation: token_id={bool(token_id)} side={side} price={bool(price)} trade_id={bool(trade_id)}")
    if not all([token_id, side, price, trade_id]):
        return None
    try:
        price = float(price)
    except (ValueError, TypeError):
        return none

    return {
        "trade_id": str(trade_id),
        "market":   market,
        "token_id": str(token_id),
        "side":     side,
        "price":    price,
    }
def place_order(market: str, token_id: str, side: str, price: float) -> bool:
    """Place an order on the given market."""
    log.info(f"attempting order: {side} {token_id[:8]}... at {price}")
    try:
        if price <= 0 or price > 1:
            log.warning(f"Skipping trade — unusual price: {price}")
            return False
        size = TRADE_SIZE_SHARES
        if PAPER_TRADING:
            log.info(
                f"PAPER_TRADE | Market: {market} | Side: {side} "
                f"| Price: {price} | Size: {size} shares (${round(size * price, 2)})"
            )
            return True
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
    log.info(f"Fields result: {fields}")
    if not fields:
        return
    trade_id = fields["trade_id"]
    if trade_id in seen_trade_ids:
        return
    log.info(
        f"New trade from {source_wallet[:8]}... "
        f"| Market: {fields['market']} "
        f"| Side: {fields['side']} "
        f"| Price: {fields['price']}"
    )
    success = place_order(fields["market"], fields["token_id"], fields["side"], fields["price"])
    if success:
        seen_trade_ids.add(trade_id)
def copy_trades():
    """Poll each target wallet and copy any new trades."""
    for wallet in TARGET_WALLETS:
        log.info(f"polling {wallet[:8]}...")
        if consecutive_errors[wallet] >= MAX_CONSECUTIVE_ERRORS:
            log.warning(f"{wallet[:8]}... has too many errors — skipping cycle.")
            continue
        trades = get_recent_trades(wallet)
        for trade in trades:
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
    log.info(f"Fixed trade size: {TRADE_SIZE_SHARES} shares per trade")
    log.info(f"Poll interval: {POLL_INTERVAL}s")
    # seed_seen_trades()
    log.info(f"Bot running. Polling every {POLL_INTERVAL} seconds.")
    while True:
        start = time.time()
        copy_trades()
        elapsed = time.time() - start
        sleep_time = max(0, POLL_INTERVAL - elapsed)
        time.sleep(sleep_time)
