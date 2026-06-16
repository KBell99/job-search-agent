from __future__ import annotations

import logging
import math
import urllib.parse
from datetime import datetime
from typing import Iterator

from bs4 import BeautifulSoup

from models import EmploymentType, Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.roberthalf.com/us/en/jobs"

# Robert Half Technology division — narrows results to tech roles without
# client-side JS keyword filtering being required.
_LOB_TECH = "RHT"

_WORKSITE_MAP: dict[str, WorkType] = {
    "remote": WorkType.REMOTE,
    "hybrid": WorkType.HYBRID,
    "onsite": WorkType.ONSITE,
}

_EMPLOYMENT_TYPE_MAP: dict[str, EmploymentType] = {
    "permanent / full time": EmploymentType.FULL_TIME,
    "full time": EmploymentType.FULL_TIME,
    "contract to hire": EmploymentType.CONTRACT,
    "contract": EmploymentType.CONTRACT,
    "temporary to hire": EmploymentType.TEMPORARY,
    "temporary": EmploymentType.TEMPORARY,
    "part time": EmploymentType.PART_TIME,
    "part-time": EmploymentType.PART_TIME,
}

_MAX_PAGES = 4
_PAGE_SIZE = 25


class RobertHalfScraper(BaseScraper):
    name = "roberthalf"

    def scrape(self, location: Location, work_type: WorkType) -> Iterator[Job]:
        # Convert hours to days for the postedwithin param (min 1, max 30)
        days = max(1, min(30, math.ceil(self.config.application.posted_within_hours / 24)))
        loc_str = urllib.parse.quote_plus(f"{location.city}, {location.state}")

        for page in range(1, _MAX_PAGES + 1):
            url = (
                f"{_BASE}"
                f"?lobid={_LOB_TECH}"
                f"&location={loc_str}"
                f"&postedwithin={days}"
                f"&pagenumber={page}"
            )
            logger.debug("[roberthalf] fetching page %d: %s", page, url)

            try:
                resp = self._get(url)
            except Exception as e:
                logger.warning("[roberthalf] request failed (page %d): %s", page, e)
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            raw_cards = soup.find_all("rhcl-job-card")
            logger.debug("[roberthalf] %d raw cards on page %d", len(raw_cards), page)

            if not raw_cards:
                break

            yielded = 0
            for card in raw_cards:
                job = self._parse_card(card, location, work_type)
                if job:
                    yielded += 1
                    yield job

            logger.debug("[roberthalf] %d/%d cards passed filters on page %d", yielded, len(raw_cards), page)

            # Stop early if fewer than a full page came back
            if len(raw_cards) < _PAGE_SIZE:
                break

    def _parse_card(self, card, location: Location, work_type: WorkType) -> Job | None:
        try:
            job_id = card.get("job-id", "")

            link = card.find("a", slot="headline")
            if not link:
                return None

            title = link.get_text(strip=True)
            url = link.get("href", "")
            if not url:
                return None

            info = card.find("ul", slot="job-info")
            if not info:
                return None

            def _sub(name: str) -> str:
                el = info.find(attrs={"data-subslot": name})
                return el.get_text(strip=True) if el else ""

            job_location = _sub("location")
            worksite_raw = _sub("worksite").lower()
            type_raw = _sub("type").lower()
            copy_raw = _sub("copy")
            date_str = _sub("date")

            # Date / time-window check (server already filtered, but enforce locally too)
            posted_at = self._parse_date(date_str)
            if not self.within_window(posted_at):
                return None

            # Work-type filter
            mapped_wt = _WORKSITE_MAP.get(worksite_raw)
            if mapped_wt is not None and mapped_wt != work_type:
                return None

            # Location filter — remote jobs bypass city check
            if mapped_wt != WorkType.REMOTE and not self._location_matches(job_location, location):
                return None

            # Employment type
            emp_type: EmploymentType | None = None
            for key, val in _EMPLOYMENT_TYPE_MAP.items():
                if key in type_raw:
                    emp_type = val
                    break

            # Salary
            salary = self._format_salary(_sub)

            # Strip HTML entities from description snippet
            description = BeautifulSoup(copy_raw, "html.parser").get_text(" ", strip=True)

            return Job(
                title=title,
                company="Robert Half",
                location=job_location or str(location),
                url=url,
                source=self.name,
                description=description,
                posted_at=posted_at,
                work_type=mapped_wt or work_type,
                employment_type=emp_type,
                salary=salary,
                job_id=job_id,
            )
        except Exception as e:
            logger.debug("[roberthalf] card parse error: %s", e)
            return None

    def _parse_date(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _location_matches(self, job_location: str, cfg: Location) -> bool:
        low = job_location.lower()
        # Require ", IL" (comma-prefixed) to avoid false matches like "il" inside "philadelphia"
        return cfg.city.lower() in low or f", {cfg.state.lower()}" in low

    def _format_salary(self, sub_fn) -> str | None:
        sal_min = sub_fn("salary-min")
        sal_max = sub_fn("salary-max")
        currency = sub_fn("salary-currency") or "USD"
        period = sub_fn("salary-period") or ""
        if not sal_min:
            return None
        try:
            lo = float(sal_min)
            hi = float(sal_max) if sal_max else None
            lo_str = f"${lo:,.2f}"
            range_str = f"{lo_str}–${hi:,.2f}" if hi else lo_str
            return f"{range_str} {currency} / {period}".strip()
        except ValueError:
            return None
