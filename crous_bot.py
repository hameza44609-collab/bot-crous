#!/usr/bin/env python3
"""
CROUS housing monitor -> Telegram alert bot.

Runs as a one-shot job with repository-backed state (designed for GitHub Actions):

    load state -> sync /start subscribers -> query CROUS -> diff -> alert -> save

Scope: ALERTING ONLY. This bot never books, never logs in, never submits a
form. It only reads the public search API and notifies you on Telegram.

Author-facing note: the CROUS search API is undocumented and may change its
field names between campaigns. Field extraction below is defensive (safe
`.get()` access with fallbacks) and the first run logs the raw item keys so the
mapping can be adjusted quickly if CROUS renames something.
"""

import json
import logging
import os
import re
import sys
import time
from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256

import requests
from cryptography.fernet import Fernet, InvalidToken

# Optional: load a local .env file when running OUTSIDE GitHub Actions.
# In production the values come from GitHub Secrets (environment variables).
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


# --------------------------------------------------------------------------- #
# CONFIGURATION
# --------------------------------------------------------------------------- #
# Every tunable value lives here. A non-technical user can change these without
# reading the logic below. Anything can also be overridden with an environment
# variable of the same name (useful for GitHub Actions).

# --- CROUS endpoints ---------------------------------------------------------
CROUS_BASE_URL = "https://trouverunlogement.lescrous.fr"
CROUS_HOMEPAGE_URL = CROUS_BASE_URL + "/"
# Search API. {tool_id} is the campaign identifier (it changes every year).
CROUS_SEARCH_URL_TEMPLATE = CROUS_BASE_URL + "/api/fr/search/{tool_id}"
# Public detail / booking page for a given accommodation id.
CROUS_ACCOMMODATION_URL_TEMPLATE = (
    CROUS_BASE_URL + "/tools/{tool_id}/accommodations/{acc_id}"
)

# --- Tool id (campaign) resolution ------------------------------------------
# The tool id is the campaign number in the URL: /tools/<ID>/search.
# For Ile-de-France 2026/2027 it is 47 (verified from the official search URL).
#
# Resolution priority:
#   1. CROUS_TOOL_ID           -> if set, used directly (pinned).
#   2. homepage auto-detection -> only if CROUS_AUTODETECT_TOOL_ID is enabled.
#   3. FALLBACK_TOOL_ID        -> safe default (47).
#
# Auto-detection is OFF by default: the homepage exposes several tool ids and
# can return the wrong one (it returned 42 in testing), so relying on the known
# pinned id is more reliable.
CROUS_TOOL_ID = os.getenv("CROUS_TOOL_ID", "").strip()
CROUS_AUTODETECT_TOOL_ID = os.getenv("CROUS_AUTODETECT_TOOL_ID", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
FALLBACK_TOOL_ID = int(os.getenv("CROUS_FALLBACK_TOOL_ID", "47"))

# --- Geographic bounding box (Ile-de-France, includes Paris) -----------------
# Two opposite corners of the search rectangle.
BBOX_WEST = float(os.getenv("CROUS_BBOX_WEST", "1.4462445"))     # min longitude
BBOX_SOUTH = float(os.getenv("CROUS_BBOX_SOUTH", "48.1201456"))  # min latitude
BBOX_EAST = float(os.getenv("CROUS_BBOX_EAST", "3.5592208"))     # max longitude
BBOX_NORTH = float(os.getenv("CROUS_BBOX_NORTH", "49.241431"))   # max latitude

# --- Search paging -----------------------------------------------------------
PAGE_SIZE = int(os.getenv("CROUS_PAGE_SIZE", "50"))
MAX_PAGES = int(os.getenv("CROUS_MAX_PAGES", "40"))  # hard safety cap

# --- HTTP behaviour (CROUS requests) -----------------------------------------
HTTP_CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "10"))
HTTP_READ_TIMEOUT = float(os.getenv("HTTP_READ_TIMEOUT", "30"))
HTTP_TIMEOUT = (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "4"))
HTTP_BACKOFF_BASE = float(os.getenv("HTTP_BACKOFF_BASE", "2.0"))  # seconds
USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

