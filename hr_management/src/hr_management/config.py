"""Shared configuration for all HR agents.

Loads environment variables from `hr_management/.env` (two levels above this
file) regardless of the directory the app is launched from, so every entry
point (orchestrator API, CLI, standalone agents) sees the same settings.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# hr_management/.env  (…/hr_management/src/hr_management/config.py -> parents[2])
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_PATH)
load_dotenv()  # also honour a .env in the current working directory, if any

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Optional: leave MONGODB_URI empty to run without a database.
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "resume_screening")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "candidates")

# Pause (seconds) between Gemini calls — useful on free-tier rate limits.
GEMINI_REQUEST_DELAY = float(os.getenv("GEMINI_REQUEST_DELAY", "0"))

if GEMINI_API_KEY:
    # CrewAI/LiteLLM and the google-generativeai SDK read these.
    os.environ.setdefault("GOOGLE_API_KEY", GEMINI_API_KEY)
    os.environ.setdefault("GEMINI_API_KEY", GEMINI_API_KEY)


def require_gemini_key() -> str:
    """Return the Gemini API key or fail with an actionable message."""
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy hr_management/.env.example to "
            f"{ENV_PATH} and paste your key (get one at https://aistudio.google.com/apikey)."
        )
    return GEMINI_API_KEY
