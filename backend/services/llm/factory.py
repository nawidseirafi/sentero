from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

from backend.config import load_agent_section
from backend.paths import ENV_PATH

load_dotenv(ENV_PATH)


class LLMClient(Protocol):
    def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        ...

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        ...


@dataclass(frozen=True)
class LLMResponse:
    text: str


class RuleBasedLLMClient:
    provider = "rule_based"

    def generate(self, prompt: str, **_: Any) -> LLMResponse:
        return LLMResponse(
            '{"status":"green","confidence":0.6,"summary":"Keine KI-Konfiguration aktiv.",'
            '"findings":[],"recommendation":"Sensorverlauf weiter beobachten.",'
            '"email_subject":"Sentero Status","email_body":"Keine Auffaelligkeiten erkannt."}'
        )

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return self.generate(prompt, **kwargs)


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout_seconds: float = 30.0


class HttpLLMClient:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.provider = config.provider

    def complete(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return self.generate(prompt, **kwargs)

    def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        system = str(kwargs.get("system") or "")
        if self.provider == "openai":
            return LLMResponse(self._openai(prompt, system))
        if self.provider == "gemini":
            return LLMResponse(self._gemini(prompt, system))
        if self.provider in {"claude", "anthropic"}:
            return LLMResponse(self._claude(prompt, system))
        if self.provider in {"llama", "ollama"}:
            return LLMResponse(self._ollama(prompt, system))
        raise RuntimeError(f"Unsupported LLM provider: {self.provider}")

    def _openai(self, prompt: str, system: str) -> str:
        response = httpx.post(
            f"{self.config.base_url or 'https://api.openai.com/v1'}/chat/completions",
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.config.model or "gpt-4.1-mini",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"])

    def _gemini(self, prompt: str, system: str) -> str:
        model = self.config.model or "gemini-2.5-flash"
        response = httpx.post(
            f"{self.config.base_url or 'https://generativelanguage.googleapis.com/v1beta'}/models/{model}:generateContent",
            params={"key": self.config.api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2},
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(str(part.get("text") or "") for part in parts)

    def _claude(self, prompt: str, system: str) -> str:
        response = httpx.post(
            f"{self.config.base_url or 'https://api.anthropic.com/v1'}/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model or "claude-3-5-sonnet-latest",
                "max_tokens": 1200,
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return "".join(str(item.get("text") or "") for item in data.get("content", []) if item.get("type") == "text")

    def _ollama(self, prompt: str, system: str) -> str:
        response = httpx.post(
            f"{(self.config.base_url or 'http://localhost:11434').rstrip('/')}/api/chat",
            json={
                "model": self.config.model or "qwen2.5:3b",
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return str(data.get("message", {}).get("content") or data.get("response") or "")


def create_llm_client(config: dict[str, Any] | None = None) -> LLMClient:
    provider_config = resolve_provider_config(config or load_agent_section("llm"))
    if not provider_config:
        return RuleBasedLLMClient()
    return HttpLLMClient(provider_config)


def resolve_provider_config(config: dict[str, Any]) -> ProviderConfig | None:
    provider = resolve_config_value(os.getenv("SENTERO_LLM_PROVIDER") or config.get("provider") or "rule_based").lower()
    if provider in {"", "none", "off", "disabled", "rule_based", "fallback"}:
        return None
    provider_block = config.get(provider) if isinstance(config.get(provider), dict) else {}
    api_key = resolve_secret(
        os.getenv(f"SENTERO_{provider.upper()}_API_KEY")
        or os.getenv(provider_block.get("api_key_env", ""))
        or provider_block.get("api_key")
        or default_key_name(provider)
    )
    if provider not in {"llama", "ollama"} and not api_key:
        return None
    timeout = provider_block.get("timeout_seconds") or config.get("timeout_seconds") or 30
    try:
        timeout_seconds = float(timeout)
    except (TypeError, ValueError):
        timeout_seconds = 30.0
    return ProviderConfig(
        provider="anthropic" if provider == "claude" else provider,
        api_key=api_key,
        model=str(provider_block.get("model") or default_model(provider)),
        base_url=str(provider_block.get("base_url") or "").rstrip("/"),
        timeout_seconds=timeout_seconds,
    )


def resolve_secret(value: Any) -> str:
    return resolve_config_value(value, secret=True)


def resolve_config_value(value: Any, *, secret: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    env_value = os.getenv(text)
    if env_value:
        return env_value.strip()
    if secret and text.isupper() and all(char.isalnum() or char == "_" for char in text):
        return ""
    return text


def default_key_name(provider: str) -> str:
    return {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "claude": "CLAUDE_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }.get(provider, "")


def default_model(provider: str) -> str:
    return {
        "openai": "gpt-4.1-mini",
        "gemini": "gemini-2.5-flash",
        "claude": "claude-3-5-sonnet-latest",
        "anthropic": "claude-3-5-sonnet-latest",
        "llama": "qwen2.5:3b",
        "ollama": "qwen2.5:3b",
    }.get(provider, "")