# --- Telegram ----------------------------------------------------------------
TELEGRAM_API_URL_TEMPLATE = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_RETRIES = int(os.getenv("TELEGRAM_MAX_RETRIES", "5"))
TELEGRAM_BACKOFF_BASE = float(os.getenv("TELEGRAM_BACKOFF_BASE", "2.0"))
TELEGRAM_UPDATES_LIMIT = 100
TELEGRAM_SEND_WORKERS = int(os.getenv("TELEGRAM_SEND_WORKERS", "8"))

# --- Monitoring behaviour ----------------------------------------------------
FAILURE_THRESHOLD = int(os.getenv("FAILURE_THRESHOLD", "3"))
HEARTBEAT_INTERVAL_HOURS = int(os.getenv("HEARTBEAT_INTERVAL_HOURS", "8"))

# --- State file (committed back to the repo by the workflow) -----------------
STATE_FILE = os.getenv("STATE_FILE", "state.json")
STATE_VERSION = 2

# --- Secrets (NEVER hardcode; provided via environment / GitHub Secrets) -----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# Optional one-time migration path for installations that previously configured
# fixed, comma-separated recipients. New recipients subscribe with /start.
LEGACY_TELEGRAM_CHAT_IDS = [
    c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()
]
# Optional explicit Fernet key. By default a stable private key is derived from
# the bot token, which is already a high-entropy GitHub secret. This keeps chat
# ids unreadable when state.json is committed to a public repository.
SUBSCRIBER_ENCRYPTION_KEY = os.getenv("SUBSCRIBER_ENCRYPTION_KEY", "").strip()


# --------------------------------------------------------------------------- #
# LOGGING (stdout -> visible in GitHub Actions logs)
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("crous-bot")


