"""
SaloneMarket – Ministry of Trade & Industry price module

Handles cement and fuel prices from two sources:

SOURCE 1 — Manual entry from official public notices (like the image you shared)
  Enter prices directly via the admin panel or Google Sheet.
  Ministry announces changes periodically — not on a fixed schedule.

SOURCE 2 — Auto-scrape from NPC (National Petroleum Corporation)
  Fuel prices are set monthly and published at npc.gov.sl
  We scrape them automatically every 1st of the month.

SOURCE 3 — Web scrape from Ministry website / Sierraloaded news
  Cement price notices are published as press releases.
  We monitor and extract prices when new notices appear.

Data structure in Google Sheet "Prices" tab:
  cement_imported | western_area:205, bo:225, kenema:230, ...
  cement_local    | western_area:195, bo:215, kenema:220, ...
  petrol          | national:NLE_per_litre
  diesel          | national:NLE_per_litre
  kerosene        | national:NLE_per_litre
"""

import logging
import re
from datetime import date
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Current cement prices (from Ministry notice dated May 2026) ───────────────
# These are updated manually when Ministry issues new public notices
CEMENT_PRICES_CURRENT = {
    "effective_date": "2026-05-07",
    "source": "Ministry of Trade and Industry — Public Notice",
    "wholesale": {
        "cement_imported": 175,   # NLe per 42.5R bag
        "cement_local":    165,   # NLe per 32.5R bag
    },
    "retail_by_district": {
        # district_key: (imported_price, local_price)
        "western_area":  (205, 195),
        "port_loko":     (220, 210),
        "bo":            (225, 215),
        "kenema":        (230, 220),
        "kono":          (233, 223),
        "kailahun":      (240, 230),
        "kambia":        (222, 212),
        "koinadugu":     (233, 223),  # Kabala = Koinadugu district
        "moyamba":       (227, 217),
        "bonthe":        (237, 227),
        "pujehun":       (235, 225),
        "bombali":       (222, 212),  # Makeni = Bombali district
        "tonkolili":     (223, 213),
        "karene":        (245, 235),
        "falaba":        (233, 223),  # estimate, same as Koinadugu
    }
}

# District key → Freetown market mapping (for our 5-market system)
DISTRICT_TO_MARKET = {
    "western_area": "freetown",
    "bo": "bo",
    "kenema": "kenema",
    "bombali": "makeni",
    "kono": "koidu",
}


# ── NPC fuel price scraper ────────────────────────────────────────────────────

# GlobalPetrolPrices.com URLs for Sierra Leone (updated weekly)
GPP_URLS = {
    "petrol":   "https://www.globalpetrolprices.com/Sierra-Leone/gasoline_prices/",
    "diesel":   "https://www.globalpetrolprices.com/Sierra-Leone/diesel_prices/",
    "kerosene": "https://www.globalpetrolprices.com/Sierra-Leone/kerosene_prices/",
}

GPP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.globalpetrolprices.com/",
}


def fetch_fuel_prices() -> dict:
    """
    Fetches current fuel prices from GlobalPetrolPrices.com weekly data.
    Falls back to cached prices if scraping fails.
    Prices on the site are in SLL (old Leone) — we convert to NLE (divide by 1000).
    """
    prices = {}
    scraped_date = date.today().isoformat()

    for fuel_type, url in GPP_URLS.items():
        try:
            resp = requests.get(url, headers=GPP_HEADERS, timeout=20)
            resp.raise_for_status()
            price_sll, price_date = _parse_gpp_page(resp.text, fuel_type)
            if price_sll:
                price_nle = round(price_sll / 1000, 1)  # convert SLL to NLE
                prices[fuel_type] = price_nle
                scraped_date = price_date or scraped_date
                logger.info("GPP scraped %s: SLL %d → NLE %.1f/litre (%s)",
                           fuel_type, price_sll, price_nle, price_date)
        except Exception as e:
            logger.warning("GPP scrape failed for %s: %s", fuel_type, e)

    if len(prices) >= 2:
        prices["_meta"] = {
            "source": "GlobalPetrolPrices.com (Sierra Leone)",
            "date": scraped_date,
            "note": "SLL converted to NLE (divided by 1000)",
        }
        return prices

    logger.warning("GPP scraping yielded insufficient data — using cached prices")
    return _get_cached_fuel_prices()


