"""
SalonePrices – Configuration
All API keys and settings live here. Copy .env.example → .env and fill in your values.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Africa's Talking ─────────────────────────────────────────────────────────
AT_API_KEY        = os.getenv("AT_API_KEY", "")
AT_USERNAME       = os.getenv("AT_USERNAME", "sandbox")   # change to your username in prod
AT_SENDER_ID      = os.getenv("AT_SENDER_ID", "SalonePrices")  # must be approved by AT
AT_USSD_CODE      = os.getenv("AT_USSD_CODE", "*384*4321#")

# ── Orange Money ─────────────────────────────────────────────────────────────
ORANGE_CLIENT_ID     = os.getenv("ORANGE_CLIENT_ID", "")
ORANGE_CLIENT_SECRET = os.getenv("ORANGE_CLIENT_SECRET", "")
ORANGE_MERCHANT_KEY  = os.getenv("ORANGE_MERCHANT_KEY", "")
ORANGE_NOTIF_URL     = os.getenv("ORANGE_NOTIF_URL", "https://yourdomain.com/webhooks/orange-money")
# Orange Money Sierra Leone country code
ORANGE_COUNTRY_CODE  = "SL"
ORANGE_CURRENCY      = "SLE"  # new Leone

# ── Google Sheets ────────────────────────────────────────────────────────────
GOOGLE_CREDS_JSON    = os.getenv("GOOGLE_CREDS_JSON", "google_credentials.json")  # relative path works on Railway
PRICES_SHEET_ID      = os.getenv("PRICES_SHEET_ID", "")   # your Google Sheet ID from the URL
SUBSCRIBERS_SHEET_ID = os.getenv("SUBSCRIBERS_SHEET_ID", "")  # can be same sheet, different tab

# ── App settings ─────────────────────────────────────────────────────────────
SUBSCRIPTION_FEE_NLE = 5_000        # NLE per month per individual farmer
ASSOCIATION_FEE_NLE  = 500_000      # NLE per month per farmers' association (500 members)
FREE_TRIAL_WEEKS     = 4            # weeks before payment is required
SMS_SEND_HOUR        = 7            # 07:00 Freetown time (UTC+0)
LOG_DIR              = "logs"

# ── Markets ──────────────────────────────────────────────────────────────────
MARKETS = {
    "freetown": {"name": "Freetown (Abacha St)", "district": "Western Area"},
    "bo":       {"name": "Bo Market",            "district": "Bo"},
    "kenema":   {"name": "Kenema Market",        "district": "Kenema"},
    "makeni":   {"name": "Makeni Market",        "district": "Bombali"},
    "koidu":    {"name": "Koidu Market",         "district": "Kono"},
}

# ── Crops tracked ────────────────────────────────────────────────────────────
CROPS = {
    "rice":        {"name": "Rice",        "unit": "kg",    "emoji": "🍚", "sheet_tab": "Rice"},
    "cassava":     {"name": "Cassava",     "unit": "kg",    "emoji": "🌿", "sheet_tab": "Cassava"},
    "palm_oil":    {"name": "Palm Oil",    "unit": "litre", "emoji": "🫙", "sheet_tab": "PalmOil"},
    "groundnut":   {"name": "Groundnut",   "unit": "kg",    "emoji": "🥜", "sheet_tab": "Groundnut"},
    "tomato":      {"name": "Tomato",      "unit": "kg",    "emoji": "🍅", "sheet_tab": "Tomato"},
    "maize":       {"name": "Maize",       "unit": "kg",    "emoji": "🌽", "sheet_tab": "Maize"},
    "fish_bonga":  {"name": "Bonga Fish",  "unit": "kg",    "emoji": "🐟", "sheet_tab": "FishBonga"},
    "onion":       {"name": "Onion",       "unit": "kg",    "emoji": "🧅", "sheet_tab": "Onion"},
    "cooking_oil": {"name": "Cooking Oil", "unit": "litre", "emoji": "🫚", "sheet_tab": "CookingOil"},
    "salt":        {"name": "Salt",        "unit": "kg",    "emoji": "🪨", "sheet_tab": "Salt"},
    "pepper":      {"name": "Pepper",      "unit": "kg",    "emoji": "🌶️", "sheet_tab": "Pepper"},
    "sweet_potato":{"name": "Sweet Potato","unit": "kg",    "emoji": "🍠", "sheet_tab": "SweetPotato"},
}

# ── Districts (for USSD registration) ────────────────────────────────────────
DISTRICTS = {
    "1": "Western Area",
    "2": "Bo",
    "3": "Kenema",
    "4": "Bombali",
    "5": "Kono",
    "6": "Kailahun",
    "7": "Kambia",
    "8": "Moyamba",
    "9": "Port Loko",
    "10": "Pujehun",
    "11": "Tonkolili",
    "12": "Falaba",
}
