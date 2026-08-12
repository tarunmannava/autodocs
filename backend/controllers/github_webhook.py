from fastapi import APIRouter, Header, HTTPException, Request, status

from backend.services.webhook import (
    InvalidWebhookSignatureError,
    MalformedWebhookError,
    WebhookService,
)


def create_router(service: WebhookService) -> APIRouter:
    router = APIRouter(prefix="/webhooks", tags=["webhooks"])

    @router.post("/github", status_code=status.HTTP_202_ACCEPTED)
    async def github_webhook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, str | None]:
        body = await request.body()
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON payload") from exc

        try:
            result = service.handle(body, x_github_event, x_github_delivery, x_hub_signature_256, payload)
        except InvalidWebhookSignatureError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature") from exc
        except MalformedWebhookError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc) or "Malformed pull request payload",
            ) from exc

        return {
            "status": result.status,
            "event": result.event,
            "delivery_id": result.delivery_id,
            "action": result.action,
            "reason": result.reason,
            "job_id": result.job_id,
        }

    return router
