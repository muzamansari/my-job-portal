"""My Job Portal: score your resume against a job, write a tailored CV and cover letter, and track applications.
Run: python server.py"""
import asyncio
import base64
import datetime
import html
import io
import ipaddress
import json
import os
import re
import socket
import sqlite3
import time
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai

HERE = Path(__file__).resolve().parent
STORAGE = Path(os.environ.get("JOBPORTAL_STORAGE", HERE / "storage"))
DB_PATH = STORAGE / "jobportal.db"
PORT = int(os.environ.get("JOBPORTAL_PORT", "8110"))
STATUSES = ["Saved", "Applied", "Interview", "Offer", "Rejected"]
MAX_TEXT = 60000

SCHEMA = """
create table if not exists settings (key text primary key, value text not null);
create table if not exists applications (
    id integer primary key,
    title text not null default '',
    company text not null default '',
    location text not null default '',
    url text not null default '',
    job_description text not null,
    fit_score integer,
    interview_chance integer,
    analysis text,
    cv text not null default '',
    cover_letter text not null default '',
    doc_notes text not null default '[]',
    status text not null default 'Saved',
    applied_on text not null default '',
    notes text not null default '',
    created_at real not null,
    updated_at real not null
);
"""


@contextmanager
def db():
    STORAGE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)


def get_setting(key, default=""):
    with db() as conn:
        row = conn.execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute("insert into settings (key, value) values (?, ?) on conflict(key) do update set value = excluded.value", (key, value))


def row_to_app(row, full=True):
    app = dict(row)
    app["analysis"] = json.loads(app["analysis"]) if app.get("analysis") else None
    app["doc_notes"] = json.loads(app.get("doc_notes") or "[]")
    if not full:
        for key in ("job_description", "cv", "cover_letter", "analysis", "doc_notes"):
            app.pop(key, None)
        app["has_documents"] = bool(row["cv"])
    return app


# ---------- reading job pages ----------

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip, self.jsonld, self.in_jsonld, self.title, self.in_title = [], 0, [], False, "", False
        # Text inside the page's main area (<main> or role="main"), if the page marks one.
        self.main_parts, self.main_tag, self.main_depth, self.main_done = [], None, 0, False

    def _add(self, text):
        self.parts.append(text)
        if self.main_tag:
            self.main_parts.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self.in_jsonld = True
        if tag == "title":
            self.in_title = True
        if self.main_tag == tag:
            self.main_depth += 1
        elif not self.main_tag and not self.main_done and (tag == "main" or (attrs.get("role") or "").lower() == "main"):
            self.main_tag, self.main_depth = tag, 1
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self._add("\n")
        if tag == "li":
            self._add("• ")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_jsonld = False
        if tag == "title":
            self.in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self._add("\n")
        if self.main_tag == tag:
            self.main_depth -= 1
            if self.main_depth == 0:
                self.main_tag, self.main_done = None, True

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld.append(data)
        elif self.in_title:
            self.title += data
        elif not self.skip:
            self._add(data)


def _clean(text):
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"(?m)^[ \t]*[•\-*][ \t]*$", "", text)  # empty bullet points
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return text.strip()


def _html_to_text(fragment):
    parser = _TextExtractor()
    parser.feed(html.unescape(fragment))
    return _clean("".join(parser.parts))


def _find_job_posting(data):
    if isinstance(data, list):
        for item in data:
            found = _find_job_posting(item)
            if found:
                return found
    elif isinstance(data, dict):
        kind = data.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                found = _find_job_posting(data[key])
                if found:
                    return found
    return None


