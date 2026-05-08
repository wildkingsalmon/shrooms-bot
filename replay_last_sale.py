"""One-off: find the most recent on-chain Shrooms sale and post it as a test."""
from __future__ import annotations

import os
import sys

from shared import (
    STATE_DIR,
    get_btc_usd,
    load_json,
    mempool_tx,
    parse_utxo,
    send_telegram,
)
from watcher import classify_spend, format_sale

INSCRIPTIONS_FILE = STATE_DIR / "inscriptions.json"


def main() -> None:
    state = load_json(INSCRIPTIONS_FILE, None)
    if state is None:
        sys.exit("Run bootstrap.py first")

    sales = []
    total = len(state)
    for i, (ins_id, info) in enumerate(state.items(), 1):
        txid, _ = parse_utxo(info["utxo"])
        try:
            tx = mempool_tx(txid)
        except Exception as e:
            print(f"  WARN [{ins_id[:12]}...]: {e}", file=sys.stderr)
            continue
        if not tx.get("status", {}).get("confirmed"):
            continue
        # Replay heuristic: the inscription input is vin[0] of the tx that
        # produced the current UTXO (true for the standard PSBT sale pattern).
        try:
            vin0 = tx["vin"][0]
            seller = vin0["prevout"]["scriptpubkey_address"]
            prev_utxo = f"{vin0['txid']}:{vin0['vout']}"
        except (IndexError, KeyError, TypeError):
            continue
        cls = classify_spend(tx, prev_utxo, seller)
        if cls["kind"] == "sale":
            sales.append({
                "block_time": tx["status"].get("block_time", 0),
                "ins_id": ins_id,
                "price_sats": cls["price_sats"],
                "seller": seller,
                "buyer": cls["new_owner"],
                "txid": txid,
            })
        if i % 25 == 0 or i == total:
            print(f"  scanned {i}/{total}, sales found so far: {len(sales)}")

    if not sales:
        print("No sales detected in current-UTXO history.")
        return

    sales.sort(key=lambda s: s["block_time"], reverse=True)
    last = sales[0]

    print("\nMost recent sale:")
    print(f"  inscription: {last['ins_id']}")
    print(f"  price:       {last['price_sats'] / 1e8:.6f} BTC")
    print(f"  seller:      {last['seller']}")
    print(f"  buyer:       {last['buyer']}")
    print(f"  tx:          {last['txid']}")

    msg = format_sale(
        last["ins_id"], last["price_sats"], last["seller"],
        last["buyer"], last["txid"], get_btc_usd(),
    )
    msg = "🧪 *TEST \\(replay of last sale\\)*\n\n" + msg
    send_telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], msg)
    print("\nSent.")


if __name__ == "__main__":
    main()
