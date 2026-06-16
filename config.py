from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from models import EmploymentType, Location, WorkType


@dataclass
class RoleConfig:
    title: str
    level: list[str]
    resume_path: Path = field(default_factory=lambda: Path("resume/resume.pdf"))
    keywords: list[str] = field(default_factory=list)
    years_experience: float = 3.0
    experience_std_dev: float = 2.0


@dataclass
class JobBoardConfig:
    name: str
    enabled: bool = True
    board_tokens: list[str] = field(default_factory=list)


@dataclass
class SalaryConfig:
    min: Optional[int] = None           # minimum acceptable yearly salary
    max: Optional[int] = None           # maximum (optional upper bound)
    currency: str = "USD"
    skip_if_below_min: bool = True      # drop jobs whose listed salary is below min
    include_unlisted: bool = True       # include jobs with no salary info


@dataclass
class ApplicationConfig:
    max_jobs: int = 10
    max_jobs_per_location: int = 25
    posted_within_hours: float = 1.0
    min_match_score: int = 65
    cover_letter: bool = True
    employment_types: list[EmploymentType] = field(default_factory=lambda: [EmploymentType.FULL_TIME])
    salary: SalaryConfig = None
    company_blacklist: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.salary is None:
            self.salary = SalaryConfig()


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "qwen2.5:14b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.3
    context_window: int = 8192


@dataclass
class ContactConfig:
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""


@dataclass
class OutputConfig:
    log_level: str = "INFO"
    db_path: str = "applications/tracker.db"
    results_dir: str = "applications"
    csv_path: str = "applications/jobs.csv"
    show_browser: bool = False
    sessions_dir: str = ".sessions"


@dataclass
class Config:
    roles: list[RoleConfig]
    job_boards: list[JobBoardConfig]
    locations: list[Location]
    work_types: list[WorkType]
    application: ApplicationConfig
    llm: LLMConfig
    contact: ContactConfig
    output: OutputConfig

    @property
    def work_type(self) -> WorkType:
        return self.work_types[0]

    @property
    def enabled_boards(self) -> list[JobBoardConfig]:
        return [b for b in self.job_boards if b.enabled]


def _parse_role(role_raw: dict, default_resume: str | None = None) -> RoleConfig:
    level = role_raw.get("level", [])
    if isinstance(level, str):
        level = [level]
    resume_val = role_raw.get("resume", default_resume) or "resume/resume.pdf"
    return RoleConfig(
        title=role_raw["title"],
        level=level,
        resume_path=Path(resume_val),
        keywords=role_raw.get("keywords", []),
        years_experience=float(role_raw.get("years_experience", 3.0)),
        experience_std_dev=float(role_raw.get("experience_std_dev", 2.0)),
    )


def load_config(path: str = "config.yaml") -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    # Support both new `roles:` list and legacy `role:` + top-level `resume:`
    if "roles" in raw:
        roles = [_parse_role(r) for r in raw["roles"]]
    else:
        default_resume = (raw.get("resume") or {}).get("path")
        roles = [_parse_role(raw["role"], default_resume=default_resume)]

    boards = [
        JobBoardConfig(
            name=b["name"],
            enabled=b.get("enabled", True),
            board_tokens=b.get("board_tokens", []),
        )
        for b in raw.get("job_boards", [])
    ]

    # location may be a list (top-level) or a single dict under the key
    loc_raw = raw.get("location", [])
    if isinstance(loc_raw, dict):
        loc_raw = [loc_raw]
    locations = [
        Location(
            city=loc["city"],
            state=loc.get("state", ""),
            country=loc.get("country", "US"),
            radius_miles=loc.get("radius_miles", 25),
        )
        for loc in loc_raw
    ]

    wt_raw = raw.get("work_type", "hybrid")
    if isinstance(wt_raw, list):
        work_types = [WorkType(w) for w in wt_raw]
    else:
        work_types = [WorkType(wt_raw)]

    app_raw = raw.get("application", {})
    sal_raw = app_raw.get("salary", {}) or {}
    salary = SalaryConfig(
        min=sal_raw.get("min"),
        max=sal_raw.get("max"),
        currency=sal_raw.get("currency", "USD"),
        skip_if_below_min=sal_raw.get("skip_if_below_min", True),
        include_unlisted=sal_raw.get("include_unlisted", True),
    )
    et_raw = app_raw.get("employment_type", ["full-time"])
    if isinstance(et_raw, str):
        et_raw = [et_raw]
    employment_types = [EmploymentType(e) for e in et_raw]

    application = ApplicationConfig(
        max_jobs=app_raw.get("max_jobs", 10),
        max_jobs_per_location=app_raw.get("max_jobs_per_location", 25),
        posted_within_hours=app_raw.get("posted_within_hours", 1.0),
        min_match_score=app_raw.get("min_match_score", 65),
        cover_letter=app_raw.get("cover_letter", True),
        employment_types=employment_types,
        salary=salary,
        company_blacklist=[c.lower() for c in app_raw.get("company_blacklist", [])],
    )

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        provider=llm_raw.get("provider", "ollama"),
        model=llm_raw.get("model", "qwen2.5:14b"),
        base_url=llm_raw.get("base_url", "http://localhost:11434"),
        temperature=llm_raw.get("temperature", 0.3),
        context_window=llm_raw.get("context_window", 8192),
    )

    contact_raw = raw.get("contact", {})
    contact = ContactConfig(
        name=contact_raw.get("name", ""),
        email=contact_raw.get("email", ""),
        phone=contact_raw.get("phone", ""),
        linkedin_url=contact_raw.get("linkedin_url", ""),
        github_url=contact_raw.get("github_url", ""),
        portfolio_url=contact_raw.get("portfolio_url", ""),
    )

    out_raw = raw.get("output", {})
    output = OutputConfig(
        log_level=out_raw.get("log_level", "INFO"),
        db_path=out_raw.get("db_path", "applications/tracker.db"),
        results_dir=out_raw.get("results_dir", "applications"),
        csv_path=out_raw.get("csv_path", "applications/jobs.csv"),
        show_browser=out_raw.get("show_browser", False),
        sessions_dir=out_raw.get("sessions_dir", ".sessions"),
    )

    return Config(
        roles=roles,
        job_boards=boards,
        locations=locations,
        work_types=work_types,
        application=application,
        llm=llm,
        contact=contact,
        output=output,
    )
