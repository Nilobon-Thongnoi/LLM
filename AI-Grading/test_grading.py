import unittest
import json
from unittest.mock import MagicMock, patch

from grading_engine import grade_answer, validate_request


class GradingTests(unittest.TestCase):

    def setUp(self):
        self.data = {
            "question_type": "descriptive",
            "question_text": "Explain a sorting algorithm.",
            "student_answer": "Compare items and swap them until sorted.",
            "reference_answer": "Compare adjacent items and swap when needed.",
            "max_score": 10,
            "rubric": [
                {
                    "criterion_id": "A",
                    "description": "Comparison",
                    "max_score": 4,
                },
                {
                    "criterion_id": "B",
                    "description": "Procedure",
                    "max_score": 6,
                },
            ],
        }

    # จำลองผลจาก Qwen เพื่อทดสอบการคำนวณ
    def make_result(self, ids, scores):
        criteria = []

        for index in range(len(ids)):
            criteria.append({
                "criterion_id": ids[index],
                "awarded_score": scores[index],
                "evidence": "Student demonstrated part of the solution.",
                "missing": "Some details are missing.",
                "reason": "Points awarded according to demonstrated work.",
            })

        return {
            "criteria": criteria,
            "feedback": "คำตอบมีส่วนที่ถูกต้อง แต่ยังขาดรายละเอียดบางส่วน",
            "confidence": "medium",
            "review_reasons": [],
        }

    def test_full_score(self):
        result_data = self.make_result(["A", "B"], [4, 6])

        with patch("grading_engine.ask_ollama", return_value=result_data):
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 10)

    def test_partial_score(self):
        result_data = self.make_result(["A", "B"], [3, 2])

        with patch("grading_engine.ask_ollama", return_value=result_data):
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 5)

    def test_zero_score(self):
        result_data = self.make_result(["A", "B"], [0, 0])

        with patch("grading_engine.ask_ollama", return_value=result_data):
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 0)

    def test_empty_answer(self):
        self.data["student_answer"] = ""

        with patch("grading_engine.ask_ollama") as model:
            result = grade_answer(self.data)

        model.assert_not_called()
        self.assertEqual(result["suggested_score"], 0)

    def test_no_teacher_rubric(self):
        self.data["rubric"] = []
        result_data = self.make_result(["OVERALL"], [7])

        with patch("grading_engine.ask_ollama", return_value=result_data):
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 7)
        self.assertEqual(result["rubric_status"], "missing")

    def test_incomplete_rubric(self):
        self.data["rubric"] = [
            {
                "criterion_id": "A",
                "description": "Comparison",
                "max_score": 4,
            }
        ]

        result_data = self.make_result(
            ["A", "UNSPECIFIED"],
            [3, 4],
        )

        with patch("grading_engine.ask_ollama", return_value=result_data):
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 7)
        self.assertEqual(result["rubric_status"], "partial")

    def test_retry_score_exceeds_limit(self):
        wrong = self.make_result(["A", "B"], [7, 6])
        corrected = self.make_result(["A", "B"], [3, 6])

        with patch(
            "grading_engine.ask_ollama",
            side_effect=[wrong, corrected],
        ) as model:
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 9)
        self.assertEqual(model.call_count, 2)

    def test_reject_repeated_invalid_score(self):
        wrong = self.make_result(["A", "B"], [9, 6])

        with patch("grading_engine.ask_ollama", return_value=wrong):
            with self.assertRaises(ValueError):
                grade_answer(self.data)

    def test_reject_multiple_choice(self):
        self.data["question_type"] = "multiple"

        with self.assertRaises(ValueError):
            validate_request(self.data)

    def test_retry_missing_criterion(self):
        wrong = self.make_result(["A"], [3])
        corrected = self.make_result(["A", "B"], [3, 4])

        with patch(
            "grading_engine.ask_ollama",
            side_effect=[wrong, corrected],
        ) as model:
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 7)
        self.assertEqual(model.call_count, 2)

    # รันโค้ดจริงทั้งหมดยกเว้นการเรียก HTTP เพื่อจับฟังก์ชันที่หายไป
    def test_real_request_path(self):
        result_data = self.make_result(["A", "B"], [4, 6])

        response = MagicMock()
        response.json.return_value = {
            "message": {"content": json.dumps(result_data)}
        }

        with patch(
            "grading_engine.requests.post",
            return_value=response,
        ) as post:
            result = grade_answer(self.data)

        self.assertEqual(result["suggested_score"], 10)

        body = post.call_args.kwargs["json"]
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]

        self.assertIn("RETRIEVED KNOWLEDGE", system)
        self.assertIn("GRADING PROCEDURE", user)
        self.assertIn("retrieved_knowledge", user)

        # ต้องเขียนเหตุผลก่อนให้คะแนน
        fields = list(
            body["format"]["properties"]["criteria"]["items"]["properties"]
        )
        self.assertEqual(fields[-1], "awarded_score")
        self.assertLess(fields.index("reason"), fields.index("awarded_score"))


if __name__ == "__main__":
    unittest.main()
