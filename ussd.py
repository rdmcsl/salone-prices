"""
SaloneMarket – USSD handler (Africa's Talking USSD callback)

Africa's Talking sends a POST request to your USSD callback URL every time
a user navigates the menu. You respond with:
    CON <text>   → continue the session (show menu, wait for input)
    END <text>   → end the session (confirmation message)

Menu flow:
    *384*4321#
    → 1. Subscribe to SaloneMarket
        → Enter your name
        → Select district (1-12)
        → Select up to 3 crops
        → Confirm & pay via Orange Money
    → 2. Unsubscribe
        → Confirm
    → 3. Change my crops
        → Select new crops

This module is designed to be called from a Flask/FastAPI route.
See app.py for the web server integration.
"""

import logging
from typing import Optional

from config import CROPS, DISTRICTS, FREE_TRIAL_WEEKS
from sheets import add_subscriber, get_all_subscribers, remove_subscriber, update_subscriber_status
from sms import format_welcome_sms, send_sms

logger = logging.getLogger(__name__)

# ── Session state (in-memory; for production use Redis or a DB) ──────────────
# Maps session_id → dict of collected values
_sessions: dict[str, dict] = {}


def handle_ussd(
    session_id: str,
    phone_number: str,
    text: str,
    service_code: str,
) -> str:
    """
    Main USSD dispatcher. Called by the Flask route on every request.

    `text` is the full input string accumulated across the session,
    with each level separated by '*'. E.g. "1*John Koroma*3*1*2*3"

    Returns a string starting with CON or END.
    """
    # Normalise phone to E.164 (+23276XXXXXXX)
    phone = _normalise_phone(phone_number)
    parts = text.split("*") if text else []
    depth = len(parts)

    logger.debug("USSD session=%s phone=%s text=%r depth=%d", session_id, phone, text, depth)

    # ── Root menu ─────────────────────────────────────────────────────────────
    if text == "":
        return (
            "CON Welcome to SaloneMarket\n"
            "Weekly crop prices by SMS\n\n"
            "1. Subscribe\n"
            "2. Unsubscribe\n"
            "3. Change my crops"
        )

    choice = parts[0]

    # ── Branch 1: Subscribe ───────────────────────────────────────────────────
    if choice == "1":
        return _subscribe_flow(session_id, phone, parts)

    # ── Branch 2: Unsubscribe ─────────────────────────────────────────────────
    if choice == "2":
        return _unsubscribe_flow(phone, parts)

    # ── Branch 3: Change crops ────────────────────────────────────────────────
    if choice == "3":
        return _change_crops_flow(phone, parts)

    return "END Invalid option. Please try again."


# ── Subscribe flow ────────────────────────────────────────────────────────────

def _subscribe_flow(session_id: str, phone: str, parts: list[str]) -> str:
    depth = len(parts)

    # Step 1: ask for name
    if depth == 1:
        return "CON Enter your first and last name:"

    name = parts[1].strip()
    if not name:
        return "END Name cannot be blank. Please try again."

    # Step 2: select district
    if depth == 2:
        district_menu = "\n".join(f"{k}. {v}" for k, v in DISTRICTS.items())
        return f"CON Select your district:\n{district_menu}"

    district_key = parts[2].strip()
    if district_key not in DISTRICTS:
        return "END Invalid district. Please try again."
    district = DISTRICTS[district_key]

    # Step 3: select crops (up to 3, comma-separated numbers)
    if depth == 3:
        crop_menu = "\n".join(
            f"{i+1}. {v['name']}" for i, (k, v) in enumerate(CROPS.items())
        )
        return (
            f"CON Select up to 3 crops (enter numbers separated by *):\n"
            f"{crop_menu}\n"
            "E.g. enter 1 for Rice"
        )

    # Step 4+: collect crop selections (depth 4, 5, 6)
    selected_indices = [p.strip() for p in parts[3:] if p.strip().isdigit()]
    crop_keys = list(CROPS.keys())
    chosen_crops = []
    for idx in selected_indices:
        i = int(idx) - 1
        if 0 <= i < len(crop_keys):
            chosen_crops.append(crop_keys[i])

    # Allow selection of up to 3 crops before proceeding
    if depth < 6 and len(chosen_crops) < 3:
        remaining = 3 - len(chosen_crops)
        already = ", ".join(CROPS[c]["name"] for c in chosen_crops)
        return (
            f"CON Selected: {already if already else 'none yet'}\n"
            f"Add another crop (or enter 0 to finish):\n"
            + "\n".join(
                f"{i+1}. {v['name']}"
                for i, (k, v) in enumerate(CROPS.items())
                if k not in chosen_crops
            )
        )

    # Remove "0" (finish signal) from crops
    chosen_crops = [c for c in chosen_crops if c]
    if not chosen_crops:
        return "END Please select at least one crop. Try again."

    # Final confirmation + payment prompt
    crop_names = ", ".join(CROPS[c]["name"] for c in chosen_crops)
    fee_display = "FREE for 4 weeks, then NLE 5,000/month"

    # Register subscriber (graceful fallback if Google Sheets not configured yet)
    try:
        success = add_subscriber(
            phone=phone,
            name=name,
            district=district,
            crops=chosen_crops,
            plan="individual",
        )
        if not success:
            return "END You are already subscribed. Dial *384*4321# to manage."
    except Exception as e:
        logger.warning("Sheets not configured, saving locally: %s", e)
        import os
        os.makedirs("logs", exist_ok=True)
        with open("logs/subscribers_pending.txt", "a") as f:
            f.write(f"{phone},{name},{district},{','.join(chosen_crops)}\n")

    # Send welcome SMS (graceful fallback if not configured)
    try:
        welcome = format_welcome_sms(name, chosen_crops)
        send_sms(phone, welcome)
    except Exception as e:
        logger.warning("Welcome SMS not sent: %s", e)

    return (
        f"END You're registered, {name.split()[0]}!\n"
        f"Crops: {crop_names}\n"
        f"District: {district}\n"
        f"Cost: {fee_display}\n"
        f"First alert Monday 7am."
    )