# --------------------------------------------------------------------------- #
# SMALL HELPERS
# --------------------------------------------------------------------------- #
def html_escape(text):
    """Escape the characters Telegram HTML parse_mode cares about."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _first_present(node, keys, default=None):
    """Return the first non-empty value among `keys` in a dict."""
    if not isinstance(node, dict):
        return default
    for key in keys:
        value = node.get(key)
        if value not in (None, ""):
            return value
    return default


def _label_or_value(node):
    """CROUS returns many fields as {'label': '...', 'value': N, ...}."""
    if node is None:
        return None
    if isinstance(node, dict):
        return _first_present(node, ["label", "value", "name"])
    return node


def _search_price(node, depth=0):
    """Best-effort search for a rent/price label inside a nested structure.

    The CROUS item does not expose rent at the top level; it lives inside a
    booking-related subtree. We scan only those subtrees to avoid picking up an
    unrelated number (surface, bed count...).
    """
    if node is None or depth > 3:
        return None
    if isinstance(node, dict):
        for key in ("rent", "price", "amount", "monthlyPrice", "totalPrice", "cost"):
            if key in node:
                value = node[key]
                label = _label_or_value(value) if isinstance(value, dict) else value
                if label not in (None, ""):
                    return label
        label = node.get("label")
        if isinstance(label, str) and "\u20ac" in label:  # contains a euro sign
            return label
        for value in node.values():
            found = _search_price(value, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _search_price(value, depth + 1)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------- #
# HTTP SESSION + RETRYING REQUEST
# --------------------------------------------------------------------------- #
def build_session():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        }
    )
    return session


def http_request(session, method, url, **kwargs):
    """HTTP request with retries + exponential backoff. Raises on final failure."""
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    last_exc = None
    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        try:
            resp = session.request(method, url, **kwargs)
            if resp.status_code >= 500:
                raise requests.HTTPError("server error %s" % resp.status_code)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == HTTP_MAX_RETRIES:
                break
            delay = HTTP_BACKOFF_BASE ** attempt
            log.warning(
                "HTTP %s failed (attempt %d/%d): %s -> retry in %.1fs",
                method,
                attempt,
                HTTP_MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(
        "HTTP request to %s failed after %d attempts: %s"
        % (url, HTTP_MAX_RETRIES, last_exc)
    )


# --------------------------------------------------------------------------- #
# TOOL ID DETECTION
# --------------------------------------------------------------------------- #
def detect_tool_id_from_homepage(session):
    """Scrape the homepage for a campaign tool id. Returns int or None."""
    try:
        resp = http_request(session, "GET", CROUS_HOMEPAGE_URL)
        html = resp.text
        candidates = []
        candidates += re.findall(r"tools/(\d+)/search", html)
        candidates += re.findall(r'"idTool"\s*:\s*(\d+)', html)
        candidates += re.findall(r"tools/(\d+)/", html)
        if candidates:
            tool_id = int(candidates[0])
            log.info("Auto-detected tool id from homepage: %d", tool_id)
            return tool_id
        log.warning("Could not detect tool id from homepage markup.")
    except Exception as exc:
        log.warning("Tool id detection failed: %s", exc)
    return None


def resolve_tool_id(session):
    """Resolve the campaign tool id using the configured priority."""
    if CROUS_TOOL_ID.isdigit():
        tool_id = int(CROUS_TOOL_ID)
        log.info("Using pinned CROUS_TOOL_ID: %d", tool_id)
        return tool_id
    if CROUS_AUTODETECT_TOOL_ID:
        detected = detect_tool_id_from_homepage(session)
        if detected is not None:
            return detected
        log.warning("Auto-detection failed; falling back.")
    log.info("Using tool id: %d", FALLBACK_TOOL_ID)
    return FALLBACK_TOOL_ID


# --------------------------------------------------------------------------- #
# LISTING PARSING
# --------------------------------------------------------------------------- #
def parse_units(item):
    """Best-effort available-unit count, used for restock detection.

    Defaults to 1 when the API does not expose an explicit count.
    """
    for key in (
        "available",
        "nbAvailable",
        "availableCount",
        "stock",
        "quantity",
        "count",
        "occupancy",
    ):
        val = item.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return max(val, 0)
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return 1


def parse_listing(item, tool_id):
    """Convert a raw API item into a normalized listing dict, or None."""
    if not isinstance(item, dict):
        return None

    acc_id = item.get("id") or item.get("uid") or item.get("code")
    if acc_id is None:
        return None
    acc_id = str(acc_id)

    residence = item.get("residence")
    if not isinstance(residence, dict):
        residence = {}

    label = _label_or_value(item.get("label")) or item.get("name") or "Logement CROUS"
    residence_label = (
        _label_or_value(residence.get("label"))
        or _label_or_value(residence)
        or "Residence CROUS"
    )
    address = (
        _first_present(residence, ["address", "adresse"])
        or _first_present(item, ["address", "adresse"])
        or "Adresse non communiquee"
    )
    area = _label_or_value(item.get("area")) or _label_or_value(item.get("surface"))
    rent = (
        _label_or_value(item.get("rent"))
        or _label_or_value(item.get("price"))
        or _search_price(item.get("bookingData"))
        or _search_price(item.get("occupationModes"))
    )

    url = item.get("url")
    if not url:
        url = CROUS_ACCOMMODATION_URL_TEMPLATE.format(tool_id=tool_id, acc_id=acc_id)
    elif isinstance(url, str) and url.startswith("/"):
        url = CROUS_BASE_URL + url

    return {
        "id": acc_id,
        "label": str(label),
        "residence": str(residence_label),
        "address": str(address),
        "area": None if area is None else str(area),
        "rent": None if rent is None else str(rent),
        "url": str(url),
        "available": bool(item.get("available", True)),
        "units": parse_units(item),
    }


def fetch_all_listings(session, tool_id):
    """Query the CROUS search API across all pages inside the bounding box."""
    url = CROUS_SEARCH_URL_TEMPLATE.format(tool_id=tool_id)
    listings = {}
    logged_sample = False

    for page in range(1, MAX_PAGES + 1):
        # This payload mirrors exactly what the CROUS website sends for its
        # Ile-de-France search (captured from the browser Network tab), so the
        # API returns the same results the site shows.
        payload = {
            "idTool": tool_id,
            "need_aggregation": True,
            "page": page,
            "pageSize": PAGE_SIZE,
            "sector": None,
            "occupationModes": [],
            "location": [
                {"lon": BBOX_WEST, "lat": BBOX_NORTH},
                {"lon": BBOX_EAST, "lat": BBOX_SOUTH},
            ],
            "residence": None,
            "precision": 4,
            "equipment": [],
            "adaptedPmr": False,
            "area": {"min": 0},
            "price": {"max": 10000000},
            "toolMechanism": "residual",
        }
        resp = http_request(
            session,
            "POST",
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError("CROUS API returned non-JSON response") from exc

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, dict):
            results = data if isinstance(data, dict) else {}
        items = results.get("items") or results.get("results") or []
        if not isinstance(items, list):
            items = []

        if not logged_sample and items and isinstance(items[0], dict):
            # Helps adjust field mapping if CROUS renames something.
            log.info("Sample raw item keys: %s", sorted(items[0].keys()))
            logged_sample = True

        for raw in items:
            parsed = parse_listing(raw, tool_id)
            if parsed:
                listings[parsed["id"]] = parsed

        total = results.get("total")
        log.info(
            "Page %d: %d items (running total %d).", page, len(items), len(listings)
        )

        if not items:
            break
        if isinstance(total, int) and len(listings) >= total:
            break
        if len(items) < PAGE_SIZE:
            break

    log.info("Fetched %d listing(s) in bounding box.", len(listings))
    return listings


# --------------------------------------------------------------------------- #
# TELEGRAM
# --------------------------------------------------------------------------- #
def _telegram_api_request(method, payload):
    """Call one Telegram Bot API method with retries and rate-limit handling."""
    api_url = TELEGRAM_API_URL_TEMPLATE.format(
        token=TELEGRAM_BOT_TOKEN, method=method
    )

    for attempt in range(1, TELEGRAM_MAX_RETRIES + 1):
        try:
            resp = requests.post(api_url, json=payload, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            error_name = type(exc).__name__
            if attempt == TELEGRAM_MAX_RETRIES:
                raise RuntimeError(
                    "Telegram network error (%s)" % error_name
                ) from None
            delay = TELEGRAM_BACKOFF_BASE ** attempt
            log.warning(
                "Telegram network error (attempt %d/%d, %s) -> retry in %.1fs",
                attempt,
                TELEGRAM_MAX_RETRIES,
                error_name,
                delay,
            )
            time.sleep(delay)
            continue

        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError as exc:
                raise RuntimeError("Telegram returned a non-JSON response") from exc
            if not isinstance(body, dict) or not body.get("ok"):
                raise RuntimeError("Telegram API returned an invalid response")
            return body.get("result")

        if resp.status_code == 429:
            retry_after = TELEGRAM_BACKOFF_BASE ** attempt
            try:
                retry_after = float(resp.json()["parameters"]["retry_after"])
            except Exception:
                pass
            log.warning("Telegram rate limited (429). Waiting %.1fs.", retry_after)
            time.sleep(retry_after + 0.5)
            continue

        if 500 <= resp.status_code < 600:
            delay = TELEGRAM_BACKOFF_BASE ** attempt
            log.warning(
                "Telegram server error %d (attempt %d/%d) -> retry in %.1fs",
                resp.status_code,
                attempt,
                TELEGRAM_MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
            continue

        # Any other 4xx is unrecoverable (bad token, webhook conflict, bad
        # chat id, bad HTML...). Do not include the token-bearing URL in logs.
        raise RuntimeError(
            "Telegram API %s error %d: %s"
            % (method, resp.status_code, resp.text)
        )

    raise RuntimeError("Telegram %s failed after all retries." % method)


def _send_telegram_single(chat_id, text, disable_preview=True):
    """Send an HTML message to ONE chat, with retries, backoff, retry_after."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    _telegram_api_request("sendMessage", payload)
    return True


