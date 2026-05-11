from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from config import Config
from llm.agents import CoverLetterWriter, JobAnalyzer
from llm.client import OllamaClient
from models import Application, ApplicationStatus, Job
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
        results: list[Application] = []

        logger.info("Evaluating %d candidate jobs (target: %d)", len(jobs), cfg.max_jobs)

        for job in jobs[:cfg.max_jobs]:
            if self.tracker.already_tracked(job.url):
                logger.debug("Already applied: %s @ %s — skipping", job.title, job.company)
                continue

            logger.info("Analyzing: %s @ %s", job.title, job.company)
            analysis = self.analyzer.analyze(job)
            logger.info("  Score: %d | %s", analysis.match_score, job.company)

            if analysis.match_score < cfg.min_match_score:
                logger.info("  Score below threshold (%d) — skipping", cfg.min_match_score)
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
            logger.info("  Tracked: %s @ %s", job.title, job.company)

        stats = self.tracker.get_stats()
        logger.info("Session complete — stats: %s", stats)
        return results

    def close(self) -> None:
        self.tracker.close()
