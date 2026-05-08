"""
SaloneMarket – Scheduler

Runs the weekly price blast every Monday at 07:00 Freetown time (UTC+0).

Two deployment modes:
  1. Embedded (default): APScheduler runs inside the Flask process.
     Start with: python scheduler.py
     Good for Railway.app / Render free tier (single process).

  2. External cron: Set SCHEDULER_MODE=external in your environment.
     Then add a cron job or Railway cron trigger that calls:
         POST /admin/trigger-blast  (with X-Admin-Key header)
     This is more reliable for paid deployments.

Also handles:
  - Trial-ending reminders (sent 3 days before trial expires)
  - Payment renewal reminders (sent 5 days before paid_until)
"""

import logging
import os
from datetime import date, datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from sheets import get_active_subscribers, get_latest_prices
from sms import (
    format_trial_ending_sms,
    run_weekly_blast,
    send_sms,
)

logger = logging.getLogger(__name__)

FREETOWN_TZ = pytz.timezone("Africa/Freetown")  # UTC+0 (no DST)


# ── Jobs ──────────────────────────────────────────────────────────────────────

def job_weekly_blast():
    """
    Every Monday at 06:00: auto-fetch WFP prices → update Google Sheet
    Every Monday at 07:00: read sheet → send SMS blast to all subscribers
    """
    logger.info("=== Step 1: Auto-fetching WFP prices ===")
    try:
        from price_fetcher import fetch_wfp_prices, update_sheet_with_wfp_prices
        wfp_prices = fetch_wfp_prices()
        if wfp_prices and "_meta" in wfp_prices:
            updated = update_sheet_with_wfp_prices(wfp_prices)
            logger.info("WFP prices written to sheet: %s", updated)
        else:
            logger.warning("No WFP prices fetched — using existing sheet data")
    except Exception as e:
        logger.warning("WFP fetch failed (will use existing sheet data): %s", e)

    logger.info("=== Step 2: Weekly SMS blast ===")
    try:
        prices  = get_latest_prices()
        results = run_weekly_blast(prices)
        sent    = sum(1 for r in results if r.get("status") == "success")
        failed  = sum(1 for r in results if r.get("status") == "failed")
        logger.info("Weekly blast done: %d sent, %d failed", sent, failed)
    except Exception as exc:
        logger.error("Weekly blast failed: %s", exc, exc_info=True)


def job_trial_reminders():
    """
    Sends a reminder SMS to trial subscribers whose trial ends in 3 days.
    Runs every day at 08:00 Freetown time.
    """
    from config import FREE_TRIAL_WEEKS
    today = date.today()
    subscribers = get_active_subscribers()

    for sub in subscribers:
        if sub.get("status") != "trial":
            continue
        joined_str = sub.get("joined_date", "")
        try:
            joined    = datetime.strptime(joined_str, "%Y-%m-%d").date()
            trial_end = joined + timedelta(weeks=FREE_TRIAL_WEEKS)
            days_left = (trial_end - today).days
            if days_left == 3:
                phone = sub.get("phone", "")
                name  = sub.get("name", "Farmer").split()[0]
                msg   = format_trial_ending_sms(name, days_left)
                send_sms(phone, msg)
                logger.info("Trial reminder sent to %s (%d days left)", phone, days_left)
        except ValueError:
            continue


def job_renewal_reminders():
    """
    Sends a renewal reminder to paid subscribers whose subscription expires in 5 days.
    Runs every day at 08:00 Freetown time (same job as trial reminders).
    """
    today = date.today()
    subscribers = get_active_subscribers()

    for sub in subscribers:
        if sub.get("status") != "active":
            continue
        paid_until_str = sub.get("paid_until", "")
        if not paid_until_str:
            continue
        try:
            paid_until = datetime.strptime(paid_until_str, "%Y-%m-%d").date()
            days_left  = (paid_until - today).days
            if days_left == 5:
                phone = sub.get("phone", "")
                name  = sub.get("name", "Farmer").split()[0]
                plan  = sub.get("plan", "individual")
                fee   = "NLE 500,000" if plan == "association" else "NLE 5,000"
                msg   = (
                    f"SaloneMarket: Your subscription expires in {days_left} days, {name}. "
                    f"Renew for {fee}/month by dialling *384*4321#."
                )[:160]
                send_sms(phone, msg)
                logger.info("Renewal reminder sent to %s", phone)
        except ValueError:
            continue


