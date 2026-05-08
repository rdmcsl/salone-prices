"""
SaloneMarket – Create Cement & Fuel Google Sheet tabs

Run this once to add CementImported, CementLocal, Petrol, Diesel, Kerosene tabs
to your existing Google Sheet with the same format as the agricultural commodity tabs.

Data pre-loaded from the Ministry of Trade Public Notice (May 2026).
"""

import logging
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SHEET_ID = "1K7vaC5oT9cujtzpHTal9bBnll85y8sZ-f0f5qnnvlZQ"
SCOPES   = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TODAY = date.today().isoformat()

HEADER = ["date", "market", "price_nle", "unit", "source"]

# ── Tab definitions ───────────────────────────────────────────────────────────
TABS = {
    "CementImported": {
        "label": "Imported Cement 42.5R (per 50kg bag)",
        "unit": "bag",
        "source": "Ministry of Trade and Industry",
        "rows": [
            # 5 main markets (used for SMS/WhatsApp alerts)
            [TODAY, "freetown",  205, "bag", "Ministry of Trade"],
            [TODAY, "bo",        225, "bag", "Ministry of Trade"],
            [TODAY, "kenema",    230, "bag", "Ministry of Trade"],
            [TODAY, "makeni",    222, "bag", "Ministry of Trade"],
            [TODAY, "koidu",     233, "bag", "Ministry of Trade"],
            # All districts from public notice
            [TODAY, "port_loko",  220, "bag", "Ministry of Trade"],
            [TODAY, "kailahun",   240, "bag", "Ministry of Trade"],
            [TODAY, "kambia",     222, "bag", "Ministry of Trade"],
            [TODAY, "koinadugu",  233, "bag", "Ministry of Trade"],
            [TODAY, "moyamba",    227, "bag", "Ministry of Trade"],
            [TODAY, "bonthe",     237, "bag", "Ministry of Trade"],
            [TODAY, "pujehun",    235, "bag", "Ministry of Trade"],
            [TODAY, "tonkolili",  223, "bag", "Ministry of Trade"],
            [TODAY, "karene",     245, "bag", "Ministry of Trade"],
            # Wholesale price
            [TODAY, "wholesale",  175, "bag", "Ministry of Trade"],
        ]
    },
    "CementLocal": {
        "label": "Local Cement 32.5R (per 50kg bag)",
        "unit": "bag",
        "source": "Ministry of Trade and Industry",
        "rows": [
            [TODAY, "freetown",  195, "bag", "Ministry of Trade"],
            [TODAY, "bo",        215, "bag", "Ministry of Trade"],
            [TODAY, "kenema",    220, "bag", "Ministry of Trade"],
            [TODAY, "makeni",    212, "bag", "Ministry of Trade"],
            [TODAY, "koidu",     223, "bag", "Ministry of Trade"],
            [TODAY, "port_loko",  210, "bag", "Ministry of Trade"],
            [TODAY, "kailahun",   230, "bag", "Ministry of Trade"],
            [TODAY, "kambia",     212, "bag", "Ministry of Trade"],
            [TODAY, "koinadugu",  223, "bag", "Ministry of Trade"],
            [TODAY, "moyamba",    217, "bag", "Ministry of Trade"],
            [TODAY, "bonthe",     227, "bag", "Ministry of Trade"],
            [TODAY, "pujehun",    225, "bag", "Ministry of Trade"],
            [TODAY, "tonkolili",  213, "bag", "Ministry of Trade"],
            [TODAY, "karene",     235, "bag", "Ministry of Trade"],
            [TODAY, "wholesale",  165, "bag", "Ministry of Trade"],
        ]
    },
    "Petrol": {
        "label": "Petrol / Premium (per litre) — NPC national pump price",
        "unit": "litre",
        "source": "NPC Sierra Leone",
        "rows": [
            # Fuel is nationally priced — same across all markets
            [TODAY, "freetown", 28, "litre", "NPC Sierra Leone"],
            [TODAY, "bo",       28, "litre", "NPC Sierra Leone"],
            [TODAY, "kenema",   28, "litre", "NPC Sierra Leone"],
            [TODAY, "makeni",   28, "litre", "NPC Sierra Leone"],
            [TODAY, "koidu",    28, "litre", "NPC Sierra Leone"],
        ]
    },
    "Diesel": {
        "label": "Diesel (per litre) — NPC national pump price",
        "unit": "litre",
        "source": "NPC Sierra Leone",
        "rows": [
            [TODAY, "freetown", 27, "litre", "NPC Sierra Leone"],
            [TODAY, "bo",       27, "litre", "NPC Sierra Leone"],
            [TODAY, "kenema",   27, "litre", "NPC Sierra Leone"],
            [TODAY, "makeni",   27, "litre", "NPC Sierra Leone"],
            [TODAY, "koidu",    27, "litre", "NPC Sierra Leone"],
        ]
    },
    "Kerosene": {
        "label": "Kerosene (per litre) — NPC national pump price",
        "unit": "litre",
        "source": "NPC Sierra Leone",
        "rows": [
            [TODAY, "freetown", 25, "litre", "NPC Sierra Leone"],
            [TODAY, "bo",       25, "litre", "NPC Sierra Leone"],
            [TODAY, "kenema",   25, "litre", "NPC Sierra Leone"],
            [TODAY, "makeni",   25, "litre", "NPC Sierra Leone"],
            [TODAY, "koidu",    25, "litre", "NPC Sierra Leone"],
        ]
    },
}


