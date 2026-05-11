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

_BASE = "https://builtin.com"

_CITY_SLUGS = {
    "chicago": "chicago",
    "seattle": "seattle",
    "san francisco": "san-francisco",
    "new york": "nyc",
    "boston": "boston",
    "austin": "austin",
    "los angeles": "los-angeles",
    "denver": "colorado",
    "atlanta": "atlanta",
    "miami": "miami",
}

# Builtin search URL that accepts a keyword and optional city filter
_SEARCH_URL = f"{_BASE}/jobs"


class BuiltinScraper(BaseScraper):
    name = "builtin"

    def scrape(self, location: Location) -> Iterator[Job]:
        city_slug = _CITY_SLUGS.get(location.city.lower(), "")

        if self.config.work_type == WorkType.REMOTE:
            url = f"{_BASE}/remote/jobs"
            params = {"search": self.config.role.title}
        elif city_slug:
            # Correct city URL — no query params, Builtin's router rejects unknown ones
            url = f"{_BASE}/{city_slug}/jobs"
            params = {"search": self.config.role.title}
        else:
            url = _SEARCH_URL
            params = {"search": self.config.role.title}

        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        logger.debug("[builtin] fetching: %s", full_url)

        html = self._fetch(url, params)
        if not html:
            return

        soup = BeautifulSoup(html, "html.parser")
        cards = (
            soup.find_all("article")
            or soup.find_all("div", class_=re.compile(r"job-card|JobCard", re.I))
            or soup.find_all("li", class_=re.compile(r"job", re.I))
        )

        role_terms = [self.config.role.title.lower()] + [
            k.lower() for k in self.config.role.keywords
        ]

        for card in cards:
            job = self._parse_card(card, location)
            if job and self._relevant(job.title, role_terms):
                yield job

    # ── fetch: HTTP first, Playwright on 404 / empty body ───────────────────

    def _fetch(self, url: str, params: dict) -> str | None:
        try:
            resp = self._get(url, params=params)
            # Builtin is React-rendered; a bare HTML shell has very little content
            if len(resp.text) < 5_000:
                logger.info("[builtin] response too short (%d chars) — retrying with browser", len(resp.text))
                return self._fetch_browser(url, params)
            return resp.text
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 403):
                logger.info("[builtin] %s on HTTP — retrying with browser", status)
                return self._fetch_browser(url, params)
            logger.warning("[builtin] request failed: %s", e)
            return None

    def _fetch_browser(self, url: str, params: dict) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "[builtin] playwright not installed — run: "
                "pip install playwright && playwright install chromium"
            )
            return None

        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                )
                page = ctx.new_page()
                page.goto(full_url, timeout=20_000, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(
                        "article, [class*='job-card'], [class*='JobCard'], li[class*='job']",
                        timeout=8_000,
                    )
                except Exception:
                    pass
                html = page.content()
                browser.close()
                logger.debug("[builtin] browser fetch succeeded (%d chars)", len(html))
                return html
        except Exception as e:
            logger.warning("[builtin] browser fetch failed: %s", e)
            return None

    # ── parsers ──────────────────────────────────────────────────────────────

    def _parse_card(self, card, location: Location) -> Job | None:
        try:
            title_el = card.find(["h2", "h3"], class_=re.compile(r"title|job-name", re.I))
            if not title_el:
                title_el = card.find("a")

            company_el = card.find(class_=re.compile(r"company", re.I))
            link_el = card.find("a", href=True)

            if not title_el or not link_el:
                return None

            href = link_el["href"]
            full_url = href if href.startswith("http") else f"{_BASE}{href}"
            job_id_m = re.search(r"/jobs/(\d+|[a-z0-9-]+)/?$", href)
            job_id = job_id_m.group(1) if job_id_m else None

            time_el = card.find(class_=re.compile(r"date|posted|ago|time", re.I))
            posted_at = self._parse_relative(time_el.get_text(strip=True) if time_el else "")

            if not self.within_window(posted_at):
                return None

            desc_el = card.find(class_=re.compile(r"desc|summary", re.I))

            return Job(
                title=title_el.get_text(strip=True),
                company=(company_el.get_text(strip=True) if company_el else ""),
                location=str(location),
                url=full_url,
                source=self.name,
                description=(desc_el.get_text(" ", strip=True) if desc_el else card.get_text(" ", strip=True)),
                posted_at=posted_at,
                work_type=self.config.work_type,
                job_id=job_id,
            )
        except Exception as e:
            logger.debug("[builtin] card parse error: %s", e)
            return None

    def _relevant(self, title: str, terms: list[str]) -> bool:
        title_lower = title.lower()
        return any(t in title_lower for t in terms)

    def _parse_relative(self, text: str) -> datetime | None:
        now = datetime.now(tz=timezone.utc)
        text = text.lower().strip()

        if text in ("today", "just now", "new"):
            return now - timedelta(minutes=30)

        m = re.search(r"(\d+)\s*(m|min|minute|h|hr|hour|d|day|w|week)", text)
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        delta_map = {
            "m": timedelta(minutes=n), "min": timedelta(minutes=n), "minute": timedelta(minutes=n),
            "h": timedelta(hours=n),   "hr": timedelta(hours=n),    "hour": timedelta(hours=n),
            "d": timedelta(days=n),    "day": timedelta(days=n),
            "w": timedelta(weeks=n),   "week": timedelta(weeks=n),
        }
        delta = delta_map.get(unit)
        return (now - delta) if delta else None
