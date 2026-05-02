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


@app.route("/admin/fetch-prices", methods=["POST"])
def admin_fetch_prices():
    """Manually trigger WFP price fetch and update Google Sheet."""
    try:
        from price_fetcher import fetch_wfp_prices, update_sheet_with_wfp_prices
        prices = fetch_wfp_prices()
        meta   = prices.pop("_meta", {})
        if prices:
            update_sheet_with_wfp_prices({**prices, "_meta": meta})
            return jsonify({
                "status": "ok",
                "commodities_fetched": list(prices.keys()),
                "source": meta.get("source"),
                "date": meta.get("date"),
            })
        else:
            return jsonify({"status": "no_data", "note": "WFP had no recent SL data — sheet unchanged"})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


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


# ── SMS inbound handler (Africa's Talking SMS callback) ──────────────────────

@app.route("/sms-inbound", methods=["POST"])
def sms_inbound():
    """
    Handles inbound SMS from Africa's Talking.
    Farmers text a number (1-15) to get instant crop prices by SMS.
    AT posts: from, to, text, date
    """
    data    = request.form.to_dict() or request.get_json(silent=True) or {}
    phone   = data.get("from", data.get("phoneNumber", ""))
    message = data.get("text", data.get("message", "")).strip().lower()

    logger.info("SMS inbound from %s: %s", phone, message)

    try:
        from sheets import get_latest_prices
        prices = get_latest_prices()
    except Exception as e:
        logger.error("Could not load prices: %s", e)
        prices = {}

    reply = _build_whatsapp_reply(message, prices)

    # Strip emoji for SMS (SMS doesn't support all emoji)
    import re
    reply_sms = re.sub(r'[*_]', '', reply)  # remove markdown bold
    reply_sms = reply_sms[:459]  # SMS safe length (3 SMS max)

    # Send reply SMS via Africa's Talking
    try:
        send_sms(phone, reply_sms[:160])
        logger.info("SMS reply sent to %s", phone)
    except Exception as e:
        logger.error("SMS reply failed: %s", e)

    return "OK", 200


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
        "13": "eggs", "14": "chicken", "15": "meat",
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
        "\U0001f33e *Welcome to SalonePrices!*\n"
        "Na wi yone free price service for Salone \U0001f1f8\U0001f1f1\n\n"
        "Text a number for TODAY\'s market prices:\n\n"
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
        "12 - \U0001f360 Sweet Potato\n"
        "13 - \U0001f95a Eggs (per crate)\n"
        "14 - \U0001f414 Chicken (per kg)\n"
        "15 - \U0001f969 Meat / Beef (per kg)\n\n"
        "Type WEEKLY for Monday morning alerts \U0001f4f2"
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


# ── Admin panel (browser-friendly) ───────────────────────────────────────────

