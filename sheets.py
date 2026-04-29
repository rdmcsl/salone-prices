"""
SalonePrices – Google Sheets data layer

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

import logging
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
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_JSON, scopes=SCOPES)
    return gspread.authorize(creds)


# ── Prices ───────────────────────────────────────────────────────────────────

def get_latest_prices(crops: Optional[list] = None) -> dict:
    """
    Returns the most recent price for each (crop, market) pair.

    Returns:
        {
          "rice": {"freetown": 460, "bo": 420, "kenema": 410},
          "cassava": {"freetown": 95, "bo": 80},
          ...
        }
    """
    client = _get_client()
    sheet = client.open_by_key(PRICES_SHEET_ID)
    crop_keys = crops or list(CROPS.keys())
    prices: dict = {}

    for crop_key in crop_keys:
        tab_name = CROPS[crop_key]["sheet_tab"]
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            logger.warning("Sheet tab not found: %s", tab_name)
            continue

        rows = ws.get_all_records()  # list of dicts keyed by header row
        if not rows:
            continue

        # Sort by date descending, keep latest price per market
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
            if len(crop_prices) == len(MARKETS):
                break

        prices[crop_key] = crop_prices

    logger.info("Loaded prices for %d crops", len(prices))
    return prices


def get_best_market(crop_key: str, prices: dict) -> Optional[tuple]:
    """
    Returns (market_key, price) with the highest price for a crop.
    Higher price = better deal for a farmer selling that crop.
    """
    crop_data = prices.get(crop_key, {})
    if not crop_data:
        return None
    best = max(crop_data.items(), key=lambda x: x[1])
    return best  # (market_key, price)


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
                pass  # malformed date – skip
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
    """
    Appends a new subscriber row. Returns True on success.
    Called by the USSD handler after successful registration.
    """
    client = _get_client()
    sheet = client.open_by_key(SUBSCRIBERS_SHEET_ID)
    ws = sheet.worksheet("Subscribers")

    # Check for duplicate phone
    existing = ws.col_values(1)  # column A = phone
    if phone in existing:
        logger.warning("Subscriber already exists: %s", phone)
        return False

    today_str = date.today().isoformat()
    row = [
        phone,
        name,
        district,
        ",".join(crops),
        plan,
        "trial",        # new subscribers start on trial
        today_str,
        "",             # paid_until – blank until payment confirmed
        association,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    logger.info("Added subscriber: %s (%s)", phone, district)
    return True


def update_subscriber_status(phone: str, status: str, paid_until: str = "") -> bool:
    """
    Updates status (and optionally paid_until) for a given phone number.
    Called by the Orange Money payment webhook.
    """
    client = _get_client()
    sheet = client.open_by_key(SUBSCRIBERS_SHEET_ID)
    ws = sheet.worksheet("Subscribers")

    phones = ws.col_values(1)
    try:
        row_index = phones.index(phone) + 1  # gspread is 1-indexed
    except ValueError:
        logger.error("Phone not found for status update: %s", phone)
        return False

    ws.update_cell(row_index, 6, status)       # column F = status
    if paid_until:
        ws.update_cell(row_index, 8, paid_until)  # column H = paid_until
    logger.info("Updated %s → status=%s paid_until=%s", phone, status, paid_until)
    return True


def remove_subscriber(phone: str) -> bool:
    """Marks subscriber as 'suspended' (soft delete). Called by USSD STOP flow."""
    return update_subscriber_status(phone, "suspended")
