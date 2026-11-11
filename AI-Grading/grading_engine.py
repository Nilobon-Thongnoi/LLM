import json
import math
import os
import re
from pathlib import Path

import requests


MODEL = os.getenv("GRADING_MODEL", "qwen3.5:9b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
PROMPT_FILE = Path(__file__).parent / "prompts" / "grading_system.txt"


def valid_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


# ตรวจข้อมูลที่เว็บส่งมา
def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid grading request.")
    if data.get("question_type") != "descriptive":
        raise ValueError("Only descriptive questions can use AI grading.")
    if not isinstance(data.get("question_text"), str) or not data["question_text"].strip():
        raise ValueError("Question text is required.")
    if not isinstance(data.get("student_answer"), str):
        raise ValueError("Invalid student answer.")
    if not isinstance(data.get("reference_answer", ""), str):
        raise ValueError("Invalid reference answer.")

    maximum = data.get("max_score")
    if not valid_number(maximum) or maximum <= 0:
        raise ValueError("Invalid maximum score.")

    rubric = data.get("rubric")
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


# ใช้คะแนน Rubric ของอาจารย์ ไม่ตั้งคะแนนขึ้นเอง
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
    if not criteria:
        return [{
            "criterion_id": "OVERALL",
            "description": (
                "Evaluate all explicit question requirements. "
                "Use the reference as guidance, not as the only solution."
            ),
            "max_score": maximum,
            "source": "overall",
        }], "missing"

    remaining = round(maximum - total, 2)
    if remaining > 0.001:
        criteria.append({
            "criterion_id": "UNSPECIFIED",
            "description": (
                "Evaluate only explicit requirements not already covered "
                "by the teacher rubric. Do not award duplicate credit."
            ),
            "max_score": remaining,
            "source": "unallocated",
        })
        return criteria, "partial"

    return criteria, "complete"


# บังคับรูปแบบผลลัพธ์ให้ตรงกับ Next.js
def get_result_schema():
    criterion = {
        "type": "object",
        "properties": {
            "criterion_id": {"type": "string"},
            "awarded_score": {"type": "number"},
            "evidence": {"type": "string"},
            "missing": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": [
            "criterion_id", "awarded_score", "evidence", "missing", "reason"
        ],
    }
    return {
        "type": "object",
        "properties": {
            "criteria": {"type": "array", "items": criterion},
            "feedback": {"type": "string"},
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "review_reasons": {
                "type": "array", "items": {"type": "string"}
            },
        },
        "required": [
            "criteria", "feedback", "confidence", "review_reasons"
        ],
    }


def get_grading_instructions():
    return (
        "GRADING PROCEDURE:\n"
        "1. Identify all explicit question requirements and subparts.\n"
        "2. Break each rubric criterion into independently verifiable components.\n"
        "3. For each component, check the actual submitted evidence.\n"
        "4. Full points require all essential components to be correct.\n"
        "5. Award partial points for actual correct work, even if other work fails.\n"
        "6. Do not infer absent work or reward keywords alone.\n"
        "7. Never penalize the same mistake repeatedly unless it separately "
        "invalidates another rubric requirement.\n"
        "8. Use the reference as guidance; correct alternatives are acceptable.\n"
        "9. If the reference is missing, derive requirements from the question.\n"
        "10. Preserve IDs, weights and teacher point allocations.\n"
        "11. When a criterion has multiple parts, explain which parts passed.\n"
        "12. Evidence, missing and reason must accurately match the code or answer.\n"
        "13. Output concise Thai explanations; lower confidence if uncertain.\n\n"
        "JAVASCRIPT FACTS:\n"
        "JavaScript variable names are case-sensitive: cc and CC differ.\n"
        "Math.round(301.5) is 302, an integer, NOT 301.5.\n"
        "Math.round, Math.trunc and Math.floor all produce integer numbers "
        "for finite inputs, but their rounding behavior differs.\n"
        "Evaluate actual thresholds and arithmetic; 1200 * 1.5 = 1800.\n"
        "Read all code before grading; distinguish function declaration, "
        "parameter use, condition structure, calculation and output.\n"
        "A present return statement is present even if some paths return undefined.\n"
        "If console.log is accepted by the rubric, do not require return.\n"
        "Do not give full execution credit to code with an undefined variable.\n"
        "Do not automatically remove all conceptual partial credit for one typo.\n"
        "Do not treat comments as implemented algorithms or calculations.\n"
        "Do not claim code was executed or tests were run.\n\n"
        "ALGORITHMS:\n"
        "Check correctness, necessary steps, termination, Big-O and its reasoning "
        "when requested. Missing steps cannot receive full credit.\n\n"
        "ER DIAGRAMS:\n"
        "Inspect actual JSON tables, field names, data types, keys, "
        "relationship endpoints and cardinalities. Ignore visual coordinates.\n"
        "Identify required entities and attributes from the question. "
        "Missing necessary attributes or relationships reduce relevant points.\n"
        "Accept valid equivalent designs. Never invent missing tables or links.\n\n"
        "FINAL CHECK:\n"
        "Check every factual statement and all arithmetic once again.\n"
        "Do not claim a statement, variable or field is absent if it is present.\n"
        "Do not claim an incomplete answer fully satisfies a broad criterion.\n"
        "Return the required JSON only."
    )


# ส่งโจทย์ เฉลย คำตอบ และ Rubric ไปให้ Qwen
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

    user_message = (
        "Evaluate the student answer carefully.\n\n"
        + get_grading_instructions()
        + "\n\nEXAM DATA:\n"
        + json.dumps(grading_data, ensure_ascii=False)
    )
    if correction:
        user_message += (
            "\n\nYour previous result was rejected: " + correction
            + "\nCorrect the factual mistake and re-evaluate all criteria. "
            "Keep valid partial credit and do not fabricate evidence."
        )

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "think": False,
            "format": get_result_schema(),
            "options": {"temperature": 0.1, "num_predict": 4500},
        },
        timeout=300,
    )
    response.raise_for_status()
    content = response.json().get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI returned an empty grading result.")
    return json.loads(content)


