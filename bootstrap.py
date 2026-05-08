"""Build state/inscriptions.json — maps each Bitcoin Shrooms inscription
to its current UTXO and owner.

Sources:
  - Inscription list: ordinalswallet.com (free, no auth)
  - Current location: ordinals.com /r/inscription endpoint
  - Owner address:    mempool.space
"""
from __future__ import annotations

import sys

from shared import (
    InscriptionState,
    ORDINALS_API,
    STATE_DIR,
    http_get,
    mempool_tx,
    save_json,
)

OW_BASE = "https://turbo.ordinalswallet.com"
COLLECTION_SLUG = "bitcoin-shrooms"
OUT = STATE_DIR / "inscriptions.json"


def fetch_inscription_ids() -> list[str]:
    r = http_get(f"{OW_BASE}/collection/{COLLECTION_SLUG}/inscriptions")
    data = r.json()
    if not data:
        raise RuntimeError("OrdinalsWallet returned empty inscription list")
    return [item["id"] for item in data]


def locate_inscription(inscription_id: str) -> tuple[str, int]:
    r = http_get(f"{ORDINALS_API}/r/inscription/{inscription_id}")
    txid, vout, _offset = r.json()["satpoint"].split(":")
    return txid, int(vout)


def main() -> None:
    print(f"Fetching inscription list for {COLLECTION_SLUG}...")
    ids = fetch_inscription_ids()
    print(f"  {len(ids)} inscriptions")

    state: dict[str, InscriptionState] = {}
    failed: list[str] = []

    for idx, ins_id in enumerate(ids, 1):
        try:
            txid, vout = locate_inscription(ins_id)
            owner = mempool_tx(txid)["vout"][vout]["scriptpubkey_address"]
            state[ins_id] = {"utxo": f"{txid}:{vout}", "owner": owner}
        except Exception as e:
            failed.append(ins_id)
            print(f"  WARN [{ins_id[:12]}...]: {e}", file=sys.stderr)
        if idx % 25 == 0 or idx == len(ids):
            print(f"  ...{idx}/{len(ids)}")

    save_json(OUT, state)
    print(f"\nWrote {len(state)} entries to {OUT}")
    if failed:
        print(f"Failed: {len(failed)} (these will be ignored by watcher)")
        for f in failed:
            print(f"  {f}")


if __name__ == "__main__":
    main()