def send_telegram(text, chat_ids, disable_preview=True):
    """Send an HTML message to every subscribed recipient.

    Each recipient is delivered independently: if one chat id fails (e.g. a
    friend who never pressed Start on the bot), the others still receive the
    message. Raises only if EVERY recipient failed.
    """
    recipients = sorted({str(chat_id) for chat_id in chat_ids})
    if not recipients:
        log.info("No Telegram subscribers; message skipped.")
        return 0

    sent = 0
    worker_count = max(1, min(TELEGRAM_SEND_WORKERS, len(recipients)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _send_telegram_single,
                chat_id,
                text,
                disable_preview=disable_preview,
            )
            for chat_id in recipients
        ]
        for future in as_completed(futures):
            try:
                future.result()
                sent += 1
            except Exception as exc:
                # Avoid printing personal chat ids in public GitHub Actions logs.
                log.error("Telegram send to one subscriber failed: %s", exc)

    if sent == 0:
        raise RuntimeError(
            "Telegram send failed for all %d subscriber(s)." % len(recipients)
        )
    log.info("Telegram message delivered to %d/%d subscriber(s).", sent, len(recipients))
    return sent


def fetch_telegram_updates(offset=None):
    """Fetch up to one Bot API batch of private/group messages."""
    payload = {
        "limit": TELEGRAM_UPDATES_LIMIT,
        "timeout": 0,
        "allowed_updates": ["message"],
    }
    if isinstance(offset, int):
        payload["offset"] = offset
    updates = _telegram_api_request("getUpdates", payload)
    if not isinstance(updates, list):
        raise RuntimeError("Telegram getUpdates returned an invalid result")
    return updates


