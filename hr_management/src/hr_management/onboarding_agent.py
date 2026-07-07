"""Onboarding plan agent.

Uses a CrewAI agent (backed by Gemini) to generate a day-by-day onboarding
plan whose length depends on the candidate's screening score:
  score <= 50 -> 7 days, 51-80 -> 5 days, > 80 -> 3 days.

Can also be run directly to backfill onboarding plans for hired candidates
stored in MongoDB:  python onboarding_agent.py
"""
import logging
from typing import Dict

from crewai import Agent, Crew, Task, LLM
from pymongo import MongoClient

from config import (
    GEMINI_MODEL,
    MONGODB_COLLECTION,
    MONGODB_DB_NAME,
    MONGODB_URI,
    require_gemini_key,
)

logger = logging.getLogger(__name__)

_planner = None  # created lazily so importing this module never needs an API key


def _get_planner() -> Agent:
    global _planner
    if _planner is None:
        api_key = require_gemini_key()
        _planner = Agent(
            role="Onboarding Planner",
            goal=(
                "Create detailed and personalized onboarding plans for new "
                "employees of varying lengths."
            ),
            backstory=(
                "You are an expert HR specialist who designs structured and"
                " engaging onboarding programs. Your plans are known for being"
                " highly effective, ensuring new hires feel welcomed, informed,"
                " and ready to contribute."
            ),
            llm=LLM(model=f"gemini/{GEMINI_MODEL}", api_key=api_key),
            verbose=False,
            allow_delegation=False,
        )
    return _planner


def _get_field(candidate: Dict, *keys, default=None):
    """Read a candidate field tolerating the key spellings used across the
    project (e.g. 'Tech_skills', 'Tech Skills', 'Tech skills')."""
    for key in keys:
        value = candidate.get(key)
        if value not in (None, "", []):
            return value
    return default


def _as_skill_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value) if value else "N/A"


def generate_onboarding_plan(candidate: Dict) -> str:
    """Generate a markdown-table onboarding plan sized to the candidate's score."""
    score = candidate.get("score", candidate.get("Score", 75))
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 75

    if score <= 50:
        plan_duration = 7
    elif score <= 80:
        plan_duration = 5
    else:
        plan_duration = 3

    logger.info("Candidate score is %s. Generating a %s-day plan.", score, plan_duration)

    tech = _get_field(candidate, "Tech_skills", "Tech Skills", "Tech skills", default=[])
    soft = _get_field(candidate, "Soft_skills", "Soft Skills", "Soft skills", default=[])
    candidate_info = (
        f"Name: {_get_field(candidate, 'name', 'Name', default='N/A')}\n"
        f"College: {_get_field(candidate, 'College', 'college', default='N/A')}\n"
        f"Tech Skills: {_as_skill_text(tech)}\n"
        f"Soft Skills: {_as_skill_text(soft)}\n"
    )
    job_description = _get_field(
        candidate, "job description", "job_role", "Job Role",
        default="No job description provided",
    )

    onboarding_task = Task(
        description=(
            f"Generate a concise, day-by-day {plan_duration}-day onboarding plan "
            "for the following new hire.\n\n"
            f"**Job Role:**\n{job_description}\n\n"
            f"**Candidate's Profile:**\n{candidate_info}"
        ),
        expected_output=(
            "A short and crisp onboarding plan presented in a markdown table. "
            "The table should have three columns: 'Day', 'Key Activities', and "
            f"'Goal'. Provide one row for each day of the {plan_duration}-day plan."
        ),
        agent=_get_planner(),
    )

    crew = Crew(agents=[_get_planner()], tasks=[onboarding_task], verbose=False)
    result = crew.kickoff()
    return result.raw


def process_database_candidates() -> None:
    """Find hired candidates in MongoDB without a plan and generate one each."""
    if not MONGODB_URI:
        print("MONGODB_URI is not set in .env — nothing to process.")
        return

    client = None
    try:
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
        collection = client[MONGODB_DB_NAME][MONGODB_COLLECTION]
        print("\n✅ Successfully connected to MongoDB.")

        query = {"Hired": "Yes", "onboarding_plan": {"$exists": False}}
        candidates_to_process = list(collection.find(query))

        if not candidates_to_process:
            print("\nNo new hired candidates found to process.")
            return

        print(f"\nFound {len(candidates_to_process)} hired candidates to process...")

        for candidate in candidates_to_process:
            candidate_name = candidate.get("Name", candidate.get("name", "Unknown"))
            print("\n" + "=" * 60)
            print(f"Processing candidate: {candidate_name}")

            plan = generate_onboarding_plan(candidate)

            update_result = collection.update_one(
                {"_id": candidate["_id"]}, {"$set": {"onboarding_plan": plan}}
            )
            if update_result.modified_count > 0:
                print(f"✅ Saved onboarding plan for {candidate_name}.")
            else:
                print(f"⚠️ Plan generated for {candidate_name} but not saved.")

    except Exception as exc:
        print(f"❌ An error occurred during database processing: {exc}")
    finally:
        if client is not None:
            client.close()
            print("\nMongoDB connection closed.")


if __name__ == "__main__":
    process_database_candidates()
    print("\n" + "=" * 60)
    print("Onboarding process complete.")
    print("=" * 60)
