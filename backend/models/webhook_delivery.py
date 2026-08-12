from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WebhookDeliveryCreate(BaseModel):
    delivery_id: str
    event_type: str
    action: str | None = None
    repository: str


class WebhookDeliveryRecord(WebhookDeliveryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    received_at: datetime
    processed: bool
