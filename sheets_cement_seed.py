"""
sheets_cement_seed.py
─────────────────────
One-shot script (and importable helper) to write the official Ministry of
Trade & Industry cement prices into the SaloneMarket Google Sheet.

Sheet layout expected (tab "Cement Prices"):
  Row 1:  headers
  Col A:  District
  Col B:  Imported 42.5R (retail, NLe)
  Col C:  Local 32.5R (retail, NLe)
  Col D:  Imported Wholesale (NLe)   ← same for every row
  Col E:  Local Wholesale (NLe)      ← same for every row
  Col F:  Source / date updated

Run:
    python sheets_cement_seed.py

Or import and call:
    from sheets_cement_seed import seed_cement_prices
    seed_cement_seed()
"""

import os
import sys
from datetime import date

# ── Google Sheets auth (same pattern as sheets.py) ───────────────────────────
def _get_gspread_client():
    import json
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_json = os.getenv("GOOGLE_CREDS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        # fallback: file path (local dev)
        creds_path = os.getenv("GOOGLE_CREDS_PATH", "credentials.json")
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)

    return gspread.authorize(creds)


# ── Cement data ───────────────────────────────────────────────────────────────
from cement_prices import (
    CEMENT_RETAIL_IMPORTED,
    CEMENT_RETAIL_LOCAL,
    CEMENT_IMPORTED_WHOLESALE,
    CEMENT_LOCAL_WHOLESALE,
)

SHEET_ID   = os.getenv("PRICES_SHEET_ID", "1K7vaC5oT9cujtzpHTal9bBnll85y8sZ-f0f5qnnvlZQ")
TAB_NAME   = "Cement Prices"
SOURCE_STR = f"Ministry of Trade & Industry Public Notice · updated {date.today().isoformat()}"

HEADERS = [
    "District",
    "Imported 42.5R Retail (NLe)",
    "Local 32.5R Retail (NLe)",
    "Imported Wholesale (NLe)",
    "Local Wholesale (NLe)",
    "Source",
]


def seed_cement_prices(dry_run: bool = False) -> list[list]:
    """
    Writes all district cement prices to the Google Sheet.
    Returns the rows that would be / were written.
    """
    rows = [HEADERS]
    for district in sorted(CEMENT_RETAIL_IMPORTED.keys()):
        rows.append([
            district,
            CEMENT_RETAIL_IMPORTED[district],
            CEMENT_RETAIL_LOCAL[district],
            CEMENT_IMPORTED_WHOLESALE,
            CEMENT_LOCAL_WHOLESALE,
            SOURCE_STR,
        ])

    if dry_run:
        print("=== DRY RUN — rows to write ===")
        for r in rows:
            print(r)
        return rows

    gc = _get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)

    # Get or create the "Cement Prices" worksheet
    try:
        ws = sh.worksheet(TAB_NAME)
        ws.clear()
        print(f"Cleared existing tab '{TAB_NAME}'")
    except Exception:
        ws = sh.add_worksheet(title=TAB_NAME, rows=50, cols=10)
        print(f"Created new tab '{TAB_NAME}'")

    ws.update("A1", rows)
    print(f"✅  Wrote {len(rows) - 1} district rows to '{TAB_NAME}'")
    return rows


# ── Read-back helper used by app.py / sms.py ─────────────────────────────────

def load_cement_prices_from_sheet() -> dict:
    """
    Reads cement prices from Google Sheets and returns a prices dict
    compatible with format_whatsapp_cement().

    Falls back to the hardcoded cement_prices.py constants if the sheet
    is unavailable.
    """
    try:
        gc = _get_gspread_client()
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.worksheet(TAB_NAME)
        records = ws.get_all_records()  # list of dicts keyed by header row

        imported = {}
        local    = {}
        imp_whl  = CEMENT_IMPORTED_WHOLESALE
        loc_whl  = CEMENT_LOCAL_WHOLESALE

        for row in records:
            d = row.get("District", "").strip()
            if not d:
                continue
            try:
                imported[d] = int(row["Imported 42.5R Retail (NLe)"])
                local[d]    = int(row["Local 32.5R Retail (NLe)"])
                imp_whl     = int(row["Imported Wholesale (NLe)"])
                loc_whl     = int(row["Local Wholesale (NLe)"])
            except (ValueError, KeyError):
                pass

        return {
            "cement_imported":           imported,
            "cement_local":              local,
            "cement_imported_wholesale": imp_whl,
            "cement_local_wholesale":    loc_whl,
        }

    except Exception as exc:
        print(f"[cement] Sheet read failed ({exc}), using hardcoded fallback")
        from cement_prices import CEMENT_PRICES
        return CEMENT_PRICES


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    seed_cement_prices(dry_run=dry)
