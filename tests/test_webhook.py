import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app

SECRET = "test-secret"


def signed(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture(autouse=True)
def configure_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTODOCS_GITHUB_WEBHOOK_SECRET", SECRET)
    from backend.config import get_settings

    get_settings.cache_clear()


def test_health() -> None:
    with TestClient(create_app(use_supabase=False)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_pull_request_is_queued() -> None:
    payload = {
        "action": "closed",
        "repository": {"full_name": "octo/example"},
        "pull_request": {
            "number": 42,
            "head": {"sha": "head-sha"},
            "base": {"sha": "base-sha"},
            "merged": True,
        },
    }
    body = json.dumps(payload).encode()
    app = create_app(use_supabase=False)
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-1",
                "X-Hub-Signature-256": signed(body),
            },
        )
    assert response.status_code == 202
    res_data = response.json()
    assert res_data["status"] == "queued"
    assert res_data["job_id"] == "delivery-1"
    assert app.state.job_queue.jobs[0].pr_number == 42


def test_invalid_signature_is_rejected() -> None:
    with TestClient(create_app(use_supabase=False)) as client:
        response = client.post(
            "/webhooks/github",
            content=b"{}",
            headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": "sha256=invalid", "X-GitHub-Delivery": "d"},
        )
    assert response.status_code == 401
