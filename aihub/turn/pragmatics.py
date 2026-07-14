"""Conversation pragmatics / intent understanding (pre-strategy).

Hybrid deterministic signals + bounded context. Optional LLM only when
ambiguity is high. Does not hardcode one-off test phrases.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from aihub.db import append_event, fetch_recent_events_by_type

log = logging.getLogger(__name__)

CORRECTION_INTENT_EVENT = "user.intent_correction"

SpeechAct = Literal[
    "factual_question",
    "request",
    "command",
    "correction",
    "complaint",
    "praise",
    "sarcasm",
    "teasing",
    "joke",
    "rhetorical_question",
    "clarification",
    "follow_up",
    "meta_request",
    "task_instruction",
    "greeting",
    "unknown",
]

ConversationState = Literal[
    "casual_chat",
    "teasing",
    "technical_debug",
    "planning",
    "research",
    "correction",
    "frustration",
    "argument",
    "task_execution",
    "follow_up",
    "meta_request",
]

ResponseMode = Literal[
    "concise_direct",
    "playful",
    "teasing_reply",
    "technical",
    "diagnostic",
    "research_summary",
    "clarification",
    "corrective",
    "empathetic",
    "task_execution",
]

StrategyName = Literal["instant", "contextual", "research", "agentic"]


class IntentCandidate(BaseModel):
    label: str
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    requires_context: bool = False
    tool_rewrite: str = ""
    response_mode: ResponseMode = "concise_direct"


class ResponseCriticResult(BaseModel):
    score: int = Field(default=100, ge=0, le=100)
    passed: bool = True
    reason_codes: list[str] = Field(default_factory=list)
    revision_instruction: str = ""


class PragmaticAnalysis(BaseModel):
    raw_text: str = ""
    normalized_text: str = ""
    language: str = "pl"
    speech_act: SpeechAct = "unknown"
    primary_intent: str = "unknown"
    alternative_intents: list[str] = Field(default_factory=list)
    candidate_intents: list[IntentCandidate] = Field(default_factory=list)
    literal_meanings: list[str] = Field(default_factory=list)
    implied_meanings: list[str] = Field(default_factory=list)
    meta_intent: str = ""
    ambiguity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    context_dependency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    slang_detected: bool = False
    irony_detected: bool = False
    sarcasm_detected: bool = False
    humor_detected: bool = False
    teasing_detected: bool = False
    sexual_innuendo_detected: bool = False
    aggression_detected: bool = False
    frustration_detected: bool = False
    rhetorical_question_detected: bool = False
    typo_or_grammar_noise: bool = False
    temporal_reference_detected: bool = False
    normalized_temporal_reference: str = ""
    relative_date: str = ""
    needs_recent_history: bool = False
    needs_memory: bool = False
    needs_psyche: bool = False
    needs_reasoning: bool = False
    needs_planner: bool = False
    needs_web: bool = False
    recommended_strategy: StrategyName = "contextual"
    response_mode: ResponseMode = "concise_direct"
    conversation_state: ConversationState = "casual_chat"
    rewritten_query_for_tools: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)
    degraded: bool = False
    timing_ms: float = 0.0
    critic: ResponseCriticResult | None = None
    correction_learned: bool = False
    history_injected: bool = False
    strategy_before: str = ""
    strategy_after: str = ""


_WS = re.compile(r"\s+")
_ASCII = str.maketrans(
    "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ",
    "acelnoszzACELNOSZZ",
)

# Food/body double-entendre stems when used in short elliptical “X robisz?” form.
_INNUENDO_ACTIVITY = re.compile(
    r"(?iu)\b("
    r"lody|lodzik\w*|banany|banan|banańcz\w*|banancz\w*|kiełbas\w*|kielbas\w*|"
    r"ogór\w*|ogor\w*|cukierki|lizak\w*|lizanie|ssanie|"
    r"obciągan\w*|obciagan\w*|kutasa|kutasa?|fiauti|fiuta|"
    r"rżnię\w*|rzniet\w*|ruchan\w*|sex|seks|oral\w*|pipk\w*|cipk\w*"
    r")\b",
)
_INNUENDO_ELLIPSIS = re.compile(
    r"(?iu)^\s*(?:a\s+)?(?:"
    r"[\wąćęłńóśźż-]{2,24}\s+){0,2}"
    r"(?:robisz|robicie|dasz|dajesz|masz|chcesz|mogę|moge|dostanę|dostane)"
    r"(?:\s+\w{1,16}){0,3}\s*\??\s*$",
)
_RECIPE_MARKERS = re.compile(
    r"(?iu)\b("
    r"przepis|składnik\w*|skladnik\w*|waniliow\w*|czekoladow\w*|porcja|"
    r"jak\s+zrobić|jak\s+zrobic|ugotować|ugotowac|piec|upiec|mikser|"
    r"lodówka|lodowka|deser|kalor\w*|"
    r"(?:\d+(?:[.,]\d+)?\s*)?(?:g|kg|ml|l)\b|"
    r"łyżk\w*|lyzk\w*|szklank\w*|mąka|maka|cukier|"
    r"gram(?:y|ów|ow)?\s+(?:cukru|mąki|maki|masła|masla|śmietany|smietany)"
    r")\b",
)
_SARCASM = re.compile(
    r"(?iu)\b("
    r"zajebiście|zajebiscie|genialnie|super\s+robiota|no\s+zajebiście|"
    r"pięknie|pieknie|brawo\s+ci|świetnie\s+poszło|swietnie\s+poszlo|"
    r"no\s+to\s+super|jak\s+zawsze"
    r")\b",
)
_NEGATIVE_CONTEXT = re.compile(
    r"(?iu)\b("
    r"nie\s+dział\w*|nie\s+dzial\w*|znowu|znów|znow|błąd|blad|error|"
    r"padło|padlo|crash|wypi.*c?rąb|poszło\s+w\s+chuj|gówno|gowno|"
    r"kurwa|fixiłeś|sypie\s+się|sypie\s+sie|"
    r"naprawiłeś|naprawiles|naprawiłaś|naprawilas|naprawione|"
    r"znowu\s+to\s+samo|nie\s+działa|wywaliło|wywalilo"
    r")\b",
)
_FRUSTRATION = re.compile(
    r"(?iu)\b("
    r"gówno|gowno|chuj|kurwa|dlaczego|czemu|nie\s+dział\w*|nie\s+dzial\w*|"
    r"wkurw|bezsens|beznadziej"
    r")\b",
)
_GREETING = re.compile(r"(?iu)^\s*(elo|siema|hej|cześć|czesc|yo|hi|hey)\s*[!.?]*\s*$")
_CORRECTION_INTENT = re.compile(
    r"(?iu)\b("
    r"chodziło\s+o|chodzilo\s+o|miałem\s+na\s+myśli|miałam\s+na\s+myśli|"
    r"mialem\s+na\s+mysli|miałam\s+na\s+mysli|nie\s+o\s+to|"
    r"to\s+była\s+zaczepka|to\s+byla\s+zaczepka|metafora|podtekst|aluzja"
    r")\b",
)
_META = re.compile(
    r"(?iu)\b("
    r"pokaż\s+wszystkie\s+endpoint\w*|pokaz\s+wszystkie\s+endpoint\w*|"
    r"lista\s+endpoint\w*|które\s+moduły\s+są\s+martwe|ktore\s+moduly\s+sa\s+martwe|"
    r"audyt\s+api|mapa\s+rout\w*|porównaj\s+api|porownaj\s+api"
    r")\b",
)
_CONTEXT_DEIXIS = re.compile(
    r"(?iu)\b("
    r"ten|ta|to|tego|tamten|tamto|wczorajszy|przedwczorajszy|"
    r"poprzedni|ostatni|tamten\s+od|jak\s+wyżej|jak\s+wyzej"
    r")\b",
)
_TEMPORAL_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?iu)\bprzed\s+wczoraj\b"), "przedwczoraj"),
    (re.compile(r"(?iu)\bprzedwczoraj\b"), "przedwczoraj"),
    (re.compile(r"(?iu)\bwczoraj\b"), "wczoraj"),
    (re.compile(r"(?iu)\bdziś|dzis|dzisiaj\b"), "dziś"),
    (re.compile(r"(?iu)\bjutro\b"), "jutro"),
]
_SPORT_CURRENT = re.compile(
    r"(?iu)\b("
    r"mecz|meczu|mistrzostw\w*|world\s*cup|mundial|reprezentac\w*|"
    r"liga|wynik|wyniki|spotkani\w*|kwalifikacj\w*"
    r")\b",
)
_TECH_SIMPLE = re.compile(
    r"(?iu)\b("
    r"import\s+json|TypeError|NameError|fixuję|napraw|fix|bug|"
    r"traceback|pytest|lint|compile"
    r")\b",
)
_IRONY_SOFT = re.compile(
    r"(?iu)\b(no\s+jasne|pewnie\s+że|pewne|jak\s+nia|niby)\b",
)
_ELLIPSIS_SHORT = re.compile(r"(?iu)^\s*[\wąćęłńóśźż?!.\-]{1,40}\s*$")
_SLANG = re.compile(
    r"(?iu)\b(elo|siema|mordo|kurwa|chuj|zajebiście|zajebiscie|git|spoko|nara)\b",
)


def _strip_diacritics(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def normalize_surface_text(raw: str) -> tuple[str, bool, list[str]]:
    """Normalize typos/spacing without inventing meaning. Returns (text, noisy, codes)."""
    codes: list[str] = []
    t = _WS.sub(" ", str(raw or "").strip())
    noisy = False
    for pat, repl in _TEMPORAL_FIXES:
        if repl == "przedwczoraj" and "przed wczoraj" in t.lower() and pat.search(t):
            t2 = pat.sub(repl, t)
            if t2 != t:
                t = t2
                noisy = True
                codes.append("PRAGMATICS_TEMPORAL_NORMALIZATION")
    t = _WS.sub(" ", t).strip()
    if re.search(r"(?iu)\b\w+\s+\w+\s+przed\s+\w+", str(raw or "")):
        noisy = True
    return t, noisy, codes


def _relative_date_for(token: str, today: date | None = None) -> str:
    today = today or date.today()
    tok = token.lower()
    if tok == "przedwczoraj":
        return (today - timedelta(days=2)).isoformat()
    if tok == "wczoraj":
        return (today - timedelta(days=1)).isoformat()
    if tok in ("dziś", "dzis", "dzisiaj"):
        return today.isoformat()
    if tok == "jutro":
        return (today + timedelta(days=1)).isoformat()
    return ""


def _history_snippets(history: list[Any] | None, limit: int = 6) -> list[str]:
    out: list[str] = []
    for msg in (history or [])[-limit:]:
        if isinstance(msg, dict):
            role = str(msg.get("role") or "")
            content = str(msg.get("content") or "")
        else:
            role = str(getattr(msg, "role", "") or "")
            content = str(getattr(msg, "content", "") or "")
        if content.strip():
            out.append(f"{role}: {content.strip()[:240]}")
    return out


def _load_intent_corrections(user_id: str, session_id: str, limit: int = 12) -> list[dict[str, Any]]:
    if not user_id:
        return []
    try:
        rows = fetch_recent_events_by_type(user_id, CORRECTION_INTENT_EVENT, limit=limit)
    except Exception as exc:
        log.debug("intent corrections unavailable: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data") if isinstance(row, dict) else {}
        if not isinstance(data, dict):
            continue
        # Session-scoped by default; durable=True spans sessions for same user.
        if data.get("durable") or not session_id or str(data.get("session_id") or "") == session_id:
            out.append(data)
    return out


def record_intent_correction(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    previous_message: str,
    correction_text: str,
    corrected_intent: str,
) -> bool:
    if not user_id or str(user_id).startswith("audit"):
        return False
    try:
        append_event(
            user_id,
            CORRECTION_INTENT_EVENT,
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "previous_message": (previous_message or "")[:400],
                "correction_text": (correction_text or "")[:500],
                "corrected_intent": corrected_intent,
                "durable": False,
                "kind": "intent",
            },
        )
        return True
    except Exception as exc:
        log.debug("intent correction write failed: %s", exc)
        return False


def analyze_pragmatics(
    *,
    raw_text: str,
    history: list[Any] | None = None,
    user_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    memory_brief: str = "",
    psyche_brief: str = "",
    today: date | None = None,
) -> PragmaticAnalysis:
    t0 = time.monotonic()
    raw = str(raw_text or "")
    normalized, noisy, codes = normalize_surface_text(raw)
    lower = normalized.lower()
    hist = _history_snippets(history)
    hist_n = len(history or [])
    words = [w for w in re.split(r"\s+", normalized) if w]
    n_words = len(words)

    pa = PragmaticAnalysis(
        raw_text=raw,
        normalized_text=normalized,
        typo_or_grammar_noise=noisy,
        reason_codes=list(codes),
    )

    # Signals
    if _SLANG.search(normalized):
        pa.slang_detected = True
    if _GREETING.match(normalized):
        pa.speech_act = "greeting"
        pa.primary_intent = "greeting"
        pa.conversation_state = "casual_chat"
        pa.response_mode = "concise_direct"
        pa.recommended_strategy = "instant"
        pa.confidence = 0.9
        pa.candidate_intents = [
            IntentCandidate(
                label="greeting",
                description="Krótke powitanie / smalltalk",
                confidence=0.9,
                response_mode="concise_direct",
            )
        ]
        pa.timing_ms = (time.monotonic() - t0) * 1000.0
        return pa

    recipe = bool(_RECIPE_MARKERS.search(normalized))
    innuendo_lex = bool(_INNUENDO_ACTIVITY.search(normalized))
    elliptical = bool(_INNUENDO_ELLIPSIS.match(normalized)) or (
        n_words <= 6 and normalized.endswith("?")
    )
    sexual = False
    teasing = False
    if innuendo_lex and elliptical and not recipe:
        sexual = True
        teasing = True
        pa.reason_codes.append("PRAGMATICS_SEXUAL_INNUENDO_DETECTED")
        pa.reason_codes.append("PRAGMATICS_TEASING_DETECTED")
    elif elliptical and n_words <= 5 and not recipe and not _TECH_SIMPLE.search(normalized):
        # Short tease-like ellipsis — raise ambiguity even without known lexeme
        pa.ambiguity_score = max(pa.ambiguity_score, 0.55)

    # Prior corrections for similar messages
    corrections = _load_intent_corrections(user_id, session_id)
    for c in corrections:
        prev = str(c.get("previous_message") or "").lower()
        if prev and (
            prev[:40] in lower
            or lower[:40] in prev
            or (
                innuendo_lex
                and str(c.get("corrected_intent") or "").startswith("sexual")
            )
        ):
            sexual = True
            teasing = True
            pa.reason_codes.append("PRAGMATICS_CORRECTION_BIAS")
            pa.primary_intent = str(c.get("corrected_intent") or "sexual_teasing")
            break

    # Correction speech act this turn
    if _CORRECTION_INTENT.search(normalized):
        pa.speech_act = "correction"
        pa.conversation_state = "correction"
        pa.response_mode = "corrective"
        pa.needs_recent_history = True
        pa.needs_memory = True
        pa.context_dependency_score = max(pa.context_dependency_score, 0.85)
        pa.reason_codes.append("PRAGMATICS_CONTEXT_REQUIRED")
        # Learn from "chodziło o …"
        corrected = "sexual_teasing" if _INNUENDO_ACTIVITY.search(normalized) or "seks" in lower or "obciągan" in lower or "obciagan" in lower else "intent_clarification"
        if "deser" in lower or "przepis" in lower or "wanili" in lower:
            corrected = "literal_recipe_or_food"
        prev_user = ""
        for h in reversed(hist):
            if h.startswith("user:"):
                prev_user = h[5:].strip()
                break
        if user_id and prev_user:
            pa.correction_learned = record_intent_correction(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                previous_message=prev_user,
                correction_text=normalized,
                corrected_intent=corrected,
            )
            if pa.correction_learned:
                pa.reason_codes.append("PRAGMATICS_CORRECTION_LEARNED")
        pa.primary_intent = "user_correction"
        pa.recommended_strategy = "contextual"
        pa.confidence = 0.88
        pa.timing_ms = (time.monotonic() - t0) * 1000.0
        return pa

    sarcasm = False
    if _SARCASM.search(normalized) and (
        _NEGATIVE_CONTEXT.search(normalized)
        or any(_NEGATIVE_CONTEXT.search(h) for h in hist[-4:])
    ):
        sarcasm = True
        pa.reason_codes.append("PRAGMATICS_SARCASM_DETECTED")
    elif _IRONY_SOFT.search(normalized) and _NEGATIVE_CONTEXT.search(normalized):
        pa.irony_detected = True
        pa.reason_codes.append("PRAGMATICS_IRONY_DETECTED")

    frustration = bool(_FRUSTRATION.search(normalized))
    aggression = bool(re.search(r"(?iu)\b(spierdalaj|wypierdalaj|zamknij\s+się)\b", normalized))

    # Temporal + sport
    temporal_token = ""
    for pat, repl in _TEMPORAL_FIXES:
        if pat.search(normalized):
            temporal_token = repl
            break
    if temporal_token:
        pa.temporal_reference_detected = True
        pa.normalized_temporal_reference = temporal_token
        pa.relative_date = _relative_date_for(temporal_token, today=today)
        if "PRAGMATICS_TEMPORAL_NORMALIZATION" not in pa.reason_codes and "przed wczoraj" in raw.lower():
            pa.reason_codes.append("PRAGMATICS_TEMPORAL_NORMALIZATION")

    needs_web = False
    if _SPORT_CURRENT.search(normalized) and (
        pa.temporal_reference_detected
        or re.search(r"(?iu)\b20\d{2}\b", normalized)
        or re.search(r"(?iu)\b(wynik|kto\s+wygra|skład)\b", normalized)
    ):
        needs_web = True
        pa.reason_codes.append("PRAGMATICS_WEB_QUERY_REWRITE")

    # Context dependency
    if _CONTEXT_DEIXIS.search(normalized) and n_words <= 12:
        pa.context_dependency_score = max(pa.context_dependency_score, 0.75)
        pa.needs_recent_history = True
        pa.reason_codes.append("PRAGMATICS_CONTEXT_REQUIRED")
    if hist_n >= 1 and elliptical and n_words <= 8:
        pa.context_dependency_score = max(pa.context_dependency_score, 0.55)

    # Candidates
    candidates: list[IntentCandidate] = []

    if _META.search(normalized):
        pa.speech_act = "meta_request"
        pa.meta_intent = "audit_surface_or_dead_modules"
        pa.conversation_state = "meta_request"
        pa.needs_reasoning = True
        pa.recommended_strategy = "contextual"
        pa.primary_intent = "meta_audit_request"
        candidates.append(
            IntentCandidate(
                label="meta_audit_request",
                description="Meta-intencja: audyt powierzchni API / martwych modułów, nie sama lista",
                confidence=0.78,
                evidence=["meta_request_markers"],
                response_mode="diagnostic",
            )
        )

    if recipe or (innuendo_lex and recipe):
        candidates.append(
            IntentCandidate(
                label="literal_food_or_recipe",
                description="Pytanie o jedzenie / przepis / deser",
                confidence=0.86 if recipe else 0.55,
                evidence=["recipe_markers"],
                response_mode="concise_direct",
            )
        )
    if sexual or (innuendo_lex and elliptical and not recipe):
        candidates.append(
            IntentCandidate(
                label="sexual_teasing",
                description="Zaczepka / podtekst seksualny lub dwuznaczność",
                confidence=0.82 if sexual else 0.62,
                evidence=["elliptical_double_entendre"],
                requires_context=True,
                response_mode="teasing_reply",
            )
        )
        pa.sexual_innuendo_detected = True
        pa.teasing_detected = True
        pa.humor_detected = True
    if sarcasm:
        candidates.append(
            IntentCandidate(
                label="sarcastic_complaint",
                description="Sarkazm / narzekanie owinięte w pochwałę",
                confidence=0.8,
                evidence=["praise_lexicon+negative_context"],
                response_mode="diagnostic",
            )
        )
        pa.sarcasm_detected = True
        pa.irony_detected = True
    if frustration and not sarcasm:
        candidates.append(
            IntentCandidate(
                label="frustrated_debug",
                description="Frustracja + problem techniczny",
                confidence=0.75,
                evidence=["frustration_markers"],
                response_mode="diagnostic",
            )
        )
        pa.frustration_detected = True
    if needs_web:
        rel = pa.relative_date or pa.normalized_temporal_reference
        cleaned = re.sub(
            r"(?iu)\b(przedwczoraj|wczoraj|dziś|dzisiaj|gramy|gram|gracie)\b",
            " ",
            normalized,
        )
        cleaned = _WS.sub(" ", cleaned).strip(" ,.?!")
        # Drop leading redundant "mecz" when already in template
        cleaned2 = re.sub(r"(?iu)^\s*mecz\b", "", cleaned).strip()
        topic = cleaned2 or cleaned or "sport"
        rewritten = f"wynik meczu {topic} data:{rel}".strip()
        rewritten = _WS.sub(" ", rewritten).strip()
        candidates.append(
            IntentCandidate(
                label="sports_result_research",
                description="Aktualny wynik sportowy / research",
                confidence=0.86,
                evidence=["sport+temporal"],
                tool_rewrite=rewritten,
                response_mode="research_summary",
            )
        )
        pa.rewritten_query_for_tools = rewritten
        pa.needs_web = True
        pa.response_mode = "research_summary"
    if _TECH_SIMPLE.search(normalized) and not sexual:
        candidates.append(
            IntentCandidate(
                label="simple_technical_fix",
                description="Prosta poprawka techniczna",
                confidence=0.84,
                evidence=["tech_tokens"],
                response_mode="technical",
            )
        )
    if not candidates:
        if "?" in normalized:
            candidates.append(
                IntentCandidate(
                    label="factual_or_open_question",
                    description="Pytanie otwarte / faktograficzne",
                    confidence=0.55,
                    response_mode="concise_direct",
                )
            )
        else:
            candidates.append(
                IntentCandidate(
                    label="statement_or_chat",
                    description="Zwyczajna wypowiedź / chat",
                    confidence=0.5,
                    response_mode="concise_direct",
                )
            )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    pa.candidate_intents = candidates
    top = candidates[0]
    alt = candidates[1] if len(candidates) > 1 else None
    pa.primary_intent = top.label
    pa.alternative_intents = [c.label for c in candidates[1:4]]
    pa.literal_meanings = [c.description for c in candidates if "literal" in c.label or "recipe" in c.label or "technical" in c.label]
    pa.implied_meanings = [c.description for c in candidates if c.label in ("sexual_teasing", "sarcastic_complaint")]
    pa.response_mode = top.response_mode
    pa.confidence = top.confidence

    # Ambiguity: close competitors or sexual+literal both present
    if alt and abs(top.confidence - alt.confidence) < 0.22:
        pa.ambiguity_score = max(pa.ambiguity_score, 0.72)
        pa.reason_codes.append("PRAGMATICS_AMBIGUITY_BLOCKED_INSTANT")
    if sexual and any(c.label.startswith("literal") for c in candidates):
        pa.ambiguity_score = max(pa.ambiguity_score, 0.8)
        if "PRAGMATICS_AMBIGUITY_BLOCKED_INSTANT" not in pa.reason_codes:
            pa.reason_codes.append("PRAGMATICS_AMBIGUITY_BLOCKED_INSTANT")
    if elliptical and n_words <= 5 and not recipe and not _TECH_SIMPLE.search(normalized):
        pa.ambiguity_score = max(pa.ambiguity_score, 0.6)

    # Speech act
    if pa.speech_act == "unknown":
        if sexual or teasing:
            pa.speech_act = "teasing"
        elif sarcasm:
            pa.speech_act = "sarcasm"
        elif frustration:
            pa.speech_act = "complaint"
        elif normalized.endswith("?"):
            pa.speech_act = "factual_question"
        elif _TECH_SIMPLE.search(normalized):
            pa.speech_act = "task_instruction"
        else:
            pa.speech_act = "request"

    # Conversation state
    if pa.speech_act == "meta_request" or pa.meta_intent:
        pa.conversation_state = "meta_request"
    elif sexual or teasing:
        pa.conversation_state = "teasing"
    elif sarcasm or frustration:
        pa.conversation_state = "frustration" if frustration else "argument"
    elif needs_web:
        pa.conversation_state = "research"
    elif _TECH_SIMPLE.search(normalized):
        pa.conversation_state = "technical_debug"
    elif pa.needs_recent_history:
        pa.conversation_state = "follow_up"
    else:
        pa.conversation_state = "casual_chat"

    # Preserve meta speech act / primary when still top
    if pa.meta_intent and pa.speech_act != "meta_request":
        pa.speech_act = "meta_request"
    if pa.meta_intent and top.label == "meta_audit_request":
        pa.primary_intent = "meta_audit_request"
        pa.response_mode = "diagnostic"
        pa.needs_reasoning = True
        pa.recommended_strategy = "contextual"

    # Strategy recommendation
    if needs_web:
        pa.recommended_strategy = "research"
        pa.needs_web = True
    elif pa.meta_intent or top.label == "meta_audit_request":
        pa.recommended_strategy = "contextual"
        pa.needs_reasoning = True
        if "PRAGMATICS_REASONING_ESCALATION" not in pa.reason_codes:
            pa.reason_codes.append("PRAGMATICS_REASONING_ESCALATION")
    elif (
        pa.ambiguity_score >= 0.55
        or sexual
        or teasing
        or sarcasm
        or pa.irony_detected
        or pa.context_dependency_score >= 0.55
    ):
        pa.recommended_strategy = "contextual"
        if pa.ambiguity_score >= 0.7:
            pa.needs_reasoning = True
            pa.reason_codes.append("PRAGMATICS_REASONING_ESCALATION")
        if sexual or teasing or sarcasm:
            pa.needs_psyche = True
        if pa.context_dependency_score >= 0.55:
            pa.needs_recent_history = True
            pa.needs_memory = True
    elif top.label == "simple_technical_fix" and n_words <= 20:
        pa.recommended_strategy = "instant"
    elif n_words <= 15 and pa.ambiguity_score < 0.35 and hist_n < 2:
        pa.recommended_strategy = "instant"
    else:
        pa.recommended_strategy = "contextual"

    # Psyche/relation — soft flags (actual tone applied in prompt)
    if psyche_brief and psyche_brief != "BRAK DANYCH":
        pa.needs_psyche = True
    if memory_brief and "BRAK" not in memory_brief[:20]:
        if pa.needs_memory or pa.context_dependency_score >= 0.4:
            pa.needs_memory = True

    # Aggression
    pa.aggression_detected = aggression
    if aggression:
        pa.response_mode = "concise_direct"

    # Rhetorical
    if sarcasm and normalized.endswith("?"):
        pa.rhetorical_question_detected = True

    # Interpreted intent text (not overwriting raw history)
    if needs_web and pa.rewritten_query_for_tools:
        pa.normalized_text = (
            f"{normalized} [interpreted: research about event on {pa.relative_date or pa.normalized_temporal_reference}]"
        )
    elif sexual:
        pa.normalized_text = (
            f"{normalized} [interpreted: likely teasing/sexual innuendo; do not answer as a dessert recipe]"
        )

    pa.timing_ms = (time.monotonic() - t0) * 1000.0
    return pa


def apply_pragmatics_to_strategy(
    *,
    selected_strategy: str,
    reason_codes: list[str],
    web_decision: str,
    web_decision_reason: str,
    pragmatics: PragmaticAnalysis,
) -> tuple[str, list[str], str, str]:
    """Hard rules: block instant / force contextual|research."""
    codes = list(reason_codes)
    strategy = selected_strategy
    pragmatics.strategy_before = selected_strategy
    web = web_decision
    web_reason = web_decision_reason

    if pragmatics.needs_web or pragmatics.recommended_strategy == "research":
        strategy = "research"
        web = "required"
        web_reason = "pragmatics_research_required"
        if "PRAGMATICS_WEB_QUERY_REWRITE" not in codes:
            codes.append("PRAGMATICS_WEB_QUERY_REWRITE")
    elif (
        strategy == "research"
        and not pragmatics.needs_web
        and pragmatics.recommended_strategy in ("contextual", "instant")
        and (
            pragmatics.needs_recent_history
            or pragmatics.context_dependency_score >= 0.55
            or pragmatics.sexual_innuendo_detected
            or pragmatics.teasing_detected
            or pragmatics.sarcasm_detected
        )
    ):
        # Deixis / follow-up / teasing must not force blind web search on broken literal query.
        strategy = "contextual"
        web = "off"
        web_reason = "pragmatics_demote_research_to_contextual"
        codes.append("PRAGMATICS_CONTEXT_REQUIRED")
    elif strategy == "instant" and (
        pragmatics.ambiguity_score >= 0.55
        or pragmatics.sexual_innuendo_detected
        or pragmatics.teasing_detected
        or pragmatics.sarcasm_detected
        or pragmatics.irony_detected
        or pragmatics.context_dependency_score >= 0.55
        or pragmatics.recommended_strategy == "contextual"
        or pragmatics.needs_reasoning
        or bool(pragmatics.meta_intent)
    ):
        strategy = "contextual"
        codes.append("PRAGMATICS_AMBIGUITY_BLOCKED_INSTANT")
        if pragmatics.context_dependency_score >= 0.55:
            codes.append("PRAGMATICS_CONTEXT_REQUIRED")
        if pragmatics.sexual_innuendo_detected:
            codes.append("PRAGMATICS_SEXUAL_INNUENDO_DETECTED")
        if pragmatics.teasing_detected:
            codes.append("PRAGMATICS_TEASING_DETECTED")
        if pragmatics.sarcasm_detected:
            codes.append("PRAGMATICS_SARCASM_DETECTED")
        if pragmatics.irony_detected:
            codes.append("PRAGMATICS_IRONY_DETECTED")
        if pragmatics.needs_reasoning or pragmatics.meta_intent:
            if "PRAGMATICS_REASONING_ESCALATION" not in codes:
                codes.append("PRAGMATICS_REASONING_ESCALATION")

    if pragmatics.needs_reasoning and "PRAGMATICS_REASONING_ESCALATION" not in codes:
        codes.append("PRAGMATICS_REASONING_ESCALATION")
    if pragmatics.needs_planner:
        codes.append("PRAGMATICS_PLANNER_ESCALATION")
        if strategy == "instant":
            strategy = "contextual"

    # Deduplicate codes preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)

    pragmatics.strategy_after = strategy
    return strategy, uniq, web, web_reason


def pragmatics_prompt_block(pa: PragmaticAnalysis, *, psyche_brief: str = "") -> str:
    """Inject interpretation / response mode into system prompt."""
    lines = [
        "PRAGMATYKA ROZMOWY (wiążące dla tej tury):",
        f"- speech_act: {pa.speech_act}",
        f"- conversation_state: {pa.conversation_state}",
        f"- primary_intent: {pa.primary_intent} (confidence={pa.confidence:.2f})",
        f"- response_mode: {pa.response_mode}",
        f"- ambiguity_score: {pa.ambiguity_score:.2f}",
    ]
    if pa.alternative_intents:
        lines.append(f"- alternative_intents: {', '.join(pa.alternative_intents[:4])}")
    if pa.sexual_innuendo_detected or pa.teasing_detected:
        lines.append(
            "- Jeśli to zaczepka/dwuznaczność: odpowiedz krótko i naturalnie, "
            "NIE dawaj przepisu kulinarnego, NIE moralizuj, NIE rozkładaj żartu na czynniki."
        )
    if pa.sarcasm_detected:
        lines.append(
            "- Wykryto sarkazm: traktuj jako skargę/ironię, nie jako pochwałę."
        )
    if pa.needs_web and pa.rewritten_query_for_tools:
        lines.append(
            f"- Research query (przepisane, nie kopiuj błędnego raw): {pa.rewritten_query_for_tools}"
        )
    if pa.normalized_temporal_reference:
        lines.append(
            f"- Czas: {pa.normalized_temporal_reference}"
            + (f" → {pa.relative_date}" if pa.relative_date else "")
        )
    if pa.meta_intent:
        lines.append(f"- meta_intent: {pa.meta_intent} — nie ograniczaj się do surowej listy.")
    if pa.response_mode == "teasing_reply":
        lines.append("- Ton: lekka riposta / playful, bez helpdesku.")
    elif pa.response_mode == "diagnostic":
        lines.append("- Ton: diagnostyczny, konkretny, bez lania wody.")
    elif pa.response_mode == "concise_direct":
        lines.append("- Ton: krótko i na temat.")
    if psyche_brief and "BRAK" not in psyche_brief[:24]:
        lines.append(
            "- Psyche/relation: dostosuj bezpośredniość, humor i formalność do stanu relacji; "
            "nie udawaj człowieka."
        )
    return "\n".join(lines)


def critique_response(
    *,
    response_text: str,
    pragmatics: PragmaticAnalysis,
) -> ResponseCriticResult:
    text = str(response_text or "")
    lower = text.lower()
    score = 100
    codes: list[str] = []
    revision = ""

    recipe_words = (
        "przepis",
        "składnik",
        "skladnik",
        "wanili",
        "ubij",
        "ml mleka",
        "lodówka",
        "deser",
        "cukru",
        "śmietan",
    )
    if pragmatics.sexual_innuendo_detected or pragmatics.teasing_detected:
        if any(w in lower for w in recipe_words) or "jak zrobić lody" in lower:
            score -= 55
            codes.append("CRITIC_LITERAL_MISREAD_INNUENDO")
            revision = (
                "Użytkownik raczej żartuje/zaczepia seksualnie — odpowiedz krótko i naturalnie "
                "na podtekst; NIE podawaj przepisu na lody/deser."
            )
        if re.search(r"(?iu)\b(prompt|dall.?e|midjourney|stable\s+diffusion|wygeneruj\s+obraz)\b", text):
            score -= 50
            codes.append("CRITIC_LITERAL_PRODUCT_FOR_INNUENDO")
            revision = (
                "To zaczepka/dwuznaczność, nie prośba o obrazek ani produkt. "
                "Krótka riposta na podtekst — bez promptów graficznych i bez poradnika."
            )
        if len(text) > 700:
            score -= 15
            codes.append("CRITIC_TOO_LONG_FOR_TEASE")

    if pragmatics.sarcasm_detected and re.search(
        r"(?iu)\b(dzięki|dziekuje|dziękuję|cieszę się|ciesze sie|miło słyszeć|milo slyszec|dziękuję za feedback)\b",
        text,
    ):
        score -= 40
        codes.append("CRITIC_TOOK_SARCASM_AS_PRAISE")
        revision = (
            "To był sarkazm/skarga. Nie dziękuj za pochwałę — odnieś się do problemu."
        )

    if pragmatics.speech_act == "greeting" and (
        "jak mogę pomóc" in lower or "co dziś potrzebujesz" in lower
    ):
        score -= 40
        codes.append("CRITIC_HELPDESK_TONE")
        revision = "To greeting — krótko i naturalnie, bez helpdesku."

    if "jestem modelem" in lower and "prompt" in lower:
        score -= 50
        codes.append("CRITIC_PROMPT_LEAKAGE")

    if pragmatics.needs_web and pragmatics.rewritten_query_for_tools:
        # If response copies broken raw phrase as search success
        if "przed wczoraj" in lower and "przedwczoraj" not in lower:
            score -= 20
            codes.append("CRITIC_BAD_TEMPORAL_COPY")

    passed = score >= 70
    if not passed and not revision:
        revision = (
            f"Popraw odpowiedź pod primary_intent={pragmatics.primary_intent}, "
            f"speech_act={pragmatics.speech_act}, response_mode={pragmatics.response_mode}."
        )
    return ResponseCriticResult(
        score=max(0, score),
        passed=passed,
        reason_codes=codes,
        revision_instruction=revision,
    )


def pragmatics_trace_fields(pa: PragmaticAnalysis) -> dict[str, Any]:
    return {
        "pragmatics_analysis_happened": True,
        "pragmatics_degraded": pa.degraded,
        "pragmatics_model_used": "hybrid_rules_v1",
        "normalized_text": (pa.normalized_text or "")[:400],
        "primary_intent": pa.primary_intent,
        "primary_intent_confidence": pa.confidence,
        "alternative_intent_count": len(pa.alternative_intents),
        "ambiguity_score": pa.ambiguity_score,
        "context_dependency_score": pa.context_dependency_score,
        "conversation_state": pa.conversation_state,
        "speech_act": pa.speech_act,
        "slang_detected": pa.slang_detected,
        "irony_detected": pa.irony_detected,
        "sarcasm_detected": pa.sarcasm_detected,
        "humor_detected": pa.humor_detected,
        "teasing_detected": pa.teasing_detected,
        "sexual_innuendo_detected": pa.sexual_innuendo_detected,
        "aggression_detected": pa.aggression_detected,
        "frustration_detected": pa.frustration_detected,
        "temporal_reference_detected": pa.temporal_reference_detected,
        "normalized_temporal_reference": pa.normalized_temporal_reference,
        "relative_date": pa.relative_date,
        "instant_path_blocked_by_pragmatics": (
            pa.strategy_before == "instant" and pa.strategy_after != "instant"
        ),
        "strategy_before_pragmatics": pa.strategy_before,
        "strategy_after_pragmatics": pa.strategy_after,
        "history_injected_by_pragmatics": pa.needs_recent_history,
        "memory_injected_by_pragmatics": pa.needs_memory,
        "psyche_injected_by_pragmatics": pa.needs_psyche,
        "reasoning_enabled_by_pragmatics": pa.needs_reasoning,
        "planner_enabled_by_pragmatics": pa.needs_planner,
        "web_enabled_by_pragmatics": pa.needs_web,
        "web_query_rewritten": bool(pa.rewritten_query_for_tools),
        "rewritten_query_for_tools": (pa.rewritten_query_for_tools or "")[:300],
        "response_mode": pa.response_mode,
        "response_critic_score": pa.critic.score if pa.critic else None,
        "response_revision_happened": bool(
            pa.critic and (not pa.critic.passed) and pa.critic.revision_instruction
        ),
        "response_revision_reason_codes": list(pa.critic.reason_codes) if pa.critic else [],
        "correction_signal_detected": pa.speech_act == "correction",
        "correction_learned": pa.correction_learned,
        "pragmatics_reason_codes": list(pa.reason_codes),
        "meta_intent": pa.meta_intent,
    }
