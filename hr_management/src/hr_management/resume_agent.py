"""Resume screening agent.

Provides:
  * PDF text extraction (pdfplumber + Tesseract OCR fallback)
  * Gemini-based field extraction and job-fit scoring
  * An optional standalone FastAPI app (port 8080) with a simple HTML flow:
    upload resume -> analyze for a job role -> mark top candidates as hired.

MongoDB storage is optional: if MONGODB_URI is not configured the agent
simply skips persistence and logs a notice.
"""
import ast
import json
import logging
import os
import re

import google.generativeai as genai
import pdfplumber
import pytesseract
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse
from pymongo import MongoClient

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    MONGODB_COLLECTION,
    MONGODB_DB_NAME,
    MONGODB_URI,
    require_gemini_key,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Resume Screening Agent")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

READ_PDFS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../read_pdfs")
)

HTML_FORM = """
<html>
    <body>
        <h2>Upload Resume PDF</h2>
        <form action="/upload_resume/" enctype="multipart/form-data" method="post">
            <input name="pdf_file" type="file" accept=".pdf"/>
            <input type="submit"/>
        </form>
    </body>
</html>
"""

JOB_ROLE_FORM = """
<html>
    <body>
        <h2>Enter Desired Job Role</h2>
        <form action="/analyze_resume/" method="post">
            <input name="job_role" type="text" placeholder="Job Role"/>
            <input type="submit"/>
        </form>
    </body>
</html>
"""

SELECT_N_FORM = """
<html>
    <body>
        <h2>How many candidates do you want to hire?</h2>
        <form action="/mark_hired/" method="post">
            <input name="n_hire" type="number" min="1" placeholder="Number to hire"/>
            <input type="submit"/>
        </form>
    </body>
</html>
"""


# ---------------------------------------------------------------- PDF reading
def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a PDF, combining embedded text with OCR.

    OCR requires the `tesseract` binary; if it is missing we fall back to
    embedded text only instead of failing the whole request.
    """
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            ocr_text = ""
            try:
                img = page.to_image(resolution=200).original.convert("L")
                img = img.point(lambda x: 0 if x < 180 else 255, "1")
                ocr_text = pytesseract.image_to_string(img)
            except Exception as exc:  # tesseract missing or OCR failure
                logger.warning("OCR skipped for a page: %s", exc)
            text += page_text + "\n" + ocr_text
    if not text.strip():
        raise ValueError(
            "Could not extract any text from the PDF. "
            "If it is a scanned document, install Tesseract OCR."
        )
    return text


# ------------------------------------------------------------- Gemini helpers
def _gemini_model() -> "genai.GenerativeModel":
    require_gemini_key()
    return genai.GenerativeModel(GEMINI_MODEL)


def _normalize_skills(value) -> list:
    """Gemini may return skills as a list, a comma-separated string, or null."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


def call_gemini_extract_fields(resume_text: str) -> dict:
    """Ask Gemini to pull structured fields out of raw resume text."""
    model = _gemini_model()
    prompt = (
        "Extract the following fields from the resume text. "
        "Return ONLY a JSON object with keys: Name, College, CGPA, "
        "Tech Skills, Soft Skills. 'Tech Skills' and 'Soft Skills' must be "
        "JSON arrays of strings. Use \"N/A\" when a field is missing.\n"
        "Resume text:\n" + resume_text
    )
    response = model.generate_content(prompt)
    cleaned = re.sub(
        r"^```json\s*|^```\s*|```$", "", response.text.strip(), flags=re.MULTILINE
    ).strip()
    try:
        fields = json.loads(cleaned)
    except Exception:
        try:
            fields = ast.literal_eval(cleaned)
        except Exception:
            logger.error("Failed to parse fields from Gemini response: %r", cleaned)
            fields = {}
    if not isinstance(fields, dict):
        fields = {}
    fields["Tech Skills"] = _normalize_skills(fields.get("Tech Skills"))
    fields["Soft Skills"] = _normalize_skills(fields.get("Soft Skills"))
    fields["Name"] = str(fields.get("Name") or "N/A")
    fields["College"] = str(fields.get("College") or "N/A")
    fields["CGPA"] = str(fields.get("CGPA") or "N/A")
    return fields


def call_gemini_score(fields: dict, job_role: str) -> int:
    """Score the candidate 0-100 for the given job role."""
    model = _gemini_model()
    prompt = (
        f"Score the following candidate out of 100 for the job role '{job_role}'.\n"
        f"Details: {fields}\n"
        "Give only the score as a number."
    )
    response = model.generate_content(prompt)
    score_match = re.search(r"\d+", response.text.strip())
    score = int(score_match.group()) if score_match else 0
    return max(0, min(100, score))


