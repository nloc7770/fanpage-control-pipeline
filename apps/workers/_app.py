"""Internal module: holds the Celery instance to avoid circular imports.

Task modules import ``celery`` from this module (not from ``worker_app``); the
public ``worker_app.py`` is the entry point that wires task registration after
the instance exists.
"""

from __future__ import annotations

from celery.schedules import crontab

from task_queue import make_celery, register_signal_logging


# Daily content-generation cycle (Asia/Ho_Chi_Minh time, server TZ should be set
# to UTC in env so we offset accordingly: ICT = UTC+7).
# Targets per spec:
#   - 5-10 reels/day/page (= 2-3 source videos -> 5-10 clips each)
#   - 2-3 image posts/day/page
#   - Posting slots: 11/17/19/21/23 ICT
# All gen tasks RUN once a day; publishing is per-minute and only fires for
# manually-approved drafts whose scheduled_at <= now.
_BEAT = {
    # ---- Discovery: 2x/day to keep source pipeline full ----
    "discovery-morning": {
        "task": "discovery.find_content_for_pages",
        # 06:00 VN = 23:00 UTC (prev day).
        "schedule": crontab(hour=23, minute=0),
        "options": {"queue": "discovery"},
    },
    "queue-sources-morning": {
        "task": "discovery.queue_sources_for_generation",
        # 06:30 VN = 23:30 UTC (prev day).
        "schedule": crontab(hour=23, minute=30),
        "options": {"queue": "discovery"},
    },
    "discovery-evening": {
        "task": "discovery.find_content_for_pages",
        # 18:00 VN = 11:00 UTC.
        "schedule": crontab(hour=11, minute=0),
        "options": {"queue": "discovery"},
    },
    "queue-sources-evening": {
        "task": "discovery.queue_sources_for_generation",
        # 18:30 VN = 11:30 UTC.
        "schedule": crontab(hour=11, minute=30),
        "options": {"queue": "discovery"},
    },
    # ---- Auto-schedule reels: 4 prime VN slots per day ----
    # Each fire stamps the next-due reel to the NEXT prime publish slot
    # (07:30 / 12:00 / 18:30 / 20:30 VN). Beat fires 5 min before to give
    # the scheduler buffer time.
    "auto-schedule-reel-0730vn": {
        # 07:25 VN = 00:25 UTC, stamps for 07:30 VN.
        "task": "facebook.auto_schedule_next_approved",
        "schedule": crontab(minute=25, hour=0),
        "options": {"queue": "facebook"},
    },
    "auto-schedule-reel-1200vn": {
        # 11:55 VN = 04:55 UTC, stamps for 12:00 VN.
        "task": "facebook.auto_schedule_next_approved",
        "schedule": crontab(minute=55, hour=4),
        "options": {"queue": "facebook"},
    },
    "auto-schedule-reel-1830vn": {
        # 18:25 VN = 11:25 UTC, stamps for 18:30 VN.
        "task": "facebook.auto_schedule_next_approved",
        "schedule": crontab(minute=25, hour=11),
        "options": {"queue": "facebook"},
    },
    "auto-schedule-reel-2030vn": {
        # 20:25 VN = 13:25 UTC, stamps for 20:30 VN.
        "task": "facebook.auto_schedule_next_approved",
        "schedule": crontab(minute=25, hour=13),
        "options": {"queue": "facebook"},
    },
    # ---- Publisher: poll per minute for due reels ----
    "publish-scheduled-reels": {
        "task": "facebook.publish_scheduled_reels",
        "schedule": crontab(minute="*/1"),
        "options": {"queue": "facebook"},
    },
}


celery = make_celery(
    name="shortform_factory_workers",
    extra_config={"beat_schedule": _BEAT, "timezone": "UTC"},
)
register_signal_logging(celery)

app = celery  # alias
