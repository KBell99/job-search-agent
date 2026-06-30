from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config import Config, RoleConfig
from models import Job
from scrapers.base import BaseScraper
from scrapers.arbeitnow import ArbeitnowScraper
from scrapers.builtin import BuiltinScraper
from scrapers.linkedin import LinkedInScraper
from scrapers.remotive import RemotiveScraper
from scrapers.wellfound import WellfoundScraper
from scrapers.weworkremotely import WeWorkRemotelyScraper
from scrapers.workatastartup import WorkAtAStartupScraper
from scrapers.roberthalf import RobertHalfScraper
from scrapers.greenhouse import GreenhouseScraper

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseScraper]] = {
    "linkedin": LinkedInScraper,
    "wellfound": WellfoundScraper,
    "builtin": BuiltinScraper,
    "remotive": RemotiveScraper,
    "weworkremotely": WeWorkRemotelyScraper,
    "arbeitnow": ArbeitnowScraper,
    "workatastartup": WorkAtAStartupScraper,
    "roberthalf": RobertHalfScraper,
    "greenhouse": GreenhouseScraper,
}


def build_scrapers(config: Config, role: RoleConfig) -> list[BaseScraper]:
    scrapers = []
    for board in config.enabled_boards:
        cls = _REGISTRY.get(board.name)
        if cls is None:
            logger.warning("Unknown job board: %s — skipping", board.name)
            continue
        scrapers.append(cls(config, role))
    return scrapers


def run_scrapers(config: Config, role: RoleConfig, max_workers: int = 4) -> list[Job]:
    """Run all enabled scrapers for a given role and return deduplicated jobs."""
    scrapers = build_scrapers(config, role)
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

    # Sort newest first; strip tzinfo so aware and naive datetimes compare cleanly
    def _sort_key(j: Job) -> datetime:
        dt = j.posted_at
        if dt is None:
            return datetime.min
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    all_jobs.sort(key=_sort_key, reverse=True)
    return all_jobs
