from __future__ import annotations

import os
from dataclasses import dataclass

from .common_modules import configure_common_modules
from .config import Settings


PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change_me",
    "insert_key_here",
    "replace_me",
    "todo",
    "your_api_key",
    "your-api-key",
    "your_key_here",
}


def resolve_openai_api_key(settings: Settings) -> str | None:
    status = configure_common_modules(
        settings.common.module_path,
        enabled=settings.common.enabled,
    )
    if status.llm_key_manager:
        try:
            from llm_key_manager import LLMKeyManager, ProviderSpec

            manager = LLMKeyManager(env=os.environ)
            if settings.openai.api_key_env == "OPENAI_API_KEY":
                config = manager.get("openai", required=False)
            else:
                provider_name = "research_agent_openai"
                manager.register_provider(ProviderSpec(provider_name, settings.openai.api_key_env))
                config = manager.get(provider_name, required=False)
            return config.api_key if config else None
        except Exception:
            pass

    value = os.environ.get(settings.openai.api_key_env, "").strip()
    if not _is_usable_secret(value):
        return None
    return value


def resolve_gemini_api_key(settings: Settings) -> str | None:
    # Google documents both names. Their SDK precedence is GOOGLE_API_KEY over GEMINI_API_KEY.
    for env_name in [settings.gemini.google_api_key_env, settings.gemini.api_key_env]:
        value = _resolve_key_from_common_module_or_env(settings, provider="google", env_name=env_name)
        if value:
            return value
    return None


@dataclass(frozen=True)
class ProviderSelection:
    provider: str
    api_key: str | None
    reason: str

    @property
    def available(self) -> bool:
        return self.api_key is not None and self.provider in {"openai", "gemini"}


def select_llm_provider(settings: Settings) -> ProviderSelection:
    requested = settings.llm.provider.strip().lower()
    openai_key = resolve_openai_api_key(settings)
    gemini_key = resolve_gemini_api_key(settings)

    if requested == "openai":
        return ProviderSelection("openai", openai_key, "forced by configuration")
    if requested == "gemini":
        return ProviderSelection("gemini", gemini_key, "forced by configuration")
    if requested not in {"auto", ""}:
        return ProviderSelection("none", None, f"unknown provider '{settings.llm.provider}'")

    if openai_key:
        return ProviderSelection("openai", openai_key, "auto selected OpenAI because OPENAI_API_KEY is configured")
    if gemini_key:
        return ProviderSelection("gemini", gemini_key, "auto selected Gemini because OpenAI key is missing")
    return ProviderSelection("none", None, "no supported LLM API key configured")


def _resolve_key_from_common_module_or_env(settings: Settings, *, provider: str, env_name: str) -> str | None:
    status = configure_common_modules(
        settings.common.module_path,
        enabled=settings.common.enabled,
    )
    if status.llm_key_manager:
        try:
            from llm_key_manager import LLMKeyManager, ProviderSpec

            manager = LLMKeyManager(env=os.environ)
            if provider == "google" and env_name == "GOOGLE_API_KEY":
                config = manager.get("google", required=False)
            else:
                provider_name = f"research_agent_{provider}_{env_name.lower()}"
                manager.register_provider(ProviderSpec(provider_name, env_name))
                config = manager.get(provider_name, required=False)
            return config.api_key if config else None
        except Exception:
            pass

    value = os.environ.get(env_name, "").strip()
    if not _is_usable_secret(value):
        return None
    return value


def _is_usable_secret(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized not in PLACEHOLDER_VALUES and " " not in value.strip()
