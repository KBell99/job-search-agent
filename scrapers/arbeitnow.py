from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator

from config import Config
from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowScraper(BaseScraper):
    name = "arbeitnow"

    def scrape(self, location: Location) -> Iterator[Job]:
        try:
            resp = self._get(_API)
            data = resp.json().get("data", [])
        except Exception as e:
            logger.warning("[arbeitnow] API error: %s", e)
            return

        title_lower = self.config.role.title.lower()
        keywords = [k.lower() for k in self.config.role.keywords]

        for item in data:
            job_title = item.get("title", "").lower()
            if title_lower not in job_title and not any(k in job_title for k in keywords):
                continue

            ts = item.get("created_at")
            posted_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

            if not self.within_window(posted_at):
                continue

            remote = item.get("remote", False)
            work_type = WorkType.REMOTE if remote else None

            yield Job(
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location=item.get("location", ""),
                url=item.get("url", ""),
                source=self.name,
                description=item.get("description", ""),
                posted_at=posted_at,
                work_type=work_type,
                job_id=item.get("slug", ""),
            )
