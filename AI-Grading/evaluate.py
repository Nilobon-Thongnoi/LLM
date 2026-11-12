"""วัดความแม่นยำของ AI Grading เทียบกับคะแนนที่อาจารย์ให้ (human labels)

ตัวอย่าง:
    python3 evaluate.py ../Grading-Data/Part2_Programming/question_part2.txt \
        ../Grading-Data/Part2_Programming/answers_part2.json

ตัวเลือก:
    --ids s01,s07      ตรวจเฉพาะบางคำตอบ
    --repeat 2         ตรวจซ้ำแต่ละคำตอบเพื่อดูความนิ่งของคะแนน
    --no-knowledge     ปิด RAG เพื่อเทียบผล
    --keep-self        ไม่ตัด graded_examples ที่เหมือนคำตอบที่กำลังทดสอบ
                       (ปกติจะตัดออก เพื่อไม่ให้ AI ได้เห็นเฉลยของข้อนั้นเอง)

ไฟล์โจทย์ต้องมีหัวข้อ === QUESTION ===, === REFERENCE_ANSWERS ===, === RUBRIC ===
บรรทัด RUBRIC: id | คำอธิบาย | คะแนนเต็ม | รายละเอียด (ไม่บังคับ)
ไฟล์คำตอบ: [{answer_id, answer_text, human_score, human_criteria: {id: score}}]
ส่ง request แบบเดียวกับ Next.js คือ question_text, reference_answer, rubric
ส่วน RULES ในไฟล์โจทย์ไม่ถูกส่ง เพราะระบบจริงไม่มีข้อมูลนี้ ให้ย้ายไปไว้ใน knowledge/
"""

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import grading_engine
import knowledge_base


RESULTS_DIR = Path(__file__).parent / "eval_results"


def read_sections(path):
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"^=== ([A-Z_]+) ===\s*$", text, flags=re.MULTILINE)
    sections = {}

    for index in range(1, len(parts), 2):
        sections[parts[index]] = parts[index + 1].strip()

    return sections


def parse_rubric(text):
    rubric = []

    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split("|")]

        if len(cells) < 3 or not cells[0]:
            continue

        description = cells[1]

        if len(cells) > 3 and cells[3]:
            description += " (" + cells[3] + ")"

        rubric.append({
            "criterion_id": cells[0],
            "description": description,
            "max_score": float(cells[2]),
        })

    if not rubric:
        raise ValueError("RUBRIC section has no criteria.")

    return rubric


def build_request(sections, rubric, answer_text):
    return {
        "question_type": "descriptive",
        "question_text": sections["QUESTION"],
        "reference_answer": sections.get("REFERENCE_ANSWERS", ""),
        "student_answer": answer_text,
        "max_score": sum(item["max_score"] for item in rubric),
        "rubric": rubric,
    }


# ตัดตัวอย่างที่เป็นคำตอบเดียวกับที่กำลังทดสอบ กันไม่ให้ "ลอกเฉลย"
def make_retriever(mode):
    original = knowledge_base.retrieve_knowledge

    def retrieve(data):
        if mode == "off":
            return {}

        knowledge = original(data)

        if mode == "exclude-self" and knowledge.get("graded_examples"):
            knowledge["graded_examples"] = [
                example for example in knowledge["graded_examples"]
                if knowledge_base.similarity(
                    example["student_answer"], data["student_answer"]
                ) < 0.95
            ]

        return knowledge

    return retrieve


def grade_once(request):
    start = time.time()

    try:
        result = grading_engine.grade_answer(request)
    except Exception as error:
        return {"error": type(error).__name__ + ": " + str(error),
                "seconds": round(time.time() - start)}

    return {
        "total": result["suggested_score"],
        "criteria": {
            item["criterion_id"]: item["awarded_score"]
            for item in result["criteria"]
        },
        "details": {
            item["criterion_id"]: {
                "evidence": item["evidence"],
                "missing": item["missing"],
                "reason": item["reason"],
            }
            for item in result["criteria"]
        },
        "confidence": result["confidence"],
        "knowledge_id": result.get("knowledge_id"),
        "seconds": round(time.time() - start),
    }