def format_listing_message(listing, restock=False):
    """Build the French, HTML-formatted alert for a single listing."""
    if restock:
        header = "\u267b\ufe0f <b>R\u00e9approvisionnement CROUS</b>"
    else:
        header = "\U0001f6a8 <b>Nouveau logement CROUS</b>"

    lines = [
        header,
        "",
        "\U0001f3f7\ufe0f <b>%s</b>" % html_escape(listing["label"]),
        "\U0001f3e0 %s" % html_escape(listing["residence"]),
        "\U0001f4cd %s" % html_escape(listing["address"]),
    ]
    if listing.get("area"):
        lines.append("\U0001f4d0 %s" % html_escape(listing["area"]))
    if listing.get("rent"):
        lines.append("\U0001f4b6 %s" % html_escape(listing["rent"]))
    if restock and listing.get("units"):
        lines.append("\U0001f4e6 Unit\u00e9s disponibles : %s" % listing["units"])
    lines.append("")
    lines.append(
        '\U0001f449 <a href="%s">R\u00e9server / voir l\'annonce</a>'
        % html_escape(listing["url"])
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# STATE MANAGEMENT
# --------------------------------------------------------------------------- #
def _subscriber_cipher():
    """Return the cipher used to keep subscriber chat ids out of public state."""
    if SUBSCRIBER_ENCRYPTION_KEY:
        key = SUBSCRIBER_ENCRYPTION_KEY.encode("ascii")
    else:
        # Domain separation prevents this derived key from being confused with
        # any other value derived from the same bot token.
        digest = sha256(
            b"crous-bot/subscribers/v1\0" + TELEGRAM_BOT_TOKEN.encode("utf-8")
        ).digest()
        key = urlsafe_b64encode(digest)
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "SUBSCRIBER_ENCRYPTION_KEY must be a valid Fernet key"
        ) from exc


