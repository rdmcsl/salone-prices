"""
SaloneMarket – SMS sender

Handles:
  - Formatting the weekly price alert SMS (fits in 160 chars)
  - Sending bulk SMS to all active subscribers via Africa's Talking
  - Sending individual confirmation / notification SMS
  - Weekly WhatsApp digest (Food, Fuel, Cement)
  - Logging send results to a dated CSV in logs/
"""

import csv
import logging
import os
from datetime import date
from typing import Optional

import africastalking

from config import (
    AT_API_KEY, AT_SENDER_ID, AT_USERNAME,
    CROPS, LOG_DIR, MARKETS,
)
from sheets import get_active_subscribers, get_best_market

logger = logging.getLogger(__name__)

# Initialise Africa's Talking SDK once at import time
try:
    africastalking.initialize(AT_USERNAME, AT_API_KEY)
    _sms = africastalking.SMS
except Exception as e:
    logger.warning("AT not initialized: %s", e)
    _sms = None


# ── SMS Message formatting ───────────────────────────────────────────────────

def format_price_sms(prices: dict, subscriber_crops: list[str]) -> str:
    """
    Builds a single SMS (<=160 chars) for the given subscriber's chosen crops.

    Example output:
        SaloneMarket 28 Apr
        RICE: Bo 420 | FTN 460 | Ken 410
        CASSAVA: Bo 80 | FTN 95
        Best: sell RICE in Freetown.
        Txt STOP to unsub
    """
    today = date.today().strftime("%-d %b")
    lines = [f"SaloneMarket {today}"]

    best_crop = None
    best_market_name = None
    best_price = 0

    market_abbrev = {
        "freetown": "FTN",
        "bo":       "Bo",
        "kenema":   "Ken",
        "makeni":   "Mak",
        "koidu":    "Koi",
    }

    for crop_key in subscriber_crops:
        if crop_key not in prices:
            continue
        crop_info   = CROPS[crop_key]
        crop_name   = crop_info["name"].upper()
        unit        = crop_info["unit"]
        emoji       = crop_info.get("emoji", "")
        crop_prices = prices[crop_key]
        if not crop_prices:
            continue

        parts = []
        for mkt, price in sorted(crop_prices.items(), key=lambda x: -x[1]):
            abbrev = market_abbrev.get(mkt, mkt[:3].title())
            parts.append(f"{abbrev} {price:,}")
            if price > best_price:
                best_price = price
                best_crop = crop_info["name"]
                best_market_name = MARKETS.get(mkt, {}).get("name", mkt.title())

        lines.append(f"{emoji} {crop_name}/{unit}: " + " | ".join(parts))

    if best_crop and best_market_name:
        lines.append(f"💡 Best: sell {best_crop} in {best_market_name}!")

    lines.append("Txt STOP to unsub")

    msg = "\n".join(lines)

    # Trim aggressively if over 160 chars
    if len(msg) > 160:
        lines_trimmed = lines[:3] + [lines[-1]]
        msg = "\n".join(lines_trimmed)

    return msg[:160]


def format_welcome_sms(name: str, crops: list[str]) -> str:
    crop_names = ", ".join(CROPS[c]["name"] for c in crops if c in CROPS)
    return (
        f"Welcome to SaloneMarket, {name}! "
        f"You'll get weekly prices for: {crop_names}. "
        f"First alert Monday 7am. Txt STOP to unsubscribe."
    )[:160]


def format_payment_confirmation_sms(name: str, paid_until: str) -> str:
    return (
        f"SaloneMarket: Payment confirmed, {name}. "
        f"Subscription active until {paid_until}. Thank you!"
    )[:160]


def format_trial_ending_sms(name: str, days_left: int) -> str:
    return (
        f"SaloneMarket: Your trial period ends in {days_left} day(s), {name}. "
        f"Dial *384*4321# to pay NLE 5,000/month and keep your alerts."
    )[:160]


# ── SMS Sending ──────────────────────────────────────────────────────────────

def send_sms(phone: str, message: str) -> dict:
    """
    Sends a single SMS via Africa's Talking (Sierra Leone numbers)
    or Twilio (international/US numbers).
    """
    is_salone = phone.startswith("+232")
    if not is_salone:
        return _send_via_twilio(phone, message)

    if _sms is None:
        logger.warning("AT SMS skipped (not initialized): %s", phone)
        return _send_via_twilio(phone, message)
    try:
        response = _sms.send(message, [phone], sender_id=AT_SENDER_ID)
        logger.debug("AT SMS sent to %s: %s", phone, response)
        return response
    except Exception as exc:
        logger.warning("AT SMS failed, trying Twilio: %s", exc)
        return _send_via_twilio(phone, message)


