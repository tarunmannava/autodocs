import os
import sys

from celery import Celery  # type: ignore[import-untyped]

# Support both AUTODOCS_REDIS_URL (from backend settings) and standard REDIS_URL
REDIS_URL = os.getenv("AUTODOCS_REDIS_URL") or os.getenv("REDIS_URL") or "redis://localhost:6379/0"

celery_app = Celery(
    "autodocs",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["backend.jobs.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
)

# On Windows, default to 'solo' worker pool to avoid multiprocessing spawn/fork errors
if sys.platform == "win32":
    celery_app.conf.worker_pool = "solo"