def load_subscribers(state):
    """Decrypt and validate the persisted Telegram subscriber registry."""
    encrypted = state.get("subscribers_encrypted")
    if not encrypted:
        return set()
    try:
        raw = _subscriber_cipher().decrypt(str(encrypted).encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
    except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Cannot decrypt subscriber registry. If the bot token changed, "
            "restore the old token/key or clear subscribers_encrypted."
        ) from exc
    if not isinstance(decoded, list) or not all(
        isinstance(chat_id, str) and chat_id for chat_id in decoded
    ):
        raise RuntimeError("Decrypted subscriber registry has an invalid format")
    return set(decoded)


def save_subscribers(state, subscribers):
    """Encrypt a normalized subscriber list into the persistent state."""
    normalized = sorted({str(chat_id) for chat_id in subscribers})
    plaintext = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    state["subscribers_encrypted"] = (
        _subscriber_cipher().encrypt(plaintext).decode("ascii")
    )


def default_state():
    return {
        "version": STATE_VERSION,
        "initialized": False,
        "listings": {},
        "consecutive_failures": 0,
        "failure_alert_sent": False,
        "last_heartbeat": None,
        "subscribers_encrypted": None,
        "telegram_update_offset": None,
        "legacy_chat_ids_migrated": False,
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        log.info("No state file found; starting fresh.")
        return default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        base = default_state()
        for key in base:
            if key in loaded:
                base[key] = loaded[key]
        # Loading an older schema performs an in-memory migration. The next
        # normal save persists the current version and new default fields.
        base["version"] = STATE_VERSION
        if not isinstance(base.get("listings"), dict):
            base["listings"] = {}
        if not isinstance(base.get("telegram_update_offset"), (int, type(None))):
            base["telegram_update_offset"] = None
        return base
    except Exception as exc:
        log.error("State file unreadable (%s). Recreating fresh state.", exc)
        return default_state()


def save_state(state):
    """Write state atomically (temp file + rename) so it is never corrupted."""
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, STATE_FILE)
    log.info(
        "State saved (%d listings, failures=%d).",
        len(state.get("listings", {})),
        state.get("consecutive_failures", 0),
    )


# --------------------------------------------------------------------------- #
# TELEGRAM SUBSCRIPTIONS
# --------------------------------------------------------------------------- #
def _telegram_command(message):
    """Return a normalized /command from a Telegram message, or None."""
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.startswith("/"):
        return None
    first_word = text.strip().split(maxsplit=1)[0].lower()
    # In groups Telegram commands can be addressed as /start@bot_username.
    return first_word.split("@", 1)[0]


