"""Long-running daemon: detect Bitcoin Shrooms sales via mempool.space, post to Telegram.

Polls the chain tip; when a new block lands, checks the outspend status of
every tracked UTXO. For each newly-spent (and confirmed) UTXO, fetches the
spending tx and classifies it as sale / self-transfer / unknown. Sales get
posted to Telegram. The tracked UTXO + owner are updated regardless.
"""
from __future__ import annotations

import os
import sys
import time

from shared import (
    InscriptionState,
    MEMPOOL_API,
    STATE_DIR,
    SpendClassification,
    get_btc_usd,
    http_get,
    load_json,
    md_escape,
    md_escape_code,
    mempool_tx,
    parse_utxo,
    save_json,
    send_telegram,
)

INSCRIPTIONS_FILE = STATE_DIR / "inscriptions.json"
SEEN_SALES_FILE = STATE_DIR / "seen_sales.txt"
LAST_BLOCK_FILE = STATE_DIR / "last_block.txt"

POLL_INTERVAL = 30
MIN_SALE_SATS = 100_000  # Net flow to seller must exceed this to count as a sale


def get_tip_height() -> int:
    return int(http_get(f"{MEMPOOL_API}/blocks/tip/height").text)


def get_outspend(txid: str, vout: int) -> dict:
    return http_get(f"{MEMPOOL_API}/tx/{txid}/outspend/{vout}").json()


def classify_spend(spending_tx: dict, tracked_utxo: str,
                   original_owner: str) -> SpendClassification:
    """Classify a tx that consumed `tracked_utxo`.

    1. Find the input that consumed the tracked UTXO.
    2. Trace the inscription's sat through outputs (Ordinals "first sat of
       first output" rule, generalized to non-zero offsets).
    3. Compute the seller's net flow: sum of outputs to seller's address
       minus sum of inputs from seller's address. If positive and ≥
       MIN_SALE_SATS, treat as a sale at that price.
    """
    track_txid, track_vout = parse_utxo(tracked_utxo)
    vins = spending_tx["vin"]
    vouts = spending_tx["vout"]
    spend_txid = spending_tx["txid"]

    k = next(
        (i for i, v in enumerate(vins)
         if v.get("txid") == track_txid and v.get("vout") == track_vout),
        None,
    )
    if k is None:
        return {"kind": "unknown", "price_sats": 0,
                "new_owner": "<unknown>", "new_utxo": tracked_utxo}

    # Sat sits at offset 0 within the tracked UTXO, so its offset in the
    # linearized input stream is the sum of preceding input values.
    offset = sum(v["prevout"]["value"] for v in vins[:k])

    cumulative = 0
    new_vout_idx: int | None = None
    for j, v in enumerate(vouts):
        next_cum = cumulative + v["value"]
        if cumulative <= offset < next_cum:
            new_vout_idx = j
            break
        cumulative = next_cum

    if new_vout_idx is None:
        # Sat fell to fees — vanishingly rare for ordinals
        return {"kind": "unknown", "price_sats": 0,
                "new_owner": "<unknown>", "new_utxo": tracked_utxo}

    new_owner = vouts[new_vout_idx].get("scriptpubkey_address") or "<unknown>"
    new_utxo = f"{spend_txid}:{new_vout_idx}"

    seller_in = sum(
        v["prevout"]["value"] for v in vins
        if v.get("prevout", {}).get("scriptpubkey_address") == original_owner
    )
    seller_out = sum(
        v["value"] for v in vouts
        if v.get("scriptpubkey_address") == original_owner
    )
    net = seller_out - seller_in

    if new_owner == original_owner:
        return {"kind": "transfer", "price_sats": 0,
                "new_owner": new_owner, "new_utxo": new_utxo}
    if net >= MIN_SALE_SATS:
        return {"kind": "sale", "price_sats": net,
                "new_owner": new_owner, "new_utxo": new_utxo}
    return {"kind": "unknown", "price_sats": 0,
            "new_owner": new_owner, "new_utxo": new_utxo}


