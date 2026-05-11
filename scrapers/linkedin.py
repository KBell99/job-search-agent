from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from config import Config
from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# f_TPR=r3600 → posted in last 3600 seconds (1 hour)
_TIME_FILTER = "r3600"

_WORK_TYPE_CODES = {
    WorkType.REMOTE: "2",
    WorkType.HYBRID: "3",
    WorkType.ONSITE: "1",
}


class LinkedInScraper(BaseScraper):
    name = "linkedin"

    def scrape(self, location: Location) -> Iterator[Job]:
        title = urllib.parse.quote_plus(self.config.role.title)
        loc = urllib.parse.quote_plus(str(location))
        wt_code = _WORK_TYPE_CODES.get(self.config.work_type, "")
        f_wt = f"&f_WT={wt_code}" if wt_code else ""

        url = (
            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={title}&location={loc}&f_TPR={_TIME_FILTER}{f_wt}&start=0"
        )

        logger.debug("[linkedin] fetching: %s", url)
        try:
            resp = self._get(url)
        except Exception as e:
            logger.warning("[linkedin] request failed: %s", e)
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("li")

        for card in cards:
            job = self._parse_card(card, location)
            if job:
                yield job

    def _parse_card(self, card, location: Location) -> Job | None:
        try:
            title_el = card.find("h3", class_=re.compile("title"))
            company_el = card.find("h4", class_=re.compile("company"))
            link_el = card.find("a", href=re.compile(r"/jobs/view/"))
            time_el = card.find("time")

            if not link_el:
                return None

            href = link_el.get("href", "")
            job_id = re.search(r"/jobs/view/(\d+)", href)
            job_id = job_id.group(1) if job_id else None

            url = f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else href

            posted_at = None
            if time_el and time_el.get("datetime"):
                try:
                    posted_at = datetime.fromisoformat(
                        time_el["datetime"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            if not self.within_window(posted_at):
                return None

            desc_text = card.get_text(" ", strip=True)

            return Job(
                title=(title_el.get_text(strip=True) if title_el else ""),
                company=(company_el.get_text(strip=True) if company_el else ""),
                location=str(location),
                url=url,
                source=self.name,
                description=desc_text,
                posted_at=posted_at,
                work_type=self.config.work_type,
                job_id=job_id,
            )
        except Exception as e:
            logger.debug("[linkedin] card parse error: %s", e)
            return None

    def get_full_description(self, job_id: str) -> str:
        """Fetch the full job description for a given LinkedIn job ID."""
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        try:
            resp = self._get(url)
            soup = BeautifulSoup(resp.text, "html.parser")
            desc = soup.find("div", class_=re.compile("description"))
            return desc.get_text("\n", strip=True) if desc else ""
        except Exception as e:
            logger.debug("[linkedin] full description fetch failed: %s", e)
            return ""
