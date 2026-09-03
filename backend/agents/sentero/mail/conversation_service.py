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
Verstehe die Frage unabhaengig von der Sprache. Die Person darf Deutsch, Englisch, Persisch/Farsi, Arabisch oder eine andere Sprache verwenden.
Ordne die aktuelle frei formulierte Angehoerigen-Frage genau einem erlaubten Intent zu.
Nutze den bereitgestellten kurzen Gespraechsverlauf, um Bezugnahmen wie "und davor?", "wie lange?", "dort?", "war das normal?", "wo ist sie?", Pronomen und gleichbedeutende Formulierungen in anderen Sprachen aufzuloesen.
Antworte ausschliesslich als JSON-Objekt ohne Markdown.
Erlaubte Intents: STATUS_SUMMARY, POWER_USAGE, CONTACT_STATUS, CURRENT_ACTIVITY, LAST_ACTIVITY, LAST_ROOM, TODAY_SUMMARY, ANOMALIES, ENVIRONMENT, NIGHT_SUMMARY, SENSOR_HEALTH, HELP, UNKNOWN.
Setze is_action_request auf true, wenn die Nachricht eine Aenderung, Steuerung, Loeschung, Tuer-/Geraeteaktion oder Sicherheitsaktion verlangt.
Wenn nach einer frueheren Aktivitaet relativ zur zuletzt besprochenen Aktivitaet gefragt wird (z. B. "davor", "vorher"), setze slots.relation auf "previous".
Wenn mehrere Intents passen, waehle den Intent, der die Frage im Gespraechskontext am direktesten beantwortet.
Wenn die Frage trotz Verlauf unklar ist, nutze UNKNOWN."""

ANSWER_SYSTEM = """Du formulierst Sentero-Antworten fuer Angehoerige in derselben Sprache wie die aktuelle Frage.
Nutze ausschliesslich die bereitgestellten Fakten. Erfinde keine Sensorwerte, Diagnosen, Standorte oder Sicherheiten.
Sentero ist read-only: keine Aktionen bestaetigen oder ausfuehren.
Antworte zuerst direkt auf die Frage und formuliere natuerlich, ruhig und dialogisch. Einfache Fragen in 1 bis 2 Saetzen beantworten; Statusfragen normalerweise in hoechstens 3 Saetzen.
Gib niemals einen technischen Datenblock aus und zaehle nicht ungefragt Sensorwerte, Lernphase, Batterien, Temperatur oder Ereigniszaehler auf. Solche Details nur nennen, wenn danach gefragt wurde oder sie fuer eine Warnung wesentlich sind.
Nutze den kurzen Gespraechsverlauf fuer Bezugnahmen und vermeide unnoetige Wiederholungen. Wenn eine Frage wirklich nicht eindeutig aufloesbar ist, stelle eine kurze Rueckfrage.
Sage Unsicherheiten klar, ohne alarmistisch zu wirken.
Wenn nur eine letzte Aktivitaet bekannt ist, formuliere sie als letzte erkannte Aktivitaet, nicht als sicheren aktuellen Aufenthaltsort.
Wenn facts.relation = "previous" ist, beschreibe das Ereignis eindeutig als das vorherige historische Aktivitaetsereignis (z. B. "Davor wurde um 21:53 Uhr Aktivitaet im Flur erkannt."). Fuege dann keinen Hinweis zur Zuverlaessigkeit einer aktuellen Raumzuordnung an.
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

    def classify(self, question: str, fallback: MailIntentService, history: list[dict[str, Any]] | None = None) -> RoutedIntent:
        language = detect_language(question)
        rule = fallback.classify(question)
        multilingual = multilingual_rule_route(question)
        followup = followup_from_history(question, history)
        if not self._llm_enabled():
            route = multilingual or followup or routed_from_intent(rule, is_action_request=bool(ACTION_RE.search(question.lower())))
            return route_with_language(route, language)
        try:
            raw = self.llm.generate(router_prompt(question, history=history), system=ROUTER_SYSTEM).text
            data = parse_json_object(raw)
            intent = MailIntent(str(data.get("intent") or MailIntent.UNKNOWN.value))
            confidence = float(data.get("confidence") if data.get("confidence") is not None else 0.0)
            is_action = bool(data.get("is_action_request")) or bool(ACTION_RE.search(question.lower()))
            slots = data.get("slots") if isinstance(data.get("slots"), dict) else {}
            slots.setdefault("language", language)
            if confidence < ROUTER_CONFIDENCE_FLOOR and multilingual is not None:
                return route_with_language(multilingual, language)
            if confidence < ROUTER_CONFIDENCE_FLOOR and rule.intent != MailIntent.UNKNOWN:
                return route_with_language(routed_from_intent(rule, is_action_request=is_action), language)
            if confidence < ROUTER_CONFIDENCE_FLOOR and followup is not None:
                return route_with_language(followup, language)
            return RoutedIntent(intent=intent, confidence=confidence, is_action_request=is_action, source="llm", slots=slots)
        except Exception:
            logger.exception("LLM intent routing failed", extra={"component": "conversation_service"})
            route = multilingual or followup or routed_from_intent(rule, is_action_request=bool(ACTION_RE.search(question.lower())))
            return route_with_language(route, language)

    def build_response(
        self,
        result: QueryResult,
        fallback: MailResponseService,
        *,
        question: str = "",
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        language = detect_language(question)
        if not self._llm_enabled() or not llm_response_allowed(result):
            return localized_fallback_response(result, fallback, language)
        try:
            text = self.llm.generate(answer_prompt(result, question=question, history=history), system=ANSWER_SYSTEM).text.strip()
            return text[:4000] if text else fallback.build(result)
        except Exception:
            logger.exception("LLM response generation failed", extra={"component": "conversation_service", "intent": result.intent.value})
            return fallback.build(result)

    def _llm_enabled(self) -> bool:
        return str(getattr(self.llm, "provider", "") or "").lower() not in {"", "rule_based"}


def routed_from_intent(intent: IntentResult, *, is_action_request: bool) -> RoutedIntent:
    return RoutedIntent(intent=intent.intent, confidence=intent.confidence, is_action_request=is_action_request)


def router_prompt(question: str, history: list[dict[str, Any]] | None = None) -> str:
    examples = [
        {"frage": "Ist bei Mama alles normal heute?", "intent": "STATUS_SUMMARY"},
        {"frage": "Wie hoch ist der Stromverbrauch?", "intent": "POWER_USAGE"},
        {"frage": "Sind alle Tueren zu?", "intent": "CONTACT_STATUS"},
        {"frage": "War Papa schon in der Kueche?", "intent": "LAST_ROOM"},
        {"frage": "Wann gab es die letzte Bewegung?", "intent": "LAST_ACTIVITY"},
        {"frage": "Wie war die Nacht?", "intent": "NIGHT_SUMMARY"},
        {"frage": "Sind die Sensoren und Batterien ok?", "intent": "SENSOR_HEALTH"},
        {"frage": "Und davor?", "intent": "LAST_ACTIVITY", "slots": {"relation": "previous"}},
        {"frage": "How is my mother doing?", "intent": "STATUS_SUMMARY"},
        {"frage": "Where is she?", "intent": "LAST_ROOM"},
        {"frage": "حالش خوبه؟", "intent": "STATUS_SUMMARY"},
        {"frage": "حال مادرم چطوره؟", "intent": "STATUS_SUMMARY"},
        {"frage": "أين هي الآن؟", "intent": "LAST_ROOM"},
        {"frage": "Mach die Haustuer auf", "intent": "UNKNOWN", "is_action_request": True},
    ]
    return json.dumps(
        {
            "question": question,
            "conversation": _prompt_history(history),
            "output_schema": {
                "intent": "one allowed intent string",
                "confidence": "number between 0 and 1",
                "is_action_request": "boolean",
                "slots": "optional object with room, time_range, topic, relation",
            },
            "examples": examples,
        },
        ensure_ascii=False,
    )


def answer_prompt(
    result: QueryResult,
    *,
    question: str = "",
    history: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {
            "question": question,
            "conversation": _prompt_history(history),
            "intent": result.intent.value,
            "status": result.status,
            "data_available": result.data_available,
            "facts": _answer_facts(result),
            "warnings": result.warnings,
            "style": "Natuerliche, fluessige Antwort fuer Angehoerige. Keine neuen Fakten erfinden.",
        },
        ensure_ascii=False,
        default=str,
    )





PERSIAN_CHARS = set("پچژگکییۀةؤئ")
ARABIC_RANGE_RE = __import__("re").compile(r"[\u0600-\u06ff]")


def detect_language(question: str) -> str:
    text = str(question or "").strip()
    lower = text.lower()
    if not text:
        return "de"
    if ARABIC_RANGE_RE.search(text):
        # Persian has several characters not normally used in Arabic. Common
        # Persian colloquialisms are also enough to disambiguate short chats.
        if any(ch in text for ch in PERSIAN_CHARS) or any(word in text for word in ("خوبه", "چطوره", "کجاست", "مامان", "مادرم", "سنسور", "باتری", "دما", "رطوبت", "قبلا", "قبلش")):
            return "fa"
        return "ar"
    english_markers = (
        "how is",
        "how's",
        "is my",
        "is mom",
        "is mum",
        "is mother",
        "is dad",
        "is father",
        "is she",
        "is he",
        "is everything",
        "where is",
        "where was",
        "which room",
        "are the",
        "sensors",
        "battery",
        "batteries",
        "temperature",
        "humidity",
        "last activity",
        "last movement",
        "what happened",
        "today",
        "help",
    )
    if any(token in lower for token in english_markers):
        return "en"
    return "de"


def route_with_language(route: RoutedIntent, language: str) -> RoutedIntent:
    slots = dict(route.slots or {})
    slots.setdefault("language", language)
    return RoutedIntent(
        intent=route.intent,
        confidence=route.confidence,
        is_action_request=route.is_action_request,
        source=route.source,
        slots=slots,
    )


def multilingual_rule_route(question: str) -> RoutedIntent | None:
    text = str(question or "").strip().lower()
    language = detect_language(text)
    if language == "de":
        return None

    groups: list[tuple[MailIntent, tuple[str, ...], float]] = []
    if language == "fa":
        groups = [
            (MailIntent.SENSOR_HEALTH, ("سنسور", "باتری", "باتري", "حسگر"), 0.96),
            (MailIntent.ENVIRONMENT, ("دما", "حرارت", "رطوبت", "روشنایی", "نور"), 0.94),
            (MailIntent.NIGHT_SUMMARY, ("خواب", "دیشب", "شب چطور", "شبش"), 0.92),
            (MailIntent.ANOMALIES, ("غیرعادی", "غيرعادي", "هشدار", "مشکوک", "عجیب"), 0.92),
            (MailIntent.LAST_ROOM, ("کجاست", "کجا است", "کجا بود", "کجا بوده", "کدام اتاق"), 0.97),
            (MailIntent.LAST_ACTIVITY, ("آخرین حرکت", "آخرین فعالیت", "اخرین حرکت", "اخرین فعالیت", "کی حرکت", "چه زمانی حرکت"), 0.94),
            (MailIntent.HELP, ("راهنما", "کمک", "چی میتونم بپرسم", "چه می توانم بپرسم"), 0.93),
            (MailIntent.STATUS_SUMMARY, ("حالش خوبه", "حال مامان", "حال ماما", "حال مادرم", "چطوره", "خوبه؟", "همه چی خوبه", "همه چیز خوبه", "اوضاع خوبه"), 0.98),
        ]
    elif language == "ar":
        groups = [
            (MailIntent.SENSOR_HEALTH, ("المستشعر", "المستشعرات", "البطارية", "الحساس", "الحساسات"), 0.96),
            (MailIntent.ENVIRONMENT, ("الحرارة", "درجة الحرارة", "الرطوبة", "الإضاءة", "الاضاءة"), 0.94),
            (MailIntent.NIGHT_SUMMARY, ("النوم", "الليلة الماضية", "كيف كانت الليلة"), 0.92),
            (MailIntent.ANOMALIES, ("غير طبيعي", "تحذير", "إنذار", "انذار", "غريب"), 0.92),
            (MailIntent.LAST_ROOM, ("أين هي", "اين هي", "أين هو", "اين هو", "في أي غرفة", "في اي غرفة"), 0.97),
            (MailIntent.LAST_ACTIVITY, ("آخر حركة", "اخر حركة", "آخر نشاط", "اخر نشاط", "متى تحرك"), 0.94),
            (MailIntent.HELP, ("مساعدة", "ماذا يمكنني أن أسأل", "ماذا يمكنني ان اسأل"), 0.93),
            (MailIntent.STATUS_SUMMARY, ("هل هي بخير", "هل هو بخير", "كيف حالها", "كيف حاله", "كل شيء بخير", "كل شي بخير"), 0.98),
        ]
    elif language == "en":
        groups = [
            (MailIntent.SENSOR_HEALTH, ("sensor", "sensors", "battery", "batteries"), 0.96),
            (MailIntent.ENVIRONMENT, ("temperature", "humidity", "light", "brightness"), 0.94),
            (MailIntent.NIGHT_SUMMARY, ("last night", "sleep", "night"), 0.92),
            (MailIntent.ANOMALIES, ("unusual", "anomaly", "warning", "alarm"), 0.92),
            (MailIntent.LAST_ROOM, ("where is", "where was", "which room"), 0.97),
            (MailIntent.LAST_ACTIVITY, ("last activity", "last movement", "when was", "when did"), 0.94),
            (MailIntent.TODAY_SUMMARY, ("what happened today", "today summary", "how was today", "what was today"), 0.94),
            (MailIntent.HELP, ("help", "what can i ask"), 0.93),
            (
                MailIntent.STATUS_SUMMARY,
                (
                    "how is my",
                    "how is mom",
                    "how is mum",
                    "how is mother",
                    "how is dad",
                    "how is father",
                    "how is she",
                    "how is he",
                    "how's mom",
                    "how's mum",
                    "how's mother",
                    "how's dad",
                    "is my mom ok",
                    "is my mom okay",
                    "is my mum ok",
                    "is my mum okay",
                    "is mom ok",
                    "is mom okay",
                    "is mum ok",
                    "is mum okay",
                    "is mother ok",
                    "is mother okay",
                    "is dad ok",
                    "is dad okay",
                    "is father ok",
                    "is father okay",
                    "is everything okay",
                    "is everything ok",
                    "is she okay",
                    "is she ok",
                    "is he okay",
                    "is he ok",
                    "everything alright",
                ),
                0.98,
            ),
        ]
    for intent, phrases, confidence in groups:
        if any(phrase in text for phrase in phrases):
            return RoutedIntent(intent=intent, confidence=confidence, source=f"multilingual_{language}", slots={"language": language})
    return None



def localize_room_label(value: Any, language: str) -> str:
    label = str(value or "").strip()
    if not label or language == "de":
        return label
    key = label.lower()
    translations = {
        "wohnzimmer": {"fa": "اتاق نشیمن", "ar": "غرفة المعيشة", "en": "living room"},
        "schlafzimmer": {"fa": "اتاق خواب", "ar": "غرفة النوم", "en": "bedroom"},
        "küche": {"fa": "آشپزخانه", "ar": "المطبخ", "en": "kitchen"},
        "kueche": {"fa": "آشپزخانه", "ar": "المطبخ", "en": "kitchen"},
        "bad": {"fa": "حمام", "ar": "الحمام", "en": "bathroom"},
        "badezimmer": {"fa": "حمام", "ar": "الحمام", "en": "bathroom"},
        "flur": {"fa": "راهرو", "ar": "الممر", "en": "hallway"},
        "eingang": {"fa": "ورودی", "ar": "المدخل", "en": "entrance"},
    }
    return translations.get(key, {}).get(language, label)

def localized_fallback_response(result: QueryResult, fallback: MailResponseService, language: str) -> str:
    if language == "de":
        return fallback.build(result)
    if result.permission_denied:
        return {
            "fa": "این نوع اطلاعات برای حساب شما مجاز نیست.",
            "ar": "هذا النوع من المعلومات غير مسموح لحسابك.",
            "en": "This type of information is not enabled for your account.",
        }.get(language, fallback.build(result))
    if not result.data_available:
        return {
            "fa": "فعلاً دادهٔ تازه و کافی برای یک پاسخ مطمئن وجود ندارد. Sentero ارتباط حسگرها را همچنان بررسی می‌کند.",
            "ar": "لا توجد حالياً بيانات حديثة كافية لإجابة موثوقة. يواصل Sentero مراقبة اتصال المستشعرات.",
            "en": "There is not enough fresh sensor data for a reliable answer right now. Sentero is still monitoring the sensor connection.",
        }.get(language, fallback.build(result))

    facts = result.facts or {}
    if result.intent == MailIntent.STATUS_SUMMARY:
        assessment = facts.get("assessment") or {}
        dashboard = facts.get("dashboard") or {}
        findings = assessment.get("findings") or []
        status = str(assessment.get("status") or dashboard.get("behavior_status") or "normal").lower()
        activity = facts.get("last_activity") or dashboard.get("last_activity") or {}
        room = localize_room_label(activity.get("room_label") or activity.get("room"), language)
        normal = status in {"green", "normal", "ok"} and not findings
        if language == "fa":
            first = "در حال حاضر Sentero نشانهٔ غیرعادی مهمی نمی‌بیند." if normal else "Sentero نشانه‌هایی دیده که با روند معمول فرق دارند."
            return first + (f" آخرین فعالیت شناسایی‌شده در {room} بوده است." if room else "")
        if language == "ar":
            first = "لا يرى Sentero حالياً أي مؤشرات غير عادية مهمة." if normal else "رصد Sentero مؤشرات تختلف عن النمط المعتاد."
            return first + (f" آخر نشاط تم رصده كان في {room}." if room else "")
        if language == "en":
            first = "Sentero is not seeing any notable unusual signs right now." if normal else "Sentero has detected signs that differ from the usual pattern."
            return first + (f" The last detected activity was in {room}." if room else "")

    if result.intent == MailIntent.SENSOR_HEALTH:
        count = int(facts.get("sensor_count") or 0)
        unreachable = facts.get("unreachable") or []
        low_battery = facts.get("low_battery") or []
        healthy = not unreachable and not low_battery
        if language == "fa":
            return f"بله. هر {count} حسگر تنظیم‌شده در دسترس هستند و هشدار باتری ضعیف وجود ندارد." if healthy else f"نه کاملاً. از {count} حسگر، {len(unreachable)} مورد در دسترس نیست و {len(low_battery)} مورد باتری ضعیف دارد."
        if language == "ar":
            return f"نعم. جميع المستشعرات المضبوطة وعددها {count} متاحة ولا يوجد تحذير من بطارية ضعيفة." if healthy else f"ليس تماماً. من أصل {count} مستشعراً، هناك {len(unreachable)} غير متاح و{len(low_battery)} ببطارية ضعيفة."
        if language == "en":
            return f"Yes. All {count} configured sensors are reachable, and there are no low-battery warnings." if healthy else f"Not quite. Of {count} configured sensors, {len(unreachable)} are unreachable and {len(low_battery)} have a low battery."

    if result.intent in {MailIntent.CURRENT_ACTIVITY, MailIntent.LAST_ACTIVITY, MailIntent.LAST_ROOM}:
        event = facts.get("activity") or {}
        room = localize_room_label(event.get("room_label") or event.get("room"), language)
        when = event.get("event_time_label") or event.get("relative_time")
        previous = facts.get("relation") == "previous"
        if not room:
            return localized_fallback_response(QueryResult(result.intent, "no_data", data_available=False), fallback, language)
        if language == "fa":
            if previous: return f"قبل از آن، فعالیت در {room}" + (f" در ساعت {when}" if when else "") + " شناسایی شد."
            return f"آخرین فعالیت شناسایی‌شده در {room} بوده است" + (f" ({when})" if when else "") + "."
        if language == "ar":
            if previous: return f"قبل ذلك، تم رصد نشاط في {room}" + (f" عند {when}" if when else "") + "."
            return f"آخر نشاط تم رصده كان في {room}" + (f" ({when})" if when else "") + "."
        if language == "en":
            if previous: return f"Before that, activity was detected in {room}" + (f" at {when}" if when else "") + "."
            return f"The last detected activity was in {room}" + (f" ({when})" if when else "") + "."

    if result.intent == MailIntent.HELP:
        return {
            "fa": "می‌توانید مثلاً بپرسید: «همه چیز خوبه؟»، «آخرین فعالیت کی بود؟»، «کجاست؟»، «سنسورها سالم‌اند؟» یا «دمای خانه چنده؟»",
            "ar": "يمكنك مثلاً أن تسأل: «هل كل شيء بخير؟»، «متى كان آخر نشاط؟»، «أين هي؟»، «هل المستشعرات سليمة؟» أو «ما درجة الحرارة؟»",
            "en": "You can ask things like: “Is everything okay?”, “When was the last activity?”, “Where was she last detected?”, “Are the sensors okay?”, or “What is the temperature?”",
        }.get(language, fallback.build(result))

    if result.intent == MailIntent.UNKNOWN:
        return {
            "fa": "هنوز نتوانستم این سؤال را با اطمینان تشخیص بدهم. می‌توانید دربارهٔ وضعیت کلی، آخرین فعالیت، محل آخرین فعالیت، حسگرها یا دمای خانه بپرسید.",
            "ar": "لم أتمكن بعد من فهم هذا السؤال بثقة. يمكنك السؤال عن الحالة العامة أو آخر نشاط أو مكان آخر نشاط أو المستشعرات أو درجة الحرارة.",
            "en": "I could not classify that question confidently yet. You can ask about the overall status, the last activity, the last detected room, the sensors, or the temperature.",
        }.get(language, fallback.build(result))

    # For less common intents, keep the deterministic response rather than
    # inventing a translation that could alter safety-relevant meaning.
    return fallback.build(result)

def followup_from_history(question: str, history: list[dict[str, Any]] | None) -> RoutedIntent | None:
    text = str(question or "").strip().lower()
    if not history or not text:
        return None
    previous_words = ("davor", "vorher", "und davor", "und vorher", "was war davor", "was war vorher")
    if any(word in text for word in previous_words):
        last_intent = next((str(item.get("intent") or "") for item in reversed(history) if item.get("intent")), "")
        intent = MailIntent.LAST_ROOM if last_intent == MailIntent.LAST_ROOM.value else MailIntent.LAST_ACTIVITY
        return RoutedIntent(intent=intent, confidence=0.72, source="conversation_followup", slots={"relation": "previous"})
    return None

def _prompt_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for item in (history or [])[-10:]:
        role = str(item.get("role") or "")
        text = str(item.get("text") or "").strip()
        if role not in {"user", "assistant"} or not text:
            continue
        turn: dict[str, Any] = {"role": role, "text": text[:1200]}
        if item.get("intent"):
            turn["intent"] = item.get("intent")
        slots = item.get("slots")
        if isinstance(slots, dict) and slots:
            turn["slots"] = slots
        facts = item.get("facts")
        if role == "assistant" and isinstance(facts, dict) and facts:
            turn["facts"] = facts
        turns.append(turn)
    return turns

def _answer_facts(result: QueryResult) -> dict[str, Any]:
    """Send only the facts needed for the current conversational answer.

    The query layer may contain rich dashboard/system payloads for deterministic
    fallback responses. Passing all of that to the LLM made simple questions
    sound like diagnostic reports.
    """
    facts = result.facts or {}
    if result.intent == MailIntent.STATUS_SUMMARY:
        dashboard = facts.get("dashboard") if isinstance(facts.get("dashboard"), dict) else {}
        assessment = facts.get("assessment") if isinstance(facts.get("assessment"), dict) else {}
        return {
            "person_name": dashboard.get("person_name"),
            "behavior_status": dashboard.get("behavior_status") or assessment.get("status"),
            "behavior_label": dashboard.get("behavior_label"),
            "behavior_summary": dashboard.get("behavior_summary") or assessment.get("summary"),
            "findings": assessment.get("findings") or [],
            "current_presence": dashboard.get("current_presence"),
            "last_activity": facts.get("last_activity") or dashboard.get("last_activity"),
        }
    if result.intent == MailIntent.SENSOR_HEALTH:
        return {
            "sensor_count": facts.get("sensor_count"),
            "unreachable": facts.get("unreachable") or [],
            "low_battery": facts.get("low_battery") or [],
            "latest_sensor_update_relative": facts.get("latest_sensor_update_relative"),
        }
    return facts


def llm_response_allowed(result: QueryResult) -> bool:
    if result.permission_denied or not result.data_available:
        return False
    return result.intent not in {MailIntent.HELP, MailIntent.UNKNOWN}


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
