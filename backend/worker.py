"""Ξεχωριστή διεργασία για το Autopilot.

Ο ίδιος ο API server μπορεί να τρέχει σε πολλά replicas ή με πολλαπλούς uvicorn
workers· αν κάθε ένα σήκωνε τον scheduler, οι εργασίες θα έτρεχαν πολλαπλά. Το
lease lock το απέτρεπε, αλλά η σωστή διάταξη είναι ένας worker:

    docker compose up -d          # api (AUTOPILOT_SCHEDULER=0) + worker
    python worker.py              # τοπικά
    python worker.py --once       # ένας κύκλος και έξοδος (cron/debug)

Ο worker μοιράζεται τη βάση με το API· δεν εκθέτει δίκτυο.
"""
import argparse
import asyncio
import logging
import os
import signal

from db import db

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("worker")

_stopping = asyncio.Event()


async def _ensure_indexes():
    """Οι ίδιοι δείκτες που φτιάχνει και το API — ο worker μπορεί να ξεκινήσει πρώτος."""
    await db.outbox.create_index([("status", 1), ("next_attempt_at", 1)])
    await db.outbox.create_index([("firm_id", 1), ("dedupe_key", 1)])
    await db.automation_runs.create_index([("firm_id", 1), ("job", 1), ("started_at", -1)])


async def run_forever(interval_minutes: int):
    from automation import run_autopilot_cycle
    logger.info("Autopilot worker ξεκίνησε (κύκλος κάθε %s λεπτά)", interval_minutes)
    while not _stopping.is_set():
        try:
            summary = await run_autopilot_cycle()
            logger.info("Κύκλος: %s", summary)
        except Exception:
            logger.exception("Ο κύκλος Autopilot απέτυχε — συνεχίζουμε στον επόμενο")
        try:
            await asyncio.wait_for(_stopping.wait(), timeout=interval_minutes * 60)
        except asyncio.TimeoutError:
            pass
    logger.info("Autopilot worker σταμάτησε καθαρά")


async def main():
    parser = argparse.ArgumentParser(description="Nomos One Autopilot worker")
    parser.add_argument("--once", action="store_true", help="Ένας κύκλος και έξοδος")
    parser.add_argument("--jobs", help="Λίστα jobs χωρισμένη με κόμμα (default: όλα)")
    parser.add_argument("--firm", help="Μόνο για ένα γραφείο (firm_id)")
    args = parser.parse_args()

    await _ensure_indexes()
    from automation import run_autopilot_cycle

    if args.once:
        jobs = [j.strip() for j in args.jobs.split(",")] if args.jobs else None
        summary = await run_autopilot_cycle(jobs=jobs, only_firm=args.firm, force=bool(args.jobs))
        logger.info("Ολοκληρώθηκε: %s", summary)
        return

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stopping.set)
        except NotImplementedError:      # Windows
            pass
    await run_forever(int(os.environ.get("AUTOPILOT_INTERVAL_MINUTES", "15")))


if __name__ == "__main__":
    asyncio.run(main())
