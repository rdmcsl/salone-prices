"""
SaloneMarket – Flask web server
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="templates", static_url_path="")

@app.route("/ussd", methods=["POST"])
def ussd_callback():
    session_id   = request.form.get("sessionId", "")
    phone_number = request.form.get("phoneNumber", "")
    text         = request.form.get("text", "")
    service_code = request.form.get("serviceCode", "")
    response_text = handle_ussd(session_id, phone_number, text, service_code)
    logger.info("USSD %s → %s chars", phone_number, len(response_text))
    return response_text, 200, {"Content-Type": "text/plain"}

@app.route("/webhooks/orange-money", methods=["POST"])
def orange_money_webhook():
    raw_body  = request.get_data()
    signature = request.headers.get("X-Orange-Signature", "")
    payload   = request.get_json(silent=True) or {}
    result = handle_orange_webhook(payload, raw_body, signature)
    return jsonify(result), 200

@app.route("/webhooks/sms-delivery", methods=["POST"])
def sms_delivery_report():
    data = request.get_json(silent=True) or request.form.to_dict()
    logger.info("SMS delivery report: %s", data)
    return jsonify({"status": "ok"}), 200

def _require_admin(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-Admin-Key", "")
       if key != os.getenv("ADMIN_API_KEY", "saloneprices2024"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper

@app.route("/admin/subscribers", methods=["GET"])
def admin_subscribers():
    all_subs = get_all_subscribers()
    active   = get_active_subscribers()
    by_status: dict = {}
    for sub in all_subs:
        s = sub.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    return jsonify({
        "total":           len(all_subs),
        "active_or_trial": len(active),
        "by_status":       by_status,
    })

@app.route("/admin/blast-preview", methods=["GET"])
def admin_blast_preview():
    from sheets import get_latest_prices
    prices   = get_latest_prices()
    subs     = get_active_subscribers()[:3]
    previews = []
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

@app.route("/admin/setup-cement-fuel-tabs", methods=["GET", "POST"])
def admin_setup_cement_fuel_tabs():
    try:
        from setup_cement_fuel_tabs import create_cement_fuel_tabs
        results = create_cement_fuel_tabs()
        return jsonify({"status": "ok", "tabs": results})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)})

@app.route("/admin/fetch-fuel-prices", methods=["GET", "POST"])
def admin_fetch_fuel_prices():
    try:
        from ministry_prices import fetch_fuel_prices
        from setup_cement_fuel_tabs import update_fuel_prices
        fuel     = fetch_fuel_prices()
        meta     = fuel.pop("_meta", {})
        petrol   = int(fuel.get("petrol",   35))
        diesel   = int(fuel.get("diesel",   40))
        kerosene = int(fuel.get("kerosene", 41))
        success  = update_fuel_prices(petrol, diesel, kerosene)
        return jsonify({
            "status":   "ok" if success else "error",
            "petrol":   f"NLE {petrol}/litre",
            "diesel":   f"NLE {diesel}/litre",
            "kerosene": f"NLE {kerosene}/litre",
            "source":   meta.get("source", "Unknown"),
            "date":     meta.get("date", ""),
        })
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)})

@app.route("/admin/fetch-cement-fuel", methods=["POST"])
def admin_fetch_cement_fuel():
    try:
        from ministry_prices import update_cement_and_fuel_in_sheet, get_cement_prices_for_sheet, get_fuel_prices_for_sheet
        cement      = get_cement_prices_for_sheet()
        fuel        = get_fuel_prices_for_sheet()
        success     = update_cement_and_fuel_in_sheet()
        cement_meta = cement.pop("_meta", {})
        fuel_meta   = fuel.pop("_meta", {})
        return jsonify({
            "status":        "ok" if success else "error",
            "cement_markets": list(list(cement.values())[0].keys()) if cement else [],
            "fuel_types":     list(fuel.keys()),
            "cement_source":  cement_meta.get("source"),
            "fuel_source":    fuel_meta.get("source"),
        })
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)})

@app.route("/admin/fetch-prices", methods=["POST"])
def admin_fetch_prices():
    try:
        from price_fetcher import fetch_wfp_prices, update_sheet_with_wfp_prices
        prices = fetch_wfp_prices()
        meta   = prices.pop("_meta", {})
        if prices:
            update_sheet_with_wfp_prices({**prices, "_meta": meta})
            return jsonify({
                "status":             "ok",
                "commodities_fetched": list(prices.keys()),
                "source":             meta.get("source"),
                "date":               meta.get("date"),
            })
        else:
            return jsonify({"status": "no_data", "note": "WFP had no recent SL data — sheet unchanged"})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route("/admin/trigger-blast", methods=["POST"])
@_require_admin
def admin_trigger_blast():
    results = trigger_manual_blast()
    return jsonify({
        "sent":   sum(1 for r in results if r.get("status") == "success"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
    })

# ── NEW: WhatsApp preview ─────────────────────────────────────────────────────

@app.route("/admin/whatsapp-preview", methods=["GET"])
def admin_whatsapp_preview():
    """Preview WhatsApp messages for first 3 subscribers without sending."""
    try:
        from sheets import get_latest_prices
        from sms import format_whatsapp_food
        prices   = get_latest_prices()
        subs     = get_active_subscribers()[:3]
        previews = []
        for sub in subs:
            district = sub.get("district", "Western Area") or "Western Area"
            plan     = sub.get("plan", "free")
            previews.append({
                "phone":    sub.get("phone"),
                "name":     sub.get("name"),
                "district": district,
                "plan":     plan,
                "food_msg": format_whatsapp_food(sub.get("name", ""), district, prices, plan),
            })
        return jsonify({
            "status":       "ok",
            "previews":     previews,
            "total_active": len(get_active_subscribers()),
        })
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500

# ── NEW: WhatsApp blast ───────────────────────────────────────────────────────

@app.route("/admin/trigger-whatsapp-blast", methods=["POST"])
@_require_admin
def admin_trigger_whatsapp_blast():
    """Manually fires the weekly WhatsApp blast to all active subscribers."""
    try:
        from sheets import get_latest_prices
        from sms import run_weekly_whatsapp_blast
        prices  = get_latest_prices()
        results = run_weekly_whatsapp_blast(prices)
        return jsonify({
            "sent":   sum(1 for r in results if r.get("status") == "success"),
            "failed": sum(1 for r in results if r.get("status") == "failed"),
            "detail": results[:5],
        })
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500

# ── Signup ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def landing():
    return app.send_static_file("signup.html")

@app.route("/signup", methods=["POST"])
def signup():
    from sms import send_sms
    data     = request.get_json(silent=True) or {}
    name     = data.get("name", "").strip()
    phone    = data.get("phone", "").strip()
    location = data.get("location", "").strip()
    crops    = data.get("crops", [])
    if not name or not phone:
        return jsonify({"status": "error", "reason": "name and phone required"}), 400
    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    district = location if location else "Unknown"
    try:
        from sheets import add_subscriber
        add_subscriber(
            phone=phone, name=name, district=district,
            crops=crops if crops else ["rice", "cassava", "palm_oil"],
            plan="individual",
        )
        logger.info("New signup: %s %s %s", name, phone, district)
    except Exception as e:
        logger.warning("Signup sheet write failed: %s", e)
        os.makedirs("logs", exist_ok=True)
        with open("logs/signups_pending.txt", "a") as f:
            f.write(",".join([name, phone, district] + crops) + "\n")
    try:
        from config import CROPS as ALL_CROPS
        first_name = name.split()[0] if name else "Farmer"
        crop_names = ", ".join(ALL_CROPS[c]["name"] for c in (crops or ["rice","cassava","palm_oil"]) if c in ALL_CROPS)
        welcome_msg = (
            f"Welcome to SaloneMarket, {first_name}! "
            f"You are now subscribed for weekly market prices. "
            f"Tracking: {crop_names}. "
            f"Prices sent every Monday 7am. "
            f"Text 1-15 anytime for instant prices. "
            f"Reply STOP to unsubscribe."
        )[:160]
        send_sms(phone, welcome_msg)
    except Exception as sms_err:
        logger.warning("Welcome SMS failed: %s", sms_err)
    return jsonify({"status": "ok", "message": "Subscribed successfully"})
@app.route("/debug-sheets", methods=["GET"])
def debug_sheets():
    try:
        import os, json
        creds_raw = os.getenv("GOOGLE_CREDS_JSON", "").strip()
        info = json.loads(creds_raw)
        return jsonify({
            "status": "json_parsed",
            "project_id": info.get("project_id"),
            "client_email": info.get("client_email"),
            "private_key_starts": info.get("private_key","")[:30],
        })
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)})
@app.route("/debug-sheets2", methods=["GET"])
def debug_sheets2():
    try:
        from sheets import _get_client
        client = _get_client()
        return jsonify({"status": "connected", "email": client.auth.service_account_email})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)})
@app.route("/debug-sheets3", methods=["GET"])
def debug_sheets3():
    try:
        from sheets import _get_client
        from config import SUBSCRIBERS_SHEET_ID
        client = _get_client()
        sheet = client.open_by_key(SUBSCRIBERS_SHEET_ID)
        worksheets = [ws.title for ws in sheet.worksheets()]
        return jsonify({"status": "connected", "worksheets": worksheets})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)})

@app.route("/debug", methods=["GET"])
def debug_env():
    from config import PRICES_SHEET_ID, SUBSCRIBERS_SHEET_ID, AT_USERNAME, GOOGLE_CREDS_CONTENT
    creds = os.getenv("GOOGLE_CREDS_CONTENT", "")
    return jsonify({
        "env_PRICES_SHEET_ID":           os.getenv("PRICES_SHEET_ID", "NOT IN ENV"),
        "config_PRICES_SHEET_ID":         PRICES_SHEET_ID,
        "env_AT_USERNAME":               os.getenv("AT_USERNAME", "NOT IN ENV"),
        "config_AT_USERNAME":             AT_USERNAME,
        "GOOGLE_CREDS_CONTENT_length":   len(creds),
        "all_env_keys":                  [k for k in os.environ.keys() if "SALONE" in k or "GOOGLE" in k or "AT_" in k or "PRICES" in k],
    })

@app.route("/sms-inbound", methods=["POST"])
def sms_inbound():
    from sms import send_sms
    import re
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
    reply     = _build_whatsapp_reply(message, prices)
    reply_sms = re.sub(r'[*_]', '', reply)[:160]
    try:
        send_sms(phone, reply_sms)
    except Exception as e:
        logger.error("SMS reply failed: %s", e)
    return "OK", 200

@app.route("/whatsapp", methods=["POST"])
def whatsapp_inbound():
    data    = request.get_json(silent=True) or request.form.to_dict()
    phone   = data.get("from", data.get("phoneNumber", ""))
    message = data.get("text", data.get("message", "")).strip().lower()
    logger.info("WhatsApp from %s: %s", phone, message)
    try:
        from sheets import get_latest_prices
        prices = get_latest_prices()
    except Exception as e:
        prices = {}
    reply = _build_whatsapp_reply(message, prices)
    return jsonify({"message": reply}), 200

def _build_whatsapp_reply(message: str, prices: dict) -> str:
    from config import CROPS, MARKETS
    market_names = {"freetown": "Freetown", "bo": "Bo", "kenema": "Kenema", "makeni": "Makeni", "koidu": "Koidu"}
    crop_menu = {
        "1": "rice", "2": "cassava", "3": "palm_oil", "4": "groundnut",
        "5": "tomato", "6": "maize", "7": "fish_bonga", "8": "onion",
        "9": "cooking_oil", "10": "salt", "11": "pepper", "12": "sweet_potato",
        "13": "eggs", "14": "chicken", "15": "meat",
        "16": "cement_imported", "17": "cement_local",
        "18": "petrol", "19": "diesel", "20": "kerosene",
    }
    if message in ["join", "subscribe"]:
        return "✅ Just text a number anytime:\n1-Rice 2-Cassava 3-Palm Oil\n4-Groundnut 5-Tomato 6-Maize\n7-Bonga Fish 8-Onion 9-Cooking Oil\n10-Salt 11-Pepper 12-Sweet Potato\n\nFor weekly Monday alerts, reply WEEKLY."
    if message in ["stop", "unsubscribe"]:
        return "You've been unsubscribed from weekly alerts. Text any number 1-12 for instant prices anytime."
    if message == "weekly":
        return "📲 To get weekly Monday price alerts, dial *384*3844321# and select option 1."
    if message in crop_menu:
        crop_key  = crop_menu[message]
        crop_info = CROPS.get(crop_key, {})
        crop_name = crop_info.get("name", crop_key)
        unit      = crop_info.get("unit", "kg")
        emoji     = crop_info.get("emoji", "🌾")
        crop_prices = prices.get(crop_key, {})
        if not crop_prices:
            return f"Sorry, no prices available for {crop_name} today. Check back Monday!"
        from datetime import date
        today = date.today().strftime("%-d %b %Y")
        lines = [f"{emoji} *{crop_name} prices — {today}*", ""]
        best_market = ""
        best_price  = 0
        for market, price in sorted(crop_prices.items(), key=lambda x: -x[1]):
            mname = market_names.get(market, market.title())
            lines.append(f"📍 {mname}: *NLE {price:,}/{unit}*")
            if price > best_price:
                best_price  = price
                best_market = mname
        if best_market:
            lines += ["", f"💡 Best price: Sell in *{best_market}* this week!"]
        lines += ["", "Text another number for more prices."]
        return "\n".join(lines)
    return (
        "🌾 *Welcome to SaloneMarket!*\n"
        "Na wi yone price service for Salone 🇸🇱\n\n"
        "Text a number for TODAY's market prices:\n\n"
        "1-Rice  2-Cassava  3-Palm Oil\n"
        "4-Groundnut  5-Tomato  6-Maize\n"
        "7-Bonga Fish  8-Onion  9-Cooking Oil\n"
        "10-Salt  11-Pepper  12-Sweet Potato\n"
        "13-Eggs  14-Chicken  15-Meat\n"
        "16-Cement Imported  17-Cement Local\n"
        "18-Petrol  19-Diesel  20-Kerosene\n\n"
        "Type WEEKLY for Monday morning alerts 📲"
    )

@app.route("/test", methods=["GET"])
def test_page():
    try:
        from sheets import get_latest_prices, get_active_subscribers
        prices     = get_latest_prices()
        subs       = get_active_subscribers()
        sample_msg = format_price_sms(prices, ["rice", "cassava", "palm_oil"])
        return jsonify({
            "status":             "live",
            "service":            "SaloneMarket 🌾",
            "active_subscribers": len(subs),
            "crops_loaded":       list(prices.keys()),
            "sample_sms":         sample_msg,
            "ussd_code":          "*384*3844321#",
        })
    except Exception as e:
        return jsonify({"status": "ok", "note": str(e)}), 200

@app.route("/admin", methods=["GET"])
def admin_panel():
    html = """<!DOCTYPE html>
