"""Create a demo database with FICTIONAL data for screenshots and trying the app.

Run from the project folder:
    .venv\\Scripts\\python.exe demo\\seed_demo.py
Then start the app on the demo data:
    $env:JOBPORTAL_STORAGE = "demo\\storage"; .venv\\Scripts\\python.exe server.py

- "Alex Sample" and every company here are made up.
- The Fabrikam Stores job holds a REAL result from the Claude API (fit check, CV, cover letter)
  produced from the fictional resume during testing.
- The other jobs are hand-written sample entries to fill the dashboard; their scores are illustrative.
The demo database lives in demo/storage/, never in your real storage/ folder.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORAGE = HERE / "storage"
os.environ["JOBPORTAL_STORAGE"] = str(STORAGE)
sys.path.insert(0, str(HERE.parent))

import server  # noqa: E402  (reads JOBPORTAL_STORAGE on import)

RESUME = """Alex Sample
Data Analyst | Example City | alex@example.com | 000-000-0000

SUMMARY
Data analyst with 3 years of experience turning retail sales data into reports and dashboards.

EXPERIENCE
Data Analyst, Northwind Retail (fictional) - 2023 to present
- Built weekly sales dashboards in Power BI used by 12 store managers.
- Wrote SQL queries in PostgreSQL to track sales, returns and stock levels.
- Cleaned and joined data with Python (pandas); cut report prep time from 6 hours to 1 hour.

Junior Analyst, Contoso Shop (fictional) - 2021 to 2023
- Maintained Excel reports with pivot tables and VLOOKUP for the finance team.
- Answered ad-hoc data questions from marketing.

SKILLS
SQL, PostgreSQL, Python, pandas, Excel, Power BI, data cleaning, dashboards

EDUCATION
BSc Statistics, Example University, 2021
"""

DAY = 86400


def sample(title, company, location, fit, chance, label, verdict, matched, missing, status, days_ago, applied_days_ago=None):
    analysis = {
        "is_job_posting": True, "job_title": title, "company": company, "location": location,
        "fit_score": fit, "interview_chance": chance, "chance_label": label, "verdict": verdict,
        "skill_match": min(100, fit + 4), "experience_match": max(0, fit - 6), "education_match": 90, "keyword_match": max(0, fit - 10),
        "matched_skills": matched, "missing_skills": missing,
        "strengths": [f"Hands-on experience with {matched[0]} and {matched[1]}.", "Measurable results on reporting work."],
        "gaps": [f"No evidence of {missing[0]} in the resume."],
        "improvements": [f"Highlight any real exposure to {missing[0]}.", "Move the strongest dashboard result to the top."],
        "ats_keywords": matched + missing,
    }
    jd = (f"{title} - {company} (fictional sample posting), {location}.\n"
          f"Requirements: {', '.join(matched + missing)}.\n"
          "This is sample text used only to demonstrate the dashboard.")
    created = time.time() - days_ago * DAY
    applied = time.strftime("%Y-%m-%d", time.localtime(time.time() - applied_days_ago * DAY)) if applied_days_ago is not None else ""
    return dict(title=title, company=company, location=location, url="", job_description=jd, fit_score=fit,
                interview_chance=chance, analysis=json.dumps(analysis), cv="", cover_letter="", doc_notes="[]",
                status=status, applied_on=applied, notes="", created_at=created, updated_at=created)


def main():
    db_file = STORAGE / "jobportal.db"
    if db_file.exists():
        db_file.unlink()  # demo data only - always rebuilt from scratch
    server.init_db()
    server.set_setting("resume", RESUME)
    server.set_setting("resume_updated", time.strftime("%Y-%m-%dT%H:%M"))

    real = json.loads((HERE / "fabrikam_data_analyst.json").read_text(encoding="utf-8"))
    real.pop("id", None)
    real.update(status="Interview", applied_on=time.strftime("%Y-%m-%d", time.localtime(time.time() - 9 * DAY)),
                notes="Video call with the analytics lead", created_at=time.time() - 12 * DAY, updated_at=time.time() - 2 * DAY)

    rows = [
        real,
        sample("BI Analyst", "Tailspin Toys", "Hybrid", 74, 52, "Moderate",
               "Strong Power BI and SQL match; the role also wants DAX modelling, which the resume does not show yet.",
               ["Power BI", "SQL", "Excel", "Dashboards"], ["DAX", "Azure"], "Applied", 6, 5),
        sample("Junior Data Scientist", "Woodgrove Labs", "Remote", 48, 22, "Low",
               "Good statistics and Python base, but the role centres on machine learning in production, which is missing.",
               ["Python", "pandas", "Statistics"], ["Machine learning", "scikit-learn", "MLOps"], "Saved", 3),
        sample("Reporting Analyst", "Litware Retail", "On-site", 86, 64, "Strong",
               "Very close match: retail reporting, SQL and Excel are exactly what the role asks for.",
               ["SQL", "Excel", "Retail", "Reporting"], ["SSRS"], "Offer", 20, 18),
        sample("Marketing Analyst", "Adatum Media", "Remote", 58, 30, "Low",
               "Solid analytics skills, but the role needs Google Analytics and campaign attribution experience.",
               ["SQL", "Python", "Dashboards"], ["Google Analytics", "Attribution"], "Rejected", 16, 15),
    ]
    with server.db() as conn:
        cols = ["title", "company", "location", "url", "job_description", "fit_score", "interview_chance", "analysis",
                "cv", "cover_letter", "doc_notes", "status", "applied_on", "notes", "created_at", "updated_at"]
        for r in rows:
            conn.execute(f"insert into applications ({', '.join(cols)}) values ({', '.join('?' * len(cols))})",
                         [r.get(c) for c in cols])
    print(f"Demo database ready at {db_file} with {len(rows)} fictional applications.")


if __name__ == "__main__":
    main()
