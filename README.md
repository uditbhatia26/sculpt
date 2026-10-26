# Sculpt – AI Resume Optimizer · Backend API

Sculpt's backend is a REST API that handles user authentication, resume storage and parsing, job description processing, ATS compatibility scoring, and AI-driven resume optimization. It is built with FastAPI and backed by a Neon PostgreSQL database.

---

## Features

### Authentication and User Management
- Email and password signup and login with bcrypt password hashing
- JWT tokens with a 24-hour expiry and a `/auth/refresh` endpoint for silent renewal
- Google OAuth support — verifies Google access tokens against the userinfo endpoint and creates or signs in the corresponding user automatically
- Email verification on signup via a one-time token link (24-hour expiry, 60-second resend throttle)
- Plan-based access control: `free` and `pro`

### Resume Upload and Parsing
- Accepts PDF (up to 50 pages) and DOCX uploads up to 2 MB, with resume text capped at 12,000 characters
- Validates files via magic-byte check and content type
- Extracts text via PyPDF2 and converts it to a structured YAML representation using an LLM
- One active resume stored per user; new uploads replace the previous one

### Job Description Processing and Caching
- Parses raw job description text into structured fields: `job_title`, `skills`, and a normalized `job_description`
- Results are cached by SHA-256 hash of the raw input text — identical job descriptions skip the LLM entirely on subsequent requests
- Returns a `jd_cache_id` that clients pass to downstream endpoints to eliminate redundant parsing calls

### ATS Scoring
Two scoring modes are available.

**Basic** — returns a single integer score from 0 to 100 with a plain-text reason.

**Detailed** — returns a full structured breakdown used by the extension:
- Keyword analysis: matched keywords, missing critical keywords, missing important keywords, and a keyword density score
- Skills analysis: matched and missing technical skills, matched and missing soft skills, and an overall skills alignment score
- Experience alignment: relevant years detected versus required, role alignment rating, and an experience score
- Formatting score: section clarity, standard header usage, bullet point quality, and readability
- Weighted overall score (keywords 40%, skills 30%, experience 20%, formatting 10%)
- Match level classification: Excellent, Good, Fair, or Poor
- ATS pass likelihood: High, Medium, or Low
- Lists of strengths, critical improvements required, and recommended improvements

### AI Resume Optimization
- Generates a tailored resume by integrating job-relevant keywords, rewriting bullet points with action verbs and quantified impact, and reordering skills sections by JD relevance
- Accepts an optional `original_ats_score` parameter to skip re-running the baseline ATS calculation when it has already been computed by the client
- Hard guardrail: the education section is always restored verbatim from the original resume after LLM generation and cannot be altered under any circumstances
- Computes a semantic diff between the original and optimized resume and returns a structured changelog covering skills added or removed, bullets enhanced per employer, projects added or removed, summary changes, and section-level additions or removals

### PDF Generation
- Renders resume YAML through Jinja2 LaTeX templates (`jakes_resume`, `classic_ivy`, `compact_onepage`) and compiles with the bundled Tectonic binary (`bin/`); can also return raw `.tex`. Falls back to ReportLab if compilation is unavailable
- Accepts the optimized YAML from the request body or falls back to the user's stored base resume if none is provided
- Streams PDF bytes directly in the response with no intermediate file written to disk

### Usage Limits and Plan Enforcement
- Weekly generation limits enforced per plan: Free = 5 per week, Pro = 30 per week
- Usage tracked in a dedicated `generation_usage` table keyed by user and ISO week start date
- Every authentication response (`/auth/me`, login, signup) includes `weekly_usage`, `weekly_limit`, `daily_usage`, and `monthly_usage` so clients always have current counts
- Returns HTTP 429 with a descriptive message when the weekly limit is reached

### Optimization History
- Every optimization run is persisted with the original score, optimized score, score improvement delta, match level, keywords added, and a list of improvements made
- `/my-optimizations` returns the full history for the authenticated user, ordered newest first

### Admin
- `/admin/cleanup` deletes local debug dump files older than a configurable number of days, protected by a static secret token

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/signup` | Create a new account and send a verification email |
| `POST` | `/auth/login` | Authenticate and receive a JWT |
| `POST` | `/auth/google` | Google OAuth login or signup |
| `GET`  | `/auth/me` | Retrieve profile and current usage stats |
| `POST` | `/auth/refresh` | Silently renew an expiring JWT |
| `GET`  | `/auth/verify-email` | Verify email address via one-time token |
| `POST` | `/auth/forgot-password` | Email a password-reset link (generic response) |
| `GET` / `POST` | `/auth/reset-password` | Reset form page / set a new password with the emailed token |
| `DELETE` | `/auth/me` | Permanently delete the account |
| `POST` | `/validate-key` | Validate a BYOK provider key (entitled users) |
| `POST` | `/explain-changes` | AI explanation of what changed and why |
| `GET`  | `/my-optimizations/{id}/pdf` | Re-download a past optimization as PDF |
| `GET/PATCH` | `/admin/users...` | Admin user list, BYOK and plan toggles |
| `POST` | `/auth/resend-verification` | Resend the verification email |
| `POST` | `/upload-resume` | Upload a PDF and parse it to YAML |
| `GET`  | `/my-resume` | Retrieve the stored resume YAML |
| `POST` | `/parse-jd` | Parse and cache a job description |
| `POST` | `/calculate-ats-detailed` | Run a detailed ATS analysis |
| `POST` | `/optimize-resume` | Generate an optimized resume |
| `POST` | `/generate-pdf` | Render a resume YAML to a downloadable PDF |
| `GET`  | `/my-optimizations` | Retrieve optimization history |

---

## Stack

| Component | Technology |
|---|---|
| API framework | FastAPI |
| Database | PostgreSQL via Neon, SQLAlchemy ORM |
| LLM integration | LangChain: OpenAI, Anthropic, Google, Groq, OpenRouter (server default or user key) |
| PDF generation | Jinja2 + Tectonic (LaTeX), ReportLab fallback |
| Workflow | LangGraph (draft → guardrails → page budget → ATS → retry) |
| PDF text extraction | PyPDF2 |
| Authentication | bcrypt, python-jose (JWT) |
| Transactional email | smtplib over Gmail SMTP |

---

## Setup and tests

```bash
cp .env.example .env   # DATABASE_URL, JWT_SECRET_KEY, OPENAI_API_KEY or GROQ_API_KEY, MAIL_*, APP_BASE_URL
pip install -r requirements.txt
uvicorn main:app --reload
```

Optional env vars: `CORS_ORIGINS` (comma-separated dashboard origins; localhost and `chrome-extension://` are always allowed), `DEBUG_DUMP=true`.

```bash
python -m unittest tests.test_hardening test_pillar1_byok   # DB-free
python test_workflow.py                                     # needs Tectonic in bin/
python test_latex_compilation.py                            # needs Tectonic in bin/

alembic stamp head      # existing database
alembic upgrade head    # fresh database
```