def _get_client():
    import json as _json
    import os
    creds_content = os.getenv("GOOGLE_CREDS_CONTENT", "")
    if creds_content:
        info = _json.loads(creds_content)
        if "private_key" in info:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("google_credentials.json", scopes=SCOPES)
    return gspread.authorize(creds)


def create_cement_fuel_tabs() -> dict:
    """
    Creates or updates CementImported, CementLocal, Petrol, Diesel, Kerosene tabs.
    Returns summary of what was created/updated.
    """
    results = {}
    try:
        client = _get_client()
        sheet  = client.open_by_key(SHEET_ID)
        existing_tabs = [ws.title for ws in sheet.worksheets()]
        logger.info("Existing tabs: %s", existing_tabs)

        for tab_name, tab_data in TABS.items():
            try:
                if tab_name in existing_tabs:
                    ws = sheet.worksheet(tab_name)
                    ws.clear()
                    logger.info("Cleared existing tab: %s", tab_name)
                    action = "updated"
                else:
                    ws = sheet.add_worksheet(title=tab_name, rows=50, cols=6)
                    logger.info("Created new tab: %s", tab_name)
                    action = "created"

                # Write header + data rows
                all_rows = [HEADER] + tab_data["rows"]
                ws.append_rows(all_rows, value_input_option="USER_ENTERED")

                # Format header row — bold + light green background
                ws.format("A1:E1", {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.88, "green": 0.95, "blue": 0.88}
                })

                # Freeze header row
                ws.freeze(rows=1)

                results[tab_name] = {
                    "status": action,
                    "rows": len(tab_data["rows"]),
                    "label": tab_data["label"],
                }
                logger.info("  %s %s — %d rows", action.upper(), tab_name, len(tab_data["rows"]))

            except Exception as tab_err:
                logger.error("Failed to create/update %s: %s", tab_name, tab_err)
                results[tab_name] = {"status": "error", "reason": str(tab_err)}

        logger.info("Cement & fuel tabs setup complete")

    except Exception as e:
        logger.error("Sheet connection failed: %s", e)
        results["_error"] = str(e)

    return results


def update_cement_prices_from_notice(notice_prices: dict) -> bool:
    """
    Updates CementImported and CementLocal tabs when Ministry issues new notice.

    notice_prices format:
    {
        "effective_date": "2026-05-07",
        "imported": {
            "wholesale": 175,
            "freetown": 205, "bo": 225, "kenema": 230,
            "makeni": 222, "koidu": 233, ...
        },
        "local": {
            "wholesale": 165,
            "freetown": 195, "bo": 215, ...
        }
    }
    """
    effective_date = notice_prices.get("effective_date", TODAY)
    results = {}

    try:
        client = _get_client()
        sheet  = client.open_by_key(SHEET_ID)

        for cement_type in ["imported", "local"]:
            tab_name = "CementImported" if cement_type == "imported" else "CementLocal"
            prices   = notice_prices.get(cement_type, {})
            if not prices:
                continue

            try:
                ws = sheet.worksheet(tab_name)
                ws.clear()

                rows = [HEADER]
                for market, price in prices.items():
                    rows.append([effective_date, market, price, "bag", "Ministry of Trade"])

                ws.append_rows(rows, value_input_option="USER_ENTERED")
                ws.format("A1:E1", {"textFormat": {"bold": True}})
                logger.info("Updated %s with %d rows", tab_name, len(rows)-1)
                results[tab_name] = "updated"

            except Exception as e:
                logger.error("Failed to update %s: %s", tab_name, e)
                results[tab_name] = f"error: {e}"

        return all("error" not in v for v in results.values())

    except Exception as e:
        logger.error("Sheet connection failed: %s", e)
        return False


def update_fuel_prices(petrol: int, diesel: int, kerosene: int) -> bool:
    """
    Updates Petrol, Diesel, Kerosene tabs with new NPC prices.
    All are nationally priced — same across all 5 markets.
    """
    fuel_data = {
        "Petrol":   petrol,
        "Diesel":   diesel,
        "Kerosene": kerosene,
    }
    try:
        client = _get_client()
        sheet  = client.open_by_key(SHEET_ID)

        for tab_name, price in fuel_data.items():
            try:
                ws = sheet.worksheet(tab_name)
                ws.clear()
                rows = [HEADER]
                for market in ["freetown", "bo", "kenema", "makeni", "koidu"]:
                    rows.append([TODAY, market, price, "litre", "NPC Sierra Leone"])
                ws.append_rows(rows, value_input_option="USER_ENTERED")
                ws.format("A1:E1", {"textFormat": {"bold": True}})
                logger.info("Updated %s: NLe %d/litre", tab_name, price)
            except Exception as e:
                logger.error("Failed to update %s: %s", tab_name, e)

        return True
    except Exception as e:
        logger.error("Fuel update failed: %s", e)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    print("Creating cement and fuel tabs in Google Sheet...")
    results = create_cement_fuel_tabs()
    for tab, result in results.items():
        status = result.get("status", "unknown") if isinstance(result, dict) else result
        label  = result.get("label", "") if isinstance(result, dict) else ""
        rows   = result.get("rows", 0) if isinstance(result, dict) else 0
        print(f"  {tab}: {status} ({rows} rows) — {label}")
    print(f"\nSheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")