<html>
<head>
  <title>SaloneMarket Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body{font-family:sans-serif;max-width:640px;margin:40px auto;padding:20px;background:#f9f9f9}
    h1{color:#2d6a4f}
    .card{background:white;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
    button{background:#2d6a4f;color:white;border:none;padding:12px 24px;border-radius:8px;font-size:15px;cursor:pointer;width:100%;margin-top:8px}
    button:hover{background:#1b4332}
    .result{margin-top:12px;padding:12px;background:#f0f4f0;border-radius:8px;font-family:monospace;font-size:13px;white-space:pre-wrap;display:none}
    .badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;background:#d8f3dc;color:#1b4332;margin-bottom:8px}
    input{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;font-size:14px;margin-top:4px}
    label{font-size:13px;color:#555}
  </style>
</head>
<body>
  <h1>🌾 SaloneMarket Admin</h1>
  <div class="card">
    <span class="badge">Live</span><h3 style="margin:0 0 4px">System Status</h3>
    <button onclick="callApi('GET','/test',null,'s1')">Check Status</button>
    <div class="result" id="s1"></div>
  </div>
  <div class="card">
    <span class="badge">Auto</span><h3 style="margin:0 0 4px">Fetch WFP Prices</h3>
    <button onclick="callApi('POST','/admin/fetch-prices',null,'s2')">Fetch WFP Prices Now</button>
    <div class="result" id="s2"></div>
  </div>
  <div class="card">
    <span class="badge" style="background:#2d6a9f;color:white">⛽ Fuel</span><h3 style="margin:0 0 4px">Fetch Live Fuel Prices</h3>
    <button onclick="callApi('GET','/admin/fetch-fuel-prices',null,'s3')" style="background:#2d6a9f">Fetch Fuel Prices Now</button>
    <div class="result" id="s3"></div>
  </div>
  <div class="card">
    <span class="badge" style="background:#c8973a;color:#333">Ministry</span><h3 style="margin:0 0 4px">Cement &amp; Fuel Setup</h3>
    <button onclick="callApi('POST','/admin/setup-cement-fuel-tabs',null,'s4')" style="background:#c8973a">Create Cement &amp; Fuel Tabs</button>
    <div class="result" id="s4"></div>
    <button onclick="callApi('POST','/admin/fetch-cement-fuel',null,'s5')" style="background:#c8973a;margin-top:8px">Update Cement &amp; Fuel Prices</button>
    <div class="result" id="s5"></div>
  </div>
  <div class="card">
    <span class="badge">Preview SMS</span><h3 style="margin:0 0 4px">Preview This Week's SMS</h3>
    <button onclick="callApi('GET','/admin/blast-preview',null,'s6')">Preview SMS Blast</button>
    <div class="result" id="s6"></div>
  </div>
  <div class="card">
    <span class="badge" style="background:#25D366;color:white">📱 WhatsApp</span><h3 style="margin:0 0 4px">Preview WhatsApp Digest</h3>
    <p style="color:#555;font-size:14px">See what subscribers will receive on WhatsApp without sending</p>
    <button onclick="callApi('GET','/admin/whatsapp-preview',null,'s10')" style="background:#25D366">Preview WhatsApp Messages</button>
    <div class="result" id="s10"></div>
  </div>
  <div class="card">
    <span class="badge" style="background:#25D366;color:white">📱 Send</span><h3 style="margin:0 0 4px">Send WhatsApp Blast</h3>
    <p style="color:#555;font-size:14px">Send this week's WhatsApp digest to all subscribers now</p>
    <button onclick="callApi('POST','/admin/trigger-whatsapp-blast',null,'s11')" style="background:#128C7E">Send WhatsApp to All Subscribers</button>
    <div class="result" id="s11"></div>
  </div>
  <div class="card">
    <span class="badge">Send SMS</span><h3 style="margin:0 0 4px">Trigger SMS Blast</h3>
    <button onclick="callApi('POST','/admin/trigger-blast',null,'s7')" style="background:#c0392b">Send SMS to All Subscribers</button>
    <div class="result" id="s7"></div>
  </div>
  <div class="card">
    <span class="badge">Subscribers</span><h3 style="margin:0 0 4px">Subscriber Count</h3>
    <button onclick="callApi('GET','/admin/subscribers',null,'s8')">View Subscribers</button>
    <div class="result" id="s8"></div>
  </div>
  <div class="card">
    <span class="badge">Test SMS</span><h3 style="margin:0 0 4px">Send Test SMS</h3>
    <label>Phone number (include country code)</label>
    <input id="test-phone" type="text" placeholder="+19199234764" value="+19199234764">
    <button onclick="sendTestSMS()">Send Test SMS Now</button>
    <div class="result" id="s9"></div>
  </div>
  <div class="card">
    <span class="badge">WhatsApp Test</span><h3 style="margin:0 0 4px">Test WhatsApp Price Lookup</h3>
    <label>Message (1-20 or JOIN or STOP)</label>
    <input id="wa-msg" type="text" placeholder="1" value="1">
    <button onclick="testWhatsapp()">Simulate WhatsApp Message</button>
    <div class="result" id="s12"></div>
  </div>
<script>
  const KEY='saloneprices2024';
  async function callApi(method,url,body,id){
    const el=document.getElementById(id);el.style.display='block';el.textContent='Loading...';
    try{
      const opts={method,headers:{'X-Admin-Key':KEY,'Content-Type':'application/json'}};
      if(body)opts.body=JSON.stringify(body);
      const res=await fetch(url,opts);
      el.textContent=JSON.stringify(await res.json(),null,2);
    }catch(e){el.textContent='Error: '+e.message;}
  }
  async function sendTestSMS(){
    const phone=document.getElementById('test-phone').value;
    const el=document.getElementById('s9');el.style.display='block';el.textContent='Sending to '+phone+'...';
    try{
      const res=await fetch('/admin/send-test-sms',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone})});
      el.textContent=JSON.stringify(await res.json(),null,2);
    }catch(e){el.textContent='Error: '+e.message;}
  }
  async function testWhatsapp(){
    const msg=document.getElementById('wa-msg').value;
    const el=document.getElementById('s12');el.style.display='block';el.textContent='Loading...';
    try{
      const res=await fetch('/whatsapp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from:'+23276000001',text:msg})});
      const data=await res.json();el.textContent=data.message||JSON.stringify(data,null,2);
    }catch(e){el.textContent='Error: '+e.message;}
  }
</script>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html"}

@app.route("/admin/send-test-sms", methods=["POST"])
def send_test_sms():
    try:
        from twilio.rest import Client
        sid   = os.getenv("TWILIO_ACCOUNT_SID") or "ACf4a122b66ac0a014d516453eeac070c8"
        token = os.getenv("TWILIO_AUTH_TOKEN")  or "3f40fc6f4f2c93af2860c2f6d858cf12"
        frm   = os.getenv("TWILIO_FROM_NUMBER") or "+12295623289"
        data  = request.get_json(silent=True) or {}
        phone = data.get("phone", "")
        if not phone:
            return jsonify({"error": "phone required"}), 400
        msg = "SaloneMarket SL! Text a number for prices: 1=Rice 2=Cassava 3=PalmOil 4=Groundnut 5=Tomato 6=Maize 7=Fish 8=Onion 9=Oil 10=Salt 11=Pepper 12=SweetPotato 13=Eggs 14=Chicken 15=Meat. Reply now!"
        client  = Client(sid, token)
        message = client.messages.create(body=msg, from_=frm, to=phone)
        return jsonify({"status": "sent", "message_sid": message.sid, "to": phone})
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "SaloneMarket"}), 200

if __name__ == "__main__":
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_ENV", "production") == "development"
    logger.info("Starting SaloneMarket on port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
