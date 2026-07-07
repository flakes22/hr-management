"""Sequential HR pipeline used by the CLI (main.py).

Chains the three agents — resume screening, onboarding planning and policy
Q&A — as plain functions, with a small shared memory that records every
step's output.
"""
from typing import Dict, List, Tuple

from onboarding_agent import generate_onboarding_plan
from policy_agent import build_prompt, gemini_chat, retrieve_context
from resume_agent import (
    call_gemini_extract_fields,
    call_gemini_score,
    extract_text_from_pdf,
)


class Memory:
    """Tiny append-only store keyed by pipeline stage."""

    def __init__(self):
        self.data: Dict[str, list] = {}

    def add(self, key, value):
        self.data.setdefault(key, []).append(value)

    def get(self, key):
        return self.data.get(key, [])


memory = Memory()


def resume_screening_task(pdf_path: str, job_role: str) -> Tuple[dict, int]:
    """Extract candidate fields from a resume PDF and score them for the role."""
    resume_text = extract_text_from_pdf(pdf_path)
    fields = call_gemini_extract_fields(resume_text)
    score = call_gemini_score(fields, job_role)
    memory.add(
        "resume_screening", {"fields": fields, "score": score, "job_role": job_role}
    )
    return fields, score


def onboarding_task(fields: dict, score: int, job_role: str) -> str:
    """Generate an onboarding plan for the screened candidate."""
    candidate = {
        "name": fields.get("Name"),
        "College": fields.get("College"),
        "Tech_skills": fields.get("Tech Skills", []),
        "Soft_skills": fields.get("Soft Skills", []),
        "score": score,
        "job description": job_role,
    }
    plan = generate_onboarding_plan(candidate)
    memory.add("onboarding", {"candidate": candidate, "plan": plan})
    return plan


def policy_qa_task(questions: List[str]) -> List[str]:
    """Answer policy questions, threading earlier Q&A in as conversation memory."""
    session_memory = list(memory.get("policy_qa"))
    answers = []
    for question in questions:
        context = retrieve_context(question)
        prompt = build_prompt(question, context, session_memory)
        answer = gemini_chat(prompt)
        answers.append(answer)
        entry = {"question": question, "answer": answer}
        session_memory.append(entry)
        memory.add("policy_qa", entry)
    return answers
