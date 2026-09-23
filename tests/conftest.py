import pytest


@pytest.fixture(autouse=True)
def disable_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Ensure automated test suite runs completely offline with local in-memory data,
    preventing any attempts to connect to remote Supabase or external network services.
    """
    monkeypatch.setenv("AUTODOCS_ENVIRONMENT", "test")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    monkeypatch.setenv("AUTODOCS_GOOGLE_DOCS_ENABLED", "false")
    monkeypatch.setenv("AUTODOCS_GOOGLE_DOCS_DOCUMENT_ID", "")
    
    # Clear cached settings and supabase client so test environment takes effect
    from backend.config import get_settings
    get_settings.cache_clear()

    try:
        from backend.infrastructure.supabase import get_supabase_client
        get_supabase_client.cache_clear()
    except ImportError:
        pass
