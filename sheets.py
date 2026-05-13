"""
SaloneMarket – Google Sheets data layer

Handles:
  - Reading crop prices from the prices sheet
  - Reading / writing subscribers from the subscribers sheet
  - Updating subscriber status (active, trial, suspended)
  - Reading cement prices from the "Cement Prices" tab

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
      E: plan         ("free" | "pro" | "biz")
      F: status       ("trial" | "active" | "suspended")
      G: joined_date  (YYYY-MM-DD)
      H: paid_until   (YYYY-MM-DD)
      I: categories   (comma-separated: "food,fuel,cement")
"""

import json
import logging
import os
from datetime import date, datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from config import (
    CROPS, MARKETS,
    PRICES_SHEET_ID, SUBSCRIBERS_SHEET_ID,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Auth ─────────────────────────────────────────────────────────────────────

def _get_gspread_client() -> gspread.Client:
    """
    Returns an authorised gspread client.

    Credential resolution order:
      1. GOOGLE_CREDS_JSON env var contains raw JSON string  ← Railway
      2. GOOGLE_CREDS_PATH env var points to a JSON file     ← local dev
      3. Falls back to 'google_credentials.json' in CWD      ← legacy
    """
    creds_json_str = os.getenv("GOOGLE_CREDS_JSON", "")

    if creds_json_str.strip().startswith("{"):
        # Railway / production: env var holds the JSON content directly
        info  = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        # Local dev: env var is a file path (or use the legacy default)
        path  = creds_json_str or os.getenv("GOOGLE_CREDS_PATH", "google_credentials.json")
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)

    return gspread.authorize(creds)


# ── Market name normalisation ────────────────────────────────────────────────
# Maps every variant spelling found in the Sheet to the canonical district name
# used in cement_prices.py and sms.py formatting functions.
_MARKET_NORM: dict[str, str] = {
    "freetown":           "Western Area",
    "western area":       "Western Area",
    "western":            "Western Area",
    "western area urban": "Western Area",
    "western area rural": "Western Area",
    "bo":                 "Bo",
    "kenema":             "Kenema",
    "kono":               "Kono",
    "koidu":              "Kono",
    "kailahun":           "Kailahun",
    "kambia":             "Kambia",
    "koinadugu":          "Kabala",
    "kabala":             "Kabala",
    "moyamba":            "Moyamba",
    "bonthe":             "Bonthe",
    "pujehun":            "Pujehun",
    "bombali":            "Makeni",
    "makeni":             "Makeni",
    "tonkolili":          "Tonkolili",
    "port loko":          "Port Loko",
    "portloko":           "Port Loko",
    "karene":             "Karene",
}

def _normalise_market(raw: str) -> str:
    """Return canonical district name for any market string from the Sheet."""
    return _MARKET_NORM.get(raw.strip().lower(), raw.strip().title())


# ── Prices ────────────────────────────────────────────────────────────────────

def get_latest_prices() -> dict:
    """
    Reads the most recent price for every crop in every market.
    Returns nested dict: prices[crop_key][district] = price_nle (int)
    Falls back to cement_prices.py constants for cement keys.
    """
    try:
        gc = _get_gspread_client()
        sh = gc.open_by_key(PRICES_SHEET_ID)

        prices: dict = {}

        for crop_key, crop_info in CROPS.items():
            tab_name = crop_info.get("sheet_tab", crop_info["name"])
            try:
                ws      = sh.worksheet(tab_name)
                records = ws.get_all_records()
            except gspread.exceptions.WorksheetNotFound:
                logger.warning("Sheet tab '%s' not found – skipping", tab_name)
                continue

            # Keep only the latest row per market
            latest: dict[str, dict] = {}
            for row in records:
                raw_mkt = str(row.get("market", "")).strip()
                if not raw_mkt:
                    continue
                mkt = _normalise_market(raw_mkt)   # canonical district name
                row_date_str = str(row.get("date", "1970-01-01"))
                try:
                    row_date = datetime.strptime(row_date_str, "%Y-%m-%d").date()
                except ValueError:
                    row_date = date.min

                if mkt not in latest or row_date > latest[mkt]["_date"]:
                    latest[mkt] = {
                        "_date":     row_date,
                        "price_nle": row.get("price_nle", 0),
                    }

            prices[crop_key] = {
                mkt: int(data["price_nle"])
                for mkt, data in latest.items()
                if data["price_nle"]
            }

        # Merge in cement prices (Sheet tab wins over hardcoded constants)
        _merge_cement_prices(sh, prices)

        return prices

    except Exception as exc:
        logger.error("get_latest_prices failed: %s", exc)
        return {}


