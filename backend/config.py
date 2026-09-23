from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AutoDocs"
    environment: str = "development"
    github_webhook_secret: str = ""
    github_token: str = Field(default="", validation_alias="GITHUB_TOKEN")
    queue_backend: str = "inline"
    redis_url: str = "redis://localhost:6379/0"
    docs_repo_url: str = ""
    docs_repo_branch: str = "main"
    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_publishable_key: str = Field(default="", validation_alias="SUPABASE_PUBLISHABLE_KEY")
    supabase_secret_key: str = Field(default="", validation_alias="SUPABASE_SECRET_KEY")
    supabase_jwks_url: str = Field(default="", validation_alias="SUPABASE_JWKS_URL")
    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL")
    openrouter_model: str = Field(default="meta/muse-spark-1.3-contributor", validation_alias="OPENROUTER_MODEL")
    openrouter_reasoning_effort: str = Field(default="medium", validation_alias="OPENROUTER_REASONING_EFFORT")
    google_docs_enabled: bool = False
    google_docs_document_id: str = "1LKH8WNZdtN0guJcozk3Vo2VKX7LDcNacq4ENEfN2bew"
    google_credentials_path: str = "google_credentials.json"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="AUTODOCS_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
