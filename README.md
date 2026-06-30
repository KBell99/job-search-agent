# Job Search Agent

Automated job search pipeline that scrapes multiple job boards, scores listings against your resume using a local LLM, and generates tailored cover letters — all from a single config file.

## How It Works

1. **Scrape** — enabled job boards are queried in parallel for each configured role and location
2. **Filter** — results are deduplicated and pre-filtered by salary, employment type, and company blacklist
3. **Score** — a local LLM (Ollama) compares each job description to your resume and returns a 0–100 match score; seniority level and years-of-experience are used to adjust the score further
4. **Generate** — jobs above the minimum score get a tailored cover letter written to `applications/`
5. **Track** — every result is persisted to a SQLite database so already-seen jobs are skipped on future runs

## Project Structure

```
job-search-agent/
├── main.py                  # Entry point — CLI arg parsing, orchestration, output tables
├── config.py                # Config dataclasses and YAML loader
├── config.template.yaml     # Annotated template — copy to config.yaml to get started
├── models.py                # Shared dataclasses: Job, AnalysisResult, Application, enums
│
├── scrapers/
│   ├── base.py              # BaseScraper ABC — shared filtering, pagination helpers
│   ├── factory.py           # Builds enabled scrapers and runs them in a ThreadPoolExecutor
│   ├── linkedin.py          # LinkedIn guest API
│   ├── wellfound.py         # Wellfound API (falls back to Playwright on 403)
│   ├── builtin.py           # Built In scraper (Playwright fallback on empty response)
│   ├── greenhouse.py        # Greenhouse public API — one request per board_token slug
│   ├── remotive.py          # Remotive free API (remote-only)
│   ├── weworkremotely.py    # We Work Remotely RSS feed (remote-only)
│   ├── arbeitnow.py         # Arbeitnow free API (EU + global)
│   ├── roberthalf.py        # Robert Half public listings
│   └── workatastartup.py    # Work at a Startup (YC)
│
├── llm/
│   ├── client.py            # OllamaClient — generate() and generate_json() wrappers
│   ├── agents.py            # JobAnalyzer and CoverLetterWriter agents
│   └── prompts.py           # Prompt templates (ANALYZE_JOB, COVER_LETTER, RESUME_SUMMARY)
│
├── applicator/
│   ├── engine.py            # ApplicationEngine — pre-filters, LLM scoring loop, score adjustment
│   └── tracker.py           # ApplicationTracker — SQLite persistence, deduplication by URL
│
├── applications/            # Output directory (gitignored)
│   ├── tracker.db           # SQLite database of all seen applications
│   ├── jobs.csv             # Latest run results as CSV
│   └── cover_*.txt          # Generated cover letters, one per qualifying job
│
├── resume/                  # Place your resume here (gitignored)
└── .sessions/               # Playwright browser session files (gitignored)
```

## Setup

### 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium      # only needed if using Wellfound or Built In
```

### 2. Install and start Ollama

```bash
# https://ollama.com
ollama pull qwen2.5:7b           # or qwen2.5:14b / qwen2.5:32b for better quality
ollama serve
```

### 3. Configure

```bash
cp config.template.yaml config.yaml
```

Edit `config.yaml`:
- Add your resume path under `roles[].resume`
- Fill in your contact details under `contact:`
- Enable/disable job boards under `job_boards:`
- Set your target location(s) and work type

### 4. Add your resume

Place your `.pdf` or `.txt` resume at the path specified in `config.yaml` (e.g. `resume/MyResume.pdf`). Resumes are gitignored.

## Usage

```bash
# Run a full search
python main.py

# Use a custom config file
python main.py --config my-other-config.yaml

# Show application database stats and exit
python main.py --stats
```

Output is printed to the terminal as a rich table. Qualifying jobs are saved to `applications/tracker.db` and `applications/jobs.csv`. Cover letters are written to `applications/cover_<Company>_<source>.txt`.

## Configuration Reference

| Section | Key | Description |
|---|---|---|
| `roles[]` | `title` | Search term sent to each job board |
| | `level` | `junior` \| `mid` \| `senior` \| `staff` \| `principal` — one or more |
| | `years_experience` | Target years; score peaks here |
| | `experience_std_dev` | Each sigma away from target reduces score by ~40% |
| | `keywords` | Extra terms used to filter/score results |
| | `resume` | Path to `.pdf` or `.txt` resume, relative to project root |
| `job_boards[]` | `name` | Board identifier (see scraper list above) |
| | `enabled` | `true` / `false` |
| | `board_tokens` | Greenhouse only — company slugs from `boards.greenhouse.io/<slug>` |
| `location[]` | `city`, `state`, `country` | Search location |
| | `radius_miles` | Search radius |
| `work_type` | | `remote` \| `hybrid` \| `onsite` — one or more |
| `application` | `max_jobs` | Max jobs to score per run (across all locations/roles) |
| | `max_jobs_per_location` | Max jobs fetched per location per board |
| | `posted_within_hours` | Only consider jobs posted within this window |
| | `min_match_score` | 0–100; jobs below this are saved as `skipped` |
| | `cover_letter` | `true` to generate cover letters |
| | `employment_type` | `full-time` \| `part-time` \| `contract` \| `temporary` |
| | `salary.min` / `.max` | Yearly salary bounds (USD). Leave blank for no limit |
| | `salary.skip_if_below_min` | Drop jobs whose listed salary is below `min` |
| | `salary.include_unlisted` | Include jobs with no salary listed |
| | `company_blacklist` | Case-insensitive substrings — matching companies are skipped |
| `llm` | `model` | Ollama model name, e.g. `qwen2.5:7b` |
| | `base_url` | Ollama server URL (default `http://localhost:11434`) |
| | `temperature` | Lower = more consistent output (default `0.3`) |
| `contact` | | Name, email, phone, LinkedIn/GitHub/portfolio URLs for cover letters |
| `output` | `log_level` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| | `show_browser` | `true` to watch Playwright scraping in a visible window |

## Scoring Details

Each job receives a base 0–100 match score from the LLM, then two rule-based adjustments are applied:

- **Seniority match** — +8 if the job title's level is within your configured range; −10 per level outside (capped at −20)
- **Experience proximity** — Gaussian penalty centred on `years_experience`; each standard deviation away reduces the score by ~40%, up to −20 total

Jobs are ranked by final score and only the top `max_jobs` are sent for cover letter generation. Jobs below `min_match_score` are recorded as `skipped` in the database and will not be re-evaluated on subsequent runs.

## Multi-Role Search

You can define multiple roles in `config.yaml`. Each role is searched and scored independently using its own resume. Results are merged and deduplicated by URL — if the same posting appears under multiple roles, the highest score wins.

```yaml
roles:
  - title: "Software Engineer"
    level: ["mid", "senior"]
    resume: "resume/SWE.pdf"
    keywords: ["Python", "backend"]

  - title: "Data Engineer"
    level: ["mid"]
    resume: "resume/DE.pdf"
    keywords: ["Python", "Spark"]
```

## Database

All results are stored in `applications/tracker.db` (SQLite). The schema has one `applications` table — jobs are keyed by URL, so re-running after increasing `posted_within_hours` will skip any URL already in the database.

To inspect the database:

```bash
sqlite3 applications/tracker.db "SELECT title, company, match_score, status FROM applications ORDER BY match_score DESC;"
```

To reset and start fresh:

```bash
rm applications/tracker.db applications/jobs.csv
```
