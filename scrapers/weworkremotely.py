from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Iterator

import feedparser

from config import Config
from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_RSS_BASE = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


class WeWorkRemotelyScraper(BaseScraper):
    name = "weworkremotely"

    def scrape(self, location: Location) -> Iterator[Job]:
        logger.debug("[weworkremotely] fetching RSS")
        feed = feedparser.parse(_RSS_BASE)

        title_lower = self.config.role.title.lower()
        keywords = [k.lower() for k in self.config.role.keywords]

        for entry in feed.entries:
            entry_title = entry.get("title", "").lower()
            # Filter by role relevance
            if title_lower not in entry_title and not any(k in entry_title for k in keywords):
                continue

            posted_at = self._parse_date(entry)
            if not self.within_window(posted_at):
                continue

            desc = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))
            region = entry.get("region", "Worldwide")
            company = entry.get("author") or self._extract_company(entry.get("title", ""))

            yield Job(
                title=entry.get("title", "").split(":")[0].strip(),
                company=company,
                location=f"Remote ({region})",
                url=entry.get("link", ""),
                source=self.name,
                description=desc,
                posted_at=posted_at,
                work_type=WorkType.REMOTE,
                job_id=entry.get("id", ""),
            )

    def _parse_date(self, entry) -> datetime | None:
        raw = entry.get("published_parsed") or entry.get("updated_parsed")
        if raw is None:
            return None
        return datetime(*raw[:6], tzinfo=timezone.utc)

    def _extract_company(self, title: str) -> str:
        # WWR titles often: "Company: Job Title at Company"
        parts = title.split(" at ")
        return parts[-1].strip() if len(parts) > 1 else ""
