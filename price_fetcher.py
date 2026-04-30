"""
SalonePrices – Automated price fetcher

Pulls Sierra Leone food prices from 2 free sources every Monday:

SOURCE 1 — HDX/WFP CSV (primary)
  URL: https://data.humdata.org/dataset/wfp-food-prices-for-sierra-leone
  CSV direct download — no API key needed
  Covers: Rice, Cassava, Palm Oil, Maize, Groundnut
  Frequency: Monthly (most recent row per commodity per market)

SOURCE 2 — WFP DataBridges API (supplement)
  URL: https://api.wfp.org/vam-data-bridges/7.0.0
  Requires free registration at: https://databridges.vam.wfp.org/
  Covers: Same commodities with more recent data

After fetching, prices are:
  1. Written to the "Prices" tab in Google Sheet (automated rows)
  2. Flagged as "WFP" in the source column
  3. Your informants can OVERRIDE any row with fresher local data

Items NOT covered by WFP (need manual informants):
  - Eggs, Chicken, Meat, Fish (Bonga), Cooking Oil, Salt, Pepper
"""

import csv
import io
import logging
import os
from datetime import date, datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── WFP commodity name mapping to our crop keys ──────────────────────────────
WFP_COMMODITY_MAP = {
    "Rice":            "rice",
    "Rice - Imported": "rice",
    "Rice - Local":    "rice",
    "Cassava":         "cassava",
    "Cassava - Fresh": "cassava",
    "Palm oil":        "palm_oil",
    "Palm Oil":        "palm_oil",
    "Maize":           "maize",
    "Groundnuts":      "groundnut",
    "Groundnut":       "groundnut",
    "Tomatoes":        "tomato",
    "Tomato":          "tomato",
    "Onions":          "onion",
    "Onion":           "onion",
    "Salt":            "salt",
}

# WFP market name → our market keys
WFP_MARKET_MAP = {
    "Freetown":  "freetown",
    "Bo":        "bo",
    "Kenema":    "kenema",
    "Makeni":    "makeni",
    "Koidu":     "koidu",
    "Kailahun":  "kenema",   # map to nearest
}

# HDX direct CSV download URL for Sierra Leone
HDX_CSV_URL = (
    "https://data.humdata.org/dataset/wfp-food-prices-for-sierra-leone"
    "/resource/d0a1b56c-4ae9-42fd-9f80-38f0e33b5c2e/download"
    "/wfp_food_prices_sle.csv"
)

# Fallback: global WFP CSV filtered to SLE
WFP_GLOBAL_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/global-food-prices/main/data/worldfoodprices.csv"
)


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_wfp_prices() -> dict:
    """
    Fetches latest Sierra Leone food prices from WFP/HDX.

    Returns:
        {
          "rice":      {"freetown": 460, "bo": 420, ...},
          "cassava":   {"freetown": 95, ...},
          ...
          "_meta": {"source": "WFP/HDX", "date": "2026-04-30", "rows_fetched": 45}
        }
    """
    logger.info("Fetching WFP prices for Sierra Leone...")

    # Try HDX direct CSV first
    prices = _fetch_from_hdx_csv()
    if prices:
        logger.info("WFP prices loaded from HDX: %d commodities", len(prices))
        return prices

    # Fallback: try alternative sources
    prices = _fetch_from_alternative()
    if prices:
        return prices

    logger.warning("Could not fetch WFP prices from any source")
    return {}


