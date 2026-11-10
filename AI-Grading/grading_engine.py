import json
import math
import os
from pathlib import Path

import requests


MODEL = os.getenv("GRADING_MODEL", "qwen3.5:9b")
OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://127.0.0.1:11434/api/chat"
)
PROMPT_FILE = Path(__file__).parent / "prompts" / "grading_system.txt"


def valid_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


# ตรวจข้อมูลที่ส่งมาจากระบบข้อสอบ
def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid grading request.")

    if data.get("question_type") != "descriptive":
        raise ValueError("Only descriptive questions can use AI grading.")

    question = data.get("question_text")
    answer = data.get("student_answer")
    reference = data.get("reference_answer", "")
    maximum = data.get("max_score")
    rubric = data.get("rubric")

    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question text is required.")

    if not isinstance(answer, str):
        raise ValueError("Invalid student answer.")

    if not isinstance(reference, str):
        raise ValueError("Invalid reference answer.")

    if not valid_number(maximum) or maximum <= 0:
        raise ValueError("Invalid maximum score.")

    if not isinstance(rubric, list) or len(rubric) > 50:
        raise ValueError("Invalid rubric.")

    ids = set()
    total = 0

    for item in rubric:
        if not isinstance(item, dict):
            raise ValueError("Invalid rubric criterion.")

        criterion_id = item.get("criterion_id")
        description = item.get("description")
        points = item.get("max_score")

        if (
            not isinstance(criterion_id, str)
            or not criterion_id.strip()
            or criterion_id in ids
            or criterion_id in ("OVERALL", "UNSPECIFIED")
        ):
            raise ValueError("Invalid criterion ID.")

        if not isinstance(description, str) or not description.strip():
            raise ValueError("Invalid criterion description.")

        if not valid_number(points) or points <= 0:
            raise ValueError("Invalid criterion score.")

        ids.add(criterion_id)
        total += points

    if total > maximum + 0.001:
        raise ValueError("Rubric points exceed question maximum.")


# เตรียมเกณฑ์คะแนนโดยยึด Rubric ของอาจารย์
def prepare_rubric(data):
    criteria = []
    total = 0

    for item in data["rubric"]:
        criteria.append({
            "criterion_id": item["criterion_id"],
            "description": item["description"],
            "max_score": item["max_score"],
            "source": "teacher",
        })

        total += item["max_score"]

    maximum = data["max_score"]

    if len(criteria) == 0:
        return [{
            "criterion_id": "OVERALL",
            "description": (
                "Evaluate all requirements explicitly stated "
                "in the question. Use the reference answer "
                "as guidance, not as the only valid solution."
            ),
            "max_score": maximum,
            "source": "overall",
        }], "missing"

    remaining = round(maximum - total, 2)

    if remaining > 0.001:
        criteria.append({
            "criterion_id": "UNSPECIFIED",
            "description": (
                "Evaluate only explicit question requirements "
                "that are not already covered by the teacher "
                "rubric. Do not award points twice."
            ),
            "max_score": remaining,
            "source": "unallocated",
        })

        return criteria, "partial"

    return criteria, "complete"


# กำหนดช่องผลตรวจที่ Qwen ต้องส่งให้ครบ
def get_result_schema():
    criterion = {
        "type": "object",
        "properties": {
            "criterion_id": {
                "type": "string"
            },
            "awarded_score": {
                "type": "number"
            },
            "evidence": {
                "type": "string"
            },
            "missing": {
                "type": "string"
            },
            "reason": {
                "type": "string"
            },
        },
        "required": [
            "criterion_id",
            "awarded_score",
            "evidence",
            "missing",
            "reason",
        ],
    }

    return {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "array",
                "items": criterion,
            },
            "feedback": {
                "type": "string"
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "review_reasons": {
                "type": "array",
                "items": {
                    "type": "string"
                },
            },
        },
        "required": [
            "criteria",
            "feedback",
            "confidence",
            "review_reasons",
        ],
    }


# ส่งคำตอบไปให้ Qwen ประเมินตาม Prompt
def ask_ollama(data, criteria, rubric_status, correction=""):
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()

    if not prompt:
        raise ValueError("Grading prompt is empty.")

    grading_data = {
        "question": data["question_text"],
        "reference_answer": data.get("reference_answer", ""),
        "student_answer": data["student_answer"],
        "max_score": data["max_score"],
        "rubric": criteria,
        "rubric_status": rubric_status,
    }

    if correction:
        grading_data["format_correction"] = correction

    user_message = (
        "Evaluate the following student answer using all "
        "the specified criteria. Return exactly one JSON "
        "object matching the required schema.\n\n"
        + json.dumps(grading_data, ensure_ascii=False)
    )

    if correction:
        user_message += (
            "\n\nThe previous grading result was rejected "
            "because: " + correction
            + "\nRe-evaluate the student answer. "
            "Include all five fields for every criterion: "
            "criterion_id, awarded_score, evidence, "
            "missing, and reason. "
            "Never invent evidence."
        )

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
            "stream": False,
            "think": False,
            "format": get_result_schema(),
            "options": {
                "temperature": 0.1,
                "num_predict": 4500,
            },
        },
        timeout=300,
    )

    response.raise_for_status()

    response_data = response.json()
    message = response_data.get("message", {})
    content = message.get("content", "")

    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI returned an empty grading result.")

    return json.loads(content)


