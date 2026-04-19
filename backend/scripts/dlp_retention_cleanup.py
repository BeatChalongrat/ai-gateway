"""CLI for the DLP raw-content retention job.

Usage examples:
    # default — read retention window from settings.dlp_raw_retention_days
    python scripts/dlp_retention_cleanup.py

    # preview only (counts what would be cleared, writes nothing)
    python scripts/dlp_retention_cleanup.py --dry-run

    # one-off with a different window (e.g. force-clean rows older than 7 days)
    python scripts/dlp_retention_cleanup.py --days 7

Wire this into a daily cron in production once the platform has a
scheduler. Until then, run it manually or via your orchestrator's
``CronJob`` resource. Exit code is 0 on success, non-zero on error.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict

from app.core.db import SessionLocal
from app.jobs.dlp_retention import run_dlp_retention_cleanup


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear raw PII from old request_logs rows.")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override settings.dlp_raw_retention_days for this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be cleared but make no DB changes.",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        stats = run_dlp_retention_cleanup(
            session,
            retention_days=args.days,
            dry_run=args.dry_run,
        )
    finally:
        session.close()

    label = "DRY RUN" if stats.dry_run else "EXECUTED"
    print(f"[dlp-retention] {label}")
    for key, value in asdict(stats).items():
        print(f"  {key:<32} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
