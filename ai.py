"""The AI parts: read a job description, score a resume against it, write a tailored CV and cover letter."""
import json
import os

import anthropic

MODEL = os.environ.get("JOBPORTAL_MODEL", "claude-opus-5-5")

_client = None


class AIError(Exception):
    """A problem the user should see, in plain words."""


def client():
    global _client
    if _client is None:
        try:
            _client = anthropic.Anthropic()
        except anthropic.AnthropicError as error:
            raise AIError("The AI isn't set up. Set ANTHROPIC_API_KEY before starting the server.") from error
    return _client


def ask(system, prompt, schema, effort="medium"):
    """One request whose answer must match `schema`. Returns the parsed JSON."""
    try:
        response = client().beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as error:
        raise AIError("The AI key was rejected. Check ANTHROPIC_API_KEY.") from error
    except anthropic.RateLimitError as error:
        raise AIError("The AI is busy. Please try again in a minute.") from error
    except anthropic.APIConnectionError as error:
        raise AIError("Couldn't reach the AI. Check your internet connection.") from error
    except anthropic.APIStatusError as error:
        print(f"[ai] API error {error.status_code}: {error.message}", flush=True)
        raise AIError("The AI couldn't answer right now. Please try again.") from error
    except anthropic.AnthropicError as error:
        raise AIError("The AI isn't set up. Set ANTHROPIC_API_KEY before starting the server.") from error
    except TypeError as error:  # The SDK raises this when it finds no credentials at all.
        if "authentication" not in str(error):
            raise
        raise AIError("The AI isn't set up. Set ANTHROPIC_API_KEY before starting the server.") from error
    if response.stop_reason == "refusal":
        raise AIError("The AI declined this request. Try a different job description.")
    if response.stop_reason == "max_tokens":
        raise AIError("The AI's answer was cut off. Please try again.")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError as error:
        raise AIError("The AI gave an answer we couldn't read. Please try again.") from error


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
STRINGS = {"type": "array", "items": STRING}

ANALYSIS_SCHEMA = _object({
    "is_job_posting": {"type": "boolean"},
    "job_title": STRING,
    "company": STRING,
    "location": STRING,
    "fit_score": INTEGER,
    "interview_chance": INTEGER,
    "chance_label": {"type": "string", "enum": ["Low", "Moderate", "Good", "Strong"]},
    "verdict": STRING,
    "skill_match": INTEGER,
    "experience_match": INTEGER,
    "education_match": INTEGER,
    "keyword_match": INTEGER,
    "matched_skills": STRINGS,
    "missing_skills": STRINGS,
    "strengths": STRINGS,
    "gaps": STRINGS,
    "improvements": STRINGS,
    "ats_keywords": STRINGS,
})

ANALYSIS_SYSTEM = """You are an experienced recruiter and hiring manager. You compare a candidate's resume with a job description and give an honest, specific assessment.

Scoring (all integers 0-100):
- fit_score: how well the resume matches what the role asks for overall.
- skill_match, experience_match, education_match, keyword_match: the parts of that fit.
- interview_chance: your realistic estimate of the chance this resume gets an interview if submitted as it is today. Weigh must-have requirements more than nice-to-haves, seniority and years of experience, and how competitive such roles usually are. Be realistic, not encouraging: most applications do not get interviews.
- chance_label: Low (<25), Moderate (25-49), Good (50-69), Strong (70+), matching interview_chance.

Rules:
- Only credit skills and experience the resume actually shows. Never assume.
- verdict: two or three plain sentences a candidate can act on.
- matched_skills / missing_skills: short skill names taken from the job description.
- strengths, gaps, improvements: 3-6 items each, one sentence each, concrete. Improvements must be things the candidate can honestly do (reword, reorder, highlight, add a real project, learn a skill) - never invent experience.
- ats_keywords: the 10-15 most important terms from the job description.
- If the text is not a job posting, set is_job_posting false and fill the rest with empty values and zeros.
- Use "" for job_title, company or location when the posting does not say."""


def analyze(resume, job_description):
    prompt = f"<resume>\n{resume}\n</resume>\n\n<job_description>\n{job_description}\n</job_description>"
    result = ask(ANALYSIS_SYSTEM, prompt, ANALYSIS_SCHEMA, effort="medium")
    if not result.get("is_job_posting"):
        raise AIError("That doesn't look like a job description. Paste the full posting and try again.")
    for key in ("fit_score", "interview_chance", "skill_match", "experience_match", "education_match", "keyword_match"):
        result[key] = max(0, min(100, int(result.get(key) or 0)))
    return result


DOCUMENTS_SCHEMA = _object({
    "cv": STRING,
    "cover_letter": STRING,
    "notes": STRINGS,
})

DOCUMENTS_SYSTEM = """You are a professional CV writer. You tailor a candidate's real resume to one job, and write a matching cover letter.

Honesty is the first rule: use only facts that are in the resume (employers, titles, dates, degrees, skills, numbers). Never invent experience, employers, tools, certifications or metrics. You may reword, reorder, emphasise, cut what's irrelevant, and use the job's vocabulary where the resume genuinely supports it. If a contact detail is missing, leave it out rather than making one up.

CV (Markdown):
- Start with "# Full Name" and one line of contact details from the resume.
- Then: ## Professional Summary (3 lines aimed at this role), ## Key Skills (ATS-friendly, grouped, job keywords first where true), ## Experience (most relevant first within reverse-chronological order, strong action-verb bullets, results kept), ## Education, and other sections the resume supports (Projects, Certifications, Languages).
- Plain Markdown only: headings, bullets, bold. No tables, no images, so it passes applicant tracking systems.

Cover letter (plain text with blank lines between paragraphs):
- 250-350 words, addressed to the hiring team at the company (use "Hiring Manager" if no name is given).
- Opening that names the role, two body paragraphs linking real achievements to the job's top needs, a short confident close, and a sign-off with the candidate's name.
- Warm and professional, no clichés like "I am writing to express my interest".

notes: 2-5 short tips on what the candidate should check or add before sending (for example a gap they could honestly fill)."""


def documents(resume, job_description, analysis=None):
    extra = ""
    if analysis:
        extra = (
            "\n\n<analysis>\nMatched: " + ", ".join(analysis.get("matched_skills", []))
            + "\nMissing: " + ", ".join(analysis.get("missing_skills", []))
            + "\nKeywords: " + ", ".join(analysis.get("ats_keywords", []))
            + "\n</analysis>"
        )
    prompt = f"<resume>\n{resume}\n</resume>\n\n<job_description>\n{job_description}\n</job_description>{extra}"
    return ask(DOCUMENTS_SYSTEM, prompt, DOCUMENTS_SCHEMA, effort="medium")