# ตรวจข้อผิดพลาดที่ยืนยันได้โดยไม่รันโค้ดนักศึกษา
def check_known_facts(result, student_answer):
    # ตรวจเฉพาะข้อสรุปที่ผิดแน่นอน ไม่เดาคะแนนแทน Qwen
    details = " ".join(
        str(item.get("missing", "")) + " " + str(item.get("reason", ""))
        for item in result["criteria"]
    )

    if "Math.round" in student_answer:
        if re.search(
            r"Math\.round[^\n.]{0,110}(?:301\.5|ทศนิยม|ไม่เป็นจำนวนเต็ม)",
            details,
            flags=re.IGNORECASE,
        ):
            raise ValueError(
                "Math.round returns an integer for finite numbers; "
                "Math.round(301.5) is 302. Re-check integer conversion."
            )

    # ตัด comment ออกเพื่อเช็กการมีอยู่ของ return แบบเบื้องต้น
    code = re.sub(r"/\*[\s\S]*?\*/", " ", student_answer)
    code = re.sub(r"//[^\n]*", " ", code)
    has_return = re.search(r"\breturn\b", code) is not None
    if has_return:
        claims_no_return = re.search(
            r"(?:ไม่มี|ขาด|ไม่ได้ใช้|ไม่พบ)\s*(?:คำสั่ง\s*)?return\b"
            r"|\bno\s+return\s+statement\b",
            details,
            flags=re.IGNORECASE,
        )
        if claims_no_return:
            raise ValueError(
                "The answer contains a return statement. "
                "Check the returned value instead of claiming return is absent."
            )


# ตรวจโครงสร้างผลลัพธ์และรวมคะแนนรายเกณฑ์
def check_result(result, criteria, rubric_status, student_answer):
    if not isinstance(result, dict):
        raise ValueError("Invalid grading result.")
    if not isinstance(result.get("criteria"), list):
        raise ValueError("Missing grading criteria.")

    expected = {item["criterion_id"]: item for item in criteria}
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
        if not valid_number(score) or score < 0 or score > maximum:
            raise ValueError("AI score is outside the allowed range.")
        for field in ("evidence", "missing", "reason"):
            if not isinstance(item.get(field), str):
                raise ValueError("Missing grading explanation: " + field)
        if not item["reason"].strip():
            raise ValueError("Grading reason is empty.")
        if score > 0 and not item["evidence"].strip():
            raise ValueError("Awarded score has no student evidence.")
        if 0 < score < maximum and not item["missing"].strip():
            raise ValueError("Partial score has no missing explanation.")
        received[criterion_id] = item

    if set(received) != set(expected):
        raise ValueError("AI did not grade every criterion.")
    if not isinstance(result.get("feedback"), str) or not result["feedback"].strip():
        raise ValueError("Missing AI feedback.")
    if result.get("confidence") not in ("high", "medium", "low"):
        raise ValueError("Invalid confidence value.")
    review_reasons = result.get("review_reasons")
    if not isinstance(review_reasons, list):
        raise ValueError("Invalid review reasons.")
    if not all(isinstance(value, str) for value in review_reasons):
        raise ValueError("Invalid review reason.")

    check_known_facts(result, student_answer)

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

    maximum = round(sum(item["max_score"] for item in criteria), 2)
    total = round(total, 2)
    if total > maximum:
        raise ValueError("Total score exceeds maximum.")

    confidence = result["confidence"]
    if rubric_status != "complete":
        if confidence == "high":
            confidence = "medium"
        review_reasons = list(review_reasons)
        note = "Teacher rubric is missing or does not cover all points."
        if note not in review_reasons:
            review_reasons.append(note)

    return {
        "criteria": checked,
        "suggested_score": total,
        "max_score": maximum,
        "feedback": result["feedback"],
        "confidence": confidence,
        "review_reasons": review_reasons,
        "rubric_status": rubric_status,
    }


# ตรวจคำตอบและคืนคะแนนแนะนำให้เว็บ
def grade_answer(data):
    validate_request(data)
    criteria, rubric_status = prepare_rubric(data)

    if not data["student_answer"].strip():
        return {
            "criteria": [{
                "criterion_id": item["criterion_id"],
                "description": item["description"],
                "source": item["source"],
                "max_score": item["max_score"],
                "awarded_score": 0,
                "evidence": "",
                "missing": "ไม่ได้ส่งคำตอบ",
                "reason": "ไม่มีคำตอบให้ประเมิน",
            } for item in criteria],
            "suggested_score": 0,
            "max_score": data["max_score"],
            "feedback": "ไม่ได้ส่งคำตอบ จึงแนะนำคะแนน 0",
            "confidence": "high",
            "review_reasons": [],
            "rubric_status": rubric_status,
        }

    error_message = ""
    for attempt in range(2):
        try:
            result = ask_ollama(data, criteria, rubric_status, error_message)
            return check_result(
                result, criteria, rubric_status, data["student_answer"]
            )
        except (ValueError, TypeError, KeyError) as error:
            error_message = str(error)
            if attempt == 1:
                raise ValueError(
                    "AI grading could not return a valid result: "
                    + error_message
                )

    raise ValueError("AI grading failed.")
