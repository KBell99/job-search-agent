from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from pathlib import Path

from config import Config
from llm.agents import CoverLetterWriter, JobAnalyzer
from llm.client import OllamaClient
from models import AnalysisResult, Application, ApplicationStatus, Job
from applicator.tracker import ApplicationTracker

logger = logging.getLogger(__name__)


def load_resume(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                return "\n".join(
                    page.extract_text() or "" for page in pdf.pages
                ).strip()
        except ImportError:
            raise RuntimeError("pdfplumber is required for PDF resumes: pip install pdfplumber")
    return path.read_text(encoding="utf-8")


def save_cover_letter(job: Job, text: str, output_dir: str) -> Path:
    safe_company = "".join(c if c.isalnum() else "_" for c in job.company)
    fname = f"cover_{safe_company}_{job.source}.txt"
    out = Path(output_dir) / fname
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


class ApplicationEngine:
    def __init__(self, config: Config):
        self.config = config
        self.tracker = ApplicationTracker(config.output.db_path)

        self.llm = OllamaClient(
            base_url=config.llm.base_url,
            model=config.llm.model,
            temperature=config.llm.temperature,
        )

        self.resume_text = load_resume(config.resume_path)
        logger.info("Resume loaded (%d chars)", len(self.resume_text))

        self.analyzer = JobAnalyzer(self.llm, self.resume_text)

        self.cl_writer = (
            CoverLetterWriter(self.llm, self.resume_text, config)
            if config.application.cover_letter
            else None
        )

    def run(self, jobs: list[Job]) -> list[Application]:
        cfg = self.config.application

        # Pre-filter before expensive LLM scoring
        jobs = [j for j in jobs if self._salary_ok(j) and self._company_ok(j)]
        logger.info("Scoring %d jobs after pre-filters...", len(jobs))
        scored: list[tuple[AnalysisResult, Job]] = []
        for job in jobs:
            if self.tracker.already_tracked(job.url):
                logger.debug("Already tracked: %s @ %s — skipping", job.title, job.company)
                continue
            logger.info("Analyzing: %s @ %s", job.title, job.company)
            analysis = self.analyzer.analyze(job)
            analysis.match_score = self._adjust_score(analysis.match_score, job)
            logger.info("  Score: %d", analysis.match_score)
            scored.append((analysis, job))

        # Select top N by score
        scored.sort(key=lambda x: x[0].match_score, reverse=True)
        top = scored[:cfg.max_jobs]
        logger.info("Top %d of %d jobs selected (min score: %d)", len(top), len(scored), cfg.min_match_score)

        results: list[Application] = []
        for analysis, job in top:
            if analysis.match_score < cfg.min_match_score:
                logger.info("  Skipping %s @ %s (score %d below threshold)", job.title, job.company, analysis.match_score)
                app = Application(
                    job=job,
                    analysis=analysis,
                    status=ApplicationStatus.SKIPPED,
                    notes=f"score {analysis.match_score} < threshold {cfg.min_match_score}",
                )
                self.tracker.upsert(app)
                results.append(app)
                continue

            cl_path = None
            if self.cl_writer is not None:
                cover_letter = self.cl_writer.write(job)
                analysis.cover_letter = cover_letter
                cl_path = save_cover_letter(job, cover_letter, self.config.output.results_dir)
            else:
                logger.info("  Cover letter generation disabled — skipping")

            app = Application(
                job=job,
                analysis=analysis,
                notes="ready for manual review",
                cover_letter_path=str(cl_path) if cl_path else None,
            )
            self.tracker.upsert(app)
            results.append(app)
            logger.info("  Tracked: %s @ %s (score %d)", job.title, job.company, analysis.match_score)

        stats = self.tracker.get_stats()

        logger.info("Session complete — stats: %s", stats)
        return results

    def _salary_ok(self, job: Job) -> bool:
        sal_cfg = self.config.application.salary
        if sal_cfg.min is None and sal_cfg.max is None:
            return True

        if not job.salary:
            return sal_cfg.include_unlisted

        # Parse the first dollar figure out of the salary string
        import re
        figures = [int(n.replace(",", "")) for n in re.findall(r"\$?([\d,]+)", job.salary)]
        if not figures:
            return sal_cfg.include_unlisted

        # Normalise hourly to annual (assume 2080 hrs/yr)
        low = figures[0]
        if "hour" in job.salary.lower() and low < 1000:
            low = low * 2080

        if sal_cfg.skip_if_below_min and sal_cfg.min and low < sal_cfg.min:
            logger.info("  Salary filter: %s @ %s — $%s below min $%s",
                        job.title, job.company, f"{low:,}", f"{sal_cfg.min:,}")
            return False
        if sal_cfg.max and low > sal_cfg.max:
            logger.info("  Salary filter: %s @ %s — $%s above max $%s",
                        job.title, job.company, f"{low:,}", f"{sal_cfg.max:,}")
            return False
        return True

    def _company_ok(self, job: Job) -> bool:
        blacklist = self.config.application.company_blacklist
        if not blacklist:
            return True
        company_lower = job.company.lower()
        blocked = any(entry in company_lower for entry in blacklist)
        if blocked:
            logger.info("  Blacklisted company: %s — skipping", job.company)
        return not blocked

    def _adjust_score(self, score: int, job: Job) -> int:
        adjustment = 0

        # Seniority: senior is the sweet spot; staff/principal overshoot; junior undershoots
        title = job.title.lower()
        if re.search(r"\b(staff|principal|distinguished|fellow|director|vp)\b", title):
            adjustment -= 10
        elif re.search(r"\b(senior|sr\.?)\b", title):
            adjustment += 8
        elif re.search(r"\b(junior|jr\.?|entry.?level|associate)\b", title):
            adjustment -= 12

        # Experience proximity: Gaussian weight centred on configured target years.
        # Scans all mentions and takes the maximum so a "7+ years total experience"
        # requirement isn't hidden by lower per-skill mentions earlier in the text.
        # For ranges (e.g. "5-7 years") the max end is used.
        desc = (job.description or "").lower()
        years_mentioned: list[float] = []
        for lo, hi in re.findall(r"(\d+)\s*(?:to|-)\s*(\d+)\s*years?", desc):
            years_mentioned.append(float(hi))
        for n in re.findall(r"(\d+)\+\s*years?", desc):
            years_mentioned.append(float(n))
        for n in re.findall(r"(\d+)\s*years?\s+(?:of\s+)?(?:[\w-]+\s+){0,2}(?:experience|exp)", desc):
            years_mentioned.append(float(n))
        if years_mentioned:
            required = max(years_mentioned)
            target = self.config.role.years_experience
            sigma = self.config.role.experience_std_dev
            gaussian = math.exp(-0.5 * ((required - target) / sigma) ** 2)
            exp_adj = round((gaussian - 1.0) * 20)  # 0 at peak, up to -20 far away
            adjustment += exp_adj
            logger.debug("  Exp adjustment: required=%.1f yrs (max of %s), target=%.1f, sigma=%.1f, gaussian=%.3f, adj=%+d",
                         required, years_mentioned, target, sigma, gaussian, exp_adj)

        raw = score + adjustment
        logger.debug("  Score adjustment for '%s': %+d → %d", job.title, adjustment, max(0, min(100, raw)))
        return max(0, min(100, raw))

    def close(self) -> None:
        self.tracker.close()
