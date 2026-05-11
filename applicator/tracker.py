from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from models import Application, ApplicationStatus, Job


_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT,
    url             TEXT NOT NULL UNIQUE,
    source          TEXT,
    posted_at       TEXT,
    match_score     INTEGER,
    match_reasons   TEXT,
    gap_reasons     TEXT,
    cover_letter    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ApplicationTracker:
    def __init__(self, db_path: str = "applications/tracker.db"):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def already_tracked(self, url: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM applications WHERE url = ?", (url,)
        )
        return cur.fetchone() is not None

    def upsert(self, app: Application) -> None:
        job = app.job
        analysis = app.analysis
        self.conn.execute(
            """
            INSERT INTO applications
                (job_id, title, company, location, url, source, posted_at,
                 match_score, match_reasons, gap_reasons, cover_letter,
                 status, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
                match_score   = excluded.match_score,
                match_reasons = excluded.match_reasons,
                gap_reasons   = excluded.gap_reasons,
                cover_letter  = excluded.cover_letter,
                status        = excluded.status,
                notes         = excluded.notes
            """,
            (
                job.job_id,
                job.title,
                job.company,
                job.location,
                job.url,
                job.source,
                job.posted_at.isoformat() if job.posted_at else None,
                analysis.match_score,
                json.dumps(analysis.match_reasons),
                json.dumps(analysis.gap_reasons),
                analysis.cover_letter,
                app.status.value,
                app.notes,
            ),
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        cur = self.conn.execute(
            "SELECT status, COUNT(*) as n FROM applications GROUP BY status"
        )
        return {row["status"]: row["n"] for row in cur.fetchall()}

    def close(self) -> None:
        self.conn.close()
