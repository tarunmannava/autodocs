import logging
from functools import lru_cache
from typing import Any

from backend.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_supabase_client() -> Any | None:
    from supabase import create_client

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        return None
    return create_client(settings.supabase_url, settings.supabase_secret_key)


def require_supabase_client() -> Any:
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    return client

