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
