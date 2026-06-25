from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.llm.factory import HttpLLMClient, RuleBasedLLMClient, create_llm_client, resolve_provider_config


class LLMFactoryTests(unittest.TestCase):
    def test_rule_based_is_default_and_returns_text_response(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = create_llm_client({"provider": "rule_based"})

        self.assertIsInstance(client, RuleBasedLLMClient)
        self.assertIn("Keine KI-Konfiguration", client.generate("test").text)

    def test_provider_without_api_key_falls_back_to_rule_based(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = create_llm_client({"provider": "openai", "openai": {"api_key": "OPENAI_API_KEY"}})

        self.assertIsInstance(client, RuleBasedLLMClient)

    def test_openai_provider_uses_env_secret(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            config = resolve_provider_config({"provider": "openai", "openai": {"api_key": "OPENAI_API_KEY", "model": "gpt-test"}})
            client = create_llm_client({"provider": "openai", "openai": {"api_key": "OPENAI_API_KEY", "model": "gpt-test"}})

        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.model, "gpt-test")
        self.assertIsInstance(client, HttpLLMClient)

    def test_provider_can_be_resolved_from_env_reference_in_config(self) -> None:
        with patch.dict(os.environ, {"SENTERO_LLM_PROVIDER": "llama"}, clear=True):
            config = resolve_provider_config(
                {"provider": "SENTERO_LLM_PROVIDER", "llama": {"base_url": "http://localhost:11434", "model": "qwen"}}
            )

        self.assertEqual(config.provider, "llama")
        self.assertEqual(config.model, "qwen")

    def test_ollama_provider_does_not_require_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = create_llm_client({"provider": "llama", "llama": {"base_url": "http://localhost:11434", "model": "qwen"}})

        self.assertIsInstance(client, HttpLLMClient)


if __name__ == "__main__":
    unittest.main()
