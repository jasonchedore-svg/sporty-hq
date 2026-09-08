from sporty_hq.engine import ScanConfig, score_quotes
from sporty_hq.lessons import apply_lessons, lessons_for_candidate
from sporty_hq.models import Lesson
from sporty_hq.providers import quotes_from_json
from tests.conftest import DEMO_JSON


def test_lessons_surface_on_similar_yankees_scan() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    yanks = next(c for c in cands if "Yankees" in c.selection)
    lesson = Lesson(
        sport="baseball_mlb",
        event_id="prior-nyy",
        event_name="New York Yankees @ Tampa Bay Rays",
        market="ml",
        selection="New York Yankees",
        postmortem="injury_missed",
        lesson="Cole IL not in the model",
        teams=["new", "york", "yankees"],
    )
    hits = lessons_for_candidate([lesson], yanks)
    assert hits
    apply_lessons(cands, [lesson])
    assert yanks.lesson_hits
    assert "injury_missed" in yanks.rationale
    assert "Cole IL" in yanks.rationale


def test_unrelated_sport_lesson_does_not_attach() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    yanks = next(c for c in cands if "Yankees" in c.selection)
    lesson = Lesson(
        sport="icehockey_nhl",
        event_id="tor",
        event_name="Boston Bruins @ Toronto Maple Leafs",
        market="ml",
        selection="Toronto Maple Leafs",
        postmortem="weather_ignored",
        lesson="n/a",
        teams=["toronto", "leafs"],
    )
    assert lessons_for_candidate([lesson], yanks) == []
