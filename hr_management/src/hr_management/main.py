"""Interactive command-line runner for the HR pipeline.

Run from this directory:  python main.py
"""
import os

from crew import memory, onboarding_task, policy_qa_task, resume_screening_task


def main():
    print("=== HR Automation Orchestrator (CLI) ===")

    # Resume screening
    pdf_path = input("Enter path to resume PDF: ").strip()
    if not os.path.isfile(pdf_path):
        print(f"File not found: {pdf_path}")
        return
    job_role = input("Enter desired job role: ").strip()

    fields, score = resume_screening_task(pdf_path, job_role)
    print("\nResume Screening Result:")
    print(fields)
    print(f"Score: {score}")

    # Onboarding plan
    plan = onboarding_task(fields, score, job_role)
    print("\nOnboarding Plan:")
    print(plan)

    # Policy Q&A
    questions = []
    print("\nEnter policy questions (type 'done' to finish):")
    while True:
        q = input("Question: ").strip()
        if q.lower() == "done":
            break
        if q:
            questions.append(q)

    if questions:
        answers = policy_qa_task(questions)
        print("\nPolicy Q&A Answers:")
        for q, a in zip(questions, answers):
            print(f"Q: {q}\nA: {a}\n")

    print("\nMemory System State:")
    print(memory.data)


if __name__ == "__main__":
    main()
