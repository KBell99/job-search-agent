from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from config import Config
from models import EmploymentType, Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_WORK_TYPE_CODES = {
    WorkType.REMOTE: "2",
    WorkType.HYBRID: "3",
    WorkType.ONSITE: "1",
}

_EMPLOYMENT_TYPE_CODES = {
    EmploymentType.FULL_TIME: "F",
    EmploymentType.PART_TIME: "P",
    EmploymentType.CONTRACT: "C",
    EmploymentType.TEMPORARY: "T",
}


class LinkedInScraper(BaseScraper):
    name = "linkedin"

    def scrape(self, location: Location, work_type: WorkType) -> Iterator[Job]:
        title = urllib.parse.quote_plus(self.role.title)
        loc = urllib.parse.quote_plus(str(location))
        wt_code = _WORK_TYPE_CODES.get(work_type, "")
        f_wt = f"&f_WT={wt_code}" if wt_code else ""
        seconds = max(3600, int(self.config.application.posted_within_hours * 3600))

        et_codes = ",".join(
            c for et in self.config.application.employment_types
            if (c := _EMPLOYMENT_TYPE_CODES.get(et))
        )
        f_jt = f"&f_JT={et_codes}" if et_codes else ""

        url = (
            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={title}&location={loc}&f_TPR=r{seconds}{f_wt}{f_jt}&start=0"
        )

        logger.debug("[linkedin] fetching: %s", url)
        try:
            resp = self._get(url)
        except Exception as e:
            logger.warning("[linkedin] request failed: %s", e)
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.find_all("li"):
            job = self._parse_card(card, location, work_type)
            if job:
                yield job

    def _parse_card(self, card, location: Location, work_type: WorkType) -> Job | None:
        try:
            title_el = card.find("h3", class_=re.compile("title"))
            company_el = (
                card.find("h4", class_=re.compile("company"))
                or card.find("h4", class_=re.compile("subtitle"))
                or card.find("a", href=re.compile(r"/company/"))
            )
            link_el = card.find("a", href=re.compile(r"/jobs/view/"))

            if not link_el:
                return None

            href = link_el.get("href", "")
            job_id = re.search(r"/jobs/view/(\d+)", href)
            job_id = job_id.group(1) if job_id else None
            url = f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else href

            posted_at = self._parse_posted_at(card)

            return Job(
                title=(title_el.get_text(strip=True) if title_el else ""),
                company=(company_el.get_text(strip=True) if company_el else ""),
                location=str(location),
                url=url,
                source=self.name,
                description=card.get_text(" ", strip=True),
                posted_at=posted_at,
                work_type=work_type,
                job_id=job_id,
            )
        except Exception as e:
            logger.debug("[linkedin] card parse error: %s", e)
            return None

    def _parse_posted_at(self, card) -> datetime | None:
        # Prefer <time datetime="ISO"> attribute
        time_el = card.find("time")
        if time_el:
            dt_attr = time_el.get("datetime", "")
            if dt_attr:
                try:
                    dt = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

        # Fall back to relative text: "2 hours ago", "1 day ago"
        text = (time_el.get_text(strip=True) if time_el else "") or card.get_text(" ", strip=True)
        m = re.search(r"(\d+)\s*(minute|hour|day|week)s?\s+ago", text.lower())
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n),
                 "day": timedelta(days=n), "week": timedelta(weeks=n)}.get(unit)
        return (datetime.now(tz=timezone.utc) - delta) if delta else None

    def get_full_description(self, job_id: str) -> str:
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        try:
            resp = self._get(url)
            soup = BeautifulSoup(resp.text, "html.parser")
            desc = soup.find("div", class_=re.compile("description"))
            return desc.get_text("\n", strip=True) if desc else ""
        except Exception as e:
            logger.debug("[linkedin] full description fetch failed: %s", e)
            return ""