def job_daily_reminders():
    """Wrapper that runs both trial and renewal reminders in one job."""
    job_trial_reminders()
    job_renewal_reminders()


def job_weekly_fuel_prices():
    """
    Runs every Monday at 06:30 — BEFORE the 07:00 SMS blast.
    Scrapes GlobalPetrolPrices.com for latest Sierra Leone fuel prices
    and writes them to the Petrol, Diesel, Kerosene Google Sheet tabs.
    """
    logger.info("=== Weekly fuel price scrape (GlobalPetrolPrices.com) ===")
    try:
        from ministry_prices import fetch_fuel_prices, update_fuel_in_sheet
        fuel_prices = fetch_fuel_prices()
        meta = fuel_prices.pop("_meta", {})
        if fuel_prices:
            petrol   = fuel_prices.get("petrol", 0)
            diesel   = fuel_prices.get("diesel", 0)
            kerosene = fuel_prices.get("kerosene", 0)
            from setup_cement_fuel_tabs import update_fuel_prices
            success = update_fuel_prices(
                int(petrol) if petrol else 35,
                int(diesel) if diesel else 40,
                int(kerosene) if kerosene else 41
            )
            logger.info("Fuel prices updated: Petrol=%s Diesel=%s Kerosene=%s Source=%s",
                       petrol, diesel, kerosene, meta.get("source"))
        else:
            logger.warning("No fuel prices scraped — sheet unchanged")
    except Exception as e:
        logger.error("Weekly fuel scrape failed: %s", e, exc_info=True)


def job_monthly_ministry_prices():
    """
    Runs on 1st of every month at 06:00.
    Updates cement prices (Ministry of Trade) in Google Sheet.
    Fuel prices are now handled weekly by job_weekly_fuel_prices().
    """
    logger.info("=== Monthly Ministry of Trade cement price update ===")
    try:
        from ministry_prices import update_cement_and_fuel_in_sheet
        success = update_cement_and_fuel_in_sheet()
        logger.info("Ministry cement update: %s", "OK" if success else "FAILED")
    except Exception as e:
        logger.error("Ministry cement update failed: %s", e, exc_info=True)


# ── Manual trigger (called from Flask admin route) ────────────────────────────

def trigger_manual_blast() -> list[dict]:
    """Called by POST /admin/trigger-blast. Returns results list."""
    logger.info("Manual blast triggered via admin API")
    prices  = get_latest_prices()
    results = run_weekly_blast(prices)
    return results


# ── Scheduler setup ───────────────────────────────────────────────────────────

def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=FREETOWN_TZ)

    # Weekly blast: every Monday at 07:00
    scheduler.add_job(
        job_weekly_blast,
        CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=FREETOWN_TZ),
        id="weekly_blast",
        name="Weekly price alert blast",
        misfire_grace_time=3600,  # fire up to 1hr late if the server was down
    )

    # Daily reminders: every day at 08:00
    scheduler.add_job(
        job_daily_reminders,
        CronTrigger(hour=8, minute=0, timezone=FREETOWN_TZ),
        id="daily_reminders",
        name="Trial and renewal reminders",
        misfire_grace_time=3600,
    )

    # Weekly fuel prices: every Monday at 06:30 (30 mins before SMS blast)
    scheduler.add_job(
        job_weekly_fuel_prices,
        CronTrigger(day_of_week="mon", hour=6, minute=30, timezone=FREETOWN_TZ),
        id="weekly_fuel_prices",
        name="GlobalPetrolPrices.com fuel price scrape",
        misfire_grace_time=3600,
    )

    # Monthly Ministry of Trade cement update: 1st of every month at 06:00
    scheduler.add_job(
        job_monthly_ministry_prices,
        CronTrigger(day=1, hour=6, minute=0, timezone=FREETOWN_TZ),
        id="monthly_ministry_prices",
        name="Ministry of Trade cement prices",
        misfire_grace_time=3600,
    )

    return scheduler


# ── Entry point (standalone scheduler process) ────────────────────────────────

if __name__ == "__main__":
    import time
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    )
    logger.info("SaloneMarket scheduler starting…")
    scheduler = create_scheduler()
    scheduler.start()

    logger.info("Scheduler running. Next jobs:")
    for job in scheduler.get_jobs():
        logger.info("  %s → next run: %s", job.name, job.next_run_time)

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Scheduler stopped.")
