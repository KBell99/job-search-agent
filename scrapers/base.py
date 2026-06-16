from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Iterator

import requests

from config import Config, RoleConfig
from models import Job, Location

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class BaseScraper(ABC):
    name: str = ""

    def __init__(self, config: Config, role: RoleConfig):
        self.config = config
        self.role = role
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    @abstractmethod
    def scrape(self, location: Location, work_type: WorkType) -> Iterator[Job]:
        """Yield Job objects for a given location and work type."""

    def scrape_all(self) -> list[Job]:
        """Scrape all configured locations × work types, deduplicate by job_id."""
        seen: set[str] = set()
        jobs: list[Job] = []
        limit = self.config.application.max_jobs_per_location
        for work_type in self.config.work_types:
            for loc in self.config.locations:
                loc_count = 0
                try:
                    for job in self.scrape(loc, work_type):
                        if loc_count >= limit:
                            break
                        key = job.job_id or f"{job.company}::{job.title}::{job.url}"
                        if key not in seen:
                            seen.add(key)
                            jobs.append(job)
                            loc_count += 1
                except Exception as e:
                    logger.error("[%s] scrape failed for %s/%s: %s", self.name, loc, work_type.value, e)
        return jobs

    def within_window(self, posted_at: datetime | None) -> bool:
        if posted_at is None:
            return True  # include if unknown — LLM will judge
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        hours_old = (now - posted_at).total_seconds() / 3600
        return hours_old <= self.config.application.posted_within_hours

    def _get(self, url: str, **kwargs) -> requests.Response:
        resp = self.session.get(url, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp
