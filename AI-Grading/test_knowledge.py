import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import knowledge_base
from grading_engine import grade_answer


TAX_QUESTION = (
    "จงสร้างฟังก์ชันเพื่อคำนวณอัตราภาษีรถยนต์นั่งส่วนบุคคลรายปี "
    "อัตราภาษีจะคำนวณอ้างอิงจากขนาดความจุของกระบอกสูบ (CC) "
    "ตั้งแต่ 1-600 cc คิดอัตราภาษี cc ละ 0.5 บาท "
    "ตั้งแต่ 601-1,800 cc คิดอัตราภาษี cc ละ 1.5 บาท "
    "ตั้งแต่ 1,801 cc ขึ้นไป คิดอัตราภาษี cc ละ 4 บาท "
    "ผลลัพธ์ที่ได้จากฟังก์ชัน คือ จำนวนเงินภาษี "
    "ซึ่งมีชนิดข้อมูลเป็นเลขจำนวนเต็ม (Integer)"
)


class KnowledgeTests(unittest.TestCase):

    def setUp(self):
        # ล้าง cache ให้แต่ละเทสต์โหลดคลังความรู้ใหม่
        knowledge_base._cache["key"] = None

    def make_data(self, question, answer="function f(cc) { return cc; }"):
        return {
            "question_type": "descriptive",
            "question_text": question,
            "student_answer": answer,
            "reference_answer": "",
            "max_score": 20,
            "rubric": [],
        }

    def test_bundled_knowledge_is_valid(self):
        questions, domains = knowledge_base.load_knowledge()
        ids = [entry["id"] for entry in questions]

        self.assertIn("PROG-01", ids)
        self.assertEqual(
            set(domains),
            {"javascript", "code_snippet", "logic", "algorithm", "er_diagram"}
        )

        for entry in questions:
            for name in entry.get("domains", []):
                self.assertIn(name, domains, entry["id"])

        for entry in questions:
            for example in entry["graded_examples"]:
                total = sum(s["awarded"] for s in example["criterion_scores"])
                maximum = sum(s["max"] for s in example["criterion_scores"])
                self.assertEqual(total, example["total"], example["label"])
                self.assertEqual(maximum, example["max_total"], example["label"])

    def test_matches_tax_question(self):
        knowledge = knowledge_base.retrieve_knowledge(
            self.make_data(TAX_QUESTION)
        )

        self.assertEqual(knowledge["question_id"], "PROG-01")
        self.assertEqual(len(knowledge["accepted_answers"]), 2)
        self.assertLessEqual(
            len(knowledge["graded_examples"]),
            knowledge_base.MAX_EXAMPLES
        )

    def test_unrelated_question_gets_no_question_knowledge(self):
        knowledge = knowledge_base.retrieve_knowledge(
            self.make_data("Explain a sorting algorithm.", "Swap items.")
        )

        self.assertNotIn("question_id", knowledge)
        self.assertEqual(knowledge["domains"], ["algorithm"])

    def test_new_questions_get_general_knowledge(self):
        cases = [
            (
                "จงเขียนฟังก์ชัน JavaScript รับอาร์เรย์ของตัวเลข "
                "แล้วคืนค่าผลรวมของเลขคู่ทั้งหมด",
                "function f(arr) { let s = 0; for (const x of arr) "
                "{ if (x % 2 === 0) s += x; } return s; }",
                "javascript",
            ),
            (
                "จากตัวอย่างส่วนของโปรแกรมด้านล่าง จงเขียนผลลัพธ์ที่เกิดขึ้น "
                "switch (num) { case 1: console.log('A'); default: "
                "console.log('B'); }",
                "A B",
                "code_snippet",
            ),
            (
                "จงออกแบบอัลกอริทึมค้นหาเส้นทางที่สั้นที่สุดในกราฟ "
                "และวิเคราะห์ความซับซ้อนในรูปของ Big O",
                "ใช้ Dijkstra ได้ O((V+E) log V)",
                "algorithm",
            ),
            (
                "จงออกแบบฐานข้อมูลในรูปแบบ ER-Diagram ของระบบห้องสมุด "
                "ระบุ relationships และ cardinality",
                '{"tables": [], "relationships": []}',
                "er_diagram",
            ),
            (
                "จงหาค่าความจริงของนิพจน์ !(a && b) || c",
                "true",
                "logic",
            ),
        ]

        for question, answer, domain in cases:
            knowledge = knowledge_base.retrieve_knowledge(
                self.make_data(question, answer)
            )

            self.assertNotIn("question_id", knowledge)
            self.assertEqual(knowledge["domains"][0], domain, question)
            self.assertTrue(knowledge["domain_guides"])
            self.assertTrue(knowledge["concepts"])

    def test_no_domain_gives_empty_knowledge(self):
        knowledge = knowledge_base.retrieve_knowledge(
            self.make_data("อธิบายประวัติศาสตร์สุโขทัย", "เป็นราชธานีแห่งแรก")
        )

        self.assertEqual(knowledge, {})

    def test_closest_example_comes_first(self):
        answer = (
            "function calculateTax(cc) { let tax = 0; "
            "if (cc <= 600) { tax = cc * 0.5; } "
            "else if (cc <= 1800) { tax = cc * 1.5; } "
            "else { tax = cc * 4; } return Math.trunc(tax); }"
        )

        knowledge = knowledge_base.retrieve_knowledge(
            self.make_data(TAX_QUESTION, answer)
        )

        self.assertEqual(
            knowledge["graded_examples"][0]["label"],
            "Flat rate in tiers 2 and 3"
        )

    def test_concepts_follow_student_answer(self):
        knowledge = knowledge_base.retrieve_knowledge(
            self.make_data(TAX_QUESTION, "return (cc * 2).toFixed(0);")
        )

        self.assertTrue(
            any("toFixed" in text for text in knowledge["concepts"])
        )

    def test_reloads_custom_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "questions" / "q.json"
            path.parent.mkdir()
            path.write_text(json.dumps({
                "id": "CUSTOM",
                "question": "Explain bubble sort step by step.",
                "accepted_answers": [],
                "grading_notes": ["note"],
                "graded_examples": [],
            }), encoding="utf-8")

            with patch.object(knowledge_base, "KNOWLEDGE_DIR", Path(folder)):
                knowledge = knowledge_base.retrieve_knowledge(
                    self.make_data("Explain bubble sort step by step.")
                )

        self.assertEqual(knowledge["question_id"], "CUSTOM")

    def test_invalid_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "bad.json").write_text(
                json.dumps({"id": "X", "question": "Q", "graded_examples": [{}]}),
                encoding="utf-8"
            )

            with patch.object(knowledge_base, "KNOWLEDGE_DIR", Path(folder)):
                with self.assertRaises(ValueError):
                    knowledge_base.load_knowledge()

    def test_grade_answer_sends_knowledge_to_model(self):
        data = self.make_data(TAX_QUESTION)
        data["rubric"] = [
            {"criterion_id": "1", "description": "Function", "max_score": 20}
        ]

        result_data = {
            "criteria": [{
                "criterion_id": "1",
                "evidence": "มีฟังก์ชัน",
                "missing": "",
                "reason": "ถูกต้อง",
                "awarded_score": 20,
            }],
            "feedback": "ถูกต้องครบถ้วน",
            "confidence": "high",
            "review_reasons": [],
        }

        with patch("grading_engine.ask_ollama", return_value=result_data) as model:
            result = grade_answer(data)

        knowledge = model.call_args.kwargs["knowledge"]
        self.assertEqual(knowledge["question_id"], "PROG-01")
        self.assertEqual(result["knowledge_id"], "PROG-01")
        self.assertEqual(result["suggested_score"], 20)


if __name__ == "__main__":
    unittest.main()
