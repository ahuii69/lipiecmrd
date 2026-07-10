#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Psyche v1 compatibility state logic used by :mod:`aihub.psyche_core`.

This module owns the compact mood/energy/focus row used by legacy HTTP routes
and as a lightweight signal source for runtime prompts. Psyche V2 owns richer
behavioral policy, habits, relation dynamics and writeback. Callers should use
``get_psyche_core()`` instead of importing this module directly.
"""

import logging
import time
from typing import Any, Dict, List, Tuple

from aihub.db import append_event, get_psyche, upsert_psyche

logger = logging.getLogger(__name__)

_POS = {
    "dobrze",
    "świetnie",
    "super",
    "ok",
    "spoko",
    "kocham",
    "lubię",
    "fajnie",
    "git",
    "dzięki",
    "mega",
    "zajebiście",
    "rewelacja",
    "top",
    "pięknie",
    "elegancko",
    "siema",
    "miodzio",
}
_NEG = {
    "źle",
    "problem",
    "błąd",
    "nienawidzę",
    "chujowo",
    "słabo",
    "porażka",
    "beznadziejnie",
    "wkurwia",
    "wkurw",
    "kurwa",
    "chuj",
    "debil",
    "oszust",
    "złodziej",
    "spierdol",
}
_INTENSIFIERS = {"bardzo", "mega", "strasznie", "naprawdę", "kurwa", "cholernie"}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _baseline() -> Dict[str, Any]:
    return {
        "user_id": "default",
        "mood": 0.55,
        "energy": 0.70,
        "focus": 0.65,
        "style": "rzeczowy",
        "temperature": 0.65,
        "traits": {
            "agreeableness": 0.55,
            "directness": 0.70,
            "sarcasm": 0.15,
            "swearing": 0.15,
            "patience": 0.45,
            "memory_hunger": 0.80,
        },
        "updated_at": time.time(),
    }


def ensure_user(user_id: str) -> Dict[str, Any]:
    st = get_psyche(user_id)
    if st:
        return st
    base = _baseline()
    base["user_id"] = user_id
    upsert_psyche(
        user_id,
        base["mood"],
        base["energy"],
        base["focus"],
        base["style"],
        base["temperature"],
        base["traits"],
    )
    append_event(user_id, "psyche.init", {"state": base})
    return get_psyche(user_id) or base


def analyze_sentiment(text: str) -> Tuple[float, float, Dict[str, Any]]:
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    t = text.lower()
    words = [w.strip(".,!?;:()[]{}\"'") for w in t.split() if w.strip()]
    pos = sum(1 for w in words if w in _POS)
    neg = sum(1 for w in words if w in _NEG)
    intens = sum(1 for w in words if w in _INTENSIFIERS)

    raw = pos - neg
    # scale sentiment to [-1..1]
    denom = max(3.0, float(pos + neg))
    s = raw / denom
    s = max(-1.0, min(1.0, s))
    # confidence grows with signal + intensity
    conf = _clamp(0.45 + 0.12 * (pos + neg) + 0.05 * intens, 0.0, 0.95)

    meta = {"pos": pos, "neg": neg, "intens": intens, "words": len(words)}
    return s, conf, meta


def evolve(user_id: str, text: str, role: str) -> Dict[str, Any]:
    st = ensure_user(user_id)
    mood = float(st["mood"])
    energy = float(st["energy"])
    focus = float(st["focus"])
    traits = dict(st.get("traits") or {})
    style = st.get("style") or "ziomek"
    temperature = float(st.get("temperature") or 0.65)

    s, conf, meta = analyze_sentiment(text)

    # Natural drift toward neutral mood over time
    dt = max(0.0, time.time() - float(st.get("updated_at") or time.time()))
    mood = mood + (0.55 - mood) * min(0.02, dt / 3600.0 * 0.02)

    # Update mood/energy/focus based on sentiment and role
    # user negativity -> lower mood, maybe lower patience, higher directness
    # assistant negativity -> slight penalty (should be stable)
    role_w = 1.0 if role == "user" else 0.35

    mood = _clamp(mood + role_w * (0.18 * s * conf))
    energy = _clamp(energy + role_w * (0.06 * s * conf) - 0.01 * (meta["words"] / 80.0))
    focus = _clamp(focus + role_w * (0.05 * conf) - 0.02 * (meta["words"] / 200.0))

    # Trait learning (autonauka w locie)
    # If user uses harsh language: increase directness, decrease patience, increase swearing tolerance
    harsh = meta["neg"] > meta["pos"] and meta["neg"] >= 2
    friendly = meta["pos"] > meta["neg"] and meta["pos"] >= 2

    if harsh:
        # Hostile user input makes the assistant more DIRECT/precise, but it must never mirror the
        # aggression. We deliberately do NOT raise swearing/sarcasm here and do NOT force a "ziomek"
        # style (06.07 response-quality fix): psyche/memory cannot copy the tone of a quarrel.
        traits["directness"] = _clamp(
            float(traits.get("directness", 0.7)) + 0.03 * conf
        )
        traits["patience"] = _clamp(float(traits.get("patience", 0.45)) - 0.03 * conf)
        traits["swearing"] = _clamp(float(traits.get("swearing", 0.15)) - 0.02 * conf)
        traits["sarcasm"] = _clamp(float(traits.get("sarcasm", 0.15)) - 0.02 * conf)
    if friendly:
        traits["agreeableness"] = _clamp(
            float(traits.get("agreeableness", 0.55)) + 0.02 * conf
        )
        traits["patience"] = _clamp(float(traits.get("patience", 0.45)) + 0.02 * conf)
        traits["sarcasm"] = _clamp(float(traits.get("sarcasm", 0.35)) - 0.01 * conf)

    # Temperature adapts: low mood -> more deterministic, high mood -> slightly more creative
    temperature = _clamp(0.55 + 0.25 * (mood - 0.5), 0.25, 0.95)

    upsert_psyche(user_id, mood, energy, focus, style, temperature, traits)
    new_state = get_psyche(user_id) or {
        "user_id": user_id,
        "mood": mood,
        "energy": energy,
        "focus": focus,
        "style": style,
        "temperature": temperature,
        "traits": traits,
        "updated_at": time.time(),
    }
    append_event(
        user_id,
        "psyche.update",
        {"delta": {"sentiment": s, "conf": conf, "meta": meta}, "state": new_state},
    )
    return new_state


def reflect(user_id: str, context: List[Dict[str, Any]]) -> Dict[str, Any]:
    st = ensure_user(user_id)
    # Simple meta reflection over last messages: topic-ish keywords via frequency
    freq: Dict[str, int] = {}
    safe_context = context if isinstance(context, list) else []
    for m in safe_context[-20:]:
        if isinstance(m, dict):
            txt = str(m.get("content") or "").lower()
        else:
            txt = str(m or "").lower()
        for w in txt.split():
            w = w.strip(".,!?;:()[]{}\"'")
            if len(w) < 4:
                continue
            freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:12]
    topics = [w for w, c in top if c >= 2][:8]

    mood = float(st["mood"])
    energy = float(st["energy"])
    focus = float(st["focus"])
    mood_desc = (
        "spoko" if mood >= 0.60 else "wkurwiony" if mood <= 0.40 else "neutralny"
    )
    energy_desc = (
        "wysoka" if energy >= 0.65 else "niska" if energy <= 0.40 else "średnia"
    )

    text = f"Stan: {mood_desc}, energia {energy_desc}, fokus {focus:.2f}. "
    if topics:
        text += "Ostatnie motywy: " + ", ".join(topics) + "."

    out = {
        "user_id": user_id,
        "reflection": text,
        "topics": topics,
        "state": st,
        "ts": time.time(),
    }
    append_event(user_id, "psyche.reflect", out)

    # Feed reflection into LearningEngine for meta-learning facts
    try:
        from aihub.learning_engine import learn_from_reflection as _lfr

        _lfr(user_id, out)
    except Exception:  # noqa: BLE001
        logger.debug("learn_from_reflection failed in reflect()", exc_info=True)

    return out
