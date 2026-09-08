"""Postmortem lessons: persist after a loss, surface on similar next-slate scans."""

from __future__ import annotations

import re

from sporty_hq.models import Candidate, Lesson
from sporty_hq.sports import family

_STOP = frozenset(
    {
        "at",
        "vs",
        "the",
        "and",
        "over",
        "under",
        "home",
        "away",
        "mlb",
        "nfl",
        "nba",
        "nhl",
        "ncaaf",
    }
)


def team_tokens(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        for tok in re.split(r"[^a-z0-9]+", (part or "").lower()):
            if len(tok) >= 3 and tok not in _STOP:
                tokens.add(tok)
    return tokens


def teams_from_event(event_name: str, selection: str = "") -> list[str]:
    return sorted(team_tokens(event_name, selection))


def lessons_for_candidate(lessons: list[Lesson], cand: Candidate) -> list[Lesson]:
    """Hypothesis: same sport family + overlapping team tokens is 'similar'.

    Market match is a bonus, not a hard filter — an injury miss on ML still
    matters when the same club is on a spread the next slate.
    """
    if not lessons:
        return []
    cand_fam = family(cand.sport)
    cand_toks = team_tokens(cand.event_name, cand.selection, cand.sport)
    hits: list[Lesson] = []
    for lesson in lessons:
        if lesson.sport and family(lesson.sport) != cand_fam:
            continue
        lesson_toks = team_tokens(lesson.event_name, lesson.selection, *lesson.teams)
        if cand_toks & lesson_toks:
            hits.append(lesson)
    return hits


def format_lesson_hit(lesson: Lesson) -> str:
    tag = lesson.postmortem or "other"
    text = (lesson.lesson or "").strip() or "(no free-text)"
    return f"{tag}: {text}"


def apply_lessons(candidates: list[Candidate], lessons: list[Lesson]) -> list[Candidate]:
    """Attach matching postmortems onto scan candidates (mutates rationale)."""
    if not lessons:
        return candidates
    for cand in candidates:
        hits = lessons_for_candidate(lessons, cand)
        if not hits:
            continue
        labels = [format_lesson_hit(h) for h in hits]
        cand.lesson_hits = labels
        cand.rationale = cand.rationale.rstrip() + " Lessons: " + " | ".join(labels)
    return candidates
