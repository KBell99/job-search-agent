from __future__ import annotations

import logging
import math
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE_URL = "https://builtin.com/jobs"

_WORK_TYPE_SLUGS = {
    WorkType.REMOTE: "remote",
    WorkType.HYBRID: "hybrid",
    WorkType.ONSITE: "office",
}

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


class BuiltinScraper(BaseScraper):
    name = "builtin"

    def scrape(self, location: Location) -> Iterator[Job]:
        work_slug = _WORK_TYPE_SLUGS.get(self.config.work_type, "hybrid")
        params: dict[str, str] = {
            "search": self.config.role.title,
            "daysSinceUpdated": str(math.ceil(self.config.application.posted_within_hours / 24)),
            "city": location.city,
            "state": _STATE_NAMES.get(location.state.upper(), location.state),
            "country": "USA",
            "allLocations": "true",
        }

        full_url = f"{_BASE_URL}/{work_slug}?{urllib.parse.urlencode(params)}"
        logger.debug("[builtin] fetching: %s", full_url)

        html = self._fetch(full_url)
        if not html:
            return

        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.find_all(["a", "div"], attrs={"data-id": "job-card-title"})
        logger.debug("[builtin] found %d job-card-title anchors", len(anchors))

        role_terms = [self.config.role.title.lower()] + [
            k.lower() for k in self.config.role.keywords
        ]

        for anchor in anchors:
            job = self._parse_card(anchor, location)
            if job and self._relevant(job.title, role_terms):
                yield job

    # ── fetch: HTTP first, Playwright on 404 / 403 / empty shell ────────────

    def _fetch(self, full_url: str) -> str | None:
        try:
            resp = self.session.get(full_url, timeout=15)
            resp.raise_for_status()
            if len(resp.text) < 5_000:
                logger.info("[builtin] response too short (%d chars) — retrying with browser", len(resp.text))
                return self._fetch_browser(full_url)
            return resp.text
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 403):
                logger.info("[builtin] %s on HTTP — retrying with browser", status)
                return self._fetch_browser(full_url)
            logger.warning("[builtin] request failed: %s", e)
            return None

    def _fetch_browser(self, full_url: str) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "[builtin] playwright not installed — run: "
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

    def _parse_card(self, anchor, location: Location) -> Job | None:
        # anchor is <a id="job-card-title">; parent container holds company + timestamp
        try:
            href = anchor.get("href", "")
            if not href:
                return None

            title = anchor.get_text(strip=True)
            full_url = href if href.startswith("http") else f"https://builtin.com{href}"
            job_id_m = re.search(r"/jobs/(\d+|[a-z0-9-]+)/?$", href)
            job_id = job_id_m.group(1) if job_id_m else None

            container = anchor.parent
            company_el = container.find(class_=re.compile(r"company", re.I)) if container else None

            time_el = container.find(
                "span",
                class_="fs-xs fw-bold bg-gray-01 font-Montserrat text-gray-03 rounded-1 py-xs px-sm",
            ) if container else None
            posted_at = self._parse_relative(time_el.get_text(strip=True) if time_el else "")
            logger.debug("[builtin] %s — posted: %s", title, time_el.get_text(strip=True) if time_el else "unknown")

            if not self.within_window(posted_at):
                return None

            desc_el = container.find(class_=re.compile(r"desc|summary", re.I)) if container else None

            return Job(
                title=title,
                company=(company_el.get_text(strip=True) if company_el else ""),
                location=str(location),
                url=full_url,
                source=self.name,
                description=(desc_el.get_text(" ", strip=True) if desc_el else ""),
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
        # Expects: "x hours ago" | "x minutes ago" | "x days ago"
        now = datetime.now(tz=timezone.utc)
        m = re.search(r"(\d+)\s*(minute|hour|day)s?\s+ago", text.lower().strip())
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        delta_map = {
            "minute": timedelta(minutes=n),
            "hour":   timedelta(hours=n),
            "day":    timedelta(days=n),
        }
        delta = delta_map.get(unit)
        return (now - delta) if delta else None