# ตรวจความครบถ้วนและขอบเขตคะแนนจาก Qwen
def check_result(result, criteria, rubric_status, student_answer):
    if not isinstance(result, dict):
        raise ValueError("Invalid grading result.")

    if not isinstance(result.get("criteria"), list):
        raise ValueError("Missing grading criteria.")

    expected = {}

    for criterion in criteria:
        expected[criterion["criterion_id"]] = criterion

    received = {}

    for item in result["criteria"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid grading criterion.")

        criterion_id = item.get("criterion_id")

        if criterion_id not in expected:
            raise ValueError("Unknown grading criterion.")

        if criterion_id in received:
            raise ValueError("Repeated grading criterion.")

        score = item.get("awarded_score")
        maximum = expected[criterion_id]["max_score"]

        if (
            not valid_number(score)
            or score < 0
            or score > maximum
        ):
            raise ValueError(
                "AI score is outside the allowed range."
            )

        for field in ("evidence", "missing", "reason"):
            value = item.get(field)

            if not isinstance(value, str):
                raise ValueError(
                    "Missing grading explanation: " + field
                )

        if not item["reason"].strip():
            raise ValueError("Grading reason is empty.")

        if score > 0 and not item["evidence"].strip():
            raise ValueError(
                "Awarded score has no student evidence."
            )

        received[criterion_id] = item

    if set(received.keys()) != set(expected.keys()):
        raise ValueError("AI did not grade every criterion.")

    feedback = result.get("feedback")

    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError("Missing AI feedback.")

    confidence = result.get("confidence")

    if confidence not in ("high", "medium", "low"):
        raise ValueError("Invalid confidence value.")

    review_reasons = result.get("review_reasons")

    if not isinstance(review_reasons, list):
        raise ValueError("Invalid review reasons.")

    for reason in review_reasons:
        if not isinstance(reason, str):
            raise ValueError("Invalid review reason.")

    checked = []
    total = 0

    for criterion in criteria:
        item = received[criterion["criterion_id"]]
        score = round(item["awarded_score"], 2)

        checked.append({
            "criterion_id": criterion["criterion_id"],
            "description": criterion["description"],
            "source": criterion["source"],
            "max_score": criterion["max_score"],
            "awarded_score": score,
            "evidence": item["evidence"],
            "missing": item["missing"],
            "reason": item["reason"],
        })

        total += score

    maximum = round(
        sum(item["max_score"] for item in criteria),
        2
    )

    total = round(total, 2)

    if total > maximum:
        raise ValueError("Total score exceeds maximum.")

    return {
        "criteria": checked,
        "suggested_score": total,
        "max_score": maximum,
        "feedback": feedback,
        "confidence": confidence,
        "review_reasons": review_reasons,
        "rubric_status": rubric_status,
    }


# ตรวจคำตอบหนึ่งข้อและคืนคะแนนแนะนำ
def grade_answer(data):
    validate_request(data)

    criteria, rubric_status = prepare_rubric(data)

    # ไม่มีคำตอบให้คะแนนศูนย์โดยไม่เรียก AI
    if not data["student_answer"].strip():
        return {
            "criteria": [
                {
                    "criterion_id": item["criterion_id"],
                    "description": item["description"],
                    "source": item["source"],
                    "max_score": item["max_score"],
                    "awarded_score": 0,
                    "evidence": "",
                    "missing": "ไม่ได้ส่งคำตอบ",
                    "reason": "ไม่มีคำตอบให้ประเมิน",
                }
                for item in criteria
            ],
            "suggested_score": 0,
            "max_score": data["max_score"],
            "feedback": "ไม่ได้ส่งคำตอบ จึงแนะนำคะแนน 0",
            "confidence": "high",
            "review_reasons": [],
            "rubric_status": rubric_status,
        }

    error_message = ""

    # ขอให้ Qwen ตรวจใหม่เมื่อผลตอบกลับไม่ถูกต้อง
    for attempt in range(2):
        try:
            result = ask_ollama(
                data,
                criteria,
                rubric_status,
                error_message
            )

            return check_result(
                result,
                criteria,
                rubric_status,
                data["student_answer"]
            )

        except (ValueError, TypeError, KeyError) as error:
            error_message = str(error)

            if attempt == 1:
                raise ValueError(
                    "AI grading could not return a valid result: "
                    + error_message
                )

    raise ValueError("AI grading failed.")