def sync_telegram_subscribers(state, subscribers):
    """Apply /start and /stop updates and return the current subscriber set."""
    subscribers = set(subscribers)
    registry_changed = False

    # Preserve recipients from the old fixed-chat configuration exactly once.
    # After migration they can unsubscribe with /stop without being re-added.
    if not state.get("legacy_chat_ids_migrated", False):
        before = len(subscribers)
        subscribers.update(LEGACY_TELEGRAM_CHAT_IDS)
        registry_changed = len(subscribers) != before
        state["legacy_chat_ids_migrated"] = True
        log.info(
            "Migrated %d legacy Telegram recipient(s).",
            len(LEGACY_TELEGRAM_CHAT_IDS),
        )

    updates = fetch_telegram_updates(state.get("telegram_update_offset"))
    max_update_id = None

    for update in updates:
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            continue
        update_id = update["update_id"]
        max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)

        message = update.get("message")
        command = _telegram_command(message)
        chat = message.get("chat") if isinstance(message, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if chat_id is None or command not in ("/start", "/stop"):
            continue
        chat_id = str(chat_id)

        if command == "/start":
            was_new = chat_id not in subscribers
            subscribers.add(chat_id)
            registry_changed = registry_changed or was_new
            status = "activé" if was_new else "déjà actif"
            confirmation = (
                "✅ <b>Abonnement CROUS %s</b>\n\n"
                "Vous recevrez les alertes de nouveaux logements, les "
                "réapprovisionnements et les messages d'état du bot.\n"
                "📦 %d logement(s) suivi(s) actuellement.\n\n"
                "Envoyez /stop pour vous désabonner."
                % (status, len(state.get("listings", {})))
            )
        else:
            was_subscribed = chat_id in subscribers
            subscribers.discard(chat_id)
            registry_changed = registry_changed or was_subscribed
            confirmation = (
                "🛑 <b>Abonnement CROUS arrêté</b>\n\n"
                "Vous ne recevrez plus les alertes. Envoyez /start pour vous "
                "réabonner."
            )

        # A reply is best-effort. The registry and update offset are still
        # persisted so one unreachable user cannot block every other subscriber.
        try:
            _send_telegram_single(chat_id, confirmation)
        except Exception as exc:
            log.warning("Could not acknowledge a subscription command: %s", exc)

    if max_update_id is not None:
        state["telegram_update_offset"] = max_update_id + 1
    if registry_changed or state.get("subscribers_encrypted") is None:
        save_subscribers(state, subscribers)

    if len(updates) == TELEGRAM_UPDATES_LIMIT:
        log.info("Telegram update backlog remains; the next run will continue it.")
    log.info(
        "Processed %d Telegram update(s); %d active subscriber(s).",
        len(updates),
        len(subscribers),
    )
    return subscribers


# --------------------------------------------------------------------------- #
# DIFF + HEARTBEAT LOGIC
# --------------------------------------------------------------------------- #
def diff_listings(old, new):
    """Return (new_ids, restocked_ids).

    A listing is *bookable* when its `available` flag is true.
    - new_ids       : ids never seen before AND currently available.
    - restocked_ids : known ids that flipped from unavailable to available,
      or (fallback) whose available-unit count increased.
    All ids (available or not) are still stored by the caller, so a later flip
    from unavailable to available is detected as a restock.
    """
    new_ids = []
    restocked = []
    for acc_id, current in new.items():
        cur_available = current.get("available", True)
        if acc_id not in old:
            if cur_available:
                new_ids.append(acc_id)
            continue
        prev = old[acc_id]
        prev_available = prev.get("available", True)
        if cur_available and not prev_available:
            restocked.append(acc_id)
        elif cur_available and current.get("units", 1) > prev.get("units", 1):
            restocked.append(acc_id)
    return new_ids, restocked


def should_send_heartbeat(state, now):
    last = state.get("last_heartbeat")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    elapsed_hours = (now - last_dt).total_seconds() / 3600.0
    return elapsed_hours >= HEARTBEAT_INTERVAL_HOURS


# --------------------------------------------------------------------------- #
# MONITORING CYCLE
# --------------------------------------------------------------------------- #
def run_monitor_cycle(state, session, now, subscribers):
    """Perform one full monitoring cycle. Raises on failure."""
    tool_id = resolve_tool_id(session)
    listings = fetch_all_listings(session, tool_id)

    if not state["initialized"]:
        # First run: record everything silently, send ONE confirmation.
        available_count = sum(
            1 for item in listings.values() if item.get("available", True)
        )
        state["listings"] = listings
        state["initialized"] = True
        state["last_heartbeat"] = now.isoformat()
        send_telegram(
            "\u2705 <b>Bot CROUS activ\u00e9</b>\n\n"
            "Surveillance de l'\u00cele-de-France d\u00e9marr\u00e9e.\n"
            "\U0001f4e6 %d logement(s) disponible(s) actuellement "
            "(%d suivi(s) au total).\n\n"
            "Vous recevrez une alerte d\u00e8s qu'un nouveau logement appara\u00eet "
            "ou qu'un logement est r\u00e9approvisionn\u00e9."
            % (available_count, len(listings)),
            subscribers,
        )
        log.info(
            "First run: recorded %d listings (%d available) silently.",
            len(listings),
            available_count,
        )
        return listings

    new_ids, restocked = diff_listings(state["listings"], listings)
    log.info("Diff: %d new, %d restocked.", len(new_ids), len(restocked))

    for acc_id in new_ids:
        send_telegram(
            format_listing_message(listings[acc_id], restock=False), subscribers
        )
    for acc_id in restocked:
        send_telegram(
            format_listing_message(listings[acc_id], restock=True), subscribers
        )

    # Only replace the persisted listings AFTER alerts were sent successfully.
    state["listings"] = listings
    return listings


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def main():
    cycle_started = time.monotonic()
    if not TELEGRAM_BOT_TOKEN:
        log.error(
            "Missing TELEGRAM_BOT_TOKEN. Set it as a GitHub Actions secret. "
            "Aborting."
        )
        return 1

    now = datetime.now(timezone.utc)
    state = load_state()
    session = build_session()
    was_in_failure = bool(state.get("failure_alert_sent", False))
    subscribers = set()

    try:
        subscribers = load_subscribers(state)
        subscribers = sync_telegram_subscribers(state, subscribers)
        listings = run_monitor_cycle(state, session, now, subscribers)

        # Recovery notification (only if a failure alert had been sent before).
        if was_in_failure:
            try:
                send_telegram(
                    "\u2705 <b>R\u00e9tablissement</b>\n\n"
                    "Le bot CROUS refonctionne normalement apr\u00e8s une panne.",
                    subscribers,
                )
            except Exception as exc:
                log.warning("Could not send recovery message: %s", exc)

        state["consecutive_failures"] = 0
        state["failure_alert_sent"] = False

        # Periodic heartbeat.
        if should_send_heartbeat(state, now):
            available_count = sum(
                1 for item in listings.values() if item.get("available", True)
            )
            try:
                send_telegram(
                    "\U0001f493 <b>Bot CROUS \u2014 je suis en ligne</b>\n\n"
                    "\u00c9tat : \u2705 op\u00e9rationnel\n"
                    "\U0001f4e6 Logements disponibles : %d (%d suivis)\n"
                    "\U0001f552 %s"
                    % (
                        available_count,
                        len(listings),
                        now.strftime("%d/%m/%Y %H:%M UTC"),
                    ),
                    subscribers,
                )
                state["last_heartbeat"] = now.isoformat()
            except Exception as exc:
                log.warning("Could not send heartbeat: %s", exc)

        save_state(state)
        log.info(
            "Cycle completed successfully in %.2f seconds.",
            time.monotonic() - cycle_started,
        )
        return 0

    except Exception as exc:
        log.exception("Monitoring cycle failed: %s", exc)
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        log.warning("Consecutive failures: %d", state["consecutive_failures"])

        if (
            state["consecutive_failures"] >= FAILURE_THRESHOLD
            and not state.get("failure_alert_sent", False)
        ):
            try:
                send_telegram(
                    "\u26a0\ufe0f <b>Alerte panne \u2014 Bot CROUS</b>\n\n"
                    "%d \u00e9checs cons\u00e9cutifs.\n"
                    "Le bot n'arrive plus \u00e0 interroger CROUS.\n"
                    "V\u00e9rifiez les logs GitHub Actions."
                    % state["consecutive_failures"],
                    subscribers,
                )
                state["failure_alert_sent"] = True
            except Exception as send_exc:
                log.error("Could not send failure alert: %s", send_exc)

        # Listings are left untouched -> saved state is never corrupted.
        save_state(state)
        log.info(
            "Failed cycle handled in %.2f seconds.",
            time.monotonic() - cycle_started,
        )
        # Exit 0: the failure is handled AND reported via Telegram. Keeping the
        # Actions run green avoids GitHub failure-notification noise; the real
        # signal is the Telegram warning after FAILURE_THRESHOLD failures.
        return 0


if __name__ == "__main__":
    sys.exit(main())