def _send_via_twilio(phone: str, message: str) -> dict:
    """Fallback SMS via Twilio for international numbers."""
    try:
        import os
        from twilio.rest import Client
        sid   = os.getenv("TWILIO_ACCOUNT_SID") or "ACf4a122b66ac0a014d516453eeac070c8"
        token = os.getenv("TWILIO_AUTH_TOKEN")  or "3f40fc6f4f2c93af2860c2f6d858cf12"
        frm   = os.getenv("TWILIO_FROM_NUMBER") or "+12295623289"
        if not sid or not token:
            return {"error": "Twilio not configured"}
        client = Client(sid, token)
        msg = client.messages.create(body=message, from_=frm, to=phone)
        logger.info("Twilio SMS sent to %s: %s", phone, msg.sid)
        return {"MessageData": {"Message": "Sent", "sid": msg.sid}}
    except Exception as exc:
        logger.error("Twilio SMS failed to %s: %s", phone, exc)
        return {"error": str(exc)}


def send_bulk_sms(recipients: list[dict], message_fn) -> list[dict]:
    """Sends personalised SMS to a list of subscriber dicts."""
    results = []
    for sub in recipients:
        phone = sub.get("phone", "")
        if not phone:
            continue
        msg = message_fn(sub)
        resp = send_sms(phone, msg)
        status = "success" if "error" not in resp else "failed"
        results.append({"phone": phone, "status": status, "message": msg, "response": resp})

    _log_results(results)
    logger.info("Bulk send complete: %d sent, %d failed",
                sum(1 for r in results if r["status"] == "success"),
                sum(1 for r in results if r["status"] == "failed"))
    return results


def send_broadcast(phones: list[str], message: str) -> dict:
    """Sends the SAME message to up to 1,000 numbers in a single API call."""
    try:
        response = _sms.send(message, phones, sender_id=AT_SENDER_ID)
        logger.info("Broadcast sent to %d numbers", len(phones))
        return response
    except Exception as exc:
        logger.error("Broadcast failed: %s", exc)
        return {"error": str(exc)}


# ── Weekly SMS blast entry point ─────────────────────────────────────────────

def run_weekly_blast(prices: dict) -> list[dict]:
    """
    Main function called by the cron scheduler every Monday at 07:00.
    Fetches active subscribers, builds personalised SMS, fires them all.
    """
    subscribers = get_active_subscribers()
    if not subscribers:
        logger.warning("No active subscribers – blast skipped")
        return []

    def build_message(sub: dict) -> str:
        crops = [c.strip() for c in str(sub.get("crops", "")).split(",") if c.strip()]
        if not crops:
            crops = ["rice", "cassava", "palm_oil"]
        return format_price_sms(prices, crops)

    results = send_bulk_sms(subscribers, build_message)
    return results


# ── WhatsApp message formatting ──────────────────────────────────────────────

def _px(prices: dict, crop: str, district: str):
    """Get price for a crop in a district with fallbacks."""
    d = prices.get(crop, {})
    if isinstance(d, dict):
        return d.get(district) or d.get("Western Area") or d.get("freetown") or "—"
    return d or "—"


def format_whatsapp_food(name: str, district: str, prices: dict, plan: str = "free") -> str:
    """Formats the weekly Food & Agriculture WhatsApp digest."""
    today = date.today().strftime("%-d %b %Y")
    first = name.split()[0] if name else "there"
    is_pro = plan in ("pro", "biz", "individual")

    lines = [
        f"👋 Hi {first}!",
        "",
        "📊 *SL Market Tracker — Food & Agriculture*",
        f"📅 Week of {today} · {district}",
        "",
        "🌾 *FOOD PRICES (retail)*",
        f"Rice local 50kg.......NLe {_px(prices, 'rice_local', district)}",
        f"Rice imported 50kg....NLe {_px(prices, 'rice_imported', district)}",
        f"Palm oil 1L..............NLe {_px(prices, 'palm_oil', district)}",
        f"Sugar 50kg...............NLe {_px(prices, 'sugar', district)}",
        f"Onion 1kg.................NLe {_px(prices, 'onion', district)}",
    ]

    if is_pro:
        lines += [
            f"Tomato 1kg................NLe {_px(prices, 'tomato', district)}",
            f"Dried fish 1kg............NLe {_px(prices, 'dried_fish', district)}",
            f"Groundnut oil 1L.........NLe {_px(prices, 'groundnut_oil', district)}",
            f"Wheat flour 50kg.........NLe {_px(prices, 'wheat_flour', district)}",
            f"Cassava 50kg..............NLe {_px(prices, 'cassava', district)}",
        ]
    else:
        lines.append("_(Upgrade to Pro for all 15 items)_")

    lines += [
        "",
        f"📌 _Retail prices · {district}_",
        "🔗 trade.gov.sl/prices",
        "",
        "_↑ up · ↓ down · — stable vs last week_",
        "_Reply STOP to unsubscribe_",
    ]
    return "\n".join(lines)


def format_whatsapp_fuel(name: str, district: str, prices: dict) -> str:
    """Formats the weekly Fuel & Energy WhatsApp digest."""
    today = date.today().strftime("%-d %b %Y")
    first = name.split()[0] if name else "there"

    return "\n".join([
        f"👋 Hi {first}!",
        "",
        "⛽ *SL Market Tracker — Fuel & Energy*",
        f"📅 Week of {today} · {district}",
        "",
        "🚗 *PUMP PRICES (per litre)*",
        f"Petrol (PMS).......NLe {_px(prices, 'petrol', district)}",
        f"Diesel (AGO).......NLe {_px(prices, 'diesel', district)}",
        f"Kerosene (DPK)...NLe {_px(prices, 'kerosene', district)}",
        "",
        f"📌 _NPA regulated pump prices · {district}_",
        "🔗 trade.gov.sl/prices",
        "",
        "_↑ up · ↓ down · — stable vs last week_",
        "_Reply STOP to unsubscribe_",
    ])


