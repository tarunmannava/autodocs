"""Backward-compatible webhook router import."""

from backend.controllers.github_webhook import create_router
from backend.services.webhook import verify_signature

__all__ = ["create_router", "verify_signature"]
