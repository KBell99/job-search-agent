from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Iterator

from config import Config
from models import EmploymentType, Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_JOBSPY_WORK_TYPE = {
    WorkType.REMOTE: "remote",
    WorkType.HYBRID: "hybrid",
    WorkType.ONSITE: "fulltime",  # jobspy has no onsite enum; omit filter instead
}

_JOBSPY_EMPLOYMENT_TYPE = {
    EmploymentType.FULL_TIME: "fulltime",
    EmploymentType.PART_TIME: "parttime",
    EmploymentType.CONTRACT: "contract",
    EmploymentType.TEMPORARY: "temporary",
}

_EMPLOYMENT_TYPE_MAP = {
    "fulltime": EmploymentType.FULL_TIME,
    "full-time": EmploymentType.FULL_TIME,
    "full_time": EmploymentType.FULL_TIME,
    "parttime": EmploymentType.PART_TIME,
    "part-time": EmploymentType.PART_TIME,
    "part_time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "contractor": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "temp": EmploymentType.TEMPORARY,
}


class IndeedScraper(BaseScraper):
    name = "indeed"

    def scrape(self, location: Location, work_type: WorkType) -> Iterator[Job]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            logger.error("[indeed] python-jobspy not installed — run: pip install python-jobspy")
            return

        hours_old = math.ceil(self.config.application.posted_within_hours / 24) * 24
        is_remote = work_type == WorkType.REMOTE

        logger.debug("[indeed] querying jobspy for '%s' in %s (hours_old=%s)",
                     self.role.title, location, hours_old)
        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term=self.role.title,
                location=str(location),
                distance=location.radius_miles,
                is_remote=is_remote,
                results_wanted=50,
                hours_old=int(hours_old),
                country_indeed="USA",
                verbose=0,
            )
        except Exception as e:
            logger.warning("[indeed] jobspy scrape failed: %s", e)
            return

        if df is None or df.empty:
            logger.info("[indeed] no results for %s", location)
            return

        logger.info("[indeed] %d results from jobspy for %s", len(df), location)

        for _, row in df.iterrows():
            posted_at = self._parse_date(row.get("date_posted"))
            if not self._within_ceiling_window(posted_at, hours_old):
                continue

            yield Job(
                title=str(row.get("title") or ""),
                company=str(row.get("company") or ""),
                location=str(row.get("location") or location),
                url=str(row.get("job_url") or ""),
                source=self.name,
                description=str(row.get("description") or ""),
                posted_at=posted_at,
                work_type=self._map_work_type(row.get("job_type")),
                employment_type=self._map_employment_type(row.get("job_type")),
                job_id=str(row.get("id") or ""),
                salary=self._format_salary(row),
            )

    def _within_ceiling_window(self, posted_at: datetime | None, ceiling_hours: int) -> bool:
        if posted_at is None:
            return True
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        return (now - posted_at).total_seconds() / 3600 <= ceiling_hours

    def _parse_date(self, value) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        try:
            return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    def _map_employment_type(self, value) -> EmploymentType | None:
        if not value:
            return None
        return _EMPLOYMENT_TYPE_MAP.get(str(value).lower().strip())

    def _map_work_type(self, value) -> WorkType | None:
        if not value:
            return None
        lower = str(value).lower()
        if "remote" in lower:
            return WorkType.REMOTE
        if "hybrid" in lower:
            return WorkType.HYBRID
        if "onsite" in lower or "on-site" in lower or "office" in lower:
            return WorkType.ONSITE
        return None

    def _format_salary(self, row) -> str | None:
        def _valid(v) -> bool:
            return v is not None and not (isinstance(v, float) and math.isnan(v))

        low = row.get("min_amount")
        high = row.get("max_amount")
        interval = row.get("salary_type") or row.get("interval") or ""
        if _valid(low) and _valid(high):
            return f"${int(low):,}–${int(high):,} {interval}".strip()
        if _valid(low):
            return f"${int(low):,}+ {interval}".strip()
        return None
