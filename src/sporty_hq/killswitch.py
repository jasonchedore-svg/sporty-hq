"""Owner kill switch: auto-pause the desk. Resume only with a written review.

Trip A — acted on flagged research before validation gates (historical
backtest + ~2–3 week paper confirm) are both clear.
Trip B — paper avg CLV is non-positive after n ≥ 100 (FAILING health).
Neither trip is weakened by a passing backtest.

There is no env / --force override. Resume copies a review artifact into data/reviews/.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sporty_hq.reports import ModelHealth, paper_clv_summary
from sporty_hq.storage import Store, utcnow

STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"

TRIP_ACTED_BEFORE_GATE = "acted_before_gate"
TRIP_PAPER_CLV_NEGATIVE = "paper_clv_negative"
TRIP_CODES = frozenset({TRIP_ACTED_BEFORE_GATE, TRIP_PAPER_CLV_NEGATIVE})

REVIEW_MIN_CHARS = 200
REVIEW_REQUIRED_HEADERS = (
    "TRIP:",
    "ROOT CAUSE:",
    "CORRECTIVE ACTION:",
    "OWNER SIGN-OFF:",
)


class KillSwitchPaused(Exception):
    def __init__(self, status: "DeskStatus") -> None:
        self.status = status
        super().__init__(status.pause_message())


class ReviewArtifactError(ValueError):
    """Resume rejected: review file missing, too thin, or missing required sections."""


@dataclass
class DeskStatus:
    status: str = STATUS_RUNNING
    trip: str | None = None
    tripped_at: str | None = None
    detail: str = ""
    review_path: str | None = None
    review_recorded_at: str | None = None

    @property
    def paused(self) -> bool:
        return self.status == STATUS_PAUSED

    def pause_message(self) -> str:
        trip = self.trip or "unknown"
        return (
            f"KILL SWITCH PAUSED ({trip}). Whole Sporty desk is stopped. "
            f"{self.detail} Resume requires a written review artifact: "
            "sporty resume --review <path>. No silent override."
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trip": self.trip,
            "tripped_at": self.tripped_at,
            "detail": self.detail,
            "review_path": self.review_path,
            "review_recorded_at": self.review_recorded_at,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> DeskStatus:
        return cls(
            status=str(data.get("status") or STATUS_RUNNING),
            trip=data.get("trip"),
            tripped_at=data.get("tripped_at"),
            detail=str(data.get("detail") or ""),
            review_path=data.get("review_path"),
            review_recorded_at=data.get("review_recorded_at"),
        )


def status_path(data_dir: Path) -> Path:
    return Path(data_dir) / "desk_status.json"


def load_status(data_dir: Path) -> DeskStatus:
    path = status_path(data_dir)
    if not path.exists():
        return DeskStatus()
    return DeskStatus.from_json_dict(json.loads(path.read_text(encoding="utf-8")))


def save_status(data_dir: Path, status: DeskStatus) -> None:
    path = status_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status.to_json_dict(), indent=2) + "\n", encoding="utf-8")


def trip(
    data_dir: Path,
    store: Store,
    *,
    code: str,
    detail: str,
) -> DeskStatus:
    """Pause the desk. Idempotent if already paused (keeps the first trip)."""
    if code not in TRIP_CODES:
        raise ValueError(f"Unknown kill-switch trip '{code}'")
    current = load_status(data_dir)
    if current.paused:
        return current
    now = utcnow().isoformat()
    current = DeskStatus(
        status=STATUS_PAUSED,
        trip=code,
        tripped_at=now,
        detail=detail,
    )
    save_status(data_dir, current)
    store.record_desk_event(kind="trip", trip=code, detail=detail, review_path=None)
    return current


def maybe_trip_paper_clv(
    data_dir: Path,
    store: Store,
    *,
    unit: float,
    judge_n: int,
) -> DeskStatus:
    """Trip B when paper CLV health is FAILING (n≥judge_n and avg CLV ≤ 0)."""
    current = load_status(data_dir)
    if current.paused:
        return current
    paper = paper_clv_summary(store.list_bets(), unit, judge_n)
    if paper.health != ModelHealth.FAILING.value:
        return current
    avg = "—" if paper.avg_clv is None else str(paper.avg_clv)
    return trip(
        data_dir,
        store,
        code=TRIP_PAPER_CLV_NEGATIVE,
        detail=(
            f"Paper avg CLV {avg} with n={paper.clv_n} (≥{judge_n}). "
            "Non-positive average CLV after 100+ paper bets — desk paused."
        ),
    )


def trip_acted_before_gate(
    data_dir: Path,
    store: Store,
    *,
    action: str,
    paper_health: str,
    paper_n: int,
    judge_n: int,
) -> DeskStatus:
    """Trip A: flagged research was acted on before the paper CLV gate cleared."""
    return trip(
        data_dir,
        store,
        code=TRIP_ACTED_BEFORE_GATE,
        detail=(
            f"Acted on flagged research ({action}) before validation gates cleared "
            f"(health={paper_health}, n={paper_n}; live needs backtest + 2–3 week paper, "
            f"kill switch B still n≥{judge_n})."
        ),
    )


def require_running(data_dir: Path) -> DeskStatus:
    status = load_status(data_dir)
    if status.paused:
        raise KillSwitchPaused(status)
    return status


def validate_review_artifact(path: Path, *, expected_trip: str) -> str:
    """Reject silent / stub reviews. Returns the file text if it qualifies."""
    if not path.exists() or not path.is_file():
        raise ReviewArtifactError(f"Review artifact not found: {path}")
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if len(stripped) < REVIEW_MIN_CHARS:
        raise ReviewArtifactError(
            f"Review artifact is too short ({len(stripped)} chars; need ≥{REVIEW_MIN_CHARS})."
        )
    upper = stripped.upper()
    missing = [h for h in REVIEW_REQUIRED_HEADERS if h not in upper]
    if missing:
        raise ReviewArtifactError(
            "Review artifact missing required sections: " + ", ".join(missing)
        )
    if expected_trip not in stripped:
        raise ReviewArtifactError(
            f"Review artifact must name the trip code that paused the desk ({expected_trip})."
        )
    return text


def resume(data_dir: Path, store: Store, review_path: Path) -> DeskStatus:
    current = load_status(data_dir)
    if not current.paused:
        raise ReviewArtifactError("Desk is not paused; nothing to resume.")
    trip_code = current.trip or ""
    validate_review_artifact(review_path, expected_trip=trip_code)
    reviews_dir = Path(data_dir) / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = reviews_dir / f"{stamp}-{trip_code}.md"
    shutil.copy2(review_path, dest)
    now = utcnow().isoformat()
    current = DeskStatus(
        status=STATUS_RUNNING,
        trip=None,
        tripped_at=None,
        detail=f"Resumed after review {dest.name} (prior trip {trip_code}).",
        review_path=str(dest),
        review_recorded_at=now,
    )
    save_status(data_dir, current)
    store.record_desk_event(
        kind="resume",
        trip=trip_code,
        detail=current.detail,
        review_path=str(dest),
    )
    return current
