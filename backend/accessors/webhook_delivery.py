from __future__ import annotations

from typing import Any, Protocol

from backend.models.webhook_delivery import WebhookDeliveryCreate


class WebhookDeliveryAccessor(Protocol):
    def claim(self, delivery: WebhookDeliveryCreate) -> bool: ...


class InMemoryWebhookDeliveryAccessor:
    """Development implementation; replace with the Supabase accessor in production."""

    def __init__(self) -> None:
        self._delivery_ids: set[str] = set()

    def claim(self, delivery: WebhookDeliveryCreate) -> bool:
        if delivery.delivery_id in self._delivery_ids:
            return False
        self._delivery_ids.add(delivery.delivery_id)
        return True


class SupabaseWebhookDeliveryAccessor:
    def __init__(self, client: Any) -> None:
        self.client = client

    def claim(self, delivery: WebhookDeliveryCreate) -> bool:
        try:
            response = (
                self.client.table("webhook_deliveries")
                .insert(delivery.model_dump(mode="json"))
                .execute()
            )
        except Exception as exc:
            if getattr(exc, "code", None) == "23505":
                return False
            raise
        return bool(response.data)
