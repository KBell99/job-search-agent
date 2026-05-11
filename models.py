from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class WorkType(str, Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class ApplicationStatus(str, Enum):
    PENDING = "pending"
    SKIPPED = "skipped"


@dataclass
class Location:
    city: str
    state: str
    country: str = "US"
    radius_miles: int = 25

    def __str__(self) -> str:
        return f"{self.city}, {self.state}"


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str                          # which job board
    description: str = ""
    posted_at: Optional[datetime] = None
    work_type: Optional[WorkType] = None
    salary: Optional[str] = None
    job_id: Optional[str] = None        # board-specific ID for deduplication

    @property
    def age_hours(self) -> Optional[float]:
        if self.posted_at is None:
            return None
        posted = self.posted_at if self.posted_at.tzinfo else self.posted_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - posted).total_seconds() / 3600


@dataclass
class AnalysisResult:
    job: Job
    match_score: int                     # 0–100
    match_reasons: list[str] = field(default_factory=list)
    gap_reasons: list[str] = field(default_factory=list)
    cover_letter: str = ""


@dataclass
class Application:
    job: Job
    analysis: AnalysisResult
    status: ApplicationStatus = ApplicationStatus.PENDING
    notes: str = ""
    cover_letter_path: Optional[str] = None