def _merge_cement_prices(sh: gspread.Spreadsheet, prices: dict) -> None:
    """
    Reads the 'Cement Prices' tab and injects cement keys into prices dict.
    Falls back silently to hardcoded constants if the tab is missing.
    """
    try:
        from cement_prices import CEMENT_PRICES
        # Start with hardcoded defaults
        for k, v in CEMENT_PRICES.items():
            prices.setdefault(k, v)

        ws      = sh.worksheet("Cement Prices")
        records = ws.get_all_records()

        imported = {}
        local    = {}
        imp_whl  = prices.get("cement_imported_wholesale", 175)
        loc_whl  = prices.get("cement_local_wholesale",    165)

        for row in records:
            d = str(row.get("District", "")).strip()
            if not d:
                continue
            try:
                imported[d] = int(row["Imported 42.5R Retail (NLe)"])
                local[d]    = int(row["Local 32.5R Retail (NLe)"])
                imp_whl     = int(row.get("Imported Wholesale (NLe)", imp_whl))
                loc_whl     = int(row.get("Local Wholesale (NLe)",    loc_whl))
            except (ValueError, KeyError):
                pass

        if imported:
            prices["cement_imported"]           = imported
            prices["cement_local"]              = local
            prices["cement_imported_wholesale"] = imp_whl
            prices["cement_local_wholesale"]    = loc_whl

    except Exception as exc:
        logger.warning("Cement Prices tab unavailable (%s) – using constants", exc)


def get_best_market(crop_key: str) -> Optional[dict]:
    """Returns the market with the highest price for a given crop."""
    prices = get_latest_prices()
    crop_prices = prices.get(crop_key, {})
    if not crop_prices:
        return None
    best_mkt = max(crop_prices, key=crop_prices.get)
    return {
        "market":    best_mkt,
        "price_nle": crop_prices[best_mkt],
        "name":      MARKETS.get(best_mkt, {}).get("name", best_mkt.title()),
    }


# ── Subscribers ───────────────────────────────────────────────────────────────

def _open_subscribers_sheet() -> gspread.Worksheet:
    gc = _get_gspread_client()
    sh = gc.open_by_key(SUBSCRIBERS_SHEET_ID)
    return sh.worksheet("Subscribers")


def get_active_subscribers() -> list[dict]:
    """Returns all subscribers whose status is 'active' or 'trial'."""
    try:
        ws      = _open_subscribers_sheet()
        records = ws.get_all_records()
        return [
            r for r in records
            if str(r.get("status", "")).strip().lower() in ("active", "trial")
            and r.get("phone", "")
        ]
    except Exception as exc:
        logger.error("get_active_subscribers failed: %s", exc)
        return []


def get_all_subscribers() -> list[dict]:
    """Returns every row from the Subscribers sheet."""
    try:
        ws = _open_subscribers_sheet()
        return ws.get_all_records()
    except Exception as exc:
        logger.error("get_all_subscribers failed: %s", exc)
        return []


def add_subscriber(
    phone: str,
    name: str,
    district: str,
    crops: list[str],
    plan: str = "free",
    categories: str = "food",
) -> bool:
    """Appends a new subscriber row. Returns True on success."""
    try:
        ws = _open_subscribers_sheet()
        ws.append_row([
            phone,
            name,
            district,
            ",".join(crops),
            plan,
            "trial",
            date.today().isoformat(),
            "",          # paid_until blank until payment confirmed
            categories,
        ])
        logger.info("Added subscriber %s (%s)", phone, name)
        return True
    except Exception as exc:
        logger.error("add_subscriber failed: %s", exc)
        return False


def update_subscriber_status(phone: str, status: str) -> bool:
    """Sets the status column for a subscriber identified by phone number."""
    try:
        ws      = _open_subscribers_sheet()
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):   # row 1 is header
            if str(row.get("phone", "")).strip() == phone.strip():
                # Column F (index 6) = status
                ws.update_cell(i, 6, status)
                logger.info("Updated %s → status=%s", phone, status)
                return True

        logger.warning("update_subscriber_status: %s not found", phone)
        return False
    except Exception as exc:
        logger.error("update_subscriber_status failed: %s", exc)
        return False


def set_paid_until(phone: str, paid_until: str) -> bool:
    """Sets the paid_until column (YYYY-MM-DD) for a subscriber."""
    try:
        ws      = _open_subscribers_sheet()
        records = ws.get_all_records()

        for i, row in enumerate(records, start=2):
            if str(row.get("phone", "")).strip() == phone.strip():
                ws.update_cell(i, 8, paid_until)   # Column H
                ws.update_cell(i, 6, "active")     # Column F = status
                logger.info("Set paid_until=%s for %s", paid_until, phone)
                return True

        logger.warning("set_paid_until: %s not found", phone)
        return False
    except Exception as exc:
        logger.error("set_paid_until failed: %s", exc)
        return False


def unsubscribe(phone: str) -> bool:
    """Marks a subscriber as suspended (soft delete)."""
    return update_subscriber_status(phone, "suspended")
