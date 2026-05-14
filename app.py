"""
SaloneMarket – Flask application entry point

Routes:
  POST /ussd                        Africa's Talking USSD callback
  POST /webhooks/orange-money       Orange Money payment webhook
  POST /webhooks/sms-delivery       SMS delivery report callback
  POST /webhooks/whatsapp-incoming  Inbound WhatsApp messages
  GET  /health                      Uptime check
  GET  /admin                       Admin dashboard (HTML)
  GET  /admin/subscribers           List all subscribers (JSON)
  GET  /admin/prices                Latest prices (JSON)
  GET  /admin/whatsapp-preview      Preview WhatsApp messages (JSON)
  POST /admin/trigger-blast         Manual SMS blast
  POST /admin/trigger-whatsapp-blast Manual WhatsApp blast
"""

import logging
import os

from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="templates", static_url_path="")


# ── Auth helper ───────────────────────────────────────────────────────────────

def _admin_key_ok() -> bool:
    """Check X-Admin-Key header or ?key= query param."""
    expected = os.getenv("ADMIN_API_KEY", "saloneprices2024")
    provided = (
        request.headers.get("X-Admin-Key", "")
        or request.args.get("key", "")
        or (request.get_json(silent=True) or {}).get("key", "")
    )
    return provided == expected


# ── Public routes ─────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "SaloneMarket"})


@app.route("/ussd", methods=["POST"])
def ussd_callback():
    from ussd import handle_ussd
    session_id   = request.form.get("sessionId", "")
    phone_number = request.form.get("phoneNumber", "")
    text         = request.form.get("text", "")
    service_code = request.form.get("serviceCode", "")
    response_text = handle_ussd(session_id, phone_number, text, service_code)
    logger.info("USSD %s → %d chars", phone_number, len(response_text))
    return response_text, 200, {"Content-Type": "text/plain"}


@app.route("/webhooks/orange-money", methods=["POST"])
def orange_money_webhook():
    from payments import handle_orange_webhook
    raw_body  = request.get_data()
    signature = request.headers.get("X-Orange-Signature", "")
    payload   = request.get_json(silent=True) or {}
    result    = handle_orange_webhook(payload, raw_body, signature)
    return jsonify(result), 200


@app.route("/webhooks/sms-delivery", methods=["POST"])
def sms_delivery_report():
    data = request.get_json(silent=True) or request.form.to_dict()
    logger.info("SMS delivery report: %s", data)
    return jsonify({"status": "received"}), 200


@app.route("/webhooks/whatsapp-incoming", methods=["POST"])
def whatsapp_incoming():
    data  = request.get_json(silent=True) or request.form.to_dict()
    phone = data.get("from", data.get("phoneNumber", ""))
    text  = (data.get("text", "") or "").strip().upper()
    logger.info("Inbound WhatsApp from %s: %s", phone, text)

    if text in ("STOP", "UNSUBSCRIBE"):
        from sheets import unsubscribe
        unsubscribe(phone)
        from sms import send_whatsapp_msg
        send_whatsapp_msg(phone, "You've been unsubscribed from SaloneMarket. Reply START to re-subscribe.")

    return jsonify({"status": "ok"}), 200


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_dashboard():
    """Serve the admin HTML page."""
    import os
    html_path = os.path.join(app.static_folder or "templates", "admin.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html"}
    return "<h1>SaloneMarket Admin</h1><p>admin.html not found in templates/</p>", 200


@app.route("/admin/subscribers")
def admin_subscribers():
    if not _admin_key_ok():
        return jsonify({"status": "error", "reason": "unauthorized"}), 401
    from sheets import get_all_subscribers
    subs = get_all_subscribers()
    return jsonify({"count": len(subs), "subscribers": subs})


@app.route("/admin/prices")
def admin_prices():
    if not _admin_key_ok():
        return jsonify({"status": "error", "reason": "unauthorized"}), 401
    from sheets import get_latest_prices
    prices = get_latest_prices()
    return jsonify(prices)


@app.route("/admin/whatsapp-preview")
def admin_whatsapp_preview():
    """
    Returns sample WhatsApp messages for each category so you can
    verify formatting before the Monday blast.
    Query params:
      ?district=Western Area   (default: Western Area)
      ?name=Aminata            (default: Test User)
      ?plan=pro                (default: free)
    """
    if not _admin_key_ok():
        return jsonify({"status": "error", "reason": "unauthorized"}), 401

    district = request.args.get("district", "Western Area")
    name     = request.args.get("name",     "Aminata")
    plan     = request.args.get("plan",     "free")

    try:
        from sheets import get_latest_prices
        from sms import format_whatsapp_food, format_whatsapp_fuel, format_whatsapp_cement
        from cement_prices import CEMENT_PRICES

        prices = get_latest_prices()
        # Always have cement prices available even before Sheet is seeded
        merged = {**CEMENT_PRICES, **prices}

        return jsonify({
            "status":   "ok",
            "district": district,
            "plan":     plan,
            "messages": {
                "food":   format_whatsapp_food(name, district, merged, plan),
                "fuel":   format_whatsapp_fuel(name, district, merged),
                "cement": format_whatsapp_cement(name, district, merged),
            },
        })

    except Exception as exc:
        logger.exception("whatsapp-preview failed")
        return jsonify({"status": "error", "reason": str(exc)}), 500


@app.route("/admin/trigger-blast", methods=["POST"])
def admin_trigger_blast():
    """Manually fires the weekly SMS blast."""
    if not _admin_key_ok():
        return jsonify({"status": "error", "reason": "unauthorized"}), 401
    try:
        from sheets import get_latest_prices
        from sms import run_weekly_blast
        prices  = get_latest_prices()
        results = run_weekly_blast(prices)
        sent    = sum(1 for r in results if r.get("status") == "success")
        failed  = sum(1 for r in results if r.get("status") == "failed")
        return jsonify({"status": "ok", "sent": sent, "failed": failed, "total": len(results)})
    except Exception as exc:
        logger.exception("trigger-blast failed")
        return jsonify({"status": "error", "reason": str(exc)}), 500


@app.route("/admin/trigger-whatsapp-blast", methods=["POST"])
def admin_trigger_whatsapp_blast():
    """Manually fires the weekly WhatsApp blast."""
    if not _admin_key_ok():
        return jsonify({"status": "error", "reason": "unauthorized"}), 401
    try:
        from sheets import get_latest_prices
        from sms import run_weekly_whatsapp_blast
        from cement_prices import CEMENT_PRICES
        prices = get_latest_prices()
        merged = {**CEMENT_PRICES, **prices}
        results = run_weekly_whatsapp_blast(merged)
        sent    = sum(1 for r in results if r.get("status") == "success")
        failed  = sum(1 for r in results if r.get("status") == "failed")
        return jsonify({"status": "ok", "sent": sent, "failed": failed, "total": len(results)})
    except Exception as exc:
        logger.exception("trigger-whatsapp-blast failed")
        return jsonify({"status": "error", "reason": str(exc)}), 500


@app.route("/admin/test-sms")
def admin_test_sms():
    """Send a single test SMS to a specified number and return raw AT response."""
    if not _admin_key_ok():
        return jsonify({"status": "error", "reason": "unauthorized"}), 401
    phone = request.args.get("phone", "")
    if not phone:
        return jsonify({"status": "error", "reason": "phone param required"}), 400
    try:
        from sms import send_sms
        resp = send_sms(phone, "SaloneMarket test message. Reply STOP to unsubscribe.")
        return jsonify({"status": "ok", "phone": phone, "response": str(resp)})
    except Exception as exc:
        return jsonify({"status": "error", "reason": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
