"""
SaloneMarket – Google Sheets data layer

Handles:
  - Reading crop prices from the prices sheet
  - Reading / writing subscribers from the subscribers sheet
  - Updating subscriber status (active, trial, suspended)

Sheet layout expected:
  Prices sheet  → one tab per crop (e.g. "Rice"), columns:
      A: date (YYYY-MM-DD)
      B: market key (e.g. "freetown")
      C: price_nle (number)
      D: source (informant name or "WFP"/"FAO")

  Subscribers sheet → single tab "Subscribers", columns:
      A: phone        (+23276XXXXXXX)
      B: name
      C: district
      D: crops        (comma-separated keys: "rice,cassava,palm_oil")
      E: plan         ("individual" | "association")
      F: status       ("trial" | "active" | "suspended")
      G: joined_date  (YYYY-MM-DD)
      H: paid_until   (YYYY-MM-DD)
      I: association  (association name or blank)
"""

import json
import logging
import os
from datetime import date, datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from config import (
    CROPS, GOOGLE_CREDS_JSON, MARKETS,
    PRICES_SHEET_ID, SUBSCRIBERS_SHEET_ID,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _get_client() -> gspread.Client:
    """
    Returns an authenticated gspread client.
    Reads credentials from GOOGLE_CREDS_JSON env var (JSON string)
    or falls back to GOOGLE_CREDS_CONTENT, then to a local file.
    """
    # Option 1: GOOGLE_CREDS_JSON env var contains raw JSON string
    creds_raw = os.getenv("GOOGLE_CREDS_JSON", "").strip()
    if creds_raw.startswith("{"):
        try:
            info = json.loads(creds_raw)
            if "private_key" in info:
                info["private_key"] = info["private_key"].replace("\\n", "\n")
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logger.warning("GOOGLE_CREDS_JSON parse failed: %s", e)

    # Option 2: GOOGLE_CREDS_CONTENT env var (legacy)
    creds_content = os.getenv("GOOGLE_CREDS_CONTENT", "").strip()
    if creds_content.startswith("{"):
        try:
            info = json.loads(creds_content)
            if "private_key" in info:
                info["private_key"] = info["private_key"].replace("\\n", "\n")
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logger.warning("GOOGLE_CREDS_CONTENT parse failed: %s", e)

    # Option 3: Fall back to file path (local development)
    creds_file = creds_raw or "google_credentials.json"
    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    return gspread.authorize(creds)


# ── Prices ───────────────────────────────────────────────────────────────────

def get_latest_prices(crops: Optional[list] = None) -> dict:
    """
    Reads from a single "Prices" tab with this simple format:
    | crop | freetown | bo | kenema | makeni | koidu | date |

    One row per crop. Just update the numbers each week!
    """
    client = _get_client()
    sheet = client.open_by_key(PRICES_SHEET_ID)
    prices: dict = {}

    # Try new simple format first (single Prices tab)
    try:
        ws = sheet.worksheet("Prices")
        rows = ws.get_all_records()
        for row in rows:
            crop_key = str(row.get("crop", "")).lower().strip().replace(" ", "_")
            if not crop_key:
                continue
            crop_prices = {}
            for market in ["freetown", "bo", "kenema", "makeni", "koidu"]:
                val = row.get(market) or row.get(market.title())
                if val:
                    try:
                        crop_prices[market] = int(float(str(val).replace(",", "")))
                    except (ValueError, TypeError):
                        pass
            if crop_prices:
                prices[crop_key] = crop_prices
        logger.info("Loaded prices from Prices tab: %d crops", len(prices))
        return prices
    except gspread.WorksheetNotFound:
        pass  # Fall back to old per-crop tab format

    # Fallback: old format (one tab per crop)
    crop_keys = crops or list(CROPS.keys())
    for crop_key in crop_keys:
        tab_name = CROPS[crop_key]["sheet_tab"]
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            continue
        rows = ws.get_all_records()
        if not rows:
            continue
        rows_sorted = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
        crop_prices: dict = {}
        for row in rows_sorted:
            market = str(row.get("market", "")).lower().strip()
            price = row.get("price_nle")
            if market and price and market not in crop_prices:
                try:
                    crop_prices[market] = int(float(price))
                except (ValueError, TypeError):
                    pass
        if crop_prices:
            prices[crop_key] = crop_prices

    logger.info("Loaded prices for %d crops (legacy tabs)", len(prices))
    return prices


def get_best_market(crop_key: str, prices: dict) -> Optional[tuple]:
    """Returns (market_key, price) with the highest price for a crop."""
    crop_data = prices.get(crop_key, {})
    if not crop_data:
        return None
    return max(crop_data.items(), key=lambda x: x[1])


# ── Subscribers ──────────────────────────────────────────────────────────────

def get_all_subscribers() -> list[dict]:
    """Returns all rows from the Subscribers tab as a list of dicts."""
    client = _get_client()
    sheet = client.open_by_key(SUBSCRIBERS_SHEET_ID)
    ws = sheet.worksheet("Subscribers")
    rows = ws.get_all_records()
    logger.info("Loaded %d subscribers", len(rows))
    return rows


def get_active_subscribers() -> list[dict]:
    """Returns only subscribers whose status is 'active' or within free trial."""
    all_subs = get_all_subscribers()
    today = date.today()
    active = []
    for sub in all_subs:
        status = str(sub.get("status", "")).lower()
        if status == "active":
            active.append(sub)
        elif status == "trial":
            joined_str = sub.get("joined_date", "")
            try:
                joined = datetime.strptime(joined_str, "%Y-%m-%d").date()
                weeks_in = (today - joined).days // 7
                if weeks_in < int(sub.get("free_trial_weeks", 4)):
                    active.append(sub)
            except ValueError:
                pass
    logger.info("%d active/trial subscribers", len(active))
    return active


def add_subscriber(
    phone: str,
    name: str,
    district: str,
    crops: list[str],
    plan: str = "individual",
    association: str = "",
) -> bool:
    """Appends a new subscriber row. Returns True on success."""
    client = _get_client()
    sheet = client.open_by_key(SUBSCRIBERS_SHEET_ID)
    ws = sheet.worksheet("Subscribers")

    existing = ws.col_values(1)
    if phone in existing:
        logger.warning("Subscriber already exists: %s", phone)
        return False

    today_str = date.today().isoformat()
    row = [
        phone, name, district, ",".join(crops),
        plan, "trial", today_str, "", association,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    logger.info("Added subscriber: %s (%s)", phone, district)
    return True


def update_subscriber_status(phone: str, status: str, paid_until: str = "") -> bool:
    """Updates status for a given phone number."""
    client = _get_client()
    sheet = client.open_by_key(SUBSCRIBERS_SHEET_ID)
    ws = sheet.worksheet("Subscribers")

    phones = ws.col_values(1)
    try:
        row_index = phones.index(phone) + 1
    except ValueError:
        logger.error("Phone not found for status update: %s", phone)
        return False

    ws.update_cell(row_index, 6, status)
    if paid_until:
        ws.update_cell(row_index, 8, paid_until)
    logger.info("Updated %s → status=%s paid_until=%s", phone, status, paid_until)
    return True