def summarize(rows, rubric):
    graded = [row for row in rows if row["runs"] and "total" in row["runs"][0]]

    if not graded:
        return {"graded": 0}

    errors = []
    abs_errors = []
    per_criterion = {item["criterion_id"]: [] for item in rubric}
    spreads = []

    for row in graded:
        ok_runs = [run for run in row["runs"] if "total" in run]
        mean_total = statistics.mean(run["total"] for run in ok_runs)
        error = mean_total - row["human_score"]
        errors.append(error)
        abs_errors.append(abs(error))

        if len(ok_runs) > 1:
            totals = [run["total"] for run in ok_runs]
            spreads.append(max(totals) - min(totals))

        for criterion_id, human in (row["human_criteria"] or {}).items():
            if criterion_id in per_criterion:
                ai = statistics.mean(
                    run["criteria"].get(criterion_id, 0) for run in ok_runs
                )
                per_criterion[criterion_id].append(abs(ai - human))

    return {
        "graded": len(graded),
        "failed": len(rows) - len(graded),
        "mae": round(statistics.mean(abs_errors), 2),
        "bias": round(statistics.mean(errors), 2),
        "exact": round(sum(e < 0.01 for e in abs_errors) / len(graded), 2),
        "within_2": round(sum(e <= 2 for e in abs_errors) / len(graded), 2),
        "criterion_mae": {
            key: round(statistics.mean(values), 2)
            for key, values in per_criterion.items() if values
        },
        "max_spread": max(spreads) if spreads else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question_file")
    parser.add_argument("answers_file")
    parser.add_argument("--ids", default="")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--no-knowledge", action="store_true")
    parser.add_argument("--keep-self", action="store_true")
    args = parser.parse_args()

    sections = read_sections(args.question_file)
    rubric = parse_rubric(sections["RUBRIC"])
    answers = json.loads(Path(args.answers_file).read_text(encoding="utf-8"))

    wanted = {item for item in args.ids.split(",") if item}
    labeled = [
        item for item in answers
        if item.get("human_score") is not None
        and (not wanted or item["answer_id"] in wanted)
    ]

    if not labeled:
        sys.exit("No labeled answers. Fill human_score and human_criteria first.")

    mode = "off" if args.no_knowledge else (
        "keep-self" if args.keep_self else "exclude-self"
    )
    grading_engine.retrieve_knowledge = make_retriever(mode)

    print(f"Grading {len(labeled)} answers x{args.repeat}, knowledge={mode}")

    rows = []

    for item in labeled:
        request = build_request(sections, rubric, item["answer_text"])
        runs = [grade_once(request) for _ in range(args.repeat)]
        row = {
            "answer_id": item["answer_id"],
            "human_score": item["human_score"],
            "human_criteria": item.get("human_criteria"),
            "label_source": item.get("label_source", ""),
            "runs": runs,
        }
        rows.append(row)

        totals = [run.get("total", "ERR") for run in runs]
        diff = ""

        if all(isinstance(t, (int, float)) for t in totals):
            gap = statistics.mean(totals) - item["human_score"]
            diff = "OK" if abs(gap) < 0.01 else f"{gap:+.1f}"

        print(f"{item['answer_id']}: human {item['human_score']}  AI {totals}  {diff}",
              flush=True)

        for run in runs:
            if "error" in run:
                print("   ", run["error"])

    summary = summarize(rows, rubric)

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # แสดงเกณฑ์ที่ AI ให้คะแนนต่างจากอาจารย์ พร้อมเหตุผลของ AI ไว้วิเคราะห์
    print("\n=== DISAGREEMENTS ===")

    for row in rows:
        run = row["runs"][0]

        if "criteria" not in run or not row["human_criteria"]:
            continue

        for criterion_id, human in row["human_criteria"].items():
            ai = run["criteria"].get(criterion_id)

            if ai is None or abs(ai - human) < 0.01:
                continue

            detail = run["details"][criterion_id]
            print(f"{row['answer_id']} {criterion_id}: human {human} AI {ai}")
            print(f"   missing: {detail['missing'][:160]}")
            print(f"   reason:  {detail['reason'][:160]}")

    RESULTS_DIR.mkdir(exist_ok=True)
    name = Path(args.answers_file).stem + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / (name + ".json")
    out.write_text(json.dumps({
        "question_file": args.question_file,
        "answers_file": args.answers_file,
        "knowledge": mode,
        "repeat": args.repeat,
        "model": grading_engine.MODEL,
        "summary": summary,
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSaved:", out)


if __name__ == "__main__":
    main()
