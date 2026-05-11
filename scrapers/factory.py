from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import Config
from models import Job
from scrapers.base import BaseScraper
from scrapers.arbeitnow import ArbeitnowScraper
from scrapers.builtin import BuiltinScraper
from scrapers.indeed import IndeedScraper
from scrapers.linkedin import LinkedInScraper
from scrapers.remotive import RemotiveScraper
from scrapers.wellfound import WellfoundScraper
from scrapers.weworkremotely import WeWorkRemotelyScraper

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseScraper]] = {
    "indeed": IndeedScraper,
    "linkedin": LinkedInScraper,
    "wellfound": WellfoundScraper,
    "builtin": BuiltinScraper,
    "remotive": RemotiveScraper,
    "weworkremotely": WeWorkRemotelyScraper,
    "arbeitnow": ArbeitnowScraper,
}


def build_scrapers(config: Config) -> list[BaseScraper]:
    scrapers = []
    for board in config.enabled_boards:
        cls = _REGISTRY.get(board.name)
        if cls is None:
            logger.warning("Unknown job board: %s — skipping", board.name)
            continue
        scrapers.append(cls(config))
    return scrapers


def run_scrapers(config: Config, max_workers: int = 4) -> list[Job]:
    """Run all enabled scrapers in parallel and return deduplicated jobs."""
    scrapers = build_scrapers(config)
    if not scrapers:
        logger.error("No enabled scrapers found.")
        return []

    all_jobs: list[Job] = []
    seen_urls: set[str] = set()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(s.scrape_all): s.name for s in scrapers}
        for future in as_completed(futures):
            board_name = futures[future]
            try:
                jobs = future.result()
                new = [j for j in jobs if j.url not in seen_urls]
                seen_urls.update(j.url for j in new)
                all_jobs.extend(new)
                logger.info("[%s] found %d new jobs", board_name, len(new))
            except Exception as e:
                logger.error("[%s] scraper raised: %s", board_name, e)

    # Sort newest first
    all_jobs.sort(key=lambda j: j.posted_at or __import__("datetime").datetime.min, reverse=True)
    return all_jobs
