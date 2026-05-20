ANALYZE_JOB = """You are a career coach helping a job seeker evaluate a job posting.

RESUME:
{resume}

JOB POSTING:
Title: {job_title}
Company: {company}
Description:
{description}

Evaluate how well this candidate's resume matches the job posting.

Respond with valid JSON only, no markdown, no extra text:
{{
  "match_score": <integer 0-100>,
  "match_reasons": [<up to 4 short strings explaining strong matches>],
  "gap_reasons": [<up to 3 short strings explaining gaps or concerns>],
  "recommendation": "<apply | skip>",
  "summary": "<one sentence overall assessment>"
}}
"""

COVER_LETTER = """You are an expert career coach writing a concise, compelling cover letter.

RESUME:
{resume}

JOB POSTING:
Title: {job_title}
Company: {company}
Description:
{description}

CANDIDATE CONTACT:
Name: {name}
Email: {email}

Write a 3-paragraph cover letter (opening hook, relevant experience, closing call-to-action).
Use a confident, professional tone. Do NOT use generic filler phrases like "I am writing to express".
Address it to "Hiring Manager" if no contact name is given.
Output only the letter text, no subject line or metadata.
"""


RESUME_SUMMARY = """Summarize the following resume in under 200 words, focusing on:
- Years of experience and seniority level
- Primary programming languages and frameworks
- Types of systems built (web, data, infra, mobile, etc.)
- Largest scale or most impressive achievement
- Industry domains

RESUME:
{resume}

Output only the summary, no headers.
"""