# ---------------------------------------------------------------- MongoDB
def store_candidate_in_mongo(candidate_data: dict) -> bool:
    """Persist a candidate document. Returns True on success."""
    if not MONGODB_URI:
        logger.info("MONGODB_URI not set — skipping database storage.")
        return False
    try:
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
        client[MONGODB_DB_NAME][MONGODB_COLLECTION].insert_one(candidate_data)
        client.close()
        return True
    except Exception as exc:
        logger.error("MongoDB insert error: %s", exc)
        return False


# ------------------------------------------------- Standalone web app (8080)
@app.get("/", response_class=HTMLResponse)
async def main():
    return HTML_FORM


def _next_resume_number() -> int:
    os.makedirs(READ_PDFS_PATH, exist_ok=True)
    nums = []
    for name in os.listdir(READ_PDFS_PATH):
        match = re.fullmatch(r"resume_text_(\d+)\.txt", name)
        if match:
            nums.append(int(match.group(1)))
    return max(nums, default=0) + 1


@app.post("/upload_resume/", response_class=HTMLResponse)
async def upload_resume(pdf_file: UploadFile = File(...)):
    pdf_path = f"temp_{pdf_file.filename}"
    with open(pdf_path, "wb") as f:
        f.write(await pdf_file.read())
    try:
        resume_text = extract_text_from_pdf(pdf_path)
    finally:
        os.remove(pdf_path)

    output_path = os.path.join(
        READ_PDFS_PATH, f"resume_text_{_next_resume_number()}.txt"
    )
    with open(output_path, "w", encoding="utf-8") as f2:
        f2.write(resume_text)

    return HTMLResponse(content=JOB_ROLE_FORM)


@app.post("/analyze_resume/", response_class=HTMLResponse)
async def analyze_resume(job_role: str = Form(...)):
    resume_files = [
        f
        for f in os.listdir(READ_PDFS_PATH)
        if re.fullmatch(r"resume_text_(\d+)\.txt", f)
    ]
    if not resume_files:
        return HTMLResponse(content="<h2>No resume found. Please upload first.</h2>")
    latest_file = max(resume_files, key=lambda f: int(re.search(r"\d+", f).group()))
    with open(os.path.join(READ_PDFS_PATH, latest_file), encoding="utf-8") as f:
        resume_text = f.read()

    fields = call_gemini_extract_fields(resume_text)
    score = call_gemini_score(fields, job_role)

    candidate_data = {
        "Name": fields["Name"],
        "College": fields["College"],
        "CGPA": fields["CGPA"],
        "Tech Skills": fields["Tech Skills"],
        "Soft Skills": fields["Soft Skills"],
        "Score": score,
        "Job Role": job_role,
        "Source File": latest_file,
    }
    stored = store_candidate_in_mongo(candidate_data)

    html = f"""
    <html>
        <body>
            <h2>Resume Analysis for Job Role: {job_role}</h2>
            <ul>
                <li><strong>Name:</strong> {fields['Name']}</li>
                <li><strong>College:</strong> {fields['College']}</li>
                <li><strong>CGPA:</strong> {fields['CGPA']}</li>
                <li><strong>Tech Skills:</strong> {', '.join(fields['Tech Skills']) or 'N/A'}</li>
                <li><strong>Soft Skills:</strong> {', '.join(fields['Soft Skills']) or 'N/A'}</li>
                <li><strong>Score (out of 100):</strong> {score}</li>
                <li><strong>Saved to database:</strong> {'Yes' if stored else 'No'}</li>
            </ul>
            <form action="/select_hired/" method="get">
                <button type="submit">Go to Hire Candidates</button>
            </form>
            <a href="/">Upload another resume</a>
        </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/select_hired/", response_class=HTMLResponse)
async def select_hired():
    return SELECT_N_FORM


@app.post("/mark_hired/", response_class=HTMLResponse)
async def mark_hired(n_hire: int = Form(...)):
    if not MONGODB_URI:
        return HTMLResponse(
            content="<h2>Database is not configured (set MONGODB_URI in .env).</h2>"
        )
    try:
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
        collection = client[MONGODB_DB_NAME][MONGODB_COLLECTION]
        collection.update_many({}, {"$set": {"Hired": "No"}})
        top_candidates = list(collection.find().sort("Score", -1).limit(n_hire))
        for candidate in top_candidates:
            collection.update_one(
                {"_id": candidate["_id"]}, {"$set": {"Hired": "Yes"}}
            )
        client.close()
        html = "<html><body><h2>Top candidates marked as Hired!</h2><ul>"
        for candidate in top_candidates:
            html += (
                f"<li>{candidate.get('Name', 'N/A')} "
                f"(Score: {candidate.get('Score', 'N/A')})</li>"
            )
        html += "</ul><a href='/'>Upload another resume</a></body></html>"
        return HTMLResponse(content=html)
    except Exception as exc:
        return HTMLResponse(content=f"<h2>Error: {exc}</h2>")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
