# HR Automation Orchestrator

An AI-powered HR pipeline that takes a candidate's resume PDF and, in **one API call**:

1. **Screens the resume** — extracts name, college, CGPA and skills, then scores the candidate (0–100) for a target job role.
2. **Generates an onboarding plan** — a day-by-day markdown table whose length adapts to the score (weaker candidates get a longer, more supportive plan).
3. **Answers policy questions** — HR policy Q&A grounded in `policy_documents.json`, with conversation memory across questions.

Built with **FastAPI**, **Google Gemini**, **CrewAI** (onboarding agent), optional **MongoDB** storage, and a simple HTML/CSS/JS frontend.

---

## Architecture

```
        frontend/index.html  (web UI)
                 │  multipart/form-data
                 ▼
┌─────────────────────────────────────────────────┐
│        Orchestrator API  (port 9000)            │
│            POST /orchestrate                    │
└─────────────────────────────────────────────────┘
        │                │                │
        ▼                ▼                ▼
┌──────────────┐ ┌───────────────┐ ┌──────────────┐
│    Resume    │ │  Onboarding   │ │  Policy Q&A  │
│   Screening  │ │  Plan Agent   │ │    Agent     │
│ (Gemini+OCR) │ │(CrewAI+Gemini)│ │   (Gemini)   │
└──────────────┘ └───────────────┘ └──────────────┘
        │
        ▼
   MongoDB (optional — skipped if not configured)
```

## Project structure

```
hr-management/
├── frontend/                     # Web UI (open index.html in a browser)
│   ├── index.html
│   ├── script.js
│   └── styles.css
├── hr_management/
│   ├── .env.example              # Template for your .env (copy & fill in)
│   ├── read_pdfs/                # Extracted resume text (generated at runtime)
│   └── src/hr_management/
│       ├── config.py             # Loads .env, shared settings
│       ├── orchestrator.py       # ★ Main API (port 9000)
│       ├── resume_agent.py       # PDF extraction + Gemini screening (standalone: port 8080)
│       ├── onboarding_agent.py   # CrewAI onboarding planner
│       ├── policy_agent.py       # Policy Q&A (standalone: port 8001)
│       ├── policy_documents.json # Editable HR policy texts
│       ├── crew.py               # Sequential pipeline used by the CLI
│       └── main.py               # Interactive CLI runner
├── requirements.txt
└── README.md
```

---

## Setup (5 steps)

### 1. Prerequisites

- **Python 3.10+**
- **Tesseract OCR** *(optional — only needed for scanned/image resumes; text PDFs work without it)*
  ```bash
  sudo apt-get install tesseract-ocr    # Ubuntu/Debian
  brew install tesseract                # macOS
  ```
- A **Google Gemini API key** — free at <https://aistudio.google.com/apikey>

### 2. Clone and create a virtual environment

```bash
git clone <repo-url>
cd hr-management
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your API key

```bash
cp hr_management/.env.example hr_management/.env
```

Open `hr_management/.env` and paste your key:

```
GEMINI_API_KEY=your_actual_key_here
```

That's the only required setting. Optional settings (all documented in `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model to use |
| `MONGODB_URI` | *(empty)* | MongoDB connection string; leave empty to run without a database |
| `GEMINI_REQUEST_DELAY` | `0` | Seconds to wait between Gemini calls — set to `4` if you hit free-tier rate limits |

### 5. Start the server

```bash
cd hr_management/src/hr_management
uvicorn orchestrator:app --host 0.0.0.0 --port 9000
```

You should see `Uvicorn running on http://0.0.0.0:9000`. Sanity-check it:

```bash
curl http://localhost:9000/health
# {"status":"ok"}
```

---

## Using the web UI

With the server running, open **`frontend/index.html`** in your browser (double-click it, or `xdg-open frontend/index.html`). Then:

1. Upload a resume PDF.
2. Type the target job role (e.g. *Software Engineer*).
3. Add any policy questions.
4. Click **Run Orchestrator** — results (profile, score, onboarding plan, policy answers) appear on the right. A full run typically takes 20–60 seconds.

## Using the API directly

Interactive docs: **http://localhost:9000/docs** (Swagger) or `/redoc`.

