from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.workatastartup.com"

# YC role slugs that map to engineering roles
_ENG_KEYWORDS = {
    "software", "engineer", "developer", "backend", "frontend", "full stack",
    "fullstack", "swe", "sde", "platform", "infrastructure", "devops", "ml",
    "machine learning", "data",
}


class WorkAtAStartupScraper(BaseScraper):
    name = "workatastartup"

    def scrape_all(self) -> list[Job]:
        """Override to search once per work type globally instead of once per location."""
        seen: set[str] = set()
        jobs: list[Job] = []
        limit = self.config.application.max_jobs_per_location * len(self.config.locations)

        for work_type in self.config.work_types:
            try:
                for job in self._search(work_type):
                    key = job.job_id or f"{job.company}::{job.title}::{job.url}"
                    if key not in seen and len(jobs) < limit:
                        seen.add(key)
                        jobs.append(job)
            except Exception as e:
                logger.error("[%s] scrape failed for %s: %s", self.name, work_type.value, e)

        return jobs

    def scrape(self, location: Location, work_type: WorkType) -> Iterator[Job]:
        # Required by ABC; scrape_all overrides it to avoid redundant requests.
        yield from self._search(work_type)

    def _search(self, work_type: WorkType) -> Iterator[Job]:
        params: dict[str, str] = {"q": self.role.title}
        if work_type == WorkType.REMOTE:
            params["remote"] = "true"

        url = f"{_BASE}/jobs?{urllib.parse.urlencode(params)}"
        logger.debug("[workatastartup] fetching: %s", url)

        html = self._fetch(url)
        if not html:
            return

        soup = BeautifulSoup(html, "html.parser")
        role_terms = [self.role.title.lower()] + [
            k.lower() for k in self.role.keywords
        ]
        yield from self._parse_jobs(soup, role_terms)

    # ── fetch: HTTP first, Playwright fallback ───────────────────────────────

    def _fetch(self, url: str) -> str | None:
        try:
            resp = self._get(url)
            if len(resp.text) < 5_000:
                logger.info(
                    "[workatastartup] short response (%d chars) — retrying with browser",
                    len(resp.text),
                )
                return self._fetch_browser(url)
            return resp.text
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (403, 429):
                logger.info("[workatastartup] %s — retrying with browser", status)
            else:
                logger.warning("[workatastartup] HTTP request failed: %s — trying browser", e)
            return self._fetch_browser(url)

    def _fetch_browser(self, url: str) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "[workatastartup] playwright not installed — run: "
                "pip install playwright && playwright install chromium"
            )
            return None

        headless = not self.config.output.show_browser
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=headless)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                )
                page = ctx.new_page()
                page.goto(url, timeout=30_000, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(
                        "a[href*='/jobs/'], [class*='job'], [class*='listing']",
                        timeout=10_000,
                    )
                except Exception:
                    pass
                html = page.content()
                browser.close()
                logger.debug(
                    "[workatastartup] browser fetch succeeded (%d chars)", len(html)
                )
                return html
        except Exception as e:
            logger.warning("[workatastartup] browser fetch failed: %s", e)
            return None

    # ── parsers ──────────────────────────────────────────────────────────────

    def _parse_jobs(self, soup: BeautifulSoup, role_terms: list[str]) -> Iterator[Job]:
        # Primary: look for links to /jobs/{id} — each unique ID is one job
        job_links = soup.find_all("a", href=re.compile(r"/jobs/\d+"))
        logger.debug("[workatastartup] found %d job links", len(job_links))

        seen_ids: set[str] = set()
        for link in job_links:
            try:
                href = link.get("href", "")
                m = re.search(r"/jobs/(\d+)", href)
                if not m:
                    continue
                job_id = m.group(1)
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                raw_text = link.get_text(strip=True)
                if not raw_text:
                    continue

                # Link text is typically "{title} at {Company}" — split on last " at "
                if " at " in raw_text:
                    at_idx = raw_text.rfind(" at ")
                    title = raw_text[:at_idx].strip()
                    inline_company = raw_text[at_idx + 4:].strip()
                else:
                    title = raw_text
                    inline_company = ""

                if not self._relevant(title, role_terms):
                    continue

                full_url = href if href.startswith("http") else f"{_BASE}{href}"

                # Walk up the DOM to find the card container
                container = self._find_card(link)

                company = self._extract_company(container, title) or inline_company
                job_location, work_type = self._extract_location_type(container)
                posted_at = self._extract_posted_at(container)

                if not self.within_window(posted_at):
                    continue

                yield Job(
                    title=title,
                    company=company,
                    location=job_location,
                    url=full_url,
                    source=self.name,
                    description=(container.get_text(" ", strip=True)[:2000] if container else ""),
                    posted_at=posted_at,
                    work_type=work_type,
                    job_id=job_id,
                )
            except Exception as e:
                logger.debug("[workatastartup] card parse error: %s", e)

    def _find_card(self, link) -> "BeautifulSoup | None":
        """Walk up the DOM to find a container with enough context for one job."""
        container = link.parent
        for _ in range(10):
            if container is None:
                break
            text = container.get_text(strip=True)
            if len(text) > 80:
                break
            container = container.parent
        return container

    def _extract_company(self, container, job_title: str) -> str:
        if container is None:
            return ""
        # Prefer a direct link to a company page
        company_link = container.find("a", href=re.compile(r"/company/"))
        if company_link:
            return company_link.get_text(strip=True)
        return ""

    def _extract_location_type(self, container) -> tuple[str, WorkType | None]:
        if container is None:
            return "Unknown", None

        text = container.get_text(" ", strip=True).lower()

        if "remote" in text:
            work_type: WorkType | None = WorkType.REMOTE
            job_location = "Remote"
        elif "hybrid" in text:
            work_type = WorkType.HYBRID
            job_location = "Hybrid"
        elif "in-person" in text or "on-site" in text or "onsite" in text:
            work_type = WorkType.ONSITE
            job_location = "Onsite"
        else:
            work_type = None
            job_location = "Unknown"

        # Try to find an explicit city/region element
        loc_el = container.find(class_=re.compile(r"locat|city|region|where", re.I))
        if loc_el:
            loc_text = loc_el.get_text(strip=True)
            if loc_text:
                job_location = loc_text

        return job_location, work_type

    def _extract_posted_at(self, container) -> datetime | None:
        if container is None:
            return None
        time_el = container.find("time")
        if time_el:
            dt_str = time_el.get("datetime", "") or time_el.get_text(strip=True)
            try:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except ValueError:
                pass
            return self._parse_relative(time_el.get_text(strip=True))
        # Look for relative date text nearby
        date_el = container.find(class_=re.compile(r"date|posted|ago|time", re.I))
        if date_el:
            return self._parse_relative(date_el.get_text(strip=True))
        return None

    def _parse_relative(self, text: str) -> datetime | None:
        now = datetime.now(tz=timezone.utc)
        m = re.search(r"(\d+)\s*(minute|hour|day|week|month)", text.lower())
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
            "week": timedelta(weeks=n),
            "month": timedelta(days=n * 30),
        }.get(unit)
        return (now - delta) if delta else None

    def _relevant(self, title: str, terms: list[str]) -> bool:
        title_lower = title.lower()
        # Accept if any configured term matches, or if it's a general eng title
        if any(t in title_lower for t in terms):
            return True
        return any(k in title_lower for k in _ENG_KEYWORDS)
