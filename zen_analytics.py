"""
Heuristic analytics for the user's latest message (0–100 scales).
Used for the statistics panel shown beneath each agent reply.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# Keywords suggesting reflective / practice-oriented tone (enlightenment-adjacent).
_ZENISH = frozenset(
    """
    awareness insight breath meditate mindfulness compassion emptiness non-duality
    suffering attachment liberation zen dao sutra koan sitting zazen vipassana
    equanimity silence witness presence surrender gratitude forgiveness karma
    consciousness awakening bodhisattva dharma sangha stillness observe letting go
    """.split()
)

_DATA_MARKERS = re.compile(
    r"(?:\d+%|\d+\.\d+|\b\d{4}\b|https?://|\bstud(?:y|ies)\b|\bresearch\b|\bpaper\b|\bstat(?:s|istic)\b)",
    re.I,
)


@dataclass(frozen=True)
class MessageAnalytics:
    enlightenment_threshold: float  # 0–100
    repetition_threshold: float  # higher = more repetitive vs recent user lines
    fixation_on_concepts: float  # higher = narrow lexical fixation / jargon loops
    data_reliance: float  # numbers, citations, “study”, URLs


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]{3,}", text.lower())


def compute_message_analytics(
    text: str,
    recent_user_messages: list[str],
    *,
    max_recent: int = 8,
) -> MessageAnalytics:
    """Score the latest user message; recent_user_messages excludes the current line."""
    t = text.strip()
    if not t:
        return MessageAnalytics(0.0, 0.0, 0.0, 0.0)

    tokens = _tokenize(t)
    if not tokens:
        # Still score data reliance on raw string
        tok_set: set[str] = set()
        diversity = 0.0
    else:
        tok_set = set(tokens)
        diversity = len(tok_set) / max(len(tokens), 1)

    # Enlightenment-ish: keyword hits + slight bonus for thoughtful length (not spam).
    zen_hits = sum(1 for w in tokens if w in _ZENISH)
    enlightenment = min(100.0, zen_hits * 12.0 + min(25.0, len(t) / 16.0))

    # Repetition vs recent user messages (Jaccard-ish on word multiset).
    rep = 0.0
    recent = recent_user_messages[-max_recent:]
    if recent:
        cur = Counter(tokens)
        overlaps: list[float] = []
        for prev in recent:
            pt = _tokenize(prev)
            if not pt or not tokens:
                continue
            pc = Counter(pt)
            inter = sum((cur & pc).values())
            union = sum((cur | pc).values()) or 1
            overlaps.append(inter / union)
        rep = min(100.0, 100.0 * (sum(overlaps) / len(overlaps)))
    else:
        # Self-repetition inside one message
        if len(tokens) >= 6:
            freq = Counter(tokens)
            top_ratio = max(freq.values()) / len(tokens)
            rep = min(100.0, top_ratio * 120.0)

    # Concept fixation: low lexical diversity + repeated stems → higher fixation.
    fixation = min(100.0, (1.0 - diversity) * 85.0 + (rep * 0.25))

    # Data reliance: regex markers + digit density
    data_score = 0.0
    if _DATA_MARKERS.search(t):
        data_score += 40.0
    digits = sum(c.isdigit() for c in t)
    data_score += min(40.0, digits * 4.0)
    if any(k in t.lower() for k in ("study", "research", "paper", "evidence", "data")):
        data_score += 15.0
    data_reliance = min(100.0, data_score)

    return MessageAnalytics(
        enlightenment_threshold=enlightenment,
        repetition_threshold=rep,
        fixation_on_concepts=fixation,
        data_reliance=data_reliance,
    )


def analytics_to_dict(a: MessageAnalytics) -> dict[str, float]:
    return {
        "enlightenment_threshold": round(a.enlightenment_threshold, 1),
        "repetition_threshold": round(a.repetition_threshold, 1),
        "fixation_on_concepts": round(a.fixation_on_concepts, 1),
        "data_reliance": round(a.data_reliance, 1),
    }
