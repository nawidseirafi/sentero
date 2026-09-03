from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.agents.sentero.mail.intent_service import ACTION_RE, MailIntentService
from backend.agents.sentero.mail.models import IntentResult, MailIntent, QueryResult
from backend.agents.sentero.mail.response_service import MailResponseService
from backend.logging_config import get_logger
from backend.services.llm import create_llm_client

logger = get_logger(__name__)

ROUTER_SYSTEM = """Du bist der sichere Anfrage-Router fuer Sentero.
Ordne eine frei formulierte Angehoerigen-Frage genau einem erlaubten Intent zu.
Antworte ausschliesslich als JSON-Objekt ohne Markdown.
Erlaubte Intents: STATUS_SUMMARY, POWER_USAGE, CONTACT_STATUS, CURRENT_ACTIVITY, LAST_ACTIVITY, LAST_ROOM, TODAY_SUMMARY, ANOMALIES, ENVIRONMENT, NIGHT_SUMMARY, SENSOR_HEALTH, HELP, UNKNOWN.
Setze is_action_request auf true, wenn die Nachricht eine Aenderung, Steuerung, Loeschung, Tuer-/Geraeteaktion oder Sicherheitsaktion verlangt.
Wenn mehrere Intents passen, waehle den Intent, der die Frage am direktesten beantwortet.
Wenn die Frage unklar ist, nutze UNKNOWN."""

ANSWER_SYSTEM = """Du formulierst Sentero-Antworten fuer Angehoerige auf die selbe Sprache wie die Frage.
Nutze ausschliesslich die bereitgestellten Fakten. Erfinde keine Sensorwerte, Diagnosen, Standorte oder Sicherheiten.
Sentero ist read-only: keine Aktionen bestaetigen oder ausfuehren.
Formuliere natuerlich, ruhig und knapp. Sage Unsicherheiten klar, ohne alarmistisch zu wirken.
Wenn nur eine letzte Aktivitaet bekannt ist, formuliere sie als letzte erkannte Aktivitaet, nicht als sicheren aktuellen Aufenthaltsort.
Bei Nichtanwesenheit: Nutze wenn möglich Sensordaten, um zu bestimmen, ob und wann die Wohnung verlassen wurde.
Bei Zeitangaben gilt: Nutze bevorzugt relative_time (z. B. "vor 1 Minute") oder event_time_label/event_time_local. event_time ist ein technischer UTC-Zeitstempel und darf niemals direkt als lokale Uhrzeit ausgegeben werden.
Wenn event_time_label vorhanden ist, ist dies die lokale Sentero-Uhrzeit.
Bei Helligkeitswerten nutze illuminance_description bzw. illuminance_display, wenn vorhanden. Formuliere z. B. "Sehr hell (2910 lx)" statt nur "2910 lx". Erfinde keine eigene Kategorie.
Antworte ohne Markdown und ohne JSON."""

ROUTER_CONFIDENCE_FLOOR = 0.55


@dataclass(frozen=True)
class RoutedIntent:
    intent: MailIntent
    confidence: float
    is_action_request: bool = False
    source: str = "rule_based"
    slots: dict[str, Any] | None = None


class ConversationService:
    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm or create_llm_client()

    def classify(self, question: str, fallback: MailIntentService) -> RoutedIntent:
        rule = fallback.classify(question)
        if not self._llm_enabled():
            return routed_from_intent(rule, is_action_request=bool(ACTION_RE.search(question.lower())))
        try:
            raw = self.llm.generate(router_prompt(question), system=ROUTER_SYSTEM).text
            data = parse_json_object(raw)
            intent = MailIntent(str(data.get("intent") or MailIntent.UNKNOWN.value))
            confidence = float(data.get("confidence") if data.get("confidence") is not None else 0.0)
            is_action = bool(data.get("is_action_request")) or bool(ACTION_RE.search(question.lower()))
            slots = data.get("slots") if isinstance(data.get("slots"), dict) else {}
            if confidence < ROUTER_CONFIDENCE_FLOOR and rule.intent != MailIntent.UNKNOWN:
                return routed_from_intent(rule, is_action_request=is_action)
            return RoutedIntent(intent=intent, confidence=confidence, is_action_request=is_action, source="llm", slots=slots)
        except Exception:
            logger.exception("LLM intent routing failed", extra={"component": "conversation_service"})
            return routed_from_intent(rule, is_action_request=bool(ACTION_RE.search(question.lower())))

    def build_response(self, result: QueryResult, fallback: MailResponseService) -> str:
        if not self._llm_enabled() or not llm_response_allowed(result):
            return fallback.build(result)
        try:
            text = self.llm.generate(answer_prompt(result), system=ANSWER_SYSTEM).text.strip()
            return text[:4000] if text else fallback.build(result)
        except Exception:
            logger.exception("LLM response generation failed", extra={"component": "conversation_service", "intent": result.intent.value})
            return fallback.build(result)

    def _llm_enabled(self) -> bool:
        return str(getattr(self.llm, "provider", "") or "").lower() not in {"", "rule_based"}


def routed_from_intent(intent: IntentResult, *, is_action_request: bool) -> RoutedIntent:
    return RoutedIntent(intent=intent.intent, confidence=intent.confidence, is_action_request=is_action_request)


def router_prompt(question: str) -> str:
    examples = [
        {"frage": "Ist bei Mama alles normal heute?", "intent": "STATUS_SUMMARY"},
        {"frage": "Wie hoch ist der Stromverbrauch?", "intent": "POWER_USAGE"},
        {"frage": "Sind alle Tueren zu?", "intent": "CONTACT_STATUS"},
        {"frage": "War Papa schon in der Kueche?", "intent": "LAST_ROOM"},
        {"frage": "Wann gab es die letzte Bewegung?", "intent": "LAST_ACTIVITY"},
        {"frage": "Wie war die Nacht?", "intent": "NIGHT_SUMMARY"},
        {"frage": "Sind die Sensoren und Batterien ok?", "intent": "SENSOR_HEALTH"},
        {"frage": "Mach die Haustuer auf", "intent": "UNKNOWN", "is_action_request": True},
    ]
    return json.dumps(
        {
            "question": question,
            "output_schema": {
                "intent": "one allowed intent string",
                "confidence": "number between 0 and 1",
                "is_action_request": "boolean",
                "slots": "optional object with room, time_range, topic",
            },
            "examples": examples,
        },
        ensure_ascii=False,
    )


def answer_prompt(result: QueryResult) -> str:
    return json.dumps(
        {
            "intent": result.intent.value,
            "status": result.status,
            "data_available": result.data_available,
            "facts": result.facts,
            "warnings": result.warnings,
            "style": "Natuerliche, fluessige Antwort fuer Angehoerige. Keine neuen Fakten erfinden.",
        },
        ensure_ascii=False,
        default=str,
    )


def llm_response_allowed(result: QueryResult) -> bool:
    if result.permission_denied or not result.data_available:
        return False
    return result.intent not in {MailIntent.HELP, MailIntent.UNKNOWN, MailIntent.SENSOR_HEALTH}


def parse_json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("llm_json_not_object")
    return data
