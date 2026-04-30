"""
SalonePrices – SMS sender

Handles:
  - Formatting the weekly price alert SMS (fits in 160 chars)
  - Sending bulk SMS to all active subscribers via Africa's Talking
  - Sending individual confirmation / notification SMS
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


# ── Message formatting ───────────────────────────────────────────────────────

def format_price_sms(prices: dict, subscriber_crops: list[str]) -> str:
    """
    Builds a single SMS (≤160 chars) for the given subscriber's chosen crops.

    Example output:
        SalonePrices 28 Apr
        RICE: Bo 420 | FTN 460 | Ken 410
        CASSAVA: Bo 80 | FTN 95
        Best: sell RICE in Freetown.
        Txt STOP to unsub
    """
    today = date.today().strftime("%-d %b")
    lines = [f"SalonePrices {today}"]

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
        lines_trimmed = lines[:3] + [lines[-1]]  # keep header + 2 crops + footer
        msg = "\n".join(lines_trimmed)

    return msg[:160]


def format_welcome_sms(name: str, crops: list[str]) -> str:
    crop_names = ", ".join(CROPS[c]["name"] for c in crops if c in CROPS)
    return (
        f"Welcome to SalonePrices, {name}! "
        f"You'll get weekly prices for: {crop_names}. "
        f"First alert Monday 7am. Txt STOP to unsubscribe."
    )[:160]


def format_payment_confirmation_sms(name: str, paid_until: str) -> str:
    return (
        f"SalonePrices: Payment confirmed, {name}. "
        f"Subscription active until {paid_until}. Thank you!"
    )[:160]


def format_trial_ending_sms(name: str, days_left: int) -> str:
    return (
        f"SalonePrices: Your free trial ends in {days_left} day(s), {name}. "
        f"Dial *384*4321# to pay NLE 5,000/month and keep your alerts."
    )[:160]


# ── Sending ──────────────────────────────────────────────────────────────────

def send_sms(phone: str, message: str) -> dict:
    """
    Sends a single SMS. Returns the Africa's Talking response dict.
    Phone must be in international format: +23276XXXXXXX
    """
    if _sms is None:
        logger.warning("SMS skipped (AT not initialized): %s", phone)
        return {"error": "AT not initialized"}
    try:
        response = _sms.send(message, [phone], sender_id=AT_SENDER_ID)
        logger.debug("SMS sent to %s: %s", phone, response)
        return response
    except Exception as exc:
        logger.error("SMS send failed to %s: %s", phone, exc)
        return {"error": str(exc)}


def send_bulk_sms(recipients: list[dict], message_fn) -> list[dict]:
    """
    Sends personalised SMS to a list of subscriber dicts.
    message_fn(subscriber) → str   (called per subscriber so crops can differ)

    Returns a list of result dicts:
        [{"phone": ..., "status": "success"|"failed", "message": ..., "response": ...}, ...]

    Africa's Talking supports batching up to 1,000 recipients in one API call,
    but because each subscriber may want different crops we send per-subscriber.
    For pure broadcast (identical message) you can batch – see send_broadcast().
    """
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
    """
    Sends the SAME message to up to 1,000 numbers in a single API call.
    Use for association-level identical blasts (e.g. sponsored alerts).
    """
    try:
        response = _sms.send(message, phones, sender_id=AT_SENDER_ID)
        logger.info("Broadcast sent to %d numbers", len(phones))
        return response
    except Exception as exc:
        logger.error("Broadcast failed: %s", exc)
        return {"error": str(exc)}


# ── Weekly blast entry point ─────────────────────────────────────────────────

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
            crops = ["rice", "cassava", "palm_oil"]  # default crops
        return format_price_sms(prices, crops)

    results = send_bulk_sms(subscribers, build_message)
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
