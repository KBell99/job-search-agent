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

_WORK_TYPE_TERMS = {
    WorkType.REMOTE: "remote",
    WorkType.HYBRID: "hybrid",
    WorkType.ONSITE: "",
}


class IndeedScraper(BaseScraper):
    name = "indeed"

    def scrape(self, location: Location) -> Iterator[Job]:
        query_parts = [self.config.role.title]
        wt_term = _WORK_TYPE_TERMS.get(self.config.work_type, "")
        if wt_term:
            query_parts.append(wt_term)
        query_parts.extend(self.config.role.keywords[:2])

        q = urllib.parse.quote_plus(" ".join(query_parts))
        loc = urllib.parse.quote_plus(str(location))
        # fromage=1 = last 24h (smallest Indeed supports); we filter to 1h ourselves
        url = f"https://www.indeed.com/rss?q={q}&l={loc}&sort=date&fromage=1"

        logger.debug("[indeed] fetching RSS: %s", url)
        feed = feedparser.parse(url)

        for entry in feed.entries:
            posted_at = self._parse_date(entry)
            if not self.within_window(posted_at):
                continue

            job_url = entry.get("link", "")
            job_id = self._extract_jk(job_url)

            description = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))

            yield Job(
                title=entry.get("title", "").split(" - ")[0].strip(),
                company=self._extract_company(entry),
                location=entry.get("location", str(location)),
                url=job_url,
                source=self.name,
                description=description,
                posted_at=posted_at,
                work_type=self._infer_work_type(description),
                job_id=job_id,
            )

    def _parse_date(self, entry) -> datetime | None:
        raw = entry.get("published_parsed") or entry.get("updated_parsed")
        if raw is None:
            return None
        return datetime(*raw[:6], tzinfo=timezone.utc)

    def _extract_jk(self, url: str) -> str | None:
        m = re.search(r"jk=([a-f0-9]+)", url)
        return m.group(1) if m else None

    def _extract_company(self, entry) -> str:
        # Indeed RSS puts "Title - Company - Location" in the title field
        parts = entry.get("title", "").split(" - ")
        return parts[1].strip() if len(parts) >= 2 else ""

    def _infer_work_type(self, text: str) -> WorkType | None:
        lower = text.lower()
        if "remote" in lower:
            return WorkType.REMOTE
        if "hybrid" in lower:
            return WorkType.HYBRID
        if "on-site" in lower or "onsite" in lower or "in-office" in lower:
            return WorkType.ONSITE
        return None
