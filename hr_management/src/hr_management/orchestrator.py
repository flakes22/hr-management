"""HR Automation Sequential Orchestrator (main API, port 9000).

Single endpoint POST /orchestrate that runs the full pipeline on an uploaded
resume PDF:
  1. Resume screening  -> extract candidate fields + score for the job role
  2. Onboarding plan   -> markdown-table plan sized to the score
  3. Policy Q&A        -> answers to any policy questions supplied

The endpoint accepts multipart/form-data (the resume is uploaded as a file):
  pdf_file          the resume PDF (file upload, required)
  job_role          target job role (text, required)
  policy_questions  JSON array of questions, e.g. ["What is the leave policy?"]
                    (text, optional)

Run from this directory:
  uvicorn orchestrator:app --host 0.0.0.0 --port 9000
"""
import json
import logging
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import GEMINI_REQUEST_DELAY
from onboarding_agent import generate_onboarding_plan
from policy_agent import build_prompt, gemini_chat, retrieve_context
from resume_agent import (
    call_gemini_extract_fields,
    call_gemini_score,
    extract_text_from_pdf,
    store_candidate_in_mongo,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="HR Automation Orchestrator",
    description="Resume screening, onboarding planning and policy Q&A in one call.",
    version="2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory record of past runs, keyed by session_id.
SESSION_MEMORY: Dict[str, Dict[str, Any]] = {}


class CandidateInfo(BaseModel):
    name: str
    College: str
    Tech_skills: List[str]
    Soft_skills: List[str]
    CGPA: str = "N/A"
    score: int


class OrchestratorResponse(BaseModel):
    session_id: str
    candidate_info: CandidateInfo
    onboarding_plan: str
    policy_answers: Dict[str, str]


def _parse_questions(raw: str) -> List[str]:
    """Accept a JSON array, a single question string, or an empty value."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]  # treat as one plain-text question
    if isinstance(parsed, list):
        return [str(q).strip() for q in parsed if str(q).strip()]
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


def _pause():
    """Optional pause between Gemini calls (helps with free-tier rate limits)."""
    if GEMINI_REQUEST_DELAY > 0:
        time.sleep(GEMINI_REQUEST_DELAY)


@app.get("/")
def root():
    return {
        "service": "HR Automation Orchestrator",
        "endpoint": "POST /orchestrate (multipart/form-data)",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# NOTE: this is a synchronous (def) endpoint on purpose — FastAPI runs it in a
# worker thread, which keeps CrewAI's blocking kickoff() out of the event loop.
@app.post("/orchestrate", response_model=OrchestratorResponse)
def orchestrate_workflow(
    job_role: str = Form(...),
    policy_questions: str = Form("[]"),
    pdf_file: UploadFile = File(...),
):
    if not (pdf_file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    if not job_role.strip():
        raise HTTPException(status_code=400, detail="job_role must not be empty.")

    questions_list = _parse_questions(policy_questions)
    session_id = str(uuid.uuid4())

    # Save the upload to a temp file so the PDF extractor can open it.
    fd, temp_pdf_path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        f.write(pdf_file.file.read())

    try:
        # 1. Resume screening: extract fields and score the candidate.
        logger.info("Extracting text from %s", pdf_file.filename)
        try:
            resume_text = extract_text_from_pdf(temp_pdf_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Could not read the PDF: {exc}"
            )

        fields = call_gemini_extract_fields(resume_text)
        _pause()
        score = call_gemini_score(fields, job_role)
        _pause()

        candidate_info = CandidateInfo(
            name=fields.get("Name", "N/A"),
            College=fields.get("College", "N/A"),
            Tech_skills=fields.get("Tech Skills", []),
            Soft_skills=fields.get("Soft Skills", []),
            CGPA=fields.get("CGPA", "N/A"),
            score=score,
        )

        # Persist to MongoDB if configured (non-fatal if it fails).
        store_candidate_in_mongo(
            {
                "Name": candidate_info.name,
                "College": candidate_info.College,
                "CGPA": candidate_info.CGPA,
                "Tech Skills": candidate_info.Tech_skills,
                "Soft Skills": candidate_info.Soft_skills,
                "Score": candidate_info.score,
                "Job Role": job_role,
                "Source File": pdf_file.filename,
                "session_id": session_id,
            }
        )

        # 2. Generate onboarding plan (include the job role for context).
        logger.info("Generating onboarding plan (score=%s)", score)
        candidate_for_plan = candidate_info.model_dump()
        candidate_for_plan["job description"] = job_role
        onboarding_plan = generate_onboarding_plan(candidate_for_plan)

        # 3. Policy Q&A with running conversation memory.
        policy_answers: Dict[str, str] = {}
        session_memory: List[Dict[str, str]] = []
        for question in questions_list:
            logger.info("Answering policy question: %s", question)
            _pause()
            context = retrieve_context(question)
            prompt = build_prompt(question, context, session_memory)
            answer = gemini_chat(prompt)
            policy_answers[question] = answer
            session_memory.append({"question": question, "answer": answer})

        SESSION_MEMORY[session_id] = {
            "job_role": job_role,
            "candidate": candidate_info.model_dump(),
            "policy": session_memory,
        }

        return OrchestratorResponse(
            session_id=session_id,
            candidate_info=candidate_info,
            onboarding_plan=onboarding_plan,
            policy_answers=policy_answers,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        # Typically a missing/invalid GEMINI_API_KEY — surface the real reason.
        logger.exception("Configuration error")
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Orchestration failed")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {exc}")
    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9000)
