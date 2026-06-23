from __future__ import annotations

from typing import Any


class RuleBasedLLMClient:
    def generate(self, prompt: str, **_: Any) -> str:
        return (
            '{"status":"green","confidence":0.6,"summary":"Keine KI-Konfiguration aktiv.",'
            '"findings":[],"recommendation":"Sensorverlauf weiter beobachten.",'
            '"email_subject":"Sentero Status","email_body":"Keine Auffaelligkeiten erkannt."}'
        )

    def complete(self, prompt: str, **kwargs: Any) -> str:
        return self.generate(prompt, **kwargs)


def create_llm_client(config: dict[str, Any] | None = None) -> RuleBasedLLMClient:
    return RuleBasedLLMClient()