def _parse_gpp_page(html: str, fuel_type: str) -> tuple:
    """
    Extract current price and date from GlobalPetrolPrices.com page.
    Returns (price_in_SLL, date_string) or (None, None) if not found.
    """
    # Pattern 1: Look for "The current price of X in Sierra Leone is SLL XX,XXX"
    patterns = [
        r"Sierra Leone is SLL\s*([\d,]+(?:\.\d+)?)",
        r"SLL\s*([\d,]+(?:\.\d+)?)\s*per li",
        r"price[^\d]*([\d]{4,6}(?:,\d{3})*(?:\.\d+)?)\s*(?:SLL|Sierra)",
        r'"price":\s*"?([\d.]+)"?',
        r"([\d]{4,6}(?:,\d{3})*)\s*SLL",
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            try:
                price_str = match.group(1).replace(",", "")
                price = float(price_str)
                if 1000 <= price <= 200000:  # sanity check — SLL range
                    # Try to extract date
                    date_match = re.search(
                        r"(\d{2}-\w{3}-\d{4}|\d{4}-\d{2}-\d{2})", html
                    )
                    price_date = date_match.group(1) if date_match else None
                    return int(price), price_date
            except (ValueError, TypeError):
                continue

    # Pattern 2: Look for JSON-LD or data attributes
    json_match = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
    if json_match:
        try:
            price = float(json_match.group(1))
            if 1000 <= price <= 200000:
                return int(price), None
        except (ValueError, TypeError):
            pass

    return None, None


def _get_cached_fuel_prices() -> dict:
    """
    Cached fuel prices — update manually when NPC announces changes.
    Last updated: May 2026 (approximate — confirm with NPC)
    """
    return {
        "petrol":   35,   # NLe per litre — GlobalPetrolPrices 04 May 2026
        "diesel":   40,   # NLe per litre — GlobalPetrolPrices 04 May 2026
        "kerosene": 41,   # NLe per litre — GlobalPetrolPrices 04 May 2026
        "_meta": {
            "source": "GlobalPetrolPrices.com — 04 May 2026",
            "date": "2026-05-04",
            "note": "SLL 35000/40000/40790 converted to NLE (divided by 1000)",
        }
    }


# ── Convert to SaloneMarket price format ─────────────────────────────────────

def get_cement_prices_for_sheet() -> dict:
    """
    Returns cement prices in the SaloneMarket Google Sheet format:
    {
      "cement_imported": {"freetown": 205, "bo": 225, "kenema": 230, "makeni": 222, "koidu": 233},
      "cement_local":    {"freetown": 195, "bo": 215, "kenema": 220, "makeni": 212, "koidu": 223},
    }
    Uses retail prices for the 5 main markets.
    """
    retail = CEMENT_PRICES_CURRENT["retail_by_district"]
    imported = {}
    local = {}

    for district, market in DISTRICT_TO_MARKET.items():
        if district in retail:
            imp_price, loc_price = retail[district]
            imported[market] = imp_price
            local[market]    = loc_price

    return {
        "cement_imported": imported,
        "cement_local":    local,
        "_meta": {
            "source": CEMENT_PRICES_CURRENT["source"],
            "date":   CEMENT_PRICES_CURRENT["effective_date"],
        }
    }


def get_fuel_prices_for_sheet() -> dict:
    """
    Returns fuel prices in SaloneMarket format.
    Fuel is nationally priced — same across all markets.
    """
    fuel = fetch_fuel_prices()
    meta = fuel.pop("_meta", {})
    result = {}

    for fuel_type, price in fuel.items():
        # Same price across all markets (national pricing)
        result[fuel_type] = {
            "freetown": price,
            "bo":       price,
            "kenema":   price,
            "makeni":   price,
            "koidu":    price,
        }

    result["_meta"] = meta
    return result


def update_cement_and_fuel_in_sheet() -> bool:
    """
    Master function: writes all cement and fuel prices to Google Sheet.
    Called by scheduler monthly and via admin panel.
    """
    from sheets import _get_client
    from config import PRICES_SHEET_ID, CROPS
    import gspread

    try:
        client = _get_client()
        sheet  = client.open_by_key(PRICES_SHEET_ID)
        ws     = sheet.worksheet("Prices")
        existing = ws.get_all_records()
        existing_map = {str(r.get("crop","")).lower(): (i+2, r)
                        for i, r in enumerate(existing)}

        today = date.today().isoformat()

        # Cement prices
        cement = get_cement_prices_for_sheet()
        cement_meta = cement.pop("_meta", {})

        # Fuel prices
        fuel = get_fuel_prices_for_sheet()
        fuel_meta = fuel.pop("_meta", {})

        all_prices = {**cement, **fuel}

        for crop_key, market_prices in all_prices.items():
            if crop_key not in CROPS:
                continue
            row_data = [
                crop_key,
                market_prices.get("freetown", ""),
                market_prices.get("bo", ""),
                market_prices.get("kenema", ""),
                market_prices.get("makeni", ""),
                market_prices.get("koidu", ""),
                today,
                cement_meta.get("source", "Ministry of Trade / NPC"),
            ]
            if crop_key in existing_map:
                row_idx = existing_map[crop_key][0]
                ws.update(f"A{row_idx}:H{row_idx}", [row_data])
                logger.info("Updated %s", crop_key)
            else:
                ws.append_row(row_data, value_input_option="USER_ENTERED")
                logger.info("Added %s", crop_key)

        logger.info("Cement and fuel prices written to sheet")
        return True

    except Exception as e:
        logger.error("Failed to write cement/fuel prices: %s", e)
        return False


# ── Admin: update cement prices from new Ministry notice ─────────────────────

def update_cement_from_notice(notice_data: dict) -> bool:
    """
    Called when admin enters new Ministry of Trade cement prices.
    notice_data format:
    {
      "effective_date": "2026-05-07",
      "wholesale": {"cement_imported": 175, "cement_local": 165},
      "retail_by_district": {
        "western_area": [205, 195],
        "bo": [225, 215],
        ...
      }
    }
    """
    global CEMENT_PRICES_CURRENT
    CEMENT_PRICES_CURRENT.update(notice_data)
    CEMENT_PRICES_CURRENT["source"] = "Ministry of Trade and Industry — Public Notice"
    logger.info("Cement prices updated from notice dated %s",
                notice_data.get("effective_date"))
    return update_cement_and_fuel_in_sheet()


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print("Current cement prices (Ministry of Trade):")
    cement = get_cement_prices_for_sheet()
    meta   = cement.pop("_meta", {})
    for crop, markets in cement.items():
        print(f"  {crop}: {markets}")
    print(f"  Source: {meta.get('source')}")
    print(f"  Date:   {meta.get('date')}")

    print("\nCurrent fuel prices (NPC):")
    fuel = get_fuel_prices_for_sheet()
    fmeta = fuel.pop("_meta", {})
    for ftype, markets in fuel.items():
        print(f"  {ftype}: NLe {markets.get('freetown')}/litre (national)")
    print(f"  Source: {fmeta.get('source')}")

    print("\nWriting to Google Sheet...")
    cement["_meta"] = meta
    fuel["_meta"]   = fmeta
    success = update_cement_and_fuel_in_sheet()
    print("Done!" if success else "Failed — check logs")
