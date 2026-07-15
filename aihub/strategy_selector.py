#!/usr/bin/env python3

"""
Pre-routing strategy selector: classifies execution intent before planner/reasoning.

StrategySelector (class) performs deterministic text+context classification.
Module-level select_strategy() adds bounded memory/psyche I/O, psyche modulation,
and maps to StrategySelection for the canonical chat runtime.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from aihub.psyche_core import get_psyche_core

logger = logging.getLogger(__name__)

StrategyType = Literal["instant", "contextual", "research", "agentic"]

_LOCAL_INFRA_NO_WEB = re.compile(
    r"(?iu)\b("
    r"zrestartuj\s+.*backend\w*|"
    r"restartuj\s+.*backend\w*|"
    r"status\s+backend\w*|"
    r"sprawd[źz]\s+.*backend\w*|"
    r"health\s+(?:\w+\s+){0,4}backend\w*|"
    r"/ops/ready|/system/ping|"
    r"ma\s+(?:teraz\s+)?adres\s+\d{1,3}(?:\.\d{1,3}){3}|"
    r"adres\s+\d{1,3}(?:\.\d{1,3}){3}"
    r")\b"
)

# Stable reason codes for classification (extended for rule-trace contract)
REASON_CODES = {
    "SIMPLE_DIRECT_ASK": "Direct factual or greeting query",
    "NO_CONTEXT_NEEDED": "Response does not require memory",
    "EMPTY_MEMORY": "No relevant memory available",
    "MEMORY_CONTINUATION": "User referencing previous context",
    "USER_PREFERENCE_MATCH": "Memory contains user preferences",
    "TASK_CONTINUATION_SIGNAL": "Ongoing task context present",
    "PSYCHE_CONTEXT_IMPORTANT": "Psyche state affects response",
    "CURRENT_INFO_REQUIRED": "Query mentions dates/current events",
    "URL_ANALYSIS_REQUIRED": "Query contains URL or requests analysis",
    "SOURCE_VERIFICATION_NEEDED": "Response needs factual sources",
    "FACTUAL_ASSERTION_HIGH_STAKES": "High-stakes factual claim",
    "MULTI_STEP_TASK": "Task requires planning/steps",
    "ACTIVE_GOAL_PRESENT": "Active goal affects execution",
    "GOAL_HIGH_URGENCY": "Goal has high urgency signal",
    "TOOL_EXECUTION_LIKELY": "Response will need tools",
    "MEMORY_UNAVAILABLE_FALLBACK": "Memory lookup failed, degraded routing",
    "PSYCHE_UNAVAILABLE_FALLBACK": "Psyche unavailable, degraded routing",
    "TIMEOUT_FALLBACK": "Timeout during selection, degraded routing",
    "CONFLICT_SIGNAL_DETECTED": "Memory has conflicting signals",
    "PSYCHE_TENSION_DOWNGRADE": "High tension/frustration triggered strategy downgrade",
    "PSYCHE_LOW_ENERGY_DOWNGRADE": "Low energy triggered strategy simplification",
    "PSYCHE_HIGH_FOCUS_BOOST": "High focus boosted confidence for complex strategy",
    "STRATEGY_RULE_INSTANT": "Deterministic short-path classification",
    "STRATEGY_RULE_CONTEXTUAL": "History/memory/reference classification",
    "STRATEGY_RULE_RESEARCH": "Web/research intent classification",
    "STRATEGY_RULE_AGENTIC_GOALS": "Active goals → agentic",
    "STRATEGY_RULE_AGENTIC_KEYWORD": "Multi-step / analysis keyword",
    "STRATEGY_RULE_AGENTIC_COMPLEXITY": "Structural multi-step complexity",
    "STRATEGY_RULE_RESEARCH_KEYWORD": "Explicit search/current-info wording",
    "STRATEGY_RULE_RESEARCH_URL": "URL present → research",
    "RESEARCH_NEEDED": "External knowledge required",
    "TIME_SENSITIVE_QUERY": "Query needs fresh/time-bound information (web)",
    "SPORTS_RESULT_QUERY": "Sports match result / standings — requires web",
    "EXPLICIT_CHECK_REQUEST": "User explicitly asked to verify/check (sprawdź/zbadaj)",
}


@dataclass
class MemoryRoutingSummary:
    """Pre-routing summary of memory state."""

    has_relevant_memory: bool
    top_relevant_facts_summary: str = ""
    top_relevant_episodes_summary: str = ""
    has_user_preference_match: bool = False
    has_task_continuation_signal: bool = False
    has_conflict_signal: bool = False
    similarity_aggregate: float | None = None
    lookup_attempted: bool = True
    lookup_succeeded: bool = True
    lookup_error: str = ""
    retrieval_latency_ms: float = 0.0


@dataclass
class PsycheRoutingSummary:
    """Pre-routing summary of psyche state."""

    sentiment: float | None = None
    energy: float = 0.5
    focus: float = 0.5
    tension_signal: float = 0.0
    urgency_signal: float = 0.0
    frustration_signal: float = 0.0
    snapshot_attempted: bool = True
    snapshot_succeeded: bool = True
    snapshot_error: str = ""


WebDecision = Literal["required", "optional", "off"]


@dataclass
class StrategySelection:
    """Pre-routing strategy classification output."""

    selected_strategy: StrategyType
    reason_codes: list[str] = field(default_factory=list)
    short_explanation: str = ""
    memory_summary_used: bool = False
    psyche_summary_used: bool = False
    research_needed: bool = False
    planner_recommended: bool = False
    agentic_recommended: bool = False
    confidence: float | None = None
    degraded: bool = False
    timing: dict[str, float] = field(default_factory=dict)
    trace_payload: dict[str, Any] = field(default_factory=dict)
    web_decision: WebDecision = "off"
    web_decision_reason: str = "not_evaluated"
    selector_output: dict[str, Any] = field(default_factory=dict)


_ZERO_CONFIDENCE_BIAS: dict[str, float] = {
    "instant": 0.0,
    "contextual": 0.0,
    "research": 0.0,
    "agentic": 0.0,
}

_MIN_SAMPLES_FOR_RULES = 3
_INSTANT_SUCCESS_FAIL_THRESHOLD = 0.42
_INSTANT_SUCCESS_RECOVER_THRESHOLD = 0.78
_AGENTIC_SUCCESS_BOOST_THRESHOLD = 0.72
_AGENTIC_SUCCESS_WEAK_THRESHOLD = 0.45
_RESEARCH_LATENCY_RATIO_VS_CONTEXTUAL = 1.45
_BIAS_CLAMP = 0.12
_INSTANT_PENALTY = -0.06
_INSTANT_REWARD = 0.03
_AGENTIC_REWARD = 0.05
_AGENTIC_PENALTY = -0.04
_RESEARCH_LATENCY_PENALTY = -0.05
_CONTEXTUAL_LATENCY_REWARD = 0.04


def _apply_confidence_bias(
    strategy: str, base: float, bias_map: dict[str, float]
) -> float:
    delta = float(bias_map.get(strategy, 0.0))
    return round(max(0.35, min(0.97, float(base) + delta)), 3)


def compute_strategy_bias_from_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    """Deterministic confidence deltas from aggregated experience metrics (pure)."""
    bias: dict[str, float] = {
        "instant": 0.0,
        "contextual": 0.0,
        "research": 0.0,
        "agentic": 0.0,
    }

    inst = metrics.get("instant") or {}
    if int(inst.get("sample_count", 0)) >= _MIN_SAMPLES_FOR_RULES:
        sr = float(inst.get("success_rate", 1.0))
        if sr < _INSTANT_SUCCESS_FAIL_THRESHOLD:
            bias["instant"] += _INSTANT_PENALTY
        elif sr > _INSTANT_SUCCESS_RECOVER_THRESHOLD:
            bias["instant"] += _INSTANT_REWARD

    ag = metrics.get("agentic") or {}
    if int(ag.get("sample_count", 0)) >= _MIN_SAMPLES_FOR_RULES:
        sr_ag = float(ag.get("success_rate", 0.0))
        if sr_ag >= _AGENTIC_SUCCESS_BOOST_THRESHOLD:
            bias["agentic"] += _AGENTIC_REWARD
        elif sr_ag < _AGENTIC_SUCCESS_WEAK_THRESHOLD:
            bias["agentic"] += _AGENTIC_PENALTY

    res = metrics.get("research") or {}
    ctx = metrics.get("contextual") or {}
    if (
        int(res.get("sample_count", 0)) >= _MIN_SAMPLES_FOR_RULES
        and int(ctx.get("sample_count", 0)) >= _MIN_SAMPLES_FOR_RULES
    ):
        rl = float(res.get("avg_latency_ms", 0.0))
        cl = float(ctx.get("avg_latency_ms", 0.0))
        if cl > 0.0 and rl >= cl * _RESEARCH_LATENCY_RATIO_VS_CONTEXTUAL:
            bias["research"] += _RESEARCH_LATENCY_PENALTY
            bias["contextual"] += _CONTEXTUAL_LATENCY_REWARD

    for k in bias:
        bias[k] = round(max(-_BIAS_CLAMP, min(_BIAS_CLAMP, bias[k])), 4)

    return dict(bias)


def adjust_strategy_bias(
    metrics: dict[str, Any], user_id: str | None = None
) -> dict[str, float]:
    """Compute bias from metrics; optionally persist per user in SQLite."""
    computed = compute_strategy_bias_from_metrics(metrics)
    uid = (user_id or "").strip()
    if uid:
        from aihub.db import save_strategy_decision_bias

        save_strategy_decision_bias(uid, computed, metrics_snapshot=metrics)
    return computed


def persist_user_strategy_bias_from_metrics(
    user_id: str, metrics: dict[str, Any]
) -> dict[str, float]:
    """Compute bias from experience metrics and upsert strategy_decision_bias row."""
    return adjust_strategy_bias(metrics, user_id=user_id)


def get_strategy_confidence_bias_for_user(user_id: str) -> dict[str, float]:
    from aihub.db import get_strategy_decision_bias

    return get_strategy_decision_bias(user_id)


def get_strategy_confidence_bias() -> dict[str, float]:
    """Deprecated alias: returns zero bias (no global state). Prefer get_strategy_confidence_bias_for_user."""
    return dict(_ZERO_CONFIDENCE_BIAS)


def reset_strategy_confidence_bias() -> None:
    """Remove all persisted per-user strategy bias rows (tests)."""
    from aihub.db import reset_all_strategy_decision_bias

    reset_all_strategy_decision_bias()


def _strip_diacritics(s: str) -> str:
    nk = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nk if not unicodedata.combining(c))


def _history_turn_count(history: Any) -> int:
    if not history:
        return 0
    n = 0
    for m in history:
        if hasattr(m, "role"):
            role = getattr(m, "role", None)
            content = getattr(m, "content", "") or ""
        elif isinstance(m, dict):
            role = m.get("role")
            content = m.get("content") or ""
        else:
            continue
        if role in ("user", "assistant") and str(content).strip():
            n += 1
    return n


def _normalize_requires_for_strategy(d: dict[str, Any]) -> None:
    s = d["strategy"]
    d["requires_memory"] = s in ("contextual", "agentic")
    d["requires_research"] = s == "research"
    d["requires_planning"] = s == "agentic"


def _keyword_in_text(keyword: str, text_lower: str, text_ascii: str) -> bool:
    """Match a research/time trigger without substring false positives (e.g. ``now`` in ``nowego``)."""
    kl = keyword.lower()
    ka = _strip_diacritics(kl)
    # Short pure-ASCII tokens need a word boundary — otherwise Polish morphology trips them.
    if len(ka) <= 4 and ka.isascii():
        import re

        pat = re.compile(rf"(?<![\w/]){re.escape(ka)}(?![\w/])", re.IGNORECASE)
        return bool(pat.search(text_ascii))
    return kl in text_lower or ka in text_ascii


def _text_has_marker(text_lower: str, text_ascii: str, markers: tuple[str, ...]) -> bool:
    return any(_keyword_in_text(m, text_lower, text_ascii) for m in markers)


def research_trigger_reason_codes(user_text: str) -> list[str]:
    """Stable reason codes for time-sensitive / sports / explicit-check research routing."""
    t = (user_text or "").strip()
    if not t:
        return []
    lower = t.lower()
    ascii_l = _strip_diacritics(lower)
    codes: list[str] = []
    if _text_has_marker(lower, ascii_l, ("sprawdź", "sprawdz", "zbadaj")):
        codes.append("EXPLICIT_CHECK_REQUEST")
    if _text_has_marker(lower, ascii_l, _TIME_SENSITIVE_MARKERS):
        codes.append("TIME_SENSITIVE_QUERY")
    if _text_has_marker(lower, ascii_l, _SPORTS_RESULT_MARKERS):
        codes.append("SPORTS_RESULT_QUERY")
    if _text_has_marker(lower, ascii_l, ("news", "newsy")) and (
        _text_has_marker(lower, ascii_l, ("najnowsze", "najświeższe", "najswiezsze", "latest"))
        or "openai" in ascii_l
    ):
        if "TIME_SENSITIVE_QUERY" not in codes:
            codes.append("TIME_SENSITIVE_QUERY")
    return codes


def listing_copy_no_web_intent(user_text: str) -> bool:
    """Treść typu opis ogłoszeniowy / Vinted / tagi — bez URL; nie wymuszaj web_decision=required."""
    t = (user_text or "").strip().lower()
    if not t or "://" in t:
        return False
    asc = _strip_diacritics(t)
    phrases = (
        "vinted",
        "olx",
        "opis sprzedaży",
        "opis sprzedazy",
        "opis na vinted",
        "opis na olx",
        "opis ogłoszenia",
        "opis ogloszenia",
        "opis przedmiotu",
        "opis mieszkania",
        "opis nieruchomości",
        "opis nieruchomosci",
        "opis produktu",
        "opis oferty",
        "opisz ten produkt",
        "opisz produkt",
        "opisz mieszkanie",
        "słowa kluczowe",
        "slowa kluczowe",
        "tagi pod",
        "tagi do ogłoszenia",
        "tagi do ogloszenia",
        "sprzedażowo",
        "sprzedazowo",
        "copywriting",
        "sales copy",
        "ogłoszenie na",
        "ogloszenie na",
        "bardziej sprzedażowo",
        "bardziej sprzedazowo",
        "napisz lepiej",
        "ulepsz opis",
        "zrób opis",
        "zrob opis",
        "napisz opis",
        "ogłoszenie sprzedażowe",
        "ogloszenie sprzedazowe",
    )
    for p in phrases:
        pl = p.lower()
        if pl in t or _strip_diacritics(pl) in asc:
            return True
    if "allegro" in t and "lokalnie" in t:
        return True
    return False


def short_followup_no_web_intent(user_text: str, history: list[Any] | None) -> bool:
    """Krótki follow-up/edycja bez URL i bez intencji research → trzymaj lokalny chat path."""
    t = (user_text or "").strip().lower()
    if not t or "://" in t:
        return False
    asc = _strip_diacritics(t)
    words = [w for w in asc.split() if w]
    n_words = len(words)

    # Krótkie komendy edycyjne, które powinny kontynuować poprzedni kontekst.
    edit_markers = (
        "popraw",
        "krocej",
        "krócej",
        "krcej",
        "krotcej",
        "dodaj",
        "dodaj więcej",
        "dodaj wiecej",
        "więcej słów kluczowych",
        "wiecej slow kluczowych",
        "rozwin",
        "przeredaguj",
        "zmien",
        "dopracuj",
    )
    # Proste kontaktowe/emo turki, które nie wymagają web.
    simple_chat_markers = ("halo", "hallo", "hej", "siema", "kurwo")
    has_edit = any(m in asc for m in edit_markers)
    has_simple = any(m in asc for m in simple_chat_markers)

    # Nie wymuszaj local-path, gdy user jawnie prosi o web/research.
    research_tokens = (
        "znajdz",
        "wyszukaj",
        "aktualne",
        "dzis",
        "dziś",
        "dzisiaj",
        "obecnie",
        "na dzis",
        "na dziś",
        "internet",
        "online",
        "w sieci",
        "sprawdz",
        "zbadaj",
        "wczoraj",
        "jutro",
        "teraz",
        "aktualnie",
        "ostatnio",
        "najnowsze",
        "najswiezsze",
        "wynik",
        "mecz",
        "news",
        "newsy",
        "kurs",
        "cena",
        "ceny",
        "kosztuje",
        "url",
        "http",
    )
    if any(tok in asc for tok in research_tokens):
        return False

    if has_edit and n_words <= 9:
        return True
    if has_simple and n_words <= 8:
        return True

    # Krótki follow-up po istniejącej rozmowie: domyślnie bez web.
    if n_words <= 6 and _history_turn_count(history) >= 2:
        return True
    return False


AGENTIC_KEYWORD_TOKENS = (
    "zrób",
    "zrob",
    "przeanalizuj",
    "rozpisz",
    "porównaj",
    "porownaj",
    "zaplanuj",
    "wykonaj",
    "wygeneruj plan",
    "wieloetapow",
    "krok po kroku",
    "analiza porównawcza",
    "analiza porownawcza",
)

# Multi-step structural cues — NOT plain "i"/commas (those fire false agentic on chat Q&A).
AGENTIC_MULTISTEP_MARKERS = (
    "krok po kroku",
    "następnie",
    "nastepnie",
    "a potem",
    "potem ",
    "wieloetap",
    "step by step",
    "and then",
    "then ",
)

# Identity / meta system questions must stay non-agentic.
_ASSISTANT_META_ASK_MARKERS = (
    "kim jesteś",
    "kim jestes",
    "kim jesteś",
    "jak działasz",
    "jak dzialasz",
    "jak działacie",
    "jak dzialacie",
    "jakie elementy",
    "jakich elementów",
    "jakich elementow",
    "czego użyłeś",
    "czego uzyles",
    "wykorzystałeś",
    "wykorzystales",
    "przedstaw się",
    "przedstaw sie",
    "kim jesteś i",
    "who are you",
    "how do you work",
    "how you work",
    "what are you",
)


def is_assistant_meta_ask(user_text: str) -> bool:
    """True for short identity / 'how do you work' / which modules questions."""
    text = (user_text or "").strip()
    if not text:
        return False
    lower = text.lower()
    ascii_l = _strip_diacritics(lower)
    n_words = len([w for w in text.split() if w])
    if n_words > 40:
        return False
    if any(m in lower or _strip_diacritics(m) in ascii_l for m in _ASSISTANT_META_ASK_MARKERS):
        return True
    # "powiedz krótko" + self-reference without execute verbs
    short_ask = "powiedz krotko" in ascii_l or "powiedz krótko" in lower or "napisz krotk" in ascii_l
    self_ref = any(
        t in ascii_l
        for t in ("kim jestes", "jak dzial", "systemu", "modulu", "element", "o sobie")
    )
    exec_blocked = any(
        _keyword_in_text(k, lower, ascii_l)
        for k in ("zaplanuj", "wykonaj", "przeanalizuj", "krok po kroku")
    )
    return bool(short_ask and self_ref and not exec_blocked)


_SIMPLE_GREETINGS = frozenset(
    {"elo", "hej", "cześć", "czesc", "siema", "hi", "hello", "hey", "yo"}
)


def is_simple_greeting(user_text: str) -> bool:
    """Single-token or very short social openers — never agentic/planner."""
    text = (user_text or "").strip()
    if not text:
        return False
    lower = text.lower().rstrip("!?., ")
    if lower in _SIMPLE_GREETINGS:
        return True
    words = [w for w in lower.split() if w]
    if len(words) <= 4 and lower.startswith(("cześć", "czesc", "hej", "siema")):
        return True
    if len(words) <= 5 and "co tam" in lower:
        return True
    if len(words) <= 7 and "co tam u ciebie" in lower:
        return True
    return False


RESEARCH_INTENT_TOKENS = (
    "znajdź",
    "znajdz",
    "wyszukaj",
    "aktualne",
    "co teraz",
    "internet",
    # Time-sensitive PL/EN (product contract — must NOT answer from training memory)
    "wczoraj",
    "jutro",
    "dzisiaj",
    "dziś",
    "dzis",
    "teraz",
    "obecnie",
    "aktualnie",
    "ostatnio",
    "najnowsze",
    "najświeższe",
    "najswiezsze",
    "latest",
    "today",
    "yesterday",
    "tomorrow",
    "currently",
    "co nowego",
    # Sports / results
    "wynik",
    "mecz",
    "score",
    "match",
    "result",
    "tabela",
    "kto wygrał",
    "kto wygral",
    "terminarz",
    "mistrzostw",
    # News / prices
    "news",
    "newsy",
    "kurs",
    "cena",
    "ceny",
    "price",
    "kosztuje",
    # Availability / status checks
    "czy działa",
    "czy dziala",
)

# Sport + result context (used for SPORTS_RESULT_QUERY reason code only).
_SPORTS_RESULT_MARKERS = (
    "wynik",
    "mecz",
    "score",
    "match",
    "result",
    "tabela",
    "kto wygrał",
    "kto wygral",
    "terminarz",
    "mistrzostw",
    "mundial",
    "liga",
    "puchar",
)

_TIME_SENSITIVE_MARKERS = (
    "wczoraj",
    "jutro",
    "dzisiaj",
    "dziś",
    "dzis",
    "teraz",
    "obecnie",
    "aktualnie",
    "ostatnio",
    "najnowsze",
    "najświeższe",
    "najswiezsze",
    "latest",
    "today",
    "yesterday",
    "tomorrow",
    "currently",
    "co nowego",
    "dzis",
    "dziś",
    "na dziś",
    "na dzis",
)

RESEARCH_INTENT_ALIASES = (
    "poszukaj",
    "przeszukaj",
    "w sieci",
    "online",
    "wyguglaj",
    "googluj",
)


class StrategySelector:
    """Deterministic strategy classification from user text + bounded context."""

    _AGENTIC_KEYWORDS_PL = AGENTIC_KEYWORD_TOKENS
    # Explicit web / current-info intent (product contract).
    _RESEARCH_INTENT_TOKENS = RESEARCH_INTENT_TOKENS
    _RESEARCH_INTENT_ALIASES = RESEARCH_INTENT_ALIASES
    # These disqualify the instant path (verification / probe → web-capable research).
    _INSTANT_BLOCKLIST_TOKENS = (
        "sprawdź",
        "sprawdz",
        "zbadaj",
    )
    _CONTEXT_KEYWORDS_PL = (
        "wcześniej",
        "wczesniej",
        "mówiłem",
        "mowilem",
        "powiedziałem",
        "powiedzialem",
        "omawialiśmy",
        "omawialismy",
        "tamten",
        "tamta",
        "previous",
        "earlier",
        "you said",
        "ostatnio",
        "na początku",
        "na poczatku",
    )

    def select_strategy(
        self,
        user_input: str,
        context: dict[str, Any],
        confidence_bias: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        text = (user_input or "").strip()
        lower = text.lower()
        ascii_l = _strip_diacritics(lower)
        words = text.split()
        n_words = len(words)

        ag_count = int(context.get("active_goals_count") or 0)
        max_urg = float(context.get("goal_max_urgency") or 0.0)
        hist = int(context.get("history_turns") or 0)
        mem_rel = bool(context.get("memory_has_relevant"))
        mem_task = bool(context.get("memory_task_continuation"))
        has_url = bool(context.get("has_url"))

        bias_map = (
            confidence_bias if confidence_bias is not None else _ZERO_CONFIDENCE_BIAS
        )

        def _has_kw(keywords: tuple[str, ...]) -> bool:
            return any(_keyword_in_text(kw, lower, ascii_l) for kw in keywords)

        def _out(
            strategy: StrategyType,
            confidence: float,
            requires_memory: bool,
            requires_research: bool,
            requires_planning: bool,
            reason: str,
        ) -> dict[str, Any]:
            return {
                "strategy": strategy,
                "confidence": _apply_confidence_bias(strategy, confidence, bias_map),
                "requires_memory": requires_memory,
                "requires_research": requires_research,
                "requires_planning": requires_planning,
                "reason": reason,
            }

        # ── 0) Assistant identity / meta-system Q&A → never agentic ─────
        if is_assistant_meta_ask(text):
            return _out(
                "contextual" if hist >= 1 or mem_rel else "instant",
                0.88,
                hist >= 1 or mem_rel,
                False,
                False,
                "Assistant identity/meta ask — contextual/direct, no planner",
            )

        # ── 1) Agentic (highest priority) ─────────────────────────────
        if ag_count > 0 and max_urg >= 0.25:
            return _out(
                "agentic",
                0.86,
                True,
                False,
                True,
                f"Active goals present (count={ag_count}, max_urgency={max_urg:.2f})",
            )

        multi_step = _text_has_marker(lower, ascii_l, AGENTIC_MULTISTEP_MARKERS)
        complex_task = (
            n_words >= 16
            and multi_step
            and (_has_kw(self._AGENTIC_KEYWORDS_PL) or "zaplanuj" in ascii_l)
        )
        if _has_kw(self._AGENTIC_KEYWORDS_PL) or complex_task:
            return _out(
                "agentic",
                0.81,
                True,
                False,
                True,
                "Multi-step or explicit analysis/planning wording detected",
            )

        # ── 2) Research (web / verification; never instant) ─────────────
        if has_url:
            return _out(
                "research",
                0.88,
                mem_rel,
                True,
                False,
                "URL in message → external fetch/research path",
            )

        research_intent = _has_kw(self._RESEARCH_INTENT_TOKENS) or _has_kw(
            self._RESEARCH_INTENT_ALIASES
        )
        instant_blocked = _has_kw(self._INSTANT_BLOCKLIST_TOKENS)
        if research_intent or instant_blocked:
            return _out(
                "research",
                0.84,
                mem_rel,
                True,
                False,
                "Research intent keywords, verification/probe wording, or web aliases",
            )

        # ── 3) Contextual ────────────────────────────────────────────
        if _has_kw(self._CONTEXT_KEYWORDS_PL) or hist >= 2 or mem_rel or mem_task:
            return _out(
                "contextual",
                0.78,
                True,
                False,
                False,
                "Dialogue history, memory hit, or explicit back-reference",
            )

        # ── 4) Instant ───────────────────────────────────────────────
        if n_words <= 15 and hist < 2 and not mem_task:
            return _out(
                "instant",
                0.82,
                False,
                False,
                False,
                "Short, self-contained turn without web/history dependency",
            )

        return _out(
            "contextual",
            0.74,
            True,
            False,
            False,
            "Default contextual path for longer ambiguous turns",
        )

    @staticmethod
    def adjust_strategy_bias(
        metrics: dict[str, Any], user_id: str | None = None
    ) -> dict[str, float]:
        return adjust_strategy_bias(metrics, user_id=user_id)


def _bounded_memory_retrieval(
    user_id: str,
    query_text: str,
    limit: int = 5,
) -> MemoryRoutingSummary:
    start_time = time.time()

    try:
        from aihub.memory_engine import retrieve_context

        context = retrieve_context(user_id, query_text, limit=limit)
        elapsed_ms = (time.time() - start_time) * 1000

        total_facts = len(context.get("semantic", []))
        total_episodes = len(context.get("episodic", []))

        facts_text = " | ".join(
            f.get("content", "")[:80]
            for f in context.get("semantic", [])[:2]
            if isinstance(f, dict)
        )
        episodes_text = " | ".join(
            f.get("content", "")[:80]
            for f in context.get("episodic", [])[:2]
            if isinstance(f, dict)
        )

        has_memory = total_facts > 0 or total_episodes > 0

        summary = MemoryRoutingSummary(
            has_relevant_memory=has_memory,
            top_relevant_facts_summary=facts_text[:200],
            top_relevant_episodes_summary=episodes_text[:200],
            has_user_preference_match=bool(
                context.get("semantic", [])
                and any(
                    "prefer" in str(f.get("content", "")).lower()
                    for f in context.get("semantic", [])[:3]
                )
            ),
            has_task_continuation_signal=bool(
                context.get("episodic", [])
                and any(
                    "task" in str(f.get("content", "")).lower()
                    for f in context.get("episodic", [])[:3]
                )
            ),
            lookup_succeeded=True,
            retrieval_latency_ms=elapsed_ms,
        )

        return summary

    except Exception as e:
        logger.warning(
            "Memory retrieval failed during strategy selection: user=%s err=%s",
            user_id,
            e,
        )
        return MemoryRoutingSummary(
            has_relevant_memory=False,
            lookup_attempted=True,
            lookup_succeeded=False,
            lookup_error=str(e),
            retrieval_latency_ms=(time.time() - start_time) * 1000,
        )


def _bounded_psyche_snapshot(user_id: str) -> PsycheRoutingSummary:
    try:
        psyche_state = get_psyche_core().ensure_user(user_id)

        energy = float(psyche_state.get("energy", 0.5))
        focus = float(psyche_state.get("focus", 0.5))
        mood = float(psyche_state.get("mood", 0.5))

        tension = 1.0 - focus if focus is not None else 0.0
        frustration = max(0.0, 0.5 - mood) if mood is not None else 0.0
        urgency = max(0.0, min(1.0, tension - energy))

        summary = PsycheRoutingSummary(
            sentiment=mood,
            energy=energy,
            focus=focus,
            tension_signal=tension,
            urgency_signal=urgency,
            frustration_signal=frustration,
            snapshot_succeeded=True,
        )

        return summary

    except Exception as e:
        logger.warning(
            "Psyche snapshot failed during strategy selection: user=%s err=%s",
            user_id,
            e,
        )
        return PsycheRoutingSummary(
            snapshot_attempted=True,
            snapshot_succeeded=False,
            snapshot_error=str(e),
        )


def _apply_psyche_modulation_select_dict(
    d: dict[str, Any],
    psyche_summary: PsycheRoutingSummary,
    memory_summary: MemoryRoutingSummary,
) -> None:
    if not psyche_summary.snapshot_succeeded:
        return

    if psyche_summary.tension_signal > 0.55 or psyche_summary.frustration_signal > 0.35:
        if d["strategy"] == "agentic":
            d["strategy"] = "contextual"
            d["reason"] += " [psyche: tension->contextual]"
            d["confidence"] = max(0.5, float(d["confidence"]) * 0.85)
        elif d["strategy"] == "research":
            d["confidence"] = max(0.5, float(d["confidence"]) * 0.88)
        _normalize_requires_for_strategy(d)

    if psyche_summary.energy < 0.35:
        if d["strategy"] == "agentic":
            d["strategy"] = "contextual"
            d["reason"] += " [psyche: low_energy->contextual]"
            d["confidence"] = max(0.45, float(d["confidence"]) * 0.85)
            _normalize_requires_for_strategy(d)
        elif d["strategy"] == "research":
            d["reason"] += " [psyche: low_energy->lower_confidence]"
            d["confidence"] = max(0.5, float(d["confidence"]) * 0.9)

    if psyche_summary.focus > 0.70 and d["strategy"] in ("research", "agentic"):
        d["confidence"] = min(0.97, float(d["confidence"]) * 1.08)


def _rule_code_from_dict(
    d: dict[str, Any], context: dict[str, Any], user_text: str
) -> str:
    s = d["strategy"]
    if s == "instant":
        return "STRATEGY_RULE_INSTANT"
    if s == "contextual":
        return "STRATEGY_RULE_CONTEXTUAL"
    if s == "research":
        if context.get("has_url"):
            return "STRATEGY_RULE_RESEARCH_URL"
        return "STRATEGY_RULE_RESEARCH_KEYWORD"
    if context.get("active_goals_count", 0) > 0:
        return "STRATEGY_RULE_AGENTIC_GOALS"
    lower = (user_text or "").lower()
    asc = _strip_diacritics(lower)
    for kw in AGENTIC_KEYWORD_TOKENS:
        kl = kw.lower()
        if kl in lower or _strip_diacritics(kl) in asc:
            return "STRATEGY_RULE_AGENTIC_KEYWORD"
    return "STRATEGY_RULE_AGENTIC_COMPLEXITY"


def select_strategy(
    user_id: str,
    user_text: str,
    mode: str,
    active_goals_summary: dict[str, Any] | None = None,
    history: list[Any] | None = None,
    pragmatics: Any | None = None,
) -> StrategySelection:
    """
    Bounded-cost classification: memory + psyche snapshots, deterministic rules.
    """
    _ = mode
    start_time = time.time()
    selection = StrategySelection(
        selected_strategy="instant",
        reason_codes=[],
        degraded=False,
    )

    memory_summary = _bounded_memory_retrieval(user_id, user_text or "", limit=5)
    selection.memory_summary_used = memory_summary.lookup_succeeded
    if not memory_summary.lookup_succeeded:
        selection.reason_codes.append("MEMORY_UNAVAILABLE_FALLBACK")
        selection.degraded = True
    selection.timing["memory_retrieval_ms"] = memory_summary.retrieval_latency_ms

    psyche_summary = _bounded_psyche_snapshot(user_id)
    selection.psyche_summary_used = psyche_summary.snapshot_succeeded
    if not psyche_summary.snapshot_succeeded:
        selection.reason_codes.append("PSYCHE_UNAVAILABLE_FALLBACK")
        selection.degraded = True

    active_goals_count = 0
    goal_urgency_max = 0.0
    if active_goals_summary and isinstance(active_goals_summary, dict):
        active_goals_count = int(active_goals_summary.get("active_count", 0))
        goal_urgency_max = float(active_goals_summary.get("max_urgency", 0.0))

    if active_goals_count > 0:
        selection.reason_codes.append("ACTIVE_GOAL_PRESENT")
        if goal_urgency_max >= 0.8:
            selection.reason_codes.append("GOAL_HIGH_URGENCY")

    hist_turns = _history_turn_count(history)
    ctx: dict[str, Any] = {
        "memory_has_relevant": memory_summary.has_relevant_memory
        or memory_summary.has_user_preference_match,
        "memory_task_continuation": memory_summary.has_task_continuation_signal,
        "history_turns": hist_turns,
        "active_goals_count": active_goals_count,
        "goal_max_urgency": goal_urgency_max,
        "has_url": "://" in (user_text or ""),
    }

    uid_clean = (user_id or "").strip()
    # Bias skip for empty user only — audit mode is handled by TurnApplicationService
    # via execution_mode, never via user_id prefix.
    if not uid_clean:
        bias_map = dict(_ZERO_CONFIDENCE_BIAS)
        bias_load_source = "default"
    else:
        from aihub.db import (
            get_strategy_decision_bias,
            user_has_persisted_strategy_bias,
        )

        bias_map = get_strategy_decision_bias(uid_clean)
        bias_load_source = (
            "persisted" if user_has_persisted_strategy_bias(uid_clean) else "default"
        )

    raw = StrategySelector().select_strategy(
        user_text or "", ctx, confidence_bias=bias_map
    )
    _apply_psyche_modulation_select_dict(raw, psyche_summary, memory_summary)

    ut = user_text or ""
    listing_local = listing_copy_no_web_intent(ut) and "://" not in ut
    followup_local = short_followup_no_web_intent(ut, history)
    lower_ut = ut.lower()
    ascii_ut = _strip_diacritics(lower_ut)
    explicit_freshness = any(
        tok in lower_ut or _strip_diacritics(tok) in ascii_ut
        for tok in (
            "dzis",
            "dziś",
            "dzisiaj",
            "obecnie",
            "aktualne",
            "na dziś",
            "na dzis",
            "ceny",
        )
    )
    explicit_research_intent = any(
        tok in lower_ut or _strip_diacritics(tok) in ascii_ut
        for tok in (
            *RESEARCH_INTENT_TOKENS,
            *RESEARCH_INTENT_ALIASES,
            "sprawdź",
            "sprawdz",
            "zbadaj",
        )
    )
    if raw.get("strategy") == "agentic" and (
        "://" in ut or explicit_freshness or explicit_research_intent
    ):
        raw = dict(raw)
        raw["strategy"] = "research"
        _normalize_requires_for_strategy(raw)
        raw["reason"] = (
            str(raw.get("reason") or "")
            + " [explicit_freshness_or_research_intent: research beats active-goal escalation]"
        )
    if listing_local:
        if raw.get("strategy") in ("research", "agentic"):
            raw = dict(raw)
            raw["strategy"] = (
                "contextual" if _history_turn_count(history) >= 2 else "instant"
            )
            _normalize_requires_for_strategy(raw)
            raw["reason"] = (
                str(raw.get("reason") or "")
                + " [listing_copy: treść sprzedażowa/ogłoszeniowa — bez wymogu web]"
            )
    if followup_local:
        raw = dict(raw)
        if raw.get("strategy") in ("research", "agentic"):
            raw["strategy"] = (
                "contextual" if _history_turn_count(history) >= 2 else "instant"
            )
            _normalize_requires_for_strategy(raw)
        raw["reason"] = (
            str(raw.get("reason") or "")
            + " [short_followup: krótka kontynuacja/edycja — lokalnie bez web]"
        )

    if selection.degraded:
        raw["confidence"] = round(max(0.4, float(raw["confidence"]) * 0.92), 3)

    rule_code = _rule_code_from_dict(raw, ctx, user_text or "")
    selection.reason_codes.append(rule_code)

    if raw["strategy"] == "research":
        selection.reason_codes.append("RESEARCH_NEEDED")
        for code in research_trigger_reason_codes(user_text or ""):
            if code not in selection.reason_codes:
                selection.reason_codes.append(code)

    if "[psyche: tension->" in raw["reason"]:
        selection.reason_codes.append("PSYCHE_TENSION_DOWNGRADE")
    if "[psyche: low_energy->" in raw["reason"]:
        selection.reason_codes.append("PSYCHE_LOW_ENERGY_DOWNGRADE")

    if (
        psyche_summary.snapshot_succeeded
        and psyche_summary.focus > 0.70
        and raw["strategy"] in ("research", "agentic")
    ):
        selection.reason_codes.append("PSYCHE_HIGH_FOCUS_BOOST")

    selection.selected_strategy = raw["strategy"]  # type: ignore[assignment]
    selection.confidence = raw["confidence"]
    selection.short_explanation = raw["reason"]
    selection.research_needed = bool(raw["requires_research"])
    selection.planner_recommended = bool(raw["requires_planning"])
    selection.agentic_recommended = raw["strategy"] == "agentic"
    selection.selector_output = dict(raw)

    _text_for_url = user_text or ""
    if listing_local or followup_local:
        selection.web_decision = "off"
        selection.web_decision_reason = (
            "listing_copy_local_followup_no_web"
            if listing_local
            else "short_followup_no_web"
        )
    elif "://" in _text_for_url:
        selection.web_decision = "required"
        selection.web_decision_reason = "explicit_url_in_query"
    elif _LOCAL_INFRA_NO_WEB.search(_text_for_url):
        selection.web_decision = "off"
        selection.web_decision_reason = "local_infra_or_fact_no_web"
        selection.research_needed = False
    elif selection.research_needed:
        selection.web_decision = "required"
        selection.web_decision_reason = "research_keywords_match"
    elif selection.selected_strategy == "agentic":
        selection.web_decision = "optional"
        selection.web_decision_reason = "agentic_may_need_web"
    else:
        selection.web_decision = "off"
        selection.web_decision_reason = "not_required"

    selection.trace_payload = {
        "selected_strategy": selection.selected_strategy,
        "reason_codes": list(selection.reason_codes),
        "short_explanation": selection.short_explanation,
        "confidence": selection.confidence,
        "degraded": selection.degraded,
        "memory_used": selection.memory_summary_used,
        "psyche_used": selection.psyche_summary_used,
        "research_needed": selection.research_needed,
        "planner_recommended": selection.planner_recommended,
        "agentic_recommended": selection.agentic_recommended,
        "web_decision": selection.web_decision,
        "web_decision_reason": selection.web_decision_reason,
        "timing": selection.timing,
        "selector_output": dict(raw),
        "memory_summary": {
            "has_relevant_memory": memory_summary.has_relevant_memory,
            "has_user_preference": memory_summary.has_user_preference_match,
            "has_task_continuation": memory_summary.has_task_continuation_signal,
        },
        "psyche_summary": {
            "sentiment": psyche_summary.sentiment,
            "energy": psyche_summary.energy,
            "focus": psyche_summary.focus,
            "tension_signal": psyche_summary.tension_signal,
            "urgency_signal": psyche_summary.urgency_signal,
            "frustration_signal": psyche_summary.frustration_signal,
            "psyche_influenced_strategy": any(
                c in selection.reason_codes
                for c in (
                    "PSYCHE_TENSION_DOWNGRADE",
                    "PSYCHE_CONTEXT_IMPORTANT",
                    "PSYCHE_LOW_ENERGY_DOWNGRADE",
                    "PSYCHE_HIGH_FOCUS_BOOST",
                )
            ),
        },
        "strategy_confidence_bias": dict(bias_map),
        "strategy_bias_load_source": bias_load_source,
    }

    if pragmatics is not None:
        try:
            from aihub.turn.pragmatics import apply_pragmatics_to_strategy

            (
                selection.selected_strategy,
                selection.reason_codes,
                selection.web_decision,
                selection.web_decision_reason,
            ) = apply_pragmatics_to_strategy(
                selected_strategy=selection.selected_strategy,
                reason_codes=list(selection.reason_codes),
                web_decision=selection.web_decision,
                web_decision_reason=selection.web_decision_reason,
                pragmatics=pragmatics,
            )
            selection.research_needed = selection.web_decision == "required" or bool(
                getattr(pragmatics, "needs_web", False)
            )
            if getattr(pragmatics, "needs_planner", False):
                selection.planner_recommended = True
            selection.trace_payload["selected_strategy"] = selection.selected_strategy
            selection.trace_payload["reason_codes"] = list(selection.reason_codes)
            selection.trace_payload["web_decision"] = selection.web_decision
            selection.trace_payload["web_decision_reason"] = selection.web_decision_reason
            selection.trace_payload["pragmatics_applied"] = True
        except Exception:
            logger.debug("pragmatics strategy apply failed", exc_info=True)
            selection.reason_codes.append("PRAGMATICS_DEGRADED_FALLBACK")
            selection.degraded = True

    total_time_ms = (time.time() - start_time) * 1000
    selection.timing["total_ms"] = total_time_ms
    selection.trace_payload["timing"] = dict(selection.timing)

    logger.debug(
        "Strategy selection: user=%s strategy=%s confidence=%s degraded=%s latency=%.1fms",
        user_id,
        selection.selected_strategy,
        selection.confidence,
        selection.degraded,
        total_time_ms,
    )

    return selection
