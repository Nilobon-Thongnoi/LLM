import hint_generator as hint


# ข้อมูลข้อสอบสำหรับทดสอบ
def make_data(teachers=None):
    if teachers is None:
        teachers = []

    return {
        "is_descriptive": True,
        "exam_part": 2,
        "question_text": "จงเขียนฟังก์ชัน JavaScript คำนวณภาษีรถยนต์แบบสะสมตามช่วง CC",
        "rubric": "ตรวจการรับพารามิเตอร์ การแบ่งช่วง และการคำนวณสะสม",
        "reference_answer": "คำนวณภาษีแยกช่วงและรวมผล",
        "teacher_hints": teachers
    }


# ตรวจว่าข้อมูลผิดต้องแจ้ง Error
def expect_error(data):
    try:
        hint.generate_hints(data)
    except ValueError:
        return

    raise AssertionError("Expected an error.")


# ทดสอบเงื่อนไขโดยจำลองคำตอบของ Qwen
def run_tests():
    original = hint.ask_qwen

    try:
        # ไม่มี Teacher Hint ต้องสร้าง AI ทั้ง 3 ระดับ
        answers = [
            "เริ่มจากพิจารณาเงื่อนไขของโจทย์",
            "ลองแยกการคำนวณตามแต่ละช่วง",
            "ตรวจสอบผลลัพธ์ว่าตรงตามที่โจทย์กำหนดหรือไม่"
        ]

        hint.ask_qwen = lambda prompt: answers.pop(0)

        result = hint.generate_hints(make_data())

        assert len(result["hints"]) == 3
        assert result["status"] == "pending_review"
        assert result["review_required"] is True

        assert [
            item["source"] for item in result["hints"]
        ] == ["ai", "ai", "ai"]

        # อาจารย์มี Hint 1 ให้ AI สร้าง Hint 2 และ 3
        teacher = {
            "level": 1,
            "text": "เริ่มจากอ่านเงื่อนไขของโจทย์"
        }

        answers = [
            "พิจารณาว่าแต่ละช่วงต้องนำข้อมูลใดมาคำนวณ",
            "ตรวจสอบว่าผลลัพธ์ครบตามข้อกำหนดหรือไม่"
        ]

        hint.ask_qwen = lambda prompt: answers.pop(0)

        result = hint.generate_hints(make_data([teacher]))

        assert [
            item["source"] for item in result["hints"]
        ] == ["teacher", "ai", "ai"]

        assert result["hints"][0]["text"] == teacher["text"]

        # อาจารย์ใส่ Hint 2 ก็ต้องรักษาระดับเดิม
        teacher = {
            "level": 2,
            "text": "พิจารณาการคำนวณแบบสะสม"
        }

        answers = [
            "ลองวิเคราะห์เงื่อนไขก่อนเริ่มเขียนฟังก์ชัน",
            "ตรวจสอบชนิดข้อมูลของผลลัพธ์ให้ตรงกับโจทย์"
        ]

        hint.ask_qwen = lambda prompt: answers.pop(0)

        result = hint.generate_hints(make_data([teacher]))

        assert [
            item["source"] for item in result["hints"]
        ] == ["ai", "teacher", "ai"]

        # มี Teacher Hint ครบ 3 ไม่ต้องเรียก Qwen
        teachers = [
            {"level": 1, "text": "อ่านโจทย์ให้ครบ"},
            {"level": 2, "text": "พิจารณาการแบ่งช่วง"},
            {"level": 3, "text": "ตรวจสอบผลลัพธ์"}
        ]

        def no_qwen(prompt):
            raise AssertionError("Qwen must not be called.")

        hint.ask_qwen = no_qwen

        result = hint.generate_hints(make_data(teachers))

        assert [
            item["source"] for item in result["hints"]
        ] == ["teacher", "teacher", "teacher"]

        # ไม่อนุญาต Hint สำหรับข้อสอบที่ไม่ใช่ Descriptive
        bad = make_data()
        bad["is_descriptive"] = False
        expect_error(bad)

        # ไม่อนุญาต Part 1
        bad = make_data()
        bad["exam_part"] = 1
        expect_error(bad)

        # Teacher Hint เกิน 300 ตัวอักษรไม่ได้
        expect_error(make_data([
            {"level": 1, "text": "ก" * 301}
        ]))

        # Teacher Hint ระดับซ้ำไม่ได้
        expect_error(make_data([
            {"level": 1, "text": "คำใบ้แรก"},
            {"level": 1, "text": "คำใบ้ที่สอง"}
        ]))

        # Qwen ตอบยาวเกิน 300 ไม่ผ่าน
        hint.ask_qwen = lambda prompt: "ก" * 301
        expect_error(make_data())

        # Qwen ตอบภาษาอังกฤษอย่างเดียวไม่ผ่าน
        hint.ask_qwen = lambda prompt: "Check your code."
        expect_error(make_data())

        # Qwen เปิดเผย Big-O ไม่ผ่าน
        hint.ask_qwen = lambda prompt: "ทำงานใน O(1)"
        expect_error(make_data())

        # Qwen ตอบซ้ำกับ Teacher Hint ไม่ผ่าน
        teacher = {
            "level": 1,
            "text": "ตรวจสอบเงื่อนไขให้ครบ"
        }

        hint.ask_qwen = lambda prompt: teacher["text"]
        expect_error(make_data([teacher]))

        # ถ้าครั้งแรกไม่ผ่าน ให้ลองสร้างใหม่
        answers = [
            "Check your code.",
            "ลองวิเคราะห์เงื่อนไขตามโจทย์",
            "พิจารณาวิธีคำนวณตามแต่ละช่วง",
            "ตรวจสอบการคืนค่าผลลัพธ์"
        ]

        hint.ask_qwen = lambda prompt: answers.pop(0)

        result = hint.generate_hints(make_data())

        assert result["hints"][0]["text"] == (
            "ลองวิเคราะห์เงื่อนไขตามโจทย์"
        )

        # ข้อมูลคำตอบนักศึกษาไม่ถูกนำไปใส่ Prompt
        data = make_data()
        data["student_answer"] = "PRIVATE_STUDENT_ANSWER"

        prompt = hint.build_prompt(data, 1, [])

        assert "PRIVATE_STUDENT_ANSWER" not in prompt

        # โจทย์เปลี่ยน Fingerprint ต้องเปลี่ยน
        data = make_data()

        old_fingerprint = hint.input_fingerprint(data)

        data["question_text"] = "โจทย์ใหม่"

        assert hint.input_fingerprint(data) != old_fingerprint

        print("All Hint tests passed.")

    finally:
        hint.ask_qwen = original


if __name__ == "__main__":
    run_tests()
