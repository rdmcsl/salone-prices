"""
cement_prices.py
────────────────
Official cement prices issued by the Ministry of Trade and Industry,
Government of Sierra Leone (Public Notice).

Prices are per 50 kg bag in New Leones (NLe).

Structure mirrors the prices dict used by format_whatsapp_cement():
  prices["cement_imported"][district]  → imported 42.5R retail
  prices["cement_local"][district]     → local 32.5R retail
  prices["cement_imported_wholesale"]  → national wholesale (scalar)
  prices["cement_local_wholesale"]     → national wholesale (scalar)

District keys match Sierra Leone's 16 districts / areas exactly
as used across the SaloneMarket codebase.
"""

# ── Wholesale (national) ─────────────────────────────────────────────────────
CEMENT_IMPORTED_WHOLESALE = 175   # NLe per 50 kg bag
CEMENT_LOCAL_WHOLESALE    = 165   # NLe per 50 kg bag

# ── Retail by district ───────────────────────────────────────────────────────
# Source: Ministry of Trade & Industry Public Notice
CEMENT_RETAIL_IMPORTED: dict[str, int] = {
    "Western Area": 205,
    "Port Loko":    220,
    "Bo":           225,
    "Kenema":       230,
    "Kono":         233,
    "Kailahun":     240,
    "Kambia":       222,
    "Kabala":       233,   # Kabala = Koinadugu district capital
    "Moyamba":      227,
    "Bonthe":       237,
    "Pujehun":      235,
    "Makeni":       222,   # Makeni = Bombali district capital
    "Tonkolili":    223,
    "Karene":       245,
}

CEMENT_RETAIL_LOCAL: dict[str, int] = {
    "Western Area": 195,
    "Port Loko":    210,
    "Bo":           215,
    "Kenema":       220,
    "Kono":         223,
    "Kailahun":     230,
    "Kambia":       212,
    "Kabala":       223,
    "Moyamba":      217,
    "Bonthe":       227,
    "Pujehun":      225,
    "Makeni":       212,
    "Tonkolili":    213,
    "Karene":       235,
}

# ── Convenience: full prices sub-dict ready to merge into the main prices dict
CEMENT_PRICES: dict = {
    "cement_imported_wholesale": CEMENT_IMPORTED_WHOLESALE,
    "cement_local_wholesale":    CEMENT_LOCAL_WHOLESALE,
    "cement_imported":           CEMENT_RETAIL_IMPORTED,
    "cement_local":              CEMENT_RETAIL_LOCAL,
}

# ── District alias map (handles variant spellings from subscriber input) ──────
DISTRICT_ALIASES: dict[str, str] = {
    # Freetown / Western
    "freetown":       "Western Area",
    "western area":   "Western Area",
    "western":        "Western Area",
    # Bo
    "bo":             "Bo",
    # Kenema
    "kenema":         "Kenema",
    # Kono
    "kono":           "Kono",
    # Kailahun
    "kailahun":       "Kailahun",
    # Kambia
    "kambia":         "Kambia",
    # Koinadugu / Kabala
    "koinadugu":      "Kabala",
    "kabala":         "Kabala",
    # Moyamba
    "moyamba":        "Moyamba",
    # Bonthe
    "bonthe":         "Bonthe",
    # Pujehun
    "pujehun":        "Pujehun",
    # Bombali / Makeni
    "bombali":        "Makeni",
    "makeni":         "Makeni",
    # Tonkolili
    "tonkolili":      "Tonkolili",
    # Port Loko
    "port loko":      "Port Loko",
    "portloko":       "Port Loko",
    # Karene
    "karene":         "Karene",
}


def resolve_district(raw: str) -> str:
    """Normalise a raw district string to the canonical key used in price dicts."""
    return DISTRICT_ALIASES.get(raw.strip().lower(), raw.strip().title())


def get_cement_prices_for_district(district: str) -> dict:
    """
    Returns a flat dict of cement prices for a given district.

    Example:
        {
            "cement_imported":           225,
            "cement_local":              215,
            "cement_imported_wholesale": 175,
            "cement_local_wholesale":    165,
        }
    """
    key = resolve_district(district)
    return {
        "cement_imported":           CEMENT_RETAIL_IMPORTED.get(key, CEMENT_RETAIL_IMPORTED["Western Area"]),
        "cement_local":              CEMENT_RETAIL_LOCAL.get(key, CEMENT_RETAIL_LOCAL["Western Area"]),
        "cement_imported_wholesale": CEMENT_IMPORTED_WHOLESALE,
        "cement_local_wholesale":    CEMENT_LOCAL_WHOLESALE,
    }