def _fetch_from_hdx_csv() -> dict:
    """Download and parse the WFP Sierra Leone food prices CSV from HDX."""
    try:
        headers = {
            "User-Agent": "SalonePrices/1.0 (salone-prices@saloneprices.iam.gserviceaccount.com)"
        }
        resp = requests.get(HDX_CSV_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        return _parse_wfp_csv(resp.text)
    except Exception as e:
        logger.warning("HDX CSV fetch failed: %s", e)
        return {}


def _fetch_from_alternative() -> dict:
    """
    Alternative: fetch from the UN HDX API directly.
    https://data.humdata.org/api/action/datastore_search
    """
    try:
        url = "https://data.humdata.org/api/action/datastore_search"
        params = {
            "resource_id": "d0a1b56c-4ae9-42fd-9f80-38f0e33b5c2e",
            "limit": 500,
            "sort": "date desc",
        }
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        records = data.get("result", {}).get("records", [])
        if not records:
            return {}
        return _parse_hdx_records(records)
    except Exception as e:
        logger.warning("HDX API fetch failed: %s", e)
        return {}


def _parse_wfp_csv(csv_text: str) -> dict:
    """
    Parse WFP CSV format:
    date,country,market,category,commodity,unit,pricetype,currency,price,usdprice
    """
    prices: dict = {}
    latest_dates: dict = {}  # (crop_key, market_key) → date of latest entry

    reader = csv.DictReader(io.StringIO(csv_text))
    rows_parsed = 0

    for row in reader:
        # Skip header comment rows
        if row.get("date", "").startswith("#") or not row.get("date"):
            continue

        commodity = row.get("commodity", row.get("Commodity", "")).strip()
        market    = row.get("market",    row.get("Market", "")).strip()
        price_str = row.get("price",     row.get("Price", "0")).strip()
        currency  = row.get("currency",  row.get("Currency", "")).strip()
        date_str  = row.get("date",      row.get("Date", "")).strip()

        crop_key   = _map_commodity(commodity)
        market_key = _map_market(market)

        if not crop_key or not market_key:
            continue

        try:
            price = float(price_str)
            if price <= 0:
                continue
        except (ValueError, TypeError):
            continue

        # Convert to NLE if price is in USD or old Leones
        price_nle = _convert_to_nle(price, currency)

        # Keep only the most recent price per (crop, market)
        key = (crop_key, market_key)
        if key not in latest_dates or date_str > latest_dates[key]:
            latest_dates[key] = date_str
            if crop_key not in prices:
                prices[crop_key] = {}
            prices[crop_key][market_key] = int(price_nle)
            rows_parsed += 1

    prices["_meta"] = {
        "source": "WFP/HDX",
        "date": date.today().isoformat(),
        "rows_fetched": rows_parsed,
    }
    return prices


def _parse_hdx_records(records: list) -> dict:
    """Parse HDX API response records."""
    # Same logic as CSV but from dict records
    csv_rows = []
    for r in records:
        csv_rows.append({
            "commodity": r.get("commodity", ""),
            "market":    r.get("market", ""),
            "price":     r.get("price", 0),
            "currency":  r.get("currency", "SLL"),
            "date":      r.get("date", ""),
        })

    prices: dict = {}
    for row in csv_rows:
        crop_key   = _map_commodity(row["commodity"])
        market_key = _map_market(row["market"])
        if not crop_key or not market_key:
            continue
        try:
            price_nle = _convert_to_nle(float(row["price"]), row["currency"])
            if crop_key not in prices:
                prices[crop_key] = {}
            if market_key not in prices[crop_key]:  # keep first (most recent)
                prices[crop_key][market_key] = int(price_nle)
        except (ValueError, TypeError):
            continue

    prices["_meta"] = {"source": "WFP/HDX API", "date": date.today().isoformat()}
    return prices


# ── Write to Google Sheet ─────────────────────────────────────────────────────

def update_sheet_with_wfp_prices(prices: dict) -> bool:
    """
    Writes WFP-fetched prices into the "Prices" tab of the Google Sheet.
    Preserves any manual entries for commodities WFP doesn't cover (eggs, chicken etc.)

    Returns True on success.
    """
    if not prices or "_meta" not in prices:
        logger.warning("No prices to write")
        return False

    try:
        from sheets import _get_client
        from config import PRICES_SHEET_ID, CROPS
        import gspread

        client = _get_client()
        sheet  = client.open_by_key(PRICES_SHEET_ID)

        try:
            ws = sheet.worksheet("Prices")
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet("Prices", rows=50, cols=8)
            ws.append_row(["crop", "freetown", "bo", "kenema", "makeni", "koidu", "date", "source"])

        # Read existing rows to preserve manual entries
        existing = ws.get_all_records()
        existing_map = {}
        for i, row in enumerate(existing):
            crop = str(row.get("crop", "")).lower().strip()
            if crop:
                existing_map[crop] = (i + 2, row)  # +2 for 1-indexed + header

        today = date.today().isoformat()
        meta  = prices.pop("_meta", {})

        for crop_key, market_prices in prices.items():
            if crop_key not in CROPS:
                continue

            row_data = [
                crop_key,
                market_prices.get("freetown", existing_map.get(crop_key, ({}, {}))[1].get("freetown", "")),
                market_prices.get("bo",       existing_map.get(crop_key, ({}, {}))[1].get("bo", "")),
                market_prices.get("kenema",   existing_map.get(crop_key, ({}, {}))[1].get("kenema", "")),
                market_prices.get("makeni",   existing_map.get(crop_key, ({}, {}))[1].get("makeni", "")),
                market_prices.get("koidu",    existing_map.get(crop_key, ({}, {}))[1].get("koidu", "")),
                today,
                meta.get("source", "WFP"),
            ]

            if crop_key in existing_map:
                row_idx = existing_map[crop_key][0]
                ws.update(f"A{row_idx}:H{row_idx}", [row_data])
                logger.info("Updated %s row %d", crop_key, row_idx)
            else:
                ws.append_row(row_data, value_input_option="USER_ENTERED")
                logger.info("Added new row for %s", crop_key)

        logger.info("Sheet updated with WFP prices for %d commodities", len(prices))
        return True

    except Exception as e:
        logger.error("Failed to update sheet with WFP prices: %s", e)
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _map_commodity(name: str) -> Optional[str]:
    for wfp_name, crop_key in WFP_COMMODITY_MAP.items():
        if wfp_name.lower() in name.lower():
            return crop_key
    return None


def _map_market(name: str) -> Optional[str]:
    for wfp_market, market_key in WFP_MARKET_MAP.items():
        if wfp_market.lower() in name.lower():
            return market_key
    return None


def _convert_to_nle(price: float, currency: str) -> float:
    """
    Convert price to New Leone (NLE).
    WFP data for Sierra Leone is in SLL (old Leone) or NLE.
    1 NLE = 1000 SLL (redenomination happened in 2022)
    Exchange rate: ~1 USD ≈ 22,000 NLE (approximate, update monthly)
    """
    currency = (currency or "").upper().strip()
    if currency in ("NLE", "SLE", ""):
        return price
    elif currency == "SLL":
        return price / 1000  # convert old Leone to new Leone
    elif currency in ("USD", "US$"):
        return price * 22000  # approximate USD to NLE
    else:
        return price  # return as-is if unknown


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print("Fetching WFP Sierra Leone food prices...")
    prices = fetch_wfp_prices()

    meta = prices.pop("_meta", {})
    print(f"\nSource: {meta.get('source', 'unknown')}")
    print(f"Date:   {meta.get('date', 'unknown')}")
    print(f"Rows:   {meta.get('rows_fetched', len(prices))}")
    print("\nPrices fetched:")
    for crop, markets in prices.items():
        print(f"  {crop}: {markets}")

    if prices:
        print("\nWriting to Google Sheet...")
        prices["_meta"] = meta
        success = update_sheet_with_wfp_prices(prices)
        print("✅ Sheet updated!" if success else "❌ Sheet update failed")
    else:
        print("\n⚠️  No prices fetched — WFP may not have recent SL data")
        print("   This is normal if WFP hasn't updated SL data this month.")
        print("   Your manual informant entries in the sheet will be used instead.")
