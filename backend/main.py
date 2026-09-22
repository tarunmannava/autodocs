from fastapi import FastAPI

from backend.accessors.run import InMemoryRunAccessor, RunAccessor, SupabaseRunAccessor
from backend.accessors.webhook_delivery import (
    InMemoryWebhookDeliveryAccessor,
    SupabaseWebhookDeliveryAccessor,
    WebhookDeliveryAccessor,
)
from backend.config import get_settings
from backend.controllers.github_webhook import create_router
from backend.infrastructure.queue import CeleryQueue, InlineJobQueue
from backend.infrastructure.supabase import get_supabase_client
from backend.services.webhook import WebhookService


def create_app(use_supabase: bool | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")

    queue = CeleryQueue() if settings.queue_backend == "celery" else InlineJobQueue()
    supabase = get_supabase_client() if use_supabase is not False else None
    delivery_accessor: WebhookDeliveryAccessor
    run_accessor: RunAccessor
    if supabase is None:
        delivery_accessor = InMemoryWebhookDeliveryAccessor()
        run_accessor = InMemoryRunAccessor()
    else:
        delivery_accessor = SupabaseWebhookDeliveryAccessor(supabase)
        run_accessor = SupabaseRunAccessor(supabase)
    webhook_service = WebhookService(settings.github_webhook_secret, queue, delivery_accessor, run_accessor)

    app.state.job_queue = queue
    app.state.run_accessor = run_accessor
    app.state.webhook_service = webhook_service
    app.include_router(create_router(webhook_service))

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
