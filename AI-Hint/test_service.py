import json
import os
from pathlib import Path

import requests


BASE_DIR = Path(__file__).parent
SERVICE_URL = os.getenv(
    "HINT_SERVICE_URL",
    "http://127.0.0.1:8765"
)
TOKEN = os.getenv("HINT_SERVICE_TOKEN", "")


# โหลดข้อสอบตัวอย่างที่เตรียมไว้
def load_test_data():
    path = BASE_DIR / "hint_test_input.json"

    with path.open(encoding="utf-8") as file:
        data = json.load(file)

    # รหัสนี้ใช้ทดสอบ API เท่านั้น ไม่บันทึกลงฐานข้อมูล
    if "question_id" not in data:
        data["question_id"] = 999999

    # ไฟล์ทดสอบเดิมเป็นโจทย์ Programming ใน Part 2
    if "exam_part" not in data:
        data["exam_part"] = 2

    data["is_descriptive"] = True

    if "teacher_hints" not in data:
        data["teacher_hints"] = []

    return data


# ตรวจว่าบริการ Python เปิดอยู่
def check_service():
    response = requests.get(
        SERVICE_URL + "/health",
        timeout=10
    )

    response.raise_for_status()

    result = response.json()

    if result.get("status") != "ok":
        raise ValueError("Hint service is not ready.")

    print("Service: ok")


# ทดสอบสร้าง Hint ผ่าน API จริง
def test_generate():
    data = load_test_data()

    response = requests.post(
        SERVICE_URL + "/generate",
        headers={
            "Authorization": "Bearer " + TOKEN
        },
        json=data,
        timeout=650
    )

    result = response.json()

    if response.status_code != 200:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            )
        )
        raise ValueError("Hint generation failed.")

    if result.get("status") != "generated":
        raise ValueError("Invalid response status.")

    if result.get("question_id") != data["question_id"]:
        raise ValueError("Incorrect question ID.")

    hints = result.get("hints")

    if not isinstance(hints, list) or len(hints) != 3:
        raise ValueError("Invalid hint count.")

    for level in range(1, 4):
        hint = hints[level - 1]

        if hint.get("level") != level:
            raise ValueError("Invalid hint level.")

        text = hint.get("text")

        if not isinstance(text, str):
            raise ValueError("Invalid hint text.")

        if not text.strip() or len(text.strip()) > 300:
            raise ValueError("Invalid hint length.")

        if hint.get("source") not in ("teacher", "ai"):
            raise ValueError("Invalid hint source.")

    print("Generation: ok")
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )


def main():
    if not TOKEN:
        raise ValueError("HINT_SERVICE_TOKEN is missing.")

    print("Checking Python Hint service...")
    check_service()

    print("Generating hints...")
    test_generate()

    print("All service tests passed.")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, ValueError) as error:
        print("Test failed:", error)
        raise SystemExit(1)
