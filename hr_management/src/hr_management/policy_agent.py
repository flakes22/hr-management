"""Policy Q&A agent.

Answers HR policy questions using simple keyword retrieval over
`policy_documents.json` plus a Gemini call, with per-session conversation
memory. Can run standalone as its own FastAPI app:

    uvicorn policy_agent:app --port 8001
"""
import json
import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config import GEMINI_MODEL, require_gemini_key

logger = logging.getLogger(__name__)

# Load policy documents from a path relative to this file, so it works no
# matter which directory the server is started from.
POLICY_DOCS_PATH = Path(__file__).resolve().parent / "policy_documents.json"
with open(POLICY_DOCS_PATH, encoding="utf-8") as f:
    POLICY_DOCS = json.load(f)

app = FastAPI(title="Policy Q&A Agent")
SESSIONS: Dict[str, List[Dict]] = {}

# Common words ignored during retrieval so matches favour meaningful terms.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "many", "may", "me", "my",
    "of", "on", "or", "tell", "the", "to", "we", "what", "when", "which",
    "who", "will", "with", "you",
}


class QARequest(BaseModel):
    session_id: Optional[str] = None
    questions: List[str]


class QAResponse(BaseModel):
    session_id: str
    answers: List[str]
    memory: List[Dict]
    retrieved_contexts: List[List[str]]


def retrieve_context(question: str, top_k: int = 3) -> List[str]:
    """Return the top_k policy paragraphs sharing the most keywords with the question."""
    q_words = {w for w in question.lower().split() if w not in _STOPWORDS}
    scores = []
    for doc in POLICY_DOCS:
        doc_words = set(doc["text"].lower().split())
        score = len(q_words & doc_words)
        scores.append((score, doc["text"]))
    scores.sort(key=lambda pair: pair[0], reverse=True)
    return [text for score, text in scores[:top_k] if score > 0]


def build_prompt(
    question: str, context: List[str], memory: List[Dict], role: str = "employee"
) -> str:
    history = "\n".join(f"Q: {m['question']} A: {m['answer']}" for m in memory)
    return (
        f"You are a helpful HR assistant answering questions from an {role}.\n"
        "Use the following policy context to answer the user's question as "
        "accurately as possible. If the context does not cover the question, "
        "say so instead of inventing a policy.\n"
        f"Policy Context:\n{chr(10).join(context) if context else 'N/A'}\n"
        f"Conversation History:\n{history}\n"
        f"User Question: {question}\n"
        "Answer:"
    )


def gemini_chat(prompt: str) -> str:
    """Send a prompt to the Gemini REST API and return the text answer."""
    api_key = require_gemini_key()
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    response = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    try:
        return result["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        logger.error("Unexpected Gemini response shape: %s", result)
        return "[ERROR] Could not parse Gemini response."


@app.post("/policy_qa", response_model=QAResponse)
def policy_qa(req: QARequest):
    session_id = req.session_id or str(uuid.uuid4())
    memory = SESSIONS.get(session_id, [])
    answers = []
    retrieved_contexts = []
    try:
        for question in req.questions:
            context = retrieve_context(question)
            prompt = build_prompt(question, context, memory)
            answer = gemini_chat(prompt)
            answers.append(answer)
            retrieved_contexts.append(context)
            memory.append({"question": question, "answer": answer})
    except RuntimeError as exc:  # missing API key
        raise HTTPException(status_code=500, detail=str(exc))
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}")
    SESSIONS[session_id] = memory
    return QAResponse(
        session_id=session_id,
        answers=answers,
        memory=memory,
        retrieved_contexts=retrieved_contexts,
    )