@app.route("/admin", methods=["GET"])
def admin_panel():
    """Simple browser admin panel — no tools needed."""
    html = """<!DOCTYPE html>
<html>
<head>
  <title>SalonePrices Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; background: #f9f9f9; }
    h1 { color: #2d6a4f; }
    .card { background: white; border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
    button { background: #2d6a4f; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-size: 15px; cursor: pointer; width: 100%; margin-top: 8px; }
    button:hover { background: #1b4332; }
    .result { margin-top: 12px; padding: 12px; background: #f0f4f0; border-radius: 8px; font-family: monospace; font-size: 13px; white-space: pre-wrap; display: none; }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 99px; font-size: 12px; background: #d8f3dc; color: #1b4332; margin-bottom: 8px; }
    input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; margin-top: 4px; }
    label { font-size: 13px; color: #555; }
  </style>
</head>
<body>
  <h1>🌾 SalonePrices Admin</h1>
  <p style="color:#555">Control panel for your Sierra Leone crop price SMS service</p>

  <div class="card">
    <span class="badge">Live</span>
    <h3 style="margin:0 0 4px">System Status</h3>
    <p style="color:#555;font-size:14px">Check if server and Google Sheets are connected</p>
    <button onclick="callApi('GET', '/test', null, 'status-result')">Check Status</button>
    <div class="result" id="status-result"></div>
  </div>

  <div class="card">
    <span class="badge">Auto</span>
    <h3 style="margin:0 0 4px">Fetch WFP Prices</h3>
    <p style="color:#555;font-size:14px">Pull latest rice, cassava, palm oil prices from WFP database → write to your Google Sheet automatically</p>
    <button onclick="callApi('POST', '/admin/fetch-prices', null, 'fetch-result')">Fetch WFP Prices Now</button>
    <div class="result" id="fetch-result"></div>
  </div>

  <div class="card">
    <span class="badge">Preview</span>
    <h3 style="margin:0 0 4px">Preview This Week's SMS</h3>
    <p style="color:#555;font-size:14px">See what farmers will receive before sending</p>
    <button onclick="callApi('GET', '/admin/blast-preview', null, 'preview-result')">Preview SMS Blast</button>
    <div class="result" id="preview-result"></div>
  </div>

  <div class="card">
    <span class="badge">Send</span>
    <h3 style="margin:0 0 4px">Trigger SMS Blast</h3>
    <p style="color:#555;font-size:14px">Send this week's price alerts to all subscribers now (normally runs Monday 7am automatically)</p>
    <button onclick="callApi('POST', '/admin/trigger-blast', null, 'blast-result')" style="background:#c0392b">Send SMS to All Subscribers</button>
    <div class="result" id="blast-result"></div>
  </div>

  <div class="card">
    <span class="badge">Subscribers</span>
    <h3 style="margin:0 0 4px">Subscriber Count</h3>
    <button onclick="callApi('GET', '/admin/subscribers', null, 'subs-result')">View Subscribers</button>
    <div class="result" id="subs-result"></div>
  </div>

  <div class="card">
    <span class="badge">Send Test SMS</span>
    <h3 style="margin:0 0 4px">Send Test SMS to a Phone</h3>
    <p style="color:#555;font-size:14px">Send a real SMS to any number to test the system</p>
    <label>Phone number (include country code e.g. +13177241951)</label>
    <input id="test-phone" type="text" placeholder="+13177241951" value="+13177241951">
    <button onclick="sendTestSMS()">Send Test SMS Now</button>
    <div class="result" id="sms-test-result"></div>
  </div>

  <div class="card">
    <span class="badge">WhatsApp Test</span>
    <h3 style="margin:0 0 4px">Test WhatsApp Price Lookup</h3>
    <p style="color:#555;font-size:14px">Simulate a farmer texting a number to get prices</p>
    <label>Message (e.g. 1 for Rice, 2 for Cassava... 15 for Meat)</label>
    <input id="wa-msg" type="text" placeholder="Type 1-15 or JOIN or STOP" value="1">
    <button onclick="testWhatsapp()">Simulate WhatsApp Message</button>
    <div class="result" id="wa-result"></div>
  </div>

  <script>
    const KEY = 'saloneprices2024';

    async function callApi(method, url, body, resultId) {
      const el = document.getElementById(resultId);
      el.style.display = 'block';
      el.textContent = 'Loading...';
      try {
        const opts = {
          method,
          headers: { 'X-Admin-Key': KEY, 'Content-Type': 'application/json' }
        };
        if (body) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        const data = await res.json();
        el.textContent = JSON.stringify(data, null, 2);
      } catch(e) {
        el.textContent = 'Error: ' + e.message;
      }
    }

    async function sendTestSMS() {
      const phone = document.getElementById('test-phone').value;
      const el = document.getElementById('sms-test-result');
      el.style.display = 'block';
      el.textContent = 'Sending to ' + phone + '...';
      try {
        const res = await fetch('/admin/send-test-sms', {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
          body: JSON.stringify({phone: phone})
        });
        const text = await res.text();
        try {
          const data = JSON.parse(text);
          el.textContent = JSON.stringify(data, null, 2);
        } catch(e) {
          el.textContent = 'Response: ' + text.substring(0, 200);
        }
      } catch(e) {
        el.textContent = 'Error: ' + e.message;
      }
    }

    async function testWhatsapp() {
      const msg = document.getElementById('wa-msg').value;
      const el = document.getElementById('wa-result');
      el.style.display = 'block';
      el.textContent = 'Loading...';
      try {
        const res = await fetch('/whatsapp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ from: '+23276000001', text: msg })
        });
        const data = await res.json();
        el.textContent = data.message || JSON.stringify(data, null, 2);
      } catch(e) {
        el.textContent = 'Error: ' + e.message;
      }
    }
  </script>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html"}


# ── Send test SMS ────────────────────────────────────────────────────────────

@app.route("/admin/send-test-sms", methods=["POST"])
def send_test_sms():
    """Send a test SMS to any number to verify AT is working."""
    try:
        data  = request.get_json(silent=True) or {}
        phone = data.get("phone", "")
        if not phone:
            return jsonify({"error": "phone required"}), 400
        msg = "SalonePrices Test! Text a number for SL market prices: 1=Rice 2=Cassava 3=PalmOil 4=Groundnut 5=Tomato 6=Maize 7=Fish 8=Onion 9=Oil 10=Salt 11=Pepper 12=SweetPotato 13=Eggs 14=Chicken 15=Meat"
        result = send_sms(phone, msg)
        return jsonify({"status": "sent", "result": str(result), "phone": phone,
                        "note": "Sandbox only delivers to SL numbers. Upgrade AT to live to send globally."})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e),
                        "note": "AT sandbox cannot send to US numbers. Add $5 credit and switch to live mode to send globally."})


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
