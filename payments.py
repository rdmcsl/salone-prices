"""
SaloneMarket – Orange Money payment webhook

Orange Money sends a POST to ORANGE_NOTIF_URL when a payment is confirmed.
This module validates the notification and activates the subscriber.

Orange Money WebPay notification payload (Sierra Leone):
{
  "status": "SUCCESS" | "FAILED" | "PENDING",
  "txnId": "...",
  "amount": 5000,
  "currency": "SLE",
  "subscriberMsisdn": "23276XXXXXXX",
  "orderId": "SALONE-23276XXXXXXX-20240428",
  "message": "..."
}

orderId format: SALONE-{phone_without_plus}-{YYYYMMDD}
This lets us identify which subscriber to activate without a database lookup.
"""

import hashlib
import hmac
import json
import logging
from datetime import date, timedelta

from config import (
    ASSOCIATION_FEE_NLE, ORANGE_CLIENT_SECRET,
    ORANGE_CURRENCY, SUBSCRIPTION_FEE_NLE,
)
from sheets import update_subscriber_status
from sms import format_payment_confirmation_sms, send_sms

logger = logging.getLogger(__name__)


# ── Payment initiation (called from USSD or web) ──────────────────────────────

def build_orange_payment_url(phone: str, amount_nle: int, plan: str = "individual") -> dict:
    """
    Returns the data needed to initiate an Orange Money Web Payment.
    The caller (USSD flow or web frontend) uses this to redirect/prompt the user.

    In production you POST this to:
        https://api.orange.com/orange-money-webpay/sl/v1/webpayment
    with your Bearer token in the Authorization header.

    Returns:
        {
          "order_id": "SALONE-23276XXXXXXX-20240428",
          "amount": 5000,
          "currency": "SLE",
          "return_url": "...",
          "notif_url": "...",
          "cancel_url": "...",
          "lang": "en",
          "reference": "SaloneMarket monthly subscription"
        }
    """
    from config import ORANGE_NOTIF_URL
    clean_phone = phone.lstrip("+")
    order_id = f"SALONE-{clean_phone}-{date.today().strftime('%Y%m%d')}"

    return {
        "merchant_key": __import__("config").ORANGE_MERCHANT_KEY,
        "order_id":     order_id,
        "amount":       amount_nle,
        "currency":     ORANGE_CURRENCY,
        "return_url":   "https://salonemarket.sl/payment/success",
        "cancel_url":   "https://salonemarket.sl/payment/cancel",
        "notif_url":    ORANGE_NOTIF_URL,
        "lang":         "en",
        "reference":    f"SaloneMarket {plan} subscription",
    }


# ── Webhook handler ───────────────────────────────────────────────────────────

def handle_orange_webhook(payload: dict, raw_body: bytes, signature: str) -> dict:
    """
    Called by the Flask route when Orange Money POSTs a payment notification.

    Args:
        payload:   Parsed JSON body
        raw_body:  Raw bytes (for HMAC verification)
        signature: X-Orange-Signature header value

    Returns:
        {"status": "ok"} or {"status": "error", "reason": "..."}
    """
    # 1. Verify HMAC signature (prevents fake notifications)
    if not _verify_signature(raw_body, signature):
        logger.warning("Invalid Orange Money signature – rejecting webhook")
        return {"status": "error", "reason": "invalid_signature"}

    txn_status = payload.get("status", "").upper()
    order_id   = payload.get("orderId", "")
    amount     = int(payload.get("amount", 0))
    currency   = payload.get("currency", "")
    msisdn     = payload.get("subscriberMsisdn", "")

    logger.info("Orange webhook: order=%s status=%s amount=%s %s from=%s",
                order_id, txn_status, amount, currency, msisdn)

    if txn_status != "SUCCESS":
        logger.info("Non-success payment notification – ignoring")
        return {"status": "ok", "note": "non_success_ignored"}

    # 2. Parse orderId to get phone
    # Format: SALONE-{phone_without_plus}-{YYYYMMDD}
    try:
        parts   = order_id.split("-")
        phone   = "+" + parts[1]   # e.g. +23276XXXXXXX
    except (IndexError, ValueError):
        logger.error("Cannot parse orderId: %s", order_id)
        return {"status": "error", "reason": "bad_order_id"}

    # 3. Determine subscription duration based on amount paid
    plan, months = _plan_from_amount(amount)
    paid_until = (date.today() + timedelta(days=30 * months)).isoformat()

    # 4. Activate subscriber in Google Sheets
    update_subscriber_status(phone, "active", paid_until)

    # 5. Send confirmation SMS
    subscriber_name = _get_name(phone)
    confirm_msg = format_payment_confirmation_sms(subscriber_name, paid_until)
    send_sms(phone, confirm_msg)

    logger.info("Activated %s until %s (plan=%s)", phone, paid_until, plan)
    return {"status": "ok"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _verify_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 verification using ORANGE_CLIENT_SECRET as the key."""
    if not ORANGE_CLIENT_SECRET:
        logger.warning("ORANGE_CLIENT_SECRET not set – skipping signature check (dev mode)")
        return True
    expected = hmac.new(
        ORANGE_CLIENT_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _plan_from_amount(amount_nle: int) -> tuple[str, int]:
    """Returns (plan_name, months) based on payment amount."""
    if amount_nle >= ASSOCIATION_FEE_NLE:
        return ("association", 1)
    elif amount_nle >= SUBSCRIPTION_FEE_NLE * 12:
        return ("individual_annual", 12)
    elif amount_nle >= SUBSCRIPTION_FEE_NLE * 3:
        return ("individual_quarterly", 3)
    else:
        return ("individual", 1)


def _get_name(phone: str) -> str:
    """Quick lookup of subscriber name for the confirmation SMS."""
    try:
        from sheets import get_all_subscribers
        for sub in get_all_subscribers():
            if sub.get("phone") == phone:
                return sub.get("name", "Farmer").split()[0]
    except Exception:
        pass
    return "Farmer"
