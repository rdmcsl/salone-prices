"""
SalonePrices – Google Sheets bootstrapper

Run this ONCE to create the correct sheet structure in your Google Sheet.
After running, you'll have:
  - A "Subscribers" tab with the right headers
  - A tab for each crop (Rice, Cassava, PalmOil, Cocoa, Groundnut, Tomato)
  - A sample row in each crop tab so you know the format

Usage:
    python setup_sheets.py

Make sure GOOGLE_CREDS_JSON, PRICES_SHEET_ID are set in your .env first.
"""

import sys
from datetime import date

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

from config import CROPS, GOOGLE_CREDS_JSON, PRICES_SHEET_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SUBSCRIBER_HEADERS = [
    "phone", "name", "district", "crops",
    "plan", "status", "joined_date", "paid_until", "association"
]

PRICE_HEADERS = ["date", "market", "price_nle", "source"]

SAMPLE_PRICE_ROWS = {
    "rice":      [["2024-04-28", "freetown", 460, "Fatmata Koroma"],
                  ["2024-04-28", "bo",       420, "Ibrahim Sesay"],
                  ["2024-04-28", "kenema",   410, "Alhaji Bangura"]],
    "cassava":   [["2024-04-28", "freetown", 95,  "Fatmata Koroma"],
                  ["2024-04-28", "bo",       80,  "Ibrahim Sesay"]],
    "palm_oil":  [["2024-04-28", "freetown", 1200, "Fatmata Koroma"],
                  ["2024-04-28", "bo",       1150, "Ibrahim Sesay"]],
    "cocoa":     [["2024-04-28", "kenema",   18000, "Alhaji Bangura"]],
    "groundnut": [["2024-04-28", "bo",       350,  "Ibrahim Sesay"]],
    "tomato":    [["2024-04-28", "freetown", 120,  "Fatmata Koroma"]],
}


def setup_sheets():
    print("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_JSON, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(PRICES_SHEET_ID)

    existing_tabs = [ws.title for ws in sheet.worksheets()]
    print(f"Existing tabs: {existing_tabs}")

    # ── Subscribers tab ───────────────────────────────────────────────────────
    if "Subscribers" not in existing_tabs:
        print("Creating Subscribers tab...")
        ws = sheet.add_worksheet(title="Subscribers", rows=1000, cols=10)
        ws.append_row(SUBSCRIBER_HEADERS)
        # Format header row bold (optional cosmetic)
        ws.format("A1:I1", {"textFormat": {"bold": True}})
        print("  ✓ Subscribers tab created")
    else:
        print("  ✓ Subscribers tab already exists")

    # ── Crop price tabs ───────────────────────────────────────────────────────
    for crop_key, crop_info in CROPS.items():
        tab_name = crop_info["sheet_tab"]
        if tab_name not in existing_tabs:
            print(f"Creating {tab_name} tab...")
            ws = sheet.add_worksheet(title=tab_name, rows=500, cols=5)
            ws.append_row(PRICE_HEADERS)
            ws.format("A1:D1", {"textFormat": {"bold": True}})
            # Add sample data
            sample_rows = SAMPLE_PRICE_ROWS.get(crop_key, [])
            for row in sample_rows:
                ws.append_row(row)
            print(f"  ✓ {tab_name} tab created with {len(sample_rows)} sample rows")
        else:
            print(f"  ✓ {tab_name} tab already exists")

    print("\nSetup complete! Your sheet is ready.")
    print(f"Sheet URL: https://docs.google.com/spreadsheets/d/{PRICES_SHEET_ID}")
    print("\nNext steps:")
    print("  1. Add your market informants as editors on the sheet")
    print("  2. Create a Google Form linked to each crop tab for easy data entry")
    print("  3. Run: python app.py  to start the server")


if __name__ == "__main__":
    setup_sheets()
