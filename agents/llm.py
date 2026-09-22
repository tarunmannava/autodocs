from __future__ import annotations

from typing import Any, Dict, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from backend.config import Settings, get_settings


def get_chat_model(
    settings: Optional[Settings] = None,
    temperature: float = 0.0,
    max_tokens: int = 4000,
    **kwargs: Any,
) -> BaseChatModel:
    """
    Instantiates and configures a tool-calling chat model connected to OpenRouter.

    Args:
        settings: Optional custom settings instance (defaults to get_settings()).
        temperature: Sampling temperature (default 0.0 for deterministic agent tool calls).
        max_tokens: Maximum token generation budget.
        **kwargs: Extra arguments passed to ChatOpenAI.

    Returns:
        BaseChatModel: Configured ChatOpenAI instance pointing to OpenRouter.
    """
    active_settings = settings or get_settings()

    api_key = active_settings.openrouter_api_key or "mock-key-for-testing"
    base_url = active_settings.openrouter_base_url or "https://openrouter.ai/api/v1"
    model_name = active_settings.openrouter_model or "meta/muse-spark-1.3-contributor"
    reasoning_effort = active_settings.openrouter_reasoning_effort or "medium"

    extra_body: Dict[str, Any] = {"reasoning": {"effort": reasoning_effort}}

    default_headers = {
        "HTTP-Referer": "https://github.com/tarunmannava/autodocs",
        "X-Title": "AutoDocs",
    }

    return ChatOpenAI(
        model=model_name,
        api_key=SecretStr(api_key),
        base_url=base_url,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        extra_body=extra_body,
        default_headers=default_headers,
        **kwargs,
    )
