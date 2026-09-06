"""Celery application configuration for the payment gateway.

Broker/backend use Redis; tasks opt into ``acks_late`` + ``reject_on_worker_lost``
so no webhook notification is lost if a worker dies mid-dispatch.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "payment_gateway",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=5,
    task_time_limit=180,
    task_soft_time_limit=150,
    # Periodic reconciliation: lock any intent stuck in PROCESSING for > 1h.
    beat_schedule={
        "reconcile-stuck-intents": {
            "task": "app.workers.tasks.reconcile_stuck_intents",
            "schedule": crontab(minute="*/15"),
        },
    },
)