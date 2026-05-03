# SaloneMarket 🌾

**Automated crop price SMS alert service for Sierra Leone.**
Weekly price alerts sent directly to farmers' phones. No smartphone required.

---

## What this does

Every Monday at 7am, SaloneMarket:
1. Reads crop prices from your Google Sheet (populated by your market informants)
2. Formats a personalised 160-char SMS per subscriber showing prices for their chosen crops
3. Sends the SMS via Africa's Talking to every active/trial subscriber
4. Includes a "best market" tip (e.g. "Best: sell Rice in Freetown this week")

Farmers subscribe by dialling `*384*4321#` on any phone. Payment is collected via Orange Money or Afrimoney. No app, no internet, no smartphone needed.

---

## Project structure

```
salonemarket/
├── app.py          # Flask web server (USSD callback, payment webhook, admin)
├── config.py       # All settings (API keys loaded from .env)
├── payments.py     # Orange Money webhook handler
├── scheduler.py    # APScheduler cron jobs (weekly blast + reminders)
├── sheets.py       # Google Sheets read/write (prices + subscribers)
├── sms.py          # SMS formatting and Africa's Talking send
├── ussd.py         # USSD menu flow (registration, crop selection, unsub)
├── requirements.txt
├── railway.toml    # Railway.app deployment
├── .env.example    # Copy to .env and fill in
└── tests/
    └── test_salonemarket.py
```

---

## Setup (step by step)

### 1. Clone and install

```bash
git clone https://github.com/yourname/salonemarket.git
cd salonemarket
pip install -r requirements.txt
cp .env.example .env
```

### 2. Google Sheets setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project → Enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** → download the JSON key file → save as `google_credentials.json`
4. Create a Google Sheet with these tabs:
   - `Subscribers` (columns: phone, name, district, crops, plan, status, joined_date, paid_until, association)
   - `Rice` (columns: date, market, price_nle, source)
   - `Cassava`, `PalmOil`, `Cocoa`, `Groundnut`, `Tomato` (same columns)
5. Share the sheet with the service account email (from the JSON file)
6. Copy the Sheet ID from the URL into `.env`

### 3. Africa's Talking setup

1. Sign up at [africastalking.com](https://africastalking.com)
2. Create an application → get your API key and username
3. Register a USSD code (e.g. `*384*4321#`) → set callback URL to `https://yourdomain.com/ussd`
4. Register a Sender ID `SaloneMarket` (requires AT approval, takes 1-3 days)
5. Add credentials to `.env`

### 4. Orange Money setup

1. Register as a merchant at [developer.orange.com](https://developer.orange.com)
2. Create an app → get Client ID, Client Secret, and Merchant Key
3. Set your notification URL to `https://yourdomain.com/webhooks/orange-money`
4. Add credentials to `.env`

### 5. Run locally

```bash
# Start the web server
python app.py

# In another terminal, test the USSD flow manually
curl -X POST http://localhost:5000/ussd \
  -d "sessionId=test123&phoneNumber=+23276000001&text=&serviceCode=*384*4321#"

# Preview this week's blast (requires ADMIN_API_KEY in .env)
curl http://localhost:5000/admin/blast-preview \
  -H "X-Admin-Key: yourkey"

# Trigger a manual blast
curl -X POST http://localhost:5000/admin/trigger-blast \
  -H "X-Admin-Key: yourkey"
```

### 6. Run tests

```bash
pytest tests/ -v
```

### 7. Deploy to Railway.app (free tier)

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Set environment variables in the Railway dashboard (same as your `.env` file).
Railway will auto-detect the `railway.toml` and use gunicorn.

---

## Weekly data entry workflow

Every **Sunday evening** (or Monday before 7am), your market informants WhatsApp you prices.
You (or they, if you give them access) enter them into the Google Sheet:

| date       | market    | price_nle | source        |
|------------|-----------|-----------|---------------|
| 2024-04-28 | bo        | 420       | Ibrahim Sesay |
| 2024-04-28 | freetown  | 460       | Fatmata Koroma|
| 2024-04-28 | kenema    | 410       | Alhaji Bangura|

The scheduler reads this at 7am Monday and builds the SMS automatically.

**Tip:** Create a simple Google Form that your informants can fill in on their phone.
Link it to the same Sheet. Zero data entry for you.

---

## Revenue model

| Tier | Price | Who |
|------|-------|-----|
| Individual farmer | NLE 5,000/mo (~$0.25) | Smallholder farmers |
| Farmers' association | NLE 500,000/mo (~$25) | 500-member groups |
| NGO/project contract | $50–150/mo | WFP, FAO, GIZ programs |
| Sponsored alert | NLE 2,000,000/blast (~$100) | Agro-input dealers |

### Month 6 conservative projection

| Revenue stream | Monthly |
|---------------|---------|
| 200 individual farmers | $50 |
| 4 associations × $25 | $100 |
| 2 NGO contracts × $75 | $150 |
| 4 sponsored blasts | $100 |
| SMS costs (2,000 subs) | -$80 |
| **Net** | **$320** |

---

## Environment variables (`.env`)

| Variable | Description |
|----------|-------------|
| `AT_API_KEY` | Africa's Talking API key |
| `AT_USERNAME` | Africa's Talking username |
| `AT_SENDER_ID` | Approved sender ID (default: `SaloneMarket`) |
| `ORANGE_CLIENT_ID` | Orange Money client ID |
| `ORANGE_CLIENT_SECRET` | Orange Money secret (used for webhook HMAC) |
| `ORANGE_MERCHANT_KEY` | Orange Money merchant key |
| `ORANGE_NOTIF_URL` | Your webhook URL for Orange Money callbacks |
| `GOOGLE_CREDS_JSON` | Path to Google service account JSON file |
| `PRICES_SHEET_ID` | Google Sheet ID for price data |
| `SUBSCRIBERS_SHEET_ID` | Google Sheet ID for subscribers (can be same sheet) |
| `ADMIN_API_KEY` | Secret key for admin routes |
| `PORT` | Server port (default: 5000) |

---

## Contacts for Sierra Leone integration

- **Africa's Talking SL support:** support@africastalking.com
- **Orange Money merchant:** developer.orange.com → Sierra Leone
- **Africell Afrimoney business:** contact Africell directly for bulk/API access
- **NFSL (farmers' association):** Search "National Farmers Sierra Leone Freetown"
- **WFP Sierra Leone:** wfp.sierraleone@wfp.org
- **iDT Labs Freetown:** idtlabs.xyz

---

## License

MIT – build freely, sell openly.
