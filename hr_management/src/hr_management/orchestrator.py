import uuid
from typing import Dict, Any, List
import asyncio
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import your existing agent functions or classes:
# Assuming you have functions exposed to call these agents programmatically.
from resume_agent import extract_text_from_pdf, call_gemini_extract_fields, call_gemini_score
from onboarding_agent import generate_onboarding_plan
from policy_agent import retrieve_context, build_prompt, gemini_chat

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple centralized memory storage, keyed by session_id
SESSION_MEMORY: Dict[str, Dict[str, Any]] = {}

# Define input/output request models for orchestration API
class CandidateInfo(BaseModel):
    name: str
    College: str
    Tech_skills: List[str]
    Soft_skills: List[str]
    CGPA: str = "N/A"
    score: int

class OrchestratorRequest(BaseModel):
    pdf_path: str
    job_role: str
    policy_questions: List[str] = []

class OrchestratorResponse(BaseModel):
    candidate_info: CandidateInfo
    onboarding_plan: str
    policy_answers: Dict[str, str]


@app.post("/orchestrate", response_model=OrchestratorResponse)
def orchestrate_workflow(
    job_role: str = Form(...),
    policy_questions: str = Form("[]"),
    pdf_file: UploadFile = File(...)
):
    import os, json, time
    
    session_id = str(uuid.uuid4())
    memory = {"policy": []}
    questions_list = json.loads(policy_questions)

    # Save UploadFile to a temporary file locally so extract methods can process it
    temp_pdf_path = f"temp_{uuid.uuid4()}_{pdf_file.filename}"
    with open(temp_pdf_path, "wb") as f:
        f.write(pdf_file.file.read())

    try:
        # 1. Resume screening: extract fields and score candidate
        resume_text = extract_text_from_pdf(temp_pdf_path)
        
        fields = call_gemini_extract_fields(resume_text)
        print("Fields extracted. Sleeping for 4s...")
        time.sleep(4)

        score = call_gemini_score(fields, job_role)
        print("Score extracted. Sleeping for 4s...")
        time.sleep(4)

        candidate_info = CandidateInfo(
            name=fields.get("Name", "N/A"),
            College=fields.get("College", "N/A"),
            Tech_skills=fields.get("Tech Skills", []),
            Soft_skills=fields.get("Soft Skills", []),
            CGPA=fields.get("CGPA", "N/A"),
            score=score
        )

        # 2. Generate onboarding plan
        onboarding_plan = generate_onboarding_plan(candidate_info.dict())

        # 3. Policy Q&A
        policy_answers = {}
        session_memory = []
        for question in questions_list:
            print("Processing question. Sleeping for 4s...")
            time.sleep(4)
            context = retrieve_context(question)
            prompt = build_prompt(question, context, session_memory)
            answer = gemini_chat(prompt)
            policy_answers[question] = answer
            session_memory.append({"question": question, "answer": answer})
        memory["policy"] = session_memory
        SESSION_MEMORY[session_id] = memory

        return OrchestratorResponse(
            candidate_info=candidate_info,
            onboarding_plan=onboarding_plan,
            policy_answers=policy_answers
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
    finally:
        # Cleanup temporary file
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=9000)



