"""
SaloneMarket – Seed sample price data into all 12 crop tabs
Run once to populate the Google Sheet with realistic sample data.
"""
import json
import os
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1K7vaC5oT9cujtzpHTal9bBnll85y8sZ-f0f5qnnvlZQ"
CREDS_FILE = "google_credentials.json"
TODAY = date.today().isoformat()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Realistic Sierra Leone market prices (NLE)
SAMPLE_DATA = {
    "Rice":        [
        [TODAY, "freetown", 460, "Sample"],
        [TODAY, "bo",       420, "Sample"],
        [TODAY, "kenema",   410, "Sample"],
        [TODAY, "makeni",   415, "Sample"],
        [TODAY, "koidu",    430, "Sample"],
    ],
    "Cassava":     [
        [TODAY, "freetown", 95,  "Sample"],
        [TODAY, "bo",       80,  "Sample"],
        [TODAY, "kenema",   85,  "Sample"],
        [TODAY, "makeni",   78,  "Sample"],
    ],
    "PalmOil":     [
        [TODAY, "freetown", 1200, "Sample"],
        [TODAY, "bo",       1150, "Sample"],
        [TODAY, "kenema",   1100, "Sample"],
        [TODAY, "makeni",   1180, "Sample"],
    ],
    "Groundnut":   [
        [TODAY, "freetown", 380, "Sample"],
        [TODAY, "bo",       350, "Sample"],
        [TODAY, "kenema",   360, "Sample"],
    ],
    "Tomato":      [
        [TODAY, "freetown", 120, "Sample"],
        [TODAY, "bo",       100, "Sample"],
        [TODAY, "kenema",   95,  "Sample"],
    ],
    "Maize":       [
        [TODAY, "bo",       280, "Sample"],
        [TODAY, "kenema",   260, "Sample"],
        [TODAY, "makeni",   270, "Sample"],
    ],
    "FishBonga":   [
        [TODAY, "freetown", 8500, "Sample"],
        [TODAY, "bo",       8000, "Sample"],
        [TODAY, "kenema",   7800, "Sample"],
    ],
    "Onion":       [
        [TODAY, "freetown", 150, "Sample"],
        [TODAY, "bo",       130, "Sample"],
        [TODAY, "makeni",   140, "Sample"],
    ],
    "CookingOil":  [
        [TODAY, "freetown", 1500, "Sample"],
        [TODAY, "bo",       1400, "Sample"],
        [TODAY, "kenema",   1380, "Sample"],
    ],
    "Salt":        [
        [TODAY, "freetown", 45, "Sample"],
        [TODAY, "bo",       40, "Sample"],
        [TODAY, "makeni",   42, "Sample"],
    ],
    "Pepper":      [
        [TODAY, "freetown", 200, "Sample"],
        [TODAY, "kenema",   180, "Sample"],
        [TODAY, "bo",       190, "Sample"],
    ],
    "SweetPotato": [
        [TODAY, "bo",       90,  "Sample"],
        [TODAY, "freetown", 100, "Sample"],
        [TODAY, "kenema",   85,  "Sample"],
    ],
}

def seed():
    print("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID)

    for tab_name, rows in SAMPLE_DATA.items():
        try:
            ws = sheet.worksheet(tab_name)
            # Clear existing data below header
            ws.delete_rows(2, ws.row_count)
            # Add all rows at once
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            print(f"  ✅ {tab_name}: {len(rows)} rows added")
        except gspread.WorksheetNotFound:
            print(f"  ❌ {tab_name}: tab not found — create it first!")
        except Exception as e:
            print(f"  ❌ {tab_name}: error — {e}")

    print("\nDone! Your Google Sheet is seeded with sample prices.")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")

if __name__ == "__main__":
    seed()
