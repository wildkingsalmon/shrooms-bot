"""HTTP, Telegram, and state-file helpers shared by bootstrap and watcher."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal, TypedDict

import requests

MEMPOOL_API = "https://mempool.space/api"
ORDINALS_API = "https://ordinals.com"
TELEGRAM_API = "https://api.telegram.org"
COINGECKO_API = "https://api.coingecko.com/api/v3"

STATE_DIR = Path(__file__).parent / "state"
STATE_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "shrooms-bot/0.1", "Accept": "application/json"}


class InscriptionState(TypedDict):
    utxo: str  # "txid:vout"
    owner: str


SpendKind = Literal["sale", "transfer", "unknown"]


class SpendClassification(TypedDict):
    kind: SpendKind
    price_sats: int
    new_owner: str
    new_utxo: str


def http_get(url: str, retries: int = 4, timeout: int = 30, **kwargs) -> requests.Response:
    """GET with exponential backoff on 429/5xx and network errors."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout, headers=HEADERS, **kwargs)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_exc = e
            time.sleep(2 ** attempt)
    raise last_exc or RuntimeError(f"GET failed: {url}")


def mempool_tx(txid: str) -> dict:
    return http_get(f"{MEMPOOL_API}/tx/{txid}").json()


def parse_utxo(s: str) -> tuple[str, int]:
    """Split a 'txid:vout' string into (txid, int(vout))."""
    txid, vout = s.split(":")
    return txid, int(vout)


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def md_escape(s: str) -> str:
    """Escape MarkdownV2 special characters (for use outside code spans)."""
    out = []
    for ch in s:
        if ch in r"_*[]()~`>#+-=|{}.!\\":
            out.append("\\")
        out.append(ch)
    return "".join(out)


def md_escape_code(s: str) -> str:
    """Escape MarkdownV2 inside `code` spans (only ` and \\ are special)."""
    return s.replace("\\", "\\\\").replace("`", "\\`")


def send_telegram(token: str, chat_id: str, text: str) -> dict:
    r = requests.post(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if not r.ok:
        raise requests.HTTPError(f"{r.status_code} {r.reason}: {r.text}", response=r)
    return r.json()


def get_btc_usd() -> float | None:
    """Spot BTC/USD from CoinGecko. Returns None on failure (price is decorative)."""
    try:
        r = http_get(f"{COINGECKO_API}/simple/price?ids=bitcoin&vs_currencies=usd")
        return float(r.json()["bitcoin"]["usd"])
    except Exception:
        return None