def format_whatsapp_cement(name: str, district: str, prices: dict) -> str:
    """Formats the weekly Cement & Construction WhatsApp digest."""
    today = date.today().strftime("%-d %b %Y")
    first = name.split()[0] if name else "there"

    return "\n".join([
        f"👋 Hi {first}!",
        "",
        "🏗 *SL Market Tracker — Cement & Construction*",
        f"📅 Week of {today} · {district}",
        "",
        "💰 *WHOLESALE — national (per 50kg bag)*",
        f"Imported 42.5R......NLe {prices.get('cement_imported_wholesale', '175')}",
        f"Local 32.5R...........NLe {prices.get('cement_local_wholesale', '165')}",
        "",
        f"🏪 *RETAIL · {district} (per 50kg bag)*",
        f"Imported 42.5R......NLe {_px(prices, 'cement_imported', district)}",
        f"Local 32.5R...........NLe {_px(prices, 'cement_local', district)}",
        "",
        "📌 _Ministry of Trade & Industry directive_",
        "🔗 trade.gov.sl/prices",
        "",
        "_↑ up · ↓ down · — stable vs last week_",
        "_Reply STOP to unsubscribe_",
    ])


# ── WhatsApp sender ──────────────────────────────────────────────────────────

def send_whatsapp_msg(phone: str, message: str) -> dict:
    """
    Sends a single WhatsApp message via Africa's Talking REST API.
    The AT Python SDK does not support WhatsApp — we call the API directly.
    Phone must be in E.164 format: +23276XXXXXXX
    """
    import requests
    url = "https://api.sandbox.africastalking.com/version1/messaging/whatsapp"
    if os.getenv("NODE_ENV", "sandbox") == "production":
        url = "https://api.africastalking.com/version1/messaging/whatsapp"
    try:
        payload = {
            "username": AT_USERNAME,
            "to":       phone,
            "from":     os.getenv("AT_WHATSAPP_SENDER", ""),
            "message":  message,
        }
        headers = {
            "apiKey":       AT_API_KEY,
            "Accept":       "application/json",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        logger.info("WhatsApp sent to %s: %s", phone, resp.status_code)
        return resp.json()
    except Exception as exc:
        logger.error("WhatsApp failed to %s: %s", phone, exc)
        return {"error": str(exc)}


# ── Weekly WhatsApp blast ────────────────────────────────────────────────────

def run_weekly_whatsapp_blast(prices: dict) -> list[dict]:
    """
    Sends personalised WhatsApp digests to all active subscribers every Monday.
    Each subscriber gets messages for their subscribed categories:
      - Food  → all plans
      - Fuel  → pro / biz only
      - Cement → pro / biz only
    """
    subscribers = get_active_subscribers()
    if not subscribers:
        logger.warning("No active subscribers — WhatsApp blast skipped")
        return []

    results = []
    for sub in subscribers:
        phone    = sub.get("phone", "")
        name     = sub.get("name", "")
        district = sub.get("district", "Western Area") or "Western Area"
        plan     = sub.get("plan", "free")
        cats_raw = str(sub.get("categories", "food"))
        cats     = [c.strip() for c in cats_raw.split(",")]

        if not phone:
            continue

        # Food — all plans
        if "food" in cats or not cats:
            msg  = format_whatsapp_food(name, district, prices, plan)
            resp = send_whatsapp_msg(phone, msg)
            results.append({
                "phone": phone, "category": "food",
                "status": "success" if "error" not in resp else "failed",
            })

        # Fuel — pro/biz only
        if "fuel" in cats and plan in ("pro", "biz"):
            msg  = format_whatsapp_fuel(name, district, prices)
            resp = send_whatsapp_msg(phone, msg)
            results.append({
                "phone": phone, "category": "fuel",
                "status": "success" if "error" not in resp else "failed",
            })

        # Cement — pro/biz only
        if "cement" in cats and plan in ("pro", "biz"):
            msg  = format_whatsapp_cement(name, district, prices)
            resp = send_whatsapp_msg(phone, msg)
            results.append({
                "phone": phone, "category": "cement",
                "status": "success" if "error" not in resp else "failed",
            })

    _log_results(results)
    sent   = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    logger.info("WhatsApp blast complete: %d sent, %d failed", sent, failed)
    return results


# ── Logging ──────────────────────────────────────────────────────────────────

def _log_results(results: list[dict]) -> None:
    """Writes send results to a dated CSV file in logs/."""
    os.makedirs(LOG_DIR, exist_ok=True)
    filename = os.path.join(LOG_DIR, f"sms_log_{date.today().isoformat()}.csv")
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["phone", "status", "message", "response"])
        writer.writeheader()
        writer.writerows(results)
    logger.info("SMS log written to %s", filename)
