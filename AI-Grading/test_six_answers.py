
import json
import sys
from pathlib import Path

# เรียกใช้ระบบตรวจคะแนนจริงที่มีอยู่แล้ว
CURRENT_FOLDER = Path(__file__).resolve().parent
PROJECT_FOLDER = CURRENT_FOLDER.parent
sys.path.insert(0, str(CURRENT_FOLDER))

from grading_engine import grade_answer


DATA_FOLDER = PROJECT_FOLDER / "Grading-Data" / "Part2_Programming"

QUESTION_FILE = DATA_FOLDER / "01_question.txt"
REFERENCE_FILE = DATA_FOLDER / "03_answer_key.txt"
ANSWERS_FILE = DATA_FOLDER / "answers_part2.json"

TEST_IDS = ["s01", "s10", "s13", "s15", "s16", "s19"]


# ใช้เกณฑ์หลัก 5 ข้อตาม Rubric ที่ส่งมา
RUBRIC = [
    {
        "criterion_id": "R1",
        "description": (
            "Function and parameter: 4 points. "
            "Valid JavaScript function 2 points. "
            "Accepts and correctly uses CC parameter 2 points."
        ),
        "max_score": 4,
    },
    {
        "criterion_id": "R2",
        "description": (
            "Conditional logic: 6 points. "
            "Correct condition for 1-600 CC: 2 points. "
            "Correct condition for 601-1800 CC: 2 points. "
            "Correct condition for 1801 CC and above: 2 points. "
            "Equivalent conditional structures are allowed."
        ),
        "max_score": 6,
    },
    {
        "criterion_id": "R3",
        "description": (
            "Tax calculation: 6 points. "
            "First tier 0.5 per CC: 2 points. "
            "Second tier includes first 300 plus remaining CC "
            "at 1.5: 2 points. "
            "Third tier includes 300 plus 1800 plus excess CC "
            "at 4: 2 points. "
            "Partial credit must reflect actual completeness."
        ),
        "max_score": 6,
    },
    {
        "criterion_id": "R4",
        "description": (
            "Output: 2 points. "
            "Displays or returns the calculated tax correctly. "
            "Both console.log and return are acceptable."
        ),
        "max_score": 2,
    },
    {
        "criterion_id": "R5",
        "description": (
            "Integer conversion: 2 points. "
            "Ensures an integer-valued tax result. "
            "Evaluate the actual rounding or conversion behavior."
        ),
        "max_score": 2,
    },
]


def main():
    question = QUESTION_FILE.read_text(encoding="utf-8")
    reference = REFERENCE_FILE.read_text(encoding="utf-8")

    answers = json.loads(
        ANSWERS_FILE.read_text(encoding="utf-8")
    )

    for answer_id in TEST_IDS:
        student = None

        for item in answers:
            if item["answer_id"] == answer_id:
                student = item
                break

        if student is None:
            print("Missing test answer:", answer_id)
            continue

        request = {
            "question_type": "descriptive",
            "question_text": question,
            "reference_answer": reference,
            "student_answer": student["answer_text"],
            "max_score": 20,
            "rubric": RUBRIC,
        }

        print("\n" + "=" * 45)
        print("Testing:", answer_id)
        print("=" * 45, flush=True)

        try:
            result = grade_answer(request)

            print(
                "Suggested score:",
                result["suggested_score"],
                "/ 20"
            )
            print("Confidence:", result["confidence"])

            for criterion in result["criteria"]:
                print()
                print(
                    criterion["criterion_id"],
                    str(criterion["awarded_score"])
                    + "/"
                    + str(criterion["max_score"])
                )
                print("Evidence:", criterion["evidence"])
                print("Missing:", criterion["missing"])
                print("Reason:", criterion["reason"])

            print()
            print("Feedback:", result["feedback"])
            print(
                "Review reasons:",
                result["review_reasons"]
            )

        except Exception as error:
            print("Grading failed:", str(error))

    print("\nFinished testing all selected answers.")


if __name__ == "__main__":
    main()
