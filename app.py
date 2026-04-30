"""
SalonePrices – Flask web server

Routes:
    POST /ussd                  ← Africa's Talking USSD callback
    POST /webhooks/orange-money ← Orange Money payment notification
    POST /webhooks/sms-delivery ← AT delivery report (optional logging)
    GET  /admin/subscribers     ← Quick subscriber count (protect with API key)
    GET  /admin/blast-preview   ← Preview this week's SMS without sending
    POST /admin/trigger-blast   ← Manually trigger the weekly SMS blast
    GET  /health                ← Uptime check for Railway/Render
"""

import logging
import os

from flask import Flask, jsonify, request

from config import AT_API_KEY
from payments import handle_orange_webhook
from scheduler import trigger_manual_blast
from sheets import get_active_subscribers, get_all_subscribers
from sms import format_price_sms, run_weekly_blast
from ussd import handle_ussd

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── USSD route ────────────────────────────────────────────────────────────────

@app.route("/ussd", methods=["POST"])
def ussd_callback():
    """
    Africa's Talking calls this URL with form data on every USSD navigation step.
    We must respond within 5 seconds or AT times out the session.
    """
    session_id   = request.form.get("sessionId", "")
    phone_number = request.form.get("phoneNumber", "")
    text         = request.form.get("text", "")
    service_code = request.form.get("serviceCode", "")

    response_text = handle_ussd(session_id, phone_number, text, service_code)
    logger.info("USSD %s → %s chars", phone_number, len(response_text))

    # AT expects plain text response, not JSON
    return response_text, 200, {"Content-Type": "text/plain"}


# ── Orange Money webhook ──────────────────────────────────────────────────────

@app.route("/webhooks/orange-money", methods=["POST"])
def orange_money_webhook():
    """Orange Money posts payment confirmations here."""
    raw_body  = request.get_data()
    signature = request.headers.get("X-Orange-Signature", "")
    payload   = request.get_json(silent=True) or {}

    result = handle_orange_webhook(payload, raw_body, signature)
    return jsonify(result), 200


# ── Africa's Talking SMS delivery report ──────────────────────────────────────

@app.route("/webhooks/sms-delivery", methods=["POST"])
def sms_delivery_report():
    """AT posts delivery status updates here (optional – useful for monitoring)."""
    data = request.get_json(silent=True) or request.form.to_dict()
    logger.info("SMS delivery report: %s", data)
    return jsonify({"status": "ok"}), 200


# ── Admin routes (protected by API key header) ────────────────────────────────

