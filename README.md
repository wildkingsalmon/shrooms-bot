# Shrooms Bot

Telegram bot that posts a message whenever a Bitcoin Shroom (Ordinals collectible) is sold on-chain.

Detection is marketplace-agnostic: tracks the 224 inscription UTXOs directly via mempool.space and classifies sales using the standard PSBT atomic-swap pattern.

## Setup (local or VPS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: paste your Telegram bot token and chat/channel id
```

### Get a Telegram bot token

1. Open Telegram, message `@BotFather`, run `/newbot`, follow prompts.
2. Copy the HTTP API token (looks like `123456:ABC-DEF...`).

### Get a channel ID

1. Create a Telegram channel, add your bot as an admin (at least with "Post Messages" permission).
2. Send any message to the channel.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser.
4. Find the channel `id` (a negative number like `-1001234567890`).

## Run

```bash
# One-time: build state/inscriptions.json (~1 minute)
python bootstrap.py

# Then start the watcher
python watcher.py
```

You should see something like:
```
Watching 224 inscriptions; last_block=0; poll=30s
New block: 0 → 891234, scanning 224 UTXOs...
```

The first scan after bootstrap will report no sales (every UTXO is unspent). When a sale occurs you'll see `[SALE] ...` in the log and a Telegram message in the channel.

## Deploy to VPS via systemd

```bash
# On the VPS:
sudo mkdir -p /opt/shrooms-bot
sudo chown $USER /opt/shrooms-bot
# scp or git-clone the repo into /opt/shrooms-bot

cd /opt/shrooms-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # then edit

.venv/bin/python bootstrap.py

sudo cp shrooms-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shrooms-bot
sudo journalctl -u shrooms-bot -f  # tail logs
```

## How sale detection works

When a tracked Shroom UTXO gets spent in a confirmed block, the watcher pulls the spending transaction and applies this rule:

- **Sale**: an output ≥ 1,000 sats goes to the original owner's address AND at least one input comes from a different address (the buyer's funding). Price = that output's value.
- **Self-transfer**: every input comes from the original owner's address. Skipped.
- **Unknown**: anything else (rare). Logged but not posted.

After processing, the inscription is assumed to have moved to **vout 0** of the spending tx (the standard Ordinals PSBT pattern). State is updated and persisted.

## Files

- `bootstrap.py` — one-time: builds `state/inscriptions.json`
- `watcher.py` — long-running daemon
- `shared.py` — HTTP / Telegram / state helpers
- `state/inscriptions.json` — `{inscription_id: {utxo, owner}}`
- `state/seen_sales.txt` — txids already posted
- `state/last_block.txt` — chain tip we've already scanned through

## Limits

- mempool.space public API has soft rate limits; the watcher uses ~225 calls per new block (~32k/day) which sits comfortably below them.
- Sale detection assumes the standard Ordinals atomic-swap pattern. Unusual sale formats may classify as "unknown" — they get logged but not posted.
