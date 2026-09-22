import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.infrastructure.queue import CeleryQueue
from backend.main import create_app
from tests.test_webhook import SECRET, signed


@pytest.fixture(autouse=True)
def configure_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTODOCS_GITHUB_WEBHOOK_SECRET", SECRET)
    from backend.config import get_settings
    get_settings.cache_clear()


def test_webhook_dispatches_to_celery_queue() -> None:
    """Test that valid PR webhook dispatches task via CeleryQueue."""
    mock_celery = MagicMock()
    queue = CeleryQueue(celery_app=mock_celery)

    app = create_app(use_supabase=False)
    app.state.webhook_service.queue = queue

    payload = {
        "action": "closed",
        "repository": {"full_name": "octo/example"},
        "pull_request": {
            "number": 101,
            "head": {"sha": "head-sha-101"},
            "base": {"sha": "base-sha-101"},
            "merged": True,
        },
    }
    body = json.dumps(payload).encode()

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-celery-101",
                "X-Hub-Signature-256": signed(body),
            },
        )

    assert response.status_code == 202
    res_data = response.json()
    assert res_data["status"] == "queued"
    assert res_data["job_id"] == "delivery-celery-101"
    mock_celery.send_task.assert_called_once()
    call_args = mock_celery.send_task.call_args
    assert call_args[0][0] == "backend.jobs.tasks.process_pr_event"
    assert call_args[1]["kwargs"]["job_payload"]["pr_number"] == 101


def test_celery_queue_backend_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that app initializes CeleryQueue when queue_backend settings is celery."""
    monkeypatch.setenv("AUTODOCS_QUEUE_BACKEND", "celery")
    from backend.config import get_settings
    get_settings.cache_clear()

    app = create_app(use_supabase=False)
    assert isinstance(app.state.job_queue, CeleryQueue)

