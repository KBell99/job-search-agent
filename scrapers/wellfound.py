from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup

from config import Config
from models import Job, Location, WorkType
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://wellfound.com"


class WellfoundScraper(BaseScraper):
    name = "wellfound"

    def scrape(self, location: Location) -> Iterator[Job]:
        role = urllib.parse.quote_plus(self.config.role.title.lower().replace(" ", "-"))
        loc = urllib.parse.quote_plus(location.city.lower().replace(" ", "-"))

        url = (
            f"{_BASE}/jobs/{role}/remote"
            if self.config.work_type == WorkType.REMOTE
            else f"{_BASE}/jobs/{role}/{loc}"
        )

        logger.debug("[wellfound] fetching: %s", url)
        html = self._fetch(url)
        if html is None:
            return

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", attrs={"data-test": re.compile("job")})

        if cards:
            for card in cards:
                job = self._parse_card(card, location)
                if job:
                    yield job
        else:
            yield from self._parse_json_ld(soup, location)

    # ── fetch: HTTP first, Playwright on 403 ────────────────────────────────

    def _fetch(self, url: str) -> str | None:
        try:
            resp = self._get(url)
            return resp.text
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 403:
                logger.info("[wellfound] 403 on HTTP — retrying with browser session")
                return self._fetch_browser(url)
            logger.warning("[wellfound] request failed: %s", e)
            return None

    def _fetch_browser(self, url: str) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "[wellfound] playwright not installed — run: "
                "pip install playwright && playwright install chromium"
            )
            return None

        try:
            with sync_playwright() as pw:
                session_path = self._session_path()

                if not session_path.exists():
                    saved = self._interactive_login(pw, session_path)
                    if not saved:
                        return None

                html = self._navigate(pw, url, session_path)

                # Saved session may have expired — detect login redirect and re-auth
                if html is None or "wellfound.com/login" in (html or ""):
                    logger.info("[wellfound] Session expired — re-authenticating")
                    session_path.unlink(missing_ok=True)
                    saved = self._interactive_login(pw, session_path)
                    if not saved:
                        return None
                    html = self._navigate(pw, url, session_path)

                return html
        except Exception as e:
            logger.warning("[wellfound] browser fetch failed: %s", e)
            return None

    def _session_path(self):
        return Path(self.config.output.sessions_dir) / "wellfound.json"

    def _interactive_login(self, pw, session_path: Path) -> bool:
        """Open a visible browser, wait for the user to log in, then save state."""
        print(
            "\n[wellfound] No saved session found.\n"
            "            A browser window will open — log in to Wellfound,\n"
            "            then wait. The session saves automatically.\n"
        )
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(f"{_BASE}/login", wait_until="domcontentloaded")

        try:
            # Wait up to 3 minutes for the user to complete login (including any CAPTCHA)
            page.wait_for_url(re.compile(r"wellfound\.com/(?!login)"), timeout=180_000)
        except Exception:
            logger.warning("[wellfound] Login timed out or was cancelled")
            browser.close()
            return False

        session_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(session_path))
        browser.close()
        logger.info("[wellfound] Session saved → %s", session_path)
        return True

    def _navigate(self, pw, url: str, session_path: Path) -> str | None:
        """Launch a browser with the saved session and return the rendered page HTML."""
        headless = not self.config.output.show_browser
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(
            storage_state=str(session_path),
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        try:
            page.goto(url, timeout=20_000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    "[data-test*='job'], script[type='application/ld+json']",
                    timeout=8_000,
                )
            except Exception:
                pass
            html = page.content()
            logger.debug("[wellfound] browser fetch succeeded (%d chars)", len(html))
            return html
        except Exception as e:
            logger.warning("[wellfound] navigation failed: %s", e)
            return None
        finally:
            browser.close()

    # ── parsers ──────────────────────────────────────────────────────────────

    def _parse_card(self, card, location: Location) -> Job | None:
        try:
            title_el = card.find(["h2", "h3", "a"])
            company_el = card.find(class_=re.compile("company", re.I))
            link_el = card.find("a", href=re.compile(r"/jobs/"))

            if not title_el or not link_el:
                return None

            href = link_el.get("href", "")
            full_url = href if href.startswith("http") else f"{_BASE}{href}"
            job_id = re.search(r"/jobs/(\d+)", href)
            job_id = job_id.group(1) if job_id else None

            time_el = card.find("time") or card.find(class_=re.compile("posted|date|ago", re.I))
            posted_at = self._parse_relative(time_el.get_text(strip=True) if time_el else "")

            if not self.within_window(posted_at):
                return None

            return Job(
                title=title_el.get_text(strip=True),
                company=(company_el.get_text(strip=True) if company_el else ""),
                location=str(location),
                url=full_url,
                source=self.name,
                description=card.get_text(" ", strip=True),
                posted_at=posted_at,
                work_type=self.config.work_type,
                job_id=job_id,
            )
        except Exception as e:
            logger.debug("[wellfound] card parse error: %s", e)
            return None

    def _parse_json_ld(self, soup: BeautifulSoup, location: Location) -> Iterator[Job]:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    items = data
                elif data.get("@type") == "ItemList":
                    items = data.get("itemListElement", [])
                else:
                    items = [data]

                for item in items:
                    job_data = item.get("item", item)
                    if job_data.get("@type") != "JobPosting":
                        continue
                    posted_raw = job_data.get("datePosted", "")
                    posted_at = None
                    if posted_raw:
                        try:
                            posted_at = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    if not self.within_window(posted_at):
                        continue
                    yield Job(
                        title=job_data.get("title", ""),
                        company=job_data.get("hiringOrganization", {}).get("name", ""),
                        location=(
                            job_data.get("jobLocation", {})
                            .get("address", {})
                            .get("addressLocality", str(location))
                        ),
                        url=job_data.get("url", ""),
                        source=self.name,
                        description=job_data.get("description", ""),
                        posted_at=posted_at,
                    )
            except Exception:
                continue

    def _parse_relative(self, text: str) -> datetime | None:
        now = datetime.now(tz=timezone.utc)
        text = text.lower()
        m = re.search(r"(\d+)\s*(minute|hour|day|week)", text)
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
            "week": timedelta(weeks=n),
        }.get(unit)
        return (now - delta) if delta else None
