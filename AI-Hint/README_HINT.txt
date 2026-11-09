// คำอธิบายว่าแต่ละไฟล์ใช้ทำอะไรและต้องเชื่อมอย่างไร
PRELIMINARY EXAM - AI HINT MODULE

Model: Qwen3.5:9b
Runtime: Ollama
Language: Python
Hint Output: Thai

========================================
PURPOSE
========================================

Generate AI hints for eligible descriptive
questions in the Preliminary Exam system.

This module is developed separately from
the Next.js application.

It only generates AI hints.
Teacher hint selection is handled by Next.js.

========================================
FILES
========================================

hint_generator.py
- Validates input
- Builds the AI prompt
- Calls Qwen via Ollama
- Validates generated hints
- Returns JSON

hint_prompt.txt
- Defines hint rules and levels

hint_api.py
- Provides HTTP endpoints
- GET /health
- POST /hint

hint_test_input.json
- Example request

test_hint.py
- Tests input rules with simulated AI

========================================
HINT REQUIREMENTS
========================================

1. Maximum 3 hints per question.
2. Teacher and AI hints share the limit.
3. Each hint is at most 300 characters.
4. Hints are opened in order.
5. Teacher hints are selected by Next.js.
6. AI creates a hint when no teacher hint
   exists at the requested level.
7. AI hints are written in Thai.
8. Do not reveal complete answers.
9. Student UI does not show hint source.
10. Only eligible descriptive questions
    can use hints.

========================================
INPUT FORMAT
========================================

{
  "is_descriptive": true,
  "question_text": "Exam question",
  "rubric": "Rubric for internal guidance",
  "student_answer": "Current student answer",
  "hint_level": 2,
  "previous_hints": [
    "Previously revealed hint"
  ]
}

previous_hints must include all teacher and
AI hints already revealed for this question,
ordered by reveal time.

========================================
OUTPUT FORMAT
========================================

{
  "hint_level": 2,
  "hint": "คำใบ้ภาษาไทย"
}

The AI module does not return a source label.

========================================
LOCAL TEST
========================================

Activate the Python environment:

source .venv/bin/activate

Install the package:

python -m pip install requests

Run rule tests:

python test_hint.py

Generate a real AI hint:

python hint_generator.py hint_test_input.json

Start API:

python hint_api.py

Health check:

curl http://127.0.0.1:8765/health

POST /hint accepts JSON.

========================================
NEXT.JS INTEGRATION
========================================

Existing file:
app/actions/student/hint.ts

The Next.js server should:

1. Authenticate the student.
2. Check exam assignment and time.
3. Check the question is eligible for hints.
4. Read previous hints from PostgreSQL.
5. Enforce the 3-hint maximum and sequence.
6. Check question_hints for a teacher hint
   at the requested hint_level.
7. If a teacher hint exists, use it.
8. Otherwise, call the Python POST /hint API.
9. Validate the generated hint before saving.
10. Save to session_hint_logs.
11. Return the saved hint to Student Exam.
12. Display stored hints in the Grading page.

Teacher hint:
hint_id = teacher hint ID
is_ai_generated = false

AI hint:
hint_id = null, if database schema allows
is_ai_generated = true

Do not modify the existing database schema
without checking with the team.

========================================
IMPORTANT NOTES
========================================

The Python module does not:
- Connect directly to PostgreSQL
- Select teacher hints
- Authenticate students
- Save hint logs
- Grade student answers
- Modify the Next.js project

The local API runs on 127.0.0.1 and has
no authentication. It must only be accessed
by a trusted server in local testing.

Production deployment requires appropriate
network configuration and access controls.

AI output must be tested for:
- Answer leakage
- Repeated meaning
- Incorrect guidance
- Language correctness
- Compliance with 300-character limit

========================================
HANDOFF
========================================

Send this module to Point for integration
with the central LLM system.

The Next.js integration must be tested
with PostgreSQL before final deployment.
