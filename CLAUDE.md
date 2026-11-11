# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Two standalone Python AI modules for a "Preliminary Exam" system whose main app is a separate Next.js + PostgreSQL project (not in this repo). Both modules call a local **Qwen (`qwen3.5:9b`) model via Ollama** and expose a small stdlib HTTP service that the Next.js server calls. The Python side never touches the database, authenticates students, or selects/saves hints — Next.js owns all of that.

- `AI-Hint/` — pre-generates 3 leveled Thai hints per descriptive question.
- `AI-Grading/` — suggests a rubric-based score for a descriptive answer (teacher reviews it).
- `Grading-Data/` — sample questions, rubrics, answer keys, student answers, and hints per exam part (Part1 Code Snippets, Part2 Programming/JS, Part3 Algorithm, Part4 ER Diagram). Reference/test material only; not loaded by code (its Part 2–4 content was hand-copied into `AI-Grading/knowledge/questions/`). Note `Part1_CodeSnippets  ` has trailing spaces in its directory name.

The only dependency is `requests` (`requirements.txt`). Code comments are in Thai; user-facing AI output (hints, feedback) must be Thai.

## Commands

There is no build step, linter, or test runner config; tests are plain scripts. Run from inside each module directory (modules import siblings by bare name, e.g. `from grading_engine import ...`).

```bash
pip3 install -r requirements.txt

# Grading: unittest with ask_ollama mocked (no Ollama needed)
cd AI-Grading && python3 test_grading.py
python3 test_knowledge.py                                       # RAG knowledge base + retrieval
python3 -m unittest test_grading.GradingTests.test_full_score   # single test

# Hint: plain-assert script with ask_qwen monkeypatched (no Ollama needed)
cd AI-Hint && python3 test_hint.py

# Hint: generate real hints from a JSON file (needs Ollama running)
cd AI-Hint && python3 hint_generator.py hint_test_input.json

# Run services (token is required, min 16 chars, sent as "Authorization: Bearer <token>")
HINT_SERVICE_TOKEN=... python3 AI-Hint/hint_service.py          # 127.0.0.1:8765, POST /generate, GET /health
GRADING_SERVICE_TOKEN=... python3 AI-Grading/grading_service.py # 127.0.0.1:8766, POST /grade,    GET /health

# Integration test against a running hint service (needs HINT_SERVICE_TOKEN, optional HINT_SERVICE_URL)
cd AI-Hint && python3 test_service.py
```

Env vars: `OLLAMA_URL` (default `http://127.0.0.1:11434/api/chat`), `QWEN_MODEL` (hint), `GRADING_MODEL` (grading), `GRADING_KNOWLEDGE_DIR` / `GRADING_MATCH_THRESHOLD` (grading RAG, defaults `AI-Grading/knowledge`, `0.45`), `*_SERVICE_HOST`/`*_SERVICE_PORT`/`*_SERVICE_TOKEN`.

## Architecture

Both modules follow the same pattern: **validate input → build prompt from a template in `prompts/` → call Ollama `/api/chat` (non-streaming, `think: False`, low temperature) → strictly validate model output → retry with a correction message on failure → raise `ValueError`**. The service layer maps `ValueError`/`KeyError`/`TypeError` to HTTP 400 and anything else to a generic 500. Tests mock the single Ollama-calling function (`ask_qwen` / `ask_ollama`), so keep all network access in those functions.

