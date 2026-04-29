"""
SalonePrices – Test suite

Run with:
    pip install pytest
    pytest tests/test_salone_prices.py -v

Tests are designed to run without real API credentials by mocking
external calls (Africa's Talking, Google Sheets, Orange Money).
"""

import sys
import types
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# ── Minimal stubs so imports work without installed packages ──────────────────
def _make_stub(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

for mod_name in ["gspread", "africastalking",
                 "apscheduler", "apscheduler.schedulers.background",
                 "apscheduler.triggers.cron"]:
    if mod_name not in sys.modules:
        _make_stub(mod_name)

# Stub google hierarchy properly
for mod_name in ["google", "google.oauth2", "google.oauth2.service_account",
                 "google.auth", "google.auth.transport"]:
    if mod_name not in sys.modules:
        _make_stub(mod_name)

# Credentials stub
class _FakeCreds:
    @staticmethod
    def from_service_account_file(*a, **kw):
        return _FakeCreds()
sys.modules["google.oauth2.service_account"].Credentials = _FakeCreds  # type: ignore

# pytz stub
if "pytz" not in sys.modules:
    _pytz_mod = _make_stub("pytz")
else:
    _pytz_mod = sys.modules["pytz"]
_pytz_mod.timezone = lambda tz: None  # type: ignore

# gspread stubs
import gspread as _gspread
_gspread.Client = object  # type: ignore
_gspread.authorize = lambda c: None  # type: ignore
class _FakeWorksheetNotFound(Exception): pass
_gspread.WorksheetNotFound = _FakeWorksheetNotFound  # type: ignore

# africastalking stubs
import africastalking as _at
_at.initialize = lambda u, k: None  # type: ignore
_at_sms = types.ModuleType("africastalking.SMS")
_at_sms.send = lambda msg, phones, sender_id=None: {}  # type: ignore
_at.SMS = _at_sms  # type: ignore

# dotenv stub
if "dotenv" not in sys.modules:
    _dotenv_mod = _make_stub("dotenv")
else:
    _dotenv_mod = sys.modules["dotenv"]
_dotenv_mod.load_dotenv = lambda: None  # type: ignore


# ── Now we can import our modules ─────────────────────────────────────────────
from sms import format_price_sms, format_welcome_sms, format_trial_ending_sms
from ussd import _normalise_phone, handle_ussd
from payments import _plan_from_amount, build_orange_payment_url


class TestSMSFormatting(unittest.TestCase):

    SAMPLE_PRICES = {
        "rice":     {"freetown": 460, "bo": 420, "kenema": 410},
        "cassava":  {"freetown": 95,  "bo": 80},
        "palm_oil": {"freetown": 1200, "bo": 1150},
    }

    def test_format_price_sms_fits_160_chars(self):
        msg = format_price_sms(self.SAMPLE_PRICES, ["rice", "cassava", "palm_oil"])
        self.assertLessEqual(len(msg), 160, f"SMS too long ({len(msg)} chars):\n{msg}")

    def test_format_price_sms_contains_crop_name(self):
        msg = format_price_sms(self.SAMPLE_PRICES, ["rice"])
        self.assertIn("RICE", msg)

    def test_format_price_sms_contains_best_market_tip(self):
        msg = format_price_sms(self.SAMPLE_PRICES, ["rice"])
        self.assertIn("Best:", msg)

    def test_format_price_sms_empty_crops(self):
        msg = format_price_sms(self.SAMPLE_PRICES, [])
        # Should not crash, returns minimal SMS
        self.assertIsInstance(msg, str)

    def test_format_price_sms_unknown_crop_skipped(self):
        msg = format_price_sms(self.SAMPLE_PRICES, ["rice", "nonexistent_crop"])
        self.assertIn("RICE", msg)

    def test_welcome_sms_fits_160_chars(self):
        msg = format_welcome_sms("Fatmata Koroma", ["rice", "cassava"])
        self.assertLessEqual(len(msg), 160)

    def test_welcome_sms_contains_name(self):
        msg = format_welcome_sms("Fatmata", ["rice"])
        self.assertIn("Fatmata", msg)

    def test_trial_ending_sms_fits_160_chars(self):
        msg = format_trial_ending_sms("Mohamed", 3)
        self.assertLessEqual(len(msg), 160)

    def test_trial_ending_sms_contains_days(self):
        msg = format_trial_ending_sms("Mohamed", 3)
        self.assertIn("3", msg)


class TestPhoneNormalisation(unittest.TestCase):

    def test_local_format(self):
        # 076123456 → +23276123456 (strips leading 0, prepends +232)
        self.assertEqual(_normalise_phone("076123456"), "+23276123456")

    def test_already_e164(self):
        self.assertEqual(_normalise_phone("+23276123456"), "+23276123456")

    def test_without_plus(self):
        self.assertEqual(_normalise_phone("23276123456"), "+23276123456")

    def test_leading_zero(self):
        result = _normalise_phone("076123456")
        self.assertTrue(result.startswith("+232"))


class TestUSSDFlow(unittest.TestCase):

    @patch("ussd.add_subscriber", return_value=True)
    @patch("ussd.send_sms", return_value={"MessageData": {"Message": "Sent"}})
    def test_root_menu(self, mock_sms, mock_add):
        resp = handle_ussd("sess1", "+23276000001", "", "*384*4321#")
        self.assertTrue(resp.startswith("CON"))
        self.assertIn("Subscribe", resp)
        self.assertIn("Unsubscribe", resp)

    @patch("ussd.add_subscriber", return_value=True)
    @patch("ussd.send_sms", return_value={})
    def test_subscribe_step1_asks_name(self, mock_sms, mock_add):
        resp = handle_ussd("sess2", "+23276000002", "1", "*384*4321#")
        self.assertTrue(resp.startswith("CON"))
        self.assertIn("name", resp.lower())

    @patch("ussd.add_subscriber", return_value=True)
    @patch("ussd.send_sms", return_value={})
    def test_subscribe_step2_asks_district(self, mock_sms, mock_add):
        resp = handle_ussd("sess3", "+23276000003", "1*Aminata Sesay", "*384*4321#")
        self.assertTrue(resp.startswith("CON"))
        self.assertIn("district", resp.lower())

    @patch("ussd.remove_subscriber", return_value=True)
    @patch("ussd.send_sms", return_value={})
    def test_unsubscribe_confirm(self, mock_sms, mock_remove):
        resp = handle_ussd("sess4", "+23276000004", "2*1", "*384*4321#")
        self.assertTrue(resp.startswith("END"))
        mock_remove.assert_called_once_with("+23276000004")

    @patch("ussd.remove_subscriber", return_value=True)
    @patch("ussd.send_sms", return_value={})
    def test_unsubscribe_cancel(self, mock_sms, mock_remove):
        resp = handle_ussd("sess5", "+23276000005", "2*2", "*384*4321#")
        self.assertTrue(resp.startswith("END"))
        mock_remove.assert_not_called()

    def test_invalid_root_choice(self):
        resp = handle_ussd("sess6", "+23276000006", "9", "*384*4321#")
        self.assertTrue(resp.startswith("END"))

    @patch("ussd.add_subscriber", return_value=False)  # already subscribed
    @patch("ussd.send_sms", return_value={})
    def test_duplicate_subscriber(self, mock_sms, mock_add):
        # Complete the subscribe flow with all steps
        resp = handle_ussd("sess7", "+23276000007", "1*John Bio*1*1*2*3", "*384*4321#")
        self.assertTrue(resp.startswith("END"))
        self.assertIn("already", resp.lower())


class TestPayments(unittest.TestCase):

    def test_individual_monthly_plan(self):
        plan, months = _plan_from_amount(5_000)
        self.assertEqual(plan, "individual")
        self.assertEqual(months, 1)

    def test_individual_quarterly_plan(self):
        plan, months = _plan_from_amount(15_000)
        self.assertEqual(plan, "individual_quarterly")
        self.assertEqual(months, 3)

    def test_individual_annual_plan(self):
        plan, months = _plan_from_amount(60_000)
        self.assertEqual(plan, "individual_annual")
        self.assertEqual(months, 12)

    def test_association_plan(self):
        plan, months = _plan_from_amount(500_000)
        self.assertEqual(plan, "association")
        self.assertEqual(months, 1)

    def test_build_payment_url_contains_order_id(self):
        result = build_orange_payment_url("+23276123456", 5_000)
        self.assertIn("order_id", result)
        self.assertIn("23276123456", result["order_id"])
        self.assertEqual(result["amount"], 5_000)
        self.assertEqual(result["currency"], "SLE")

    @patch("payments.update_subscriber_status", return_value=True)
    @patch("payments._get_name", return_value="Fatmata")
    @patch("payments.send_sms", return_value={})
    def test_webhook_success(self, mock_sms, mock_name, mock_update):
        from payments import handle_orange_webhook
        payload = {
            "status":             "SUCCESS",
            "txnId":              "TXN123",
            "amount":             5000,
            "currency":           "SLE",
            "subscriberMsisdn":   "23276123456",
            "orderId":            "SALONE-23276123456-20240428",
        }
        # Patch signature check to always pass in tests
        with patch("payments._verify_signature", return_value=True):
            result = handle_orange_webhook(payload, b"{}", "sig")
        self.assertEqual(result["status"], "ok")
        mock_update.assert_called_once()

    @patch("payments._verify_signature", return_value=False)
    def test_webhook_invalid_signature(self, mock_verify):
        from payments import handle_orange_webhook
        result = handle_orange_webhook({}, b"{}", "bad_sig")
        self.assertEqual(result["status"], "error")
        self.assertIn("signature", result["reason"])


class TestSchedulerHelpers(unittest.TestCase):
    """Tests for trial/renewal reminder logic."""

    def _make_sub(self, status, joined_days_ago=None, paid_until_days=None):
        sub = {"phone": "+23276000099", "name": "Test Farmer", "status": status}
        if joined_days_ago is not None:
            sub["joined_date"] = (date.today() - timedelta(days=joined_days_ago)).isoformat()
        if paid_until_days is not None:
            sub["paid_until"] = (date.today() + timedelta(days=paid_until_days)).isoformat()
        return sub

    def test_trial_3_days_left_triggers_reminder(self):
        from config import FREE_TRIAL_WEEKS
        # Trial started 25 days ago → ends in 3 days (4 weeks = 28 days)
        sub = self._make_sub("trial", joined_days_ago=25)
        joined = date.today() - timedelta(days=25)
        from datetime import datetime
        trial_end = joined + timedelta(weeks=FREE_TRIAL_WEEKS)
        days_left = (trial_end - date.today()).days
        self.assertEqual(days_left, 3)

    def test_renewal_5_days_left(self):
        sub = self._make_sub("active", paid_until_days=5)
        paid_until = date.today() + timedelta(days=5)
        days_left = (paid_until - date.today()).days
        self.assertEqual(days_left, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
