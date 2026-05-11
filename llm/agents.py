from __future__ import annotations

import logging

from config import Config
from llm.client import OllamaClient
from llm.prompts import ANALYZE_JOB, COVER_LETTER, RESUME_SUMMARY
from models import AnalysisResult, Job

logger = logging.getLogger(__name__)


class JobAnalyzer:
    def __init__(self, client: OllamaClient, resume: str):
        self.client = client
        self.resume = resume
        self._resume_summary: str | None = None

    def resume_summary(self) -> str:
        if self._resume_summary is None:
            prompt = RESUME_SUMMARY.format(resume=self.resume)
            self._resume_summary = self.client.generate(prompt)
        return self._resume_summary

    def analyze(self, job: Job) -> AnalysisResult:
        prompt = ANALYZE_JOB.format(
            resume=self.resume,
            job_title=job.title,
            company=job.company,
            location=job.location,
            description=job.description[:4000],  # guard context window
        )
        try:
            data = self.client.generate_json(prompt)
            return AnalysisResult(
                job=job,
                match_score=int(data.get("match_score", 0)),
                match_reasons=data.get("match_reasons", []),
                gap_reasons=data.get("gap_reasons", []),
            )
        except Exception as e:
            logger.warning("Analysis failed for %s @ %s: %s", job.title, job.company, e)
            return AnalysisResult(job=job, match_score=0)


class CoverLetterWriter:
    def __init__(self, client: OllamaClient, resume: str, config: Config):
        self.client = client
        self.resume = resume
        self.config = config

    def write(self, job: Job) -> str:
        prompt = COVER_LETTER.format(
            resume=self.resume,
            job_title=job.title,
            company=job.company,
            description=job.description[:3000],
            name=self.config.contact.name,
            email=self.config.contact.email,
        )
        try:
            return self.client.generate(prompt)
        except Exception as e:
            logger.warning("Cover letter generation failed for %s: %s", job.company, e)
            return ""
