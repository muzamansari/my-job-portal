"""API tests with the AI replaced by fixed answers. Run: .venv\\Scripts\\python.exe -m pytest tests"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["JOBPORTAL_STORAGE"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import ai
import server

RESUME = "Jane Doe · jane@example.com\nData Analyst with 3 years of SQL, Python and Power BI experience. " * 3
JD = "We are hiring a Data Analyst. You will build dashboards in Power BI, write SQL, and work with stakeholders. " * 3

ANALYSIS = {
    "is_job_posting": True, "job_title": "Data Analyst", "company": "Acme", "location": "Berlin",
    "fit_score": 82, "interview_chance": 140, "chance_label": "Strong", "verdict": "Good match.",
    "skill_match": 80, "experience_match": 75, "education_match": 90, "keyword_match": 70,
    "matched_skills": ["SQL"], "missing_skills": ["dbt"], "strengths": ["s"], "gaps": ["g"],
    "improvements": ["i"], "ats_keywords": ["SQL", "Power BI"],
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ai, "ask", lambda system, prompt, schema, effort="medium":
                        ANALYSIS if schema is ai.ANALYSIS_SCHEMA else {"cv": "# Jane Doe", "cover_letter": "Dear Hiring Manager", "notes": ["n"]})
    return TestClient(server.app)


def test_analyze_needs_resume(client):
    server.set_setting("resume", "")
    r = client.post("/api/analyze", json={"job_description": JD})
    assert r.status_code == 400 and "resume" in r.json()["detail"].lower()


def test_full_flow(client):
    assert client.put("/api/resume", json={"text": RESUME}).status_code == 200
    app = client.post("/api/analyze", json={"job_description": JD, "url": "https://example.com/job"}).json()
    assert app["title"] == "Data Analyst" and app["fit_score"] == 82
    assert app["interview_chance"] == 100  # clamped to 0-100

    docs = client.post(f"/api/applications/{app['id']}/documents").json()
    assert docs["cv"].startswith("# Jane") and docs["doc_notes"] == ["n"]

    updated = client.patch(f"/api/applications/{app['id']}", json={"status": "Applied"}).json()
    assert updated["status"] == "Applied" and updated["applied_on"]
    assert client.patch(f"/api/applications/{app['id']}", json={"status": "Nope"}).status_code == 400

    stats = client.get("/api/stats").json()
    assert stats["by_status"]["Applied"] >= 1 and stats["sent"] >= 1
    listed = client.get("/api/applications").json()
    assert "job_description" not in listed[0] and listed[0]["has_documents"]

    assert client.delete(f"/api/applications/{app['id']}").status_code == 200
    assert client.get(f"/api/applications/{app['id']}").status_code == 404


def test_fetch_blocks_private_addresses(client):
    r = client.post("/api/fetch-job", json={"url": "http://127.0.0.1:8110/"})
    assert r.status_code == 400
    assert client.post("/api/fetch-job", json={"url": "file:///etc/passwd"}).status_code == 400


def test_short_job_description(client):
    client.put("/api/resume", json={"text": RESUME})
    assert client.post("/api/analyze", json={"job_description": "too short"}).status_code == 400


def test_job_page_prefers_main_area():
    body = "We need a Data Analyst who writes SQL and builds Power BI dashboards for the sales team. " * 5
    page = (
        "<html><head><title>Job</title></head><body>"
        "<header><ul><li><a href='/'></a></li><li></li><li>Menu</li></ul></header>"
        "<div class='sidebar'><ul><li>Donate</li><li></li></ul></div>"
        f"<section role='main'><h1>Data Analyst</h1><section><p>{body}</p></section><ul><li>SQL</li><li></li></ul></section>"
        "<div><ul><li>Sponsor A</li><li></li></ul></div>"
        "</body></html>"
    )
    text = server.job_from_html(page)["text"]
    assert text.startswith("Data Analyst") and "• SQL" in text
    assert "Donate" not in text and "Sponsor" not in text and "Menu" not in text
    assert all(line.strip() != "•" for line in text.splitlines())


def test_docx_resume():
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>Jane Doe</w:t></w:r></w:p><w:p><w:r><w:t>SQL &amp; Python</w:t></w:r></w:p></w:document>")
    text = server.resume_from_file("cv.docx", buf.getvalue())
    assert "Jane Doe" in text and "SQL & Python" in text
