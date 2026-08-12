from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AutoDocs"
    environment: str = "development"
    github_webhook_secret: str = ""
    queue_backend: str = "inline"
    redis_url: str = "redis://localhost:6379/0"
    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_publishable_key: str = Field(default="", validation_alias="SUPABASE_PUBLISHABLE_KEY")
    supabase_secret_key: str = Field(default="", validation_alias="SUPABASE_SECRET_KEY")
    supabase_jwks_url: str = Field(default="", validation_alias="SUPABASE_JWKS_URL")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="AUTODOCS_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
