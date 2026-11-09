import hashlib
import json
import os
import re
import sys
from pathlib import Path

import requests


MODEL = os.getenv("QWEN_MODEL", "qwen3.5:9b")
OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://127.0.0.1:11434/api/chat"
)

MAX_HINTS = 3
MAX_LENGTH = 300


# อ่าน Prompt ที่เตรียมไว้
def load_prompt():
    path = (
        Path(__file__).parent
        / "prompts"
        / "pre_generated_hint_prompt.txt"
    )
    return path.read_text(encoding="utf-8").strip()


def valid_text(text):
    return (
        isinstance(text, str)
        and bool(text.strip())
        and len(text.strip()) <= MAX_LENGTH
    )


def contains_thai(text):
    return (
        isinstance(text, str)
        and any("\u0e01" <= char <= "\u0e5b" for char in text)
    )


# ตรวจข้อมูลข้อสอบ
def validate_input(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid input.")

    if data.get("is_descriptive") is not True:
        raise ValueError("Descriptive questions only.")

    part = data.get("exam_part")

    if type(part) is not int or part <= 0:
        raise ValueError("Invalid exam part.")

    question = data.get("question_text")

    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question text is required.")

    for field in ("rubric", "reference_answer"):
        if not isinstance(data.get(field, ""), str):
            raise ValueError("Invalid " + field + ".")

    if "question_id" in data:
        question_id = data["question_id"]

        if type(question_id) is not int or question_id <= 0:
            raise ValueError("Invalid question ID.")

    teacher_hints = data.get("teacher_hints", [])

    if not isinstance(teacher_hints, list):
        raise ValueError("Invalid teacher hints.")

    if len(teacher_hints) > MAX_HINTS:
        raise ValueError("Hint limit reached.")

    levels = []

    for item in teacher_hints:
        if not isinstance(item, dict):
            raise ValueError("Invalid teacher hint.")

        level = item.get("level")
        text = item.get("text")

        if (
            type(level) is not int
            or level not in (1, 2, 3)
            or level in levels
        ):
            raise ValueError("Invalid teacher hint level.")

        if not valid_text(text):
            raise ValueError("Invalid teacher hint text.")

        levels.append(level)


def clean_teacher_hints(data):
    hints = []

    for item in data.get("teacher_hints", []):
        hints.append({
            "level": item["level"],
            "text": item["text"].strip(),
            "source": "teacher"
        })

    return sorted(
        hints,
        key=lambda item: item["level"]
    )


# ตรวจเวอร์ชันข้อมูลข้อสอบ
def input_fingerprint(data):
    validate_input(data)

    original = {
        "question_id": data.get("question_id"),
        "exam_part": data["exam_part"],
        "question_text": data["question_text"].strip(),
        "rubric": data.get("rubric", "").strip(),
        "reference_answer": data.get(
            "reference_answer", ""
        ).strip(),
        "teacher_hints": clean_teacher_hints(data),
        "prompt": load_prompt()
    }

    raw = json.dumps(
        original,
        ensure_ascii=False,
        sort_keys=True
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# เตรียม Prompt ให้ Qwen
def build_prompt(data, level, existing):
    template = load_prompt()

    part = data["exam_part"]

    part_names = {
        1: "Part 1 - Code Snippets",
        2: "Part 2 - Programming (JavaScript)",
        3: "Part 3 - Algorithm",
        4: "Part 4 - ER Diagram"
    }

    part_name = part_names.get(
        part,
        "Part " + str(part)
    )

    all_existing = clean_teacher_hints(data) + existing

    replacements = {
        "{EXAM_PART}": part_name,
        "{QUESTION}": data["question_text"],
        "{RUBRIC}": (
            data.get("rubric", "").strip()
            or "Not provided"
        ),
        "{REFERENCE_ANSWER}": (
            data.get("reference_answer", "").strip()
            or "Not provided"
        ),
        "{EXISTING_HINTS}": json.dumps(
            all_existing,
            ensure_ascii=False
        ),
        "{HINT_LEVEL}": str(level)
    }

    for name in replacements:
        if name not in template:
            raise ValueError(
                "Missing prompt field: " + name
            )

    pattern = "|".join(
        re.escape(name) for name in replacements
    )

    return re.sub(
        pattern,
        lambda match: replacements[match.group()],
        template
    )


# เรียก Qwen ผ่าน Ollama
def ask_qwen(prompt):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": prompt
                }
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 250
            }
        },
        timeout=240
    )

    response.raise_for_status()

    result = response.json()

    return result.get(
        "message", {}
    ).get("content", "").strip()


# ตรวจผล AI เบื้องต้น
def valid_generated_hint(text, existing):
    if not valid_text(text):
        return False

    if not contains_thai(text):
        return False

    if "```" in text:
        return False

    if re.search(
        r"\bO\s*\([^)]*\)",
        text,
        flags=re.IGNORECASE
    ):
        return False

    current = " ".join(text.casefold().split())

    for item in existing:
        old = " ".join(
            item["text"].casefold().split()
        )

        if current == old:
            return False

    return True


# สร้าง AI Hint หนึ่งระดับ
def generate_one_hint(data, level, existing):
    previous = clean_teacher_hints(data) + existing

    prompt = build_prompt(data, level, existing)

    for attempt in range(3):
        text = ask_qwen(prompt).strip()

        if valid_generated_hint(text, previous):
            return {
                "level": level,
                "text": text,
                "source": "ai"
            }

        prompt += (
            "\nPrevious output failed validation."
            "\nGenerate ONE different Thai hint."
            "\nDo not reveal the answer."
            "\nMaximum 300 Unicode characters."
        )

    raise ValueError(
        "Unable to generate a valid hint."
    )


# รวม Teacher Hint กับ AI Hint ให้ครบสามระดับ
def generate_hints(data):
    validate_input(data)

    teachers = clean_teacher_hints(data)

    result = []
    generated = []

    for level in range(1, MAX_HINTS + 1):
        teacher = None

        for item in teachers:
            if item["level"] == level:
                teacher = item
                break

        if teacher is not None:
            result.append(teacher)
        else:
            new_hint = generate_one_hint(
                data,
                level,
                generated
            )

            result.append(new_hint)
            generated.append(new_hint)

    return {
        "question_id": data.get("question_id"),
        "input_fingerprint": input_fingerprint(data),
        "hints": result,
        "review_required": False,
        "status": "generated"
    }


# สำหรับทดลองสร้าง Hint ผ่าน Terminal
def main():
    if len(sys.argv) != 2:
        raise ValueError(
            "Usage: python hint_generator.py hint_test_input.json"
        )

    input_path = Path(sys.argv[1])

    with input_path.open(encoding="utf-8") as file:
        data = json.load(file)

    result = generate_hints(data)

    print(json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    ))


if __name__ == "__main__":
    try:
        main()

    except (
        ValueError,
        OSError,
        requests.RequestException,
        json.JSONDecodeError
    ) as error:
        print("Error:", error, file=sys.stderr)
        sys.exit(1)