### AI-Hint (`hint_generator.py`, `hint_service.py`)
- Input: `is_descriptive: true`, `exam_part` (int), `question_text`, optional `rubric`/`reference_answer` (strings), `question_id`, and `teacher_hints: [{level, text}]`.
- Output always has exactly 3 hints (levels 1–3). Teacher hints are kept verbatim at their level (`source: "teacher"`); missing levels are filled by AI (`source: "ai"`), each generated with knowledge of all earlier hints. Up to 3 attempts per AI hint.
- AI hint validation: ≤300 chars, contains Thai, no code fences, no Big-O notation (to avoid leaking algorithm answers), not a duplicate of an existing hint. `hint_service.check_hints` re-validates the full set and rejects any altered teacher hint.
- `generate_hints` also returns `input_fingerprint` (SHA-256 over question data + teacher hints + prompt text) and `review_required`, but `hint_service` drops them: the HTTP response is only `{question_id, hints, status: "generated"}`. The fingerprint is currently only visible via the CLI/tests.
- The active prompt is `prompts/pre_generated_hint_prompt.txt`; it must contain placeholders `{EXAM_PART}`, `{QUESTION}`, `{RUBRIC}`, `{REFERENCE_ANSWER}`, `{EXISTING_HINTS}`, `{HINT_LEVEL}` or `build_prompt` raises. Other files in `prompts/` and `hint_prompt.txt` are not referenced by code.
- `README_HINT.txt` describes an older per-request design (`hint_api.py`, `POST /hint`, `previous_hints`, student answer input) that no longer matches the code — trust the code. Its Next.js integration notes (teacher hint precedence, 3-hint limit, `session_hint_logs`, don't change the DB schema without the team) still describe the intended contract.
- `output/` contains sample JSON results (pending/approved); nothing in the code writes there.

### AI-Grading (`grading_engine.py`, `grading_service.py`)
- Input: `question_type: "descriptive"`, `question_text`, `student_answer`, `reference_answer`, `max_score`, `rubric: [{criterion_id, description, max_score}]`.
- `prepare_rubric` determines `rubric_status`: `complete` (teacher rubric sums to max), `partial` (adds an `UNSPECIFIED` criterion for the remaining points), or `missing` (single `OVERALL` criterion). These IDs are reserved and rejected in teacher input.
- Empty student answers return score 0 without calling the model.
- RAG: `grade_answer` calls `knowledge_base.retrieve_knowledge` once, then passes the result to `ask_ollama(..., knowledge=...)`, which sends it as `retrieved_knowledge` in the user message. The prompt rules for using it are the `K1`–`K7` rules in `grading_system.txt`. The result gets a `knowledge_id` (matched entry id or `None`); Next.js ignores extra keys.
- Knowledge lives in `AI-Grading/knowledge/` (JSON only: no DB, no embeddings, no extra dependency), in two layers:
  - `general/<domain>.json`: one file per subject area (`javascript`, `code_snippet`, `logic`, `algorithm`, `er_diagram`). Fields: `domain`, `detect` (keywords that identify the area), `grading_guide` (rules always sent for that area), and `concepts: [{id, keywords, text}]` (verified facts). This layer covers **every** question, including ones with no question file. It is the part that scales to many exam sets.
  - `questions/*.json` (optional, per question): `id`, `domains`, `question` (full text, used for matching), `accepted_answers: [{label, answer}]`, `grading_notes`, and `graded_examples: [{label, student_answer, criterion_scores: [{criterion, awarded, max}], total, max_total, explanation}]`. Example criterion names are matched to the real rubric by meaning, because DB `criteria_id`s are unknown here. Use these files for questions with several correct answers or a history of mis-grading.
- Retrieval (`retrieve_knowledge`):
  1. **Question match.** The request has no question id, so the question file is chosen by character-trigram cosine between `question_text` and its `question`, and must score ≥ the threshold. The real question scores ~0.8–0.99, unrelated questions ≤0.3, and a short paraphrase ~0.35 (no match).
  2. **Domains.** The matched file's `domains` come first, then up to 2 domains detected by `detect` keyword hits (question hits count ×2, answer hits ×1; a domain needs ≥ half the top score).
  3. **Content sent.** All guides of the chosen domains; up to 6 concepts ranked by keyword hits in question + answer; the 3 graded examples most similar to the student answer.
  4. **Limits.** English keywords match whole words; others match as substrings. Avoid single-symbol keywords like `+` or `[`, which match almost everything. Total knowledge is capped at 14000 chars (concepts trimmed first, then examples). Files are reloaded when their mtime changes.
- `test_knowledge.py` checks that every bundled example's `total`/`max_total` equals the sum of its `criterion_scores`, that each question's `domains` exist, and that a new question in each domain gets that domain's knowledge.
- The output schema lists `evidence`, `missing`, `reason` **before** `awarded_score`, so the model writes its justification before it commits to a number. Keep that order.
- The model is constrained with Ollama's `format` JSON schema (`get_result_schema`); `check_result` then enforces every criterion graded exactly once, scores within bounds, non-empty reason, and evidence required for any non-zero score. One retry with the rejection reason fed back.
- Result includes per-criterion breakdown, `suggested_score`, `feedback`, `confidence`, `review_reasons` — it is a suggestion for teacher review, not a final grade.
- System prompt: `prompts/grading_system.txt`.

### Next.js caller side (hints; lives in the other repo)
- A server-only module (`import "server-only"`) sends `POST {HINT_SERVICE_URL}/generate` with a 600 s timeout. Its request always includes `question_id`, `exam_part` (positive ints), `question_text`, `rubric` and `reference_answer` (strings, possibly empty), and `teacher_hints`.
- It **re-validates the response**, so keep these invariants: `question_id` echoed back; `status === "generated"`; exactly 3 hints ordered by level 1–3; text non-empty and ≤300 code points after trim (Python's `len` and JS `Array.from().length` agree); teacher levels have `source: "teacher"` and text equal to the trimmed teacher text; other levels have `source: "ai"` and a Thai character in U+0E01–U+0E5B; no duplicates after trim + lowercase. Python's own checks are equal or stricter: it compares with `casefold` and also collapses whitespace.
- A non-2xx response turns into one generic error on the Next.js side, so the 400 error text never reaches the user.

### Next.js caller side (grading; lives in the other repo)
Data flows DB → Next.js → this service → Next.js → DB. Python only sees the JSON request body.
- A background worker picks one `answer` at a time: descriptive question, `exam_student.status = 'submitted'`, and `ai_suggested_score`, `ai_feedback`, `final_score` all NULL.
- Request mapping: `rubric_criteria.criteria_id` → `criterion_id` (stringified), `criterion_text` → `description`, `max_points` → `max_score`; `question.correct_answer` → `reference_answer`. `answer_json` is unwrapped (`answer`/`text` keys) or JSON-stringified, so ER-diagram answers arrive as a JSON string of `{tables, relationships}`.
- Blank answers (including empty ER diagrams) are scored 0 by Next.js without calling the service.
- `lib/server/grading-service.ts` sends `POST {GRADING_SERVICE_URL}/grade` with a 600 s timeout and **re-validates the response**. Changing the result shape will break it. It requires: unique `criterion_id`s; every teacher criterion present with the same `max_score`; `criteria.length == rubric.length + 1` when `rubric_status` is `partial`/`missing`; Σ`awarded_score` ≈ `suggested_score` and Σ`max_score` ≈ request `max_score` (±0.02); `confidence` ∈ high/medium/low; non-empty `reason`; non-empty `evidence` for any score > 0.
- Next.js ignores the Python `feedback` string. It builds its own Thai feedback (≤1000 chars, saved to `answer.ai_feedback`) from each criterion's `description`, `evidence`/`missing`/`reason`. It only uses text that is ≤100 chars, contains Thai, and is not truncated with `...`/`…`. Keep those per-criterion fields short, complete Thai sentences.
- The save is conditional: the update is skipped if a teacher `final_score` exists or `answer_json` changed meanwhile.