def _require_admin(fn):
    """Simple API key guard for admin routes. Pass X-Admin-Key header."""
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-Admin-Key", "")
        if key != os.getenv("ADMIN_API_KEY", "changeme"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


@app.route("/admin/subscribers", methods=["GET"])
@_require_admin
def admin_subscribers():
    """Returns subscriber counts by status."""
    all_subs = get_all_subscribers()
    active   = get_active_subscribers()
    by_status: dict = {}
    for sub in all_subs:
        s = sub.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    return jsonify({
        "total":          len(all_subs),
        "active_or_trial": len(active),
        "by_status":      by_status,
    })


@app.route("/admin/blast-preview", methods=["GET"])
@_require_admin
def admin_blast_preview():
    """
    Shows what this Monday's SMS will look like for the first 3 active subscribers,
    without actually sending anything.
    """
    from sheets import get_latest_prices
    prices    = get_latest_prices()
    subs      = get_active_subscribers()[:3]
    previews  = []
    for sub in subs:
        crops = [c.strip() for c in str(sub.get("crops", "")).split(",") if c.strip()]
        msg   = format_price_sms(prices, crops or ["rice", "cassava"])
        previews.append({
            "phone":   sub.get("phone"),
            "crops":   crops,
            "message": msg,
            "chars":   len(msg),
        })
    return jsonify({"previews": previews, "total_active": len(get_active_subscribers())})


@app.route("/admin/trigger-blast", methods=["POST"])
@_require_admin
def admin_trigger_blast():
    """Manually fires the weekly SMS blast (same as the Monday cron)."""
    results = trigger_manual_blast()
    return jsonify({
        "sent":   sum(1 for r in results if r.get("status") == "success"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
    })


# ── Debug endpoint ───────────────────────────────────────────────────────────

@app.route("/debug", methods=["GET"])
def debug_env():
    """Temporary debug endpoint - remove after fixing."""
    import os
    from config import PRICES_SHEET_ID, SUBSCRIBERS_SHEET_ID, AT_USERNAME, GOOGLE_CREDS_CONTENT
    creds = os.getenv("GOOGLE_CREDS_CONTENT", "")
    return jsonify({
        "env_PRICES_SHEET_ID": os.getenv("PRICES_SHEET_ID", "NOT IN ENV"),
        "config_PRICES_SHEET_ID": PRICES_SHEET_ID,
        "env_AT_USERNAME": os.getenv("AT_USERNAME", "NOT IN ENV"),
        "config_AT_USERNAME": AT_USERNAME,
        "GOOGLE_CREDS_CONTENT_length": len(creds),
        "config_GOOGLE_CREDS_CONTENT_length": len(GOOGLE_CREDS_CONTENT),
        "all_env_keys": [k for k in os.environ.keys() if "SALONE" in k or "GOOGLE" in k or "AT_" in k or "PRICES" in k],
    })


# ── WhatsApp inbound message handler ─────────────────────────────────────────

@app.route("/whatsapp", methods=["POST"])
def whatsapp_inbound():
    """
    Handles inbound WhatsApp messages from Africa's Talking.
    Farmers text a number (1-12) to get instant crop prices.
    """
    data = request.get_json(silent=True) or request.form.to_dict()
    phone = data.get("from", data.get("phoneNumber", ""))
    message = data.get("text", data.get("message", "")).strip().lower()

    logger.info("WhatsApp from %s: %s", phone, message)

    try:
        from sheets import get_latest_prices
        prices = get_latest_prices()
    except Exception as e:
        logger.error("Could not load prices: %s", e)
        prices = {}

    reply = _build_whatsapp_reply(message, prices)
    return jsonify({"message": reply}), 200


def _build_whatsapp_reply(message: str, prices: dict) -> str:
    """Build instant price reply based on farmer's message."""
    from config import CROPS, MARKETS

    market_names = {
        "freetown": "Freetown",
        "bo": "Bo",
        "kenema": "Kenema",
        "makeni": "Makeni",
        "koidu": "Koidu",
    }

    # Map number to crop
    crop_menu = {
        "1": "rice", "2": "cassava", "3": "palm_oil",
        "4": "groundnut", "5": "tomato", "6": "maize",
        "7": "fish_bonga", "8": "onion", "9": "cooking_oil",
        "10": "salt", "11": "pepper", "12": "sweet_potato",
    }

    # JOIN → subscribe
    if message in ["join", "subscribe"]:
        return (
            "✅ You don\u2019t need to sign up for instant prices!\n\n"
            "Just text a number anytime:\n"
            "1-Rice 2-Cassava 3-Palm Oil\n"
            "4-Groundnut 5-Tomato 6-Maize\n"
            "7-Bonga Fish 8-Onion 9-Cooking Oil\n"
            "10-Salt 11-Pepper 12-Sweet Potato\n\n"
            "For weekly Monday alerts, reply WEEKLY."
        )

    # STOP → unsubscribe
    if message in ["stop", "unsubscribe"]:
        return "You\u2019ve been unsubscribed from weekly alerts. Text any number 1-12 for instant prices anytime."

    # WEEKLY → subscribe to Monday blast
    if message == "weekly":
        return (
            "\U0001f4f2 To get weekly Monday price alerts, dial *384*3844321# "
            "on your phone and select option 1 to subscribe. It\u2019s free for 4 weeks!"
        )

    # Number → instant price
    if message in crop_menu:
        crop_key = crop_menu[message]
        crop_info = CROPS.get(crop_key, {})
        crop_name = crop_info.get("name", crop_key)
        unit = crop_info.get("unit", "kg")
        emoji = crop_info.get("emoji", "\U0001f33e")
        crop_prices = prices.get(crop_key, {})

        if not crop_prices:
            return f"Sorry, no prices available for {crop_name} today. Check back Monday!"

        from datetime import date
        today = date.today().strftime("%-d %b %Y")

        lines = [f"{emoji} *{crop_name} prices — {today}*", ""]
        best_market = ""
        best_price = 0

        for market, price in sorted(crop_prices.items(), key=lambda x: -x[1]):
            mname = market_names.get(market, market.title())
            lines.append(f"📍 {mname}: *NLE {price:,}/{unit}*")
            if price > best_price:
                best_price = price
                best_market = mname

        if best_market:
            lines.append("")
            lines.append(f"\U0001f4a1 Best price: Sell in *{best_market}* this week!")

        lines.append("")
        lines.append("Text another number for more prices.")
        lines.append("1-Rice 2-Cassava 3-Palm Oil 4-Groundnut 5-Tomato 6-Maize 7-Fish 8-Onion 9-Oil 10-Salt 11-Pepper 12-Sweet Potato")

        return "\n".join(lines)

    # Default → show menu
    return (
        "\U0001f33e *Welcome to SalonePrices!*\n\n"
        "Text a number for today\'s market prices:\n\n"
        "1 - \U0001f35a Rice\n"
        "2 - \U0001f33f Cassava\n"
        "3 - \U0001f6ab Palm Oil\n"
        "4 - \U0001f95c Groundnut\n"
        "5 - \U0001f345 Tomato\n"
        "6 - \U0001f33d Maize\n"
        "7 - \U0001f41f Bonga Fish\n"
        "8 - \U0001f9c5 Onion\n"
        "9 - \U0001f6ab Cooking Oil\n"
        "10 - \U0001fab8 Salt\n"
        "11 - \U0001f336 Pepper\n"
        "12 - \U0001f360 Sweet Potato\n\n"
        "Type JOIN for weekly Monday alerts \U0001f4f2"
    )


# ── Public test page ─────────────────────────────────────────────────────────

@app.route("/test", methods=["GET"])
def test_page():
    """Public test page — shows a sample SMS and subscriber count."""
    try:
        from sheets import get_latest_prices, get_active_subscribers
        prices = get_latest_prices()
        subs = get_active_subscribers()
        sample_msg = format_price_sms(prices, ["rice", "cassava", "palm_oil"])
        return jsonify({
            "status": "live",
            "service": "SalonePrices 🌾",
            "active_subscribers": len(subs),
            "crops_loaded": list(prices.keys()),
            "sample_sms": sample_msg,
            "ussd_code": "*384*3844321#",
            "whatsapp": "SalonePrices Business",
            "github": "https://github.com/rdmcsl/salone-prices"
        })
    except Exception as e:
        return jsonify({"status": "ok", "note": str(e)}), 200


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "SalonePrices"}), 200


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    logger.info("Starting SalonePrices on port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
