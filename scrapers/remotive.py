from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator

from config import Config
from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API = "https://remotive.com/api/remote-jobs"


class RemotiveScraper(BaseScraper):
    name = "remotive"

    def scrape(self, location: Location) -> Iterator[Job]:
        # Remotive is remote-only; ignore location filter
        params = {
            "category": "software-dev",
            "search": self.config.role.title,
            "limit": 50,
        }
        try:
            resp = self._get(_API, params=params)
            jobs = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning("[remotive] API error: %s", e)
            return

        for item in jobs:
            posted_at = None
            raw = item.get("publication_date", "")
            if raw:
                try:
                    posted_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    pass

            if not self.within_window(posted_at):
                continue

            yield Job(
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location="Remote",
                url=item.get("url", ""),
                source=self.name,
                description=item.get("description", ""),
                posted_at=posted_at,
                work_type=WorkType.REMOTE,
                job_id=str(item.get("id", "")),
            )