### `POST /orchestrate` — multipart/form-data

| Field | Type | Required | Description |
|---|---|---|---|
| `pdf_file` | file | yes | The resume PDF |
| `job_role` | text | yes | Target job role for scoring |
| `policy_questions` | text | no | JSON array of questions, e.g. `["What is the leave policy?"]` |

**cURL example:**

```bash
curl -X POST "http://localhost:9000/orchestrate" \
  -F "pdf_file=@/path/to/resume.pdf" \
  -F "job_role=Software Engineer" \
  -F 'policy_questions=["How many leave days do I get?", "What is the remote work policy?"]'
```

**Python example:**

```python
import requests

with open("resume.pdf", "rb") as f:
    response = requests.post(
        "http://localhost:9000/orchestrate",
        files={"pdf_file": f},
        data={
            "job_role": "Software Engineer",
            "policy_questions": '["How many leave days do I get?"]',
        },
    )
print(response.json())
```

**Response:**

```json
{
  "session_id": "a1b2c3d4-...",
  "candidate_info": {
    "name": "John Doe",
    "College": "MIT",
    "Tech_skills": ["Python", "Java", "React"],
    "Soft_skills": ["Leadership", "Communication"],
    "CGPA": "8.5",
    "score": 85
  },
  "onboarding_plan": "| Day | Key Activities | Goal |\n|-----|-----|-----|\n| 1 | Orientation | Welcome |...",
  "policy_answers": {
    "How many leave days do I get?": "Employees are entitled to 20 days of paid leave annually..."
  }
}
```

Errors return a JSON body with a `detail` message — `400` for bad input (non-PDF file, unreadable PDF, empty job role), `500`/`502` for configuration or upstream API problems.

---

## Other ways to run it

All commands below are run from `hr_management/src/hr_management/`.

| What | Command | Notes |
|---|---|---|
| **Interactive CLI** (no server) | `python main.py` | Prompts for a PDF path, job role and questions |
| **Resume agent standalone** | `python resume_agent.py` | Simple HTML flow on port 8080: upload → analyze → mark top-N hired (hiring needs MongoDB) |
| **Policy Q&A standalone** | `uvicorn policy_agent:app --port 8001` | `POST /policy_qa` with `{"questions": [...]}` |
| **Onboarding backfill** | `python onboarding_agent.py` | Generates plans for hired candidates in MongoDB that don't have one yet |

## Customizing the policies

Edit `hr_management/src/hr_management/policy_documents.json` — a list of objects with `section` and `text` keys. The Q&A agent retrieves the most relevant paragraphs by keyword overlap and answers from them. Restart the server after editing.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `GEMINI_API_KEY is not set` | Copy `hr_management/.env.example` to `hr_management/.env` and paste your key. |
| `Form data requires "python-multipart"` | `pip install -r requirements.txt` (installs `python-multipart`). |
| Gemini rate-limit / quota errors (429) | Set `GEMINI_REQUEST_DELAY=4` in `.env`, or use a paid-tier key. |
| Scanned PDF returns "Could not extract any text" | Install Tesseract OCR (see Prerequisites). |
| Port 9000 already in use | `uvicorn orchestrator:app --port 9001` (and update `API_BASE_URL` at the top of `frontend/script.js`). |
| Candidates not saved to database | That's expected when `MONGODB_URI` is unset — storage is optional. Set it in `.env` to enable. |
| `ModuleNotFoundError` on startup | Make sure you start uvicorn **from** `hr_management/src/hr_management/` with your virtualenv activated. |

## Security notes

- **Never commit `.env`** — it is already in `.gitignore`.
- MongoDB credentials live only in `.env` (`MONGODB_URI`), never in code.
- The API currently has no authentication and CORS is open — fine for local demos; add an auth layer (e.g. FastAPI `HTTPBearer`) and restrict `allow_origins` before exposing it publicly.

## Production deployment

```bash
pip install gunicorn
cd hr_management/src/hr_management
gunicorn orchestrator:app --workers 2 --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:9000 --timeout 300
```

The long `--timeout` matters: a full run makes several sequential LLM calls.

---

## License

This project is for educational and demonstration purposes.
