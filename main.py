#!/usr/bin/env python3
"""
Job Search Agent — entry point.

Usage:
    python main.py                    # use config.yaml in cwd
    python main.py --config my.yaml   # custom config
    python main.py --stats            # show DB stats and exit
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from config import load_config
from models import Application, ApplicationStatus
from scrapers.factory import run_scrapers
from applicator.engine import ApplicationEngine

console = Console()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def print_jobs_table(jobs) -> None:
    t = Table(title=f"Jobs found: {len(jobs)}", show_lines=True)
    t.add_column("#", style="dim", width=4)
    t.add_column("Title", min_width=28)
    t.add_column("Company", min_width=20)
    t.add_column("Location", min_width=16)
    t.add_column("Source", style="cyan", width=14)
    t.add_column("Posted", width=14)
    for i, job in enumerate(jobs, 1):
        age = f"{job.age_hours:.1f}h ago" if job.age_hours is not None else "unknown"
        t.add_row(str(i), job.title, job.company, job.location, job.source, age)
    console.print(t)


def print_results_table(applications) -> None:
    t = Table(title="Results", show_lines=True)
    t.add_column("Company", min_width=20)
    t.add_column("Title", min_width=28)
    t.add_column("Score", justify="center", width=7)
    t.add_column("Status", width=10)
    t.add_column("Notes", min_width=24)

    status_styles = {
        ApplicationStatus.PENDING: "green",
        ApplicationStatus.SKIPPED: "yellow",
    }
    for app in applications:
        style = status_styles.get(app.status, "")
        t.add_row(
            app.job.company,
            app.job.title,
            str(app.analysis.match_score),
            f"[{style}]{app.status.value}[/{style}]",
            app.notes[:60],
        )
    console.print(t)


_CSV_FIELDS = [
    "title", "company", "location", "source",
    "url", "posted_at", "work_type",
    "match_score", "status", "notes",
]


def write_csv(applications, path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for app in applications:
            job = app.job
            writer.writerow({
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "source": job.source,
                "url": job.url,
                "posted_at": job.posted_at.isoformat() if job.posted_at else "",
                "work_type": job.work_type.value if job.work_type else "",
                "match_score": app.analysis.match_score,
                "status": app.status.value,
                "notes": app.notes,
            })


def main() -> None:
    parser = argparse.ArgumentParser(description="Job search agent")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--stats", action="store_true", help="Show DB stats and exit")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.output.log_level)

    if args.stats:
        from applicator.tracker import ApplicationTracker
        tracker = ApplicationTracker(config.output.db_path)
        stats = tracker.get_stats()
        tracker.close()
        console.print("[bold]Application stats:[/bold]", stats)
        return

    for role in config.roles:
        if not role.resume_path.exists():
            console.print(f"[red]Resume not found:[/red] {role.resume_path} (role: {role.title})")
            sys.exit(1)

    console.rule("[bold blue]Job Search Agent")
    for role in config.roles:
        console.print(f"Role:      [bold]{role.title}[/bold] ({', '.join(role.level)})  resume: {role.resume_path}")
    console.print(f"Locations: {', '.join(str(l) for l in config.locations)}")
    console.print(f"Work type: {', '.join(wt.value for wt in config.work_types)}")
    console.print(f"Emp. type: {', '.join(et.value for et in config.application.employment_types)}")
    console.print(f"Boards:    {', '.join(b.name for b in config.enabled_boards)}")
    console.print(f"Window:    {config.application.posted_within_hours}h")
    console.rule()

    from llm.client import OllamaClient
    llm = OllamaClient(config.llm.base_url, config.llm.model)
    if not llm.health_check():
        console.print(f"[red]Ollama is not reachable at {config.llm.base_url}[/red]")
        console.print("Start it with: [bold]ollama serve[/bold]")
        sys.exit(1)
    if not llm.model_available():
        console.print(f"[yellow]Model {config.llm.model} may not be pulled.[/yellow]")
        console.print(f"Run: [bold]ollama pull {config.llm.model}[/bold]")

    # Scrape and score per role; deduplicate by URL keeping the highest-scoring result.
    best: dict[str, Application] = {}
    for role in config.roles:
        console.print(f"\n[bold]Scraping for: {role.title}...[/bold]")
        jobs = run_scrapers(config, role)

        if not jobs:
            console.print(f"[yellow]No jobs found for '{role.title}' within the time window.[/yellow]")
            continue

        print_jobs_table(jobs)

        console.print(f"\n[bold]Scoring '{role.title}' jobs — selecting top {config.application.max_jobs} by match score...[/bold]\n")
        engine = ApplicationEngine(config, role)
        try:
            applications = engine.run(jobs)
        finally:
            engine.close()

        for app in applications:
            url = app.job.url
            if url not in best or app.analysis.match_score > best[url].analysis.match_score:
                best[url] = app

    if not best:
        console.print("[yellow]No jobs found across all roles within the time window.[/yellow]")
        console.print("Try increasing [bold]posted_within_hours[/bold] in config.yaml.")
        return

    applications = sorted(best.values(), key=lambda a: a.analysis.match_score, reverse=True)
    print_results_table(applications)

    if config.output.csv_path:
        write_csv(applications, config.output.csv_path)
        console.print(f"CSV saved → [bold]{config.output.csv_path}[/bold]")

    tracked = sum(1 for a in applications if a.status == ApplicationStatus.PENDING)
    console.print(f"\n[bold green]Done — {tracked} job(s) ready for review.[/bold green]")


if __name__ == "__main__":
    main()