# ── Unsubscribe flow ──────────────────────────────────────────────────────────

def _unsubscribe_flow(phone: str, parts: list[str]) -> str:
    depth = len(parts)

    if depth == 1:
        return (
            "CON Are you sure you want to unsubscribe from SaloneMarket?\n\n"
            "1. Yes, unsubscribe\n"
            "2. No, keep my subscription"
        )

    confirm = parts[1].strip()
    if confirm == "1":
        remove_subscriber(phone)
        send_sms(phone, "You have been unsubscribed from SaloneMarket. Dial *384*4321# to re-subscribe anytime.")
        return "END You have been unsubscribed. We're sorry to see you go!"
    else:
        return "END Your subscription is still active. Thank you for staying!"


# ── Change crops flow ─────────────────────────────────────────────────────────

def _change_crops_flow(phone: str, parts: list[str]) -> str:
    depth = len(parts)

    if depth == 1:
        crop_menu = "\n".join(
            f"{i+1}. {v['name']}" for i, (k, v) in enumerate(CROPS.items())
        )
        return (
            f"CON Select your new crops (up to 3, enter numbers with *):\n"
            f"{crop_menu}"
        )

    selected_indices = [p.strip() for p in parts[1:] if p.strip().isdigit() and p.strip() != "0"]
    crop_keys = list(CROPS.keys())
    chosen_crops = []
    for idx in selected_indices:
        i = int(idx) - 1
        if 0 <= i < len(crop_keys) and len(chosen_crops) < 3:
            chosen_crops.append(crop_keys[i])

    if depth < 4 and len(chosen_crops) < 3:
        already = ", ".join(CROPS[c]["name"] for c in chosen_crops)
        return (
            f"CON Selected: {already if already else 'none'}\n"
            f"Add another (or enter 0 to save):\n"
            + "\n".join(
                f"{i+1}. {v['name']}"
                for i, (k, v) in enumerate(CROPS.items())
                if k not in chosen_crops
            )
        )

    if not chosen_crops:
        return "END No crops selected. Your previous crops are unchanged."

    # Update in Google Sheets
    from sheets import get_all_subscribers
    import gspread
    from config import GOOGLE_CREDS_JSON, SUBSCRIBERS_SHEET_ID
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        GOOGLE_CREDS_JSON,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SUBSCRIBERS_SHEET_ID)
    ws = sheet.worksheet("Subscribers")
    phones = ws.col_values(1)
    try:
        row_idx = phones.index(phone) + 1
        ws.update_cell(row_idx, 4, ",".join(chosen_crops))  # column D = crops
        crop_names = ", ".join(CROPS[c]["name"] for c in chosen_crops)
        return f"END Crops updated to: {crop_names}. New alerts start next Monday."
    except ValueError:
        return "END Phone not found. Please subscribe first by dialling *384*4321#."


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_phone(phone: str) -> str:
    """Ensures phone is in +23276XXXXXXX format."""
    phone = phone.strip().replace(" ", "")
    if phone.startswith("0"):
        phone = "+232" + phone[1:]
    elif phone.startswith("232") and not phone.startswith("+"):
        phone = "+" + phone
    elif not phone.startswith("+"):
        phone = "+232" + phone
    return phone