def format_sale(inscription_id: str, price_sats: int, seller: str, buyer: str,
                txid: str, btc_usd: float | None) -> str:
    price_btc = price_sats / 100_000_000
    if btc_usd:
        usd_part = f"  \\(≈${md_escape(f'{price_btc * btc_usd:,.0f}')}\\)"
    else:
        usd_part = ""
    short_ins = inscription_id[:8] + "…" + inscription_id[-4:]
    short_seller = seller[:6] + "…" + seller[-4:]
    short_buyer = buyer[:6] + "…" + buyer[-4:] if len(buyer) > 10 else buyer
    short_tx = txid[:8] + "…"
    btc_str = f"{price_btc:.4f}".rstrip("0").rstrip(".")
    return (
        f"🍄 *Bitcoin Shroom sold*\n"
        f"`{md_escape_code(short_ins)}`\n"
        f"*{md_escape(btc_str)} BTC*{usd_part}\n"
        f"`{md_escape_code(short_seller)}` → `{md_escape_code(short_buyer)}`\n"
        f"[{md_escape(short_tx)}](https://mempool.space/tx/{txid})"
    )


def process_spend(inscription_id: str, info: InscriptionState, spend: dict,
                  seen: set[str], bot_token: str, chat_id: str) -> None:
    spend_txid = spend["txid"]
    if spend_txid in seen:
        return
    if not spend.get("status", {}).get("confirmed"):
        return  # wait for confirmation; we'll see it next block

    classification = classify_spend(mempool_tx(spend_txid), info["utxo"], info["owner"])
    seller = info["owner"]
    new_owner = classification["new_owner"]

    if classification["kind"] == "sale":
        msg = format_sale(
            inscription_id, classification["price_sats"], seller,
            new_owner, spend_txid, get_btc_usd(),
        )
        try:
            send_telegram(bot_token, chat_id, msg)
            print(
                f"[SALE] {inscription_id[:12]}... "
                f"{classification['price_sats'] / 1e8:.4f} BTC  "
                f"{seller[:8]}→{new_owner[:8]}"
            )
        except Exception as e:
            # Don't mark seen if posting failed — we'll retry next loop.
            print(f"  Telegram send failed: {e}", file=sys.stderr)
            return
    else:
        print(f"[{classification['kind'].upper()}] {inscription_id[:12]}... ({spend_txid[:10]})")

    info["utxo"] = classification["new_utxo"]
    info["owner"] = new_owner
    seen.add(spend_txid)


def main() -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars")

    state: dict[str, InscriptionState] | None = load_json(INSCRIPTIONS_FILE, None)
    if state is None:
        sys.exit(f"Missing {INSCRIPTIONS_FILE}; run bootstrap.py first")

    seen: set[str] = (
        set(SEEN_SALES_FILE.read_text().splitlines())
        if SEEN_SALES_FILE.exists() else set()
    )
    last_block = int(LAST_BLOCK_FILE.read_text().strip()) if LAST_BLOCK_FILE.exists() else 0

    print(f"Watching {len(state)} inscriptions; last_block={last_block}; poll={POLL_INTERVAL}s")

    while True:
        try:
            tip = get_tip_height()
            if tip > last_block:
                print(f"New block: {last_block} → {tip}, scanning {len(state)} UTXOs...")
                for ins_id, info in list(state.items()):
                    txid, vout = parse_utxo(info["utxo"])
                    try:
                        spend = get_outspend(txid, vout)
                    except Exception as e:
                        print(f"  outspend lookup failed [{ins_id[:12]}...]: {e}", file=sys.stderr)
                        continue
                    if spend.get("spent"):
                        process_spend(ins_id, info, spend, seen, bot_token, chat_id)

                last_block = tip
                save_json(INSCRIPTIONS_FILE, state)
                LAST_BLOCK_FILE.write_text(str(last_block))
                SEEN_SALES_FILE.write_text("\n".join(sorted(seen)))
        except Exception as e:
            # Daemon must survive transient network errors and resume next tick.
            print(f"Loop error: {e}", file=sys.stderr)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