def _check_public(url):
    """Only fetch public http(s) pages, not this computer or the local network."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, "Enter a full link that starts with http:// or https://")
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except socket.gaierror:
        raise HTTPException(400, "That website couldn't be found. Check the link.")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise HTTPException(400, "That link points to a private address, so it can't be read.")


def fetch_job_page(url):
    _check_public(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en;q=0.9,de;q=0.8",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(4_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as error:
        raise HTTPException(400, f"The job site refused the request (error {error.code}). Copy the job description and paste it instead.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise HTTPException(400, "Couldn't open that link. Copy the job description and paste it instead.")
    return job_from_html(raw.decode(charset, errors="replace"))


def job_from_html(page):
    parser = _TextExtractor()
    parser.feed(page)
    title = _clean(html.unescape(parser.title))
    for block in parser.jsonld:
        try:
            posting = _find_job_posting(json.loads(block))
        except ValueError:
            continue
        if posting and posting.get("description"):
            org = posting.get("hiringOrganization") or {}
            company = org.get("name", "") if isinstance(org, dict) else ""
            header = "\n".join(x for x in [posting.get("title", ""), company] if x)
            return {"text": (header + "\n\n" + _html_to_text(posting["description"]))[:MAX_TEXT], "title": posting.get("title") or title}

    text = _clean(html.unescape("".join(parser.parts)))
    main_text = _clean(html.unescape("".join(parser.main_parts)))
    if len(main_text) >= 300:  # prefer the page's main area over menus and footers
        text = main_text
    if len(text) < 300:
        raise HTTPException(400, "This site doesn't show the job text to tools (common on LinkedIn and some portals). Copy the job description and paste it instead.")
    return {"text": text[:MAX_TEXT], "title": title}


# ---------- reading resume files ----------

def resume_from_file(name, data):
    lower = name.lower()
    if lower.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="replace")
    if lower.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as docx:
                xml = docx.read("word/document.xml").decode("utf-8", errors="replace")
        except (zipfile.BadZipFile, KeyError):
            raise HTTPException(400, "That Word file couldn't be read.")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        return _clean(html.unescape(re.sub(r"<[^>]+>", "", xml)))
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise HTTPException(400, "PDF reading needs the pypdf package (pip install -r requirements.txt).")
        try:
            reader = PdfReader(io.BytesIO(data))
            return _clean("\n".join(page.extract_text() or "" for page in reader.pages))
        except Exception:
            raise HTTPException(400, "That PDF couldn't be read. Try a .docx or paste the text.")
    raise HTTPException(400, "Use a PDF, Word (.docx) or text file, or paste your resume.")


# ---------- API ----------

app = FastAPI(title="My Job Portal")


@app.exception_handler(ai.AIError)
async def ai_error(_, error):
    return JSONResponse({"detail": str(error)}, status_code=502)


class ResumeIn(BaseModel):
    text: str


class FileIn(BaseModel):
    name: str
    data: str  # base64


class FetchIn(BaseModel):
    url: str


class AnalyzeIn(BaseModel):
    job_description: str
    url: str = ""


class UpdateIn(BaseModel):
    status: str | None = None
    applied_on: str | None = None
    notes: str | None = None
    title: str | None = None
    company: str | None = None
    cv: str | None = None
    cover_letter: str | None = None


def require_resume():
    resume = get_setting("resume").strip()
    if len(resume) < 100:
        raise HTTPException(400, "Add your resume first (the My Resume tab).")
    return resume


@app.get("/api/resume")
def read_resume():
    return {"text": get_setting("resume"), "updated_at": get_setting("resume_updated", "")}


@app.put("/api/resume")
def save_resume(body: ResumeIn):
    text = body.text.strip()[:MAX_TEXT]
    set_setting("resume", text)
    set_setting("resume_updated", datetime.datetime.now().isoformat(timespec="minutes"))
    return {"ok": True, "chars": len(text)}


@app.post("/api/resume/file")
def parse_resume_file(body: FileIn):
    try:
        data = base64.b64decode(body.data)
    except ValueError:
        raise HTTPException(400, "That file couldn't be read.")
    if len(data) > 10_000_000:
        raise HTTPException(400, "That file is too big (10 MB max).")
    text = resume_from_file(body.name, data).strip()
    if len(text) < 50:
        raise HTTPException(400, "No text found in that file. If it is a scanned PDF, paste the text instead.")
    return {"text": text[:MAX_TEXT]}


@app.post("/api/fetch-job")
async def fetch_job(body: FetchIn):
    return await asyncio.to_thread(fetch_job_page, body.url.strip())


@app.post("/api/analyze")
async def analyze(body: AnalyzeIn):
    resume = require_resume()
    jd = body.job_description.strip()[:MAX_TEXT]
    if len(jd) < 150:
        raise HTTPException(400, "The job description is too short. Paste the full posting.")
    result = await asyncio.to_thread(ai.analyze, resume, jd)
    now = time.time()
    with db() as conn:
        cursor = conn.execute(
            "insert into applications (title, company, location, url, job_description, fit_score, interview_chance, analysis, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result["job_title"], result["company"], result["location"], body.url.strip(), jd,
             result["fit_score"], result["interview_chance"], json.dumps(result), now, now),
        )
        row = conn.execute("select * from applications where id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_app(row)


def load_app(app_id):
    with db() as conn:
        row = conn.execute("select * from applications where id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Application not found.")
    return row


@app.post("/api/applications/{app_id}/documents")
async def make_documents(app_id: int):
    resume = require_resume()
    row = load_app(app_id)
    analysis = json.loads(row["analysis"]) if row["analysis"] else None
    result = await asyncio.to_thread(ai.documents, resume, row["job_description"], analysis)
    with db() as conn:
        conn.execute("update applications set cv = ?, cover_letter = ?, doc_notes = ?, updated_at = ? where id = ?",
                     (result["cv"], result["cover_letter"], json.dumps(result["notes"]), time.time(), app_id))
    return row_to_app(load_app(app_id))


@app.get("/api/applications")
def list_apps():
    with db() as conn:
        rows = conn.execute("select * from applications order by created_at desc").fetchall()
    return [row_to_app(r, full=False) for r in rows]


@app.get("/api/applications/{app_id}")
def get_app(app_id: int):
    return row_to_app(load_app(app_id))


@app.patch("/api/applications/{app_id}")
def update_app(app_id: int, body: UpdateIn):
    load_app(app_id)
    changes = body.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in STATUSES:
        raise HTTPException(400, "Unknown status.")
    if changes.get("status") == "Applied" and "applied_on" not in changes:
        with db() as conn:
            current = conn.execute("select applied_on from applications where id = ?", (app_id,)).fetchone()
        if not current["applied_on"]:
            changes["applied_on"] = datetime.date.today().isoformat()
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        with db() as conn:
            conn.execute(f"update applications set {sets}, updated_at = ? where id = ?", (*changes.values(), time.time(), app_id))
    return row_to_app(load_app(app_id))


@app.delete("/api/applications/{app_id}")
def delete_app(app_id: int):
    load_app(app_id)
    with db() as conn:
        conn.execute("delete from applications where id = ?", (app_id,))
    return {"ok": True}


@app.get("/api/stats")
def stats():
    with db() as conn:
        rows = conn.execute("select status, fit_score, interview_chance, created_at, applied_on from applications").fetchall()
    by_status = {s: 0 for s in STATUSES}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    sent = by_status["Applied"] + by_status["Interview"] + by_status["Offer"] + by_status["Rejected"]
    fits = [r["fit_score"] for r in rows if r["fit_score"] is not None]
    chances = [r["interview_chance"] for r in rows if r["interview_chance"] is not None]
    today = datetime.date.today()
    weeks = []
    for back in range(7, -1, -1):
        start = today - datetime.timedelta(days=today.weekday() + 7 * back)
        end = start + datetime.timedelta(days=7)
        count = sum(1 for r in rows if r["applied_on"] and start.isoformat() <= r["applied_on"] < end.isoformat())
        weeks.append({"week": start.isoformat(), "count": count})
    return {
        "total": len(rows),
        "by_status": by_status,
        "sent": sent,
        "interview_rate": round(100 * (by_status["Interview"] + by_status["Offer"]) / sent) if sent else None,
        "avg_fit": round(sum(fits) / len(fits)) if fits else None,
        "avg_chance": round(sum(chances) / len(chances)) if chances else None,
        "weeks": weeks,
        "statuses": STATUSES,
    }


init_db()
app.mount("/", StaticFiles(directory=HERE / "static", html=True), name="static")

if __name__ == "__main__":
    print(f"My Job Portal running at http://localhost:{PORT}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT)
