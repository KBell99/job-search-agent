from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Iterator

from bs4 import BeautifulSoup

from config import Config, RoleConfig
from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseScraper(BaseScraper):
    name = "greenhouse"

    def __init__(self, config: Config, role: RoleConfig):
        super().__init__(config, role)
        board_cfg = next((b for b in config.job_boards if b.name == self.name), None)
        self.board_tokens: list[str] = board_cfg.board_tokens if board_cfg else []
        if not self.board_tokens:
            logger.warning(
                "[greenhouse] no board_tokens configured — add them under "
                "job_boards[name=greenhouse].board_tokens in config.yaml"
            )

    def scrape(self, location: Location, work_type: WorkType) -> Iterator[Job]:
        role_terms = [self.role.title.lower()] + [k.lower() for k in self.role.keywords]
        for token in self.board_tokens:
            yield from self._scrape_board(token, location, work_type, role_terms)

    def _scrape_board(
        self,
        token: str,
        location: Location,
        work_type: WorkType,
        role_terms: list[str],
    ) -> Iterator[Job]:
        url = _API.format(token=token)
        logger.debug("[greenhouse] fetching board '%s'", token)
        try:
            resp = self._get(url)
            data = resp.json()
        except Exception as e:
            logger.warning("[greenhouse] failed to fetch board '%s': %s", token, e)
            return

        jobs = data.get("jobs", [])
        logger.debug("[greenhouse] %d jobs on board '%s'", len(jobs), token)

        matched = 0
        for job in jobs:
            title = job.get("title", "")

            # Relevance: title must contain role title or a keyword
            if not any(t in title.lower() for t in role_terms):
                continue

            # Time window — use first_published; fall back to updated_at
            posted_at = self._parse_date(
                job.get("first_published") or job.get("updated_at")
            )
            if not self.within_window(posted_at):
                continue

            loc_name = (job.get("location") or {}).get("name", "")

            # Work-type and location filter
            mapped_wt = self._infer_work_type(loc_name)
            if not self._location_ok(loc_name, location, work_type, mapped_wt):
                continue

            # Decode HTML entities then strip tags from description
            raw_content = html.unescape(job.get("content") or "")
            description = BeautifulSoup(raw_content, "html.parser").get_text(" ", strip=True)

            company = job.get("company_name") or token

            yield Job(
                title=title,
                company=company,
                location=loc_name,
                url=job.get("absolute_url", ""),
                source=self.name,
                description=description,
                posted_at=posted_at,
                work_type=mapped_wt or work_type,
                job_id=str(job.get("id", "")),
            )
            matched += 1

        logger.debug("[greenhouse] %d/%d jobs passed filters on board '%s'", matched, len(jobs), token)

    def _parse_date(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _infer_work_type(self, loc_name: str) -> WorkType | None:
        low = loc_name.lower()
        # Greenhouse locations can be multi-value: "US-Remote, US-Chicago"
        parts = [p.strip() for p in low.split(",")]
        has_remote = any("remote" in p for p in parts)
        has_hybrid = any("hybrid" in p for p in parts)
        if has_remote and has_hybrid:
            return WorkType.HYBRID
        if has_remote:
            return WorkType.REMOTE
        if has_hybrid:
            return WorkType.HYBRID
        return None  # assume onsite if unspecified

    def _location_ok(
        self,
        loc_name: str,
        cfg: Location,
        desired_wt: WorkType,
        mapped_wt: WorkType | None,
    ) -> bool:
        low = loc_name.lower()
        parts = [p.strip() for p in low.split(",")]

        if desired_wt == WorkType.REMOTE:
            return any("remote" in p for p in parts)

        # For onsite / hybrid: accept if the city or state appears in any location part,
        # or if the job allows remote (some multi-location postings include remote)
        city_low = cfg.city.lower()
        # Use ", ST" to avoid short abbreviation matching inside city names
        state_suffix = f", {cfg.state.lower()}"

        for part in parts:
            if "remote" in part:
                continue  # don't count a "remote" part for city match
            if city_low in part:
                return True
            # Check state abbreviation anchored with a comma
            if part.rstrip().endswith(cfg.state.lower()):
                return True

        # Also match full location string for ", IL" suffix
        if f", {cfg.state.lower()}" in low:
            return True

        return False
