import json
import math
import os
import re
from collections import Counter
from pathlib import Path


KNOWLEDGE_DIR = Path(
    os.getenv(
        "GRADING_KNOWLEDGE_DIR",
        str(Path(__file__).parent / "knowledge")
    )
)

# ความคล้ายขั้นต่ำที่ถือว่าเป็นโจทย์ข้อเดียวกัน
QUESTION_MATCH_THRESHOLD = float(
    os.getenv("GRADING_MATCH_THRESHOLD", "0.45")
)

MAX_DOMAINS = 2
MAX_EXAMPLES = 3
MAX_CONCEPTS = 6
MAX_KNOWLEDGE_CHARS = 14000

_cache = {"key": None, "questions": [], "domains": {}}


# แปลงข้อความเป็นชุด character trigram ใช้ได้ทั้งภาษาไทยและโค้ด
def trigrams(text):
    text = " ".join(str(text).lower().split())
    counts = Counter()

    for index in range(len(text) - 2):
        counts[text[index:index + 3]] += 1

    return counts


def cosine(left, right):
    if not left or not right:
        return 0.0

    if len(left) > len(right):
        left, right = right, left

    dot = sum(count * right.get(gram, 0) for gram, count in left.items())

    norm = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(
        sum(v * v for v in right.values())
    )

    return dot / norm if norm else 0.0


def similarity(text_a, text_b):
    return cosine(trigrams(text_a), trigrams(text_b))


def is_text_list(value):
    return isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value
    )


def check_question_entry(entry, path):
    if not isinstance(entry.get("id"), str) or not entry["id"].strip():
        raise ValueError("Knowledge entry needs an id: " + path.name)

    if not isinstance(entry.get("question"), str) or not entry["question"].strip():
        raise ValueError("Knowledge entry needs question text: " + path.name)

    if not is_text_list(entry.get("domains", [])):
        raise ValueError("domains must be a list of names: " + path.name)

    for field in ("accepted_answers", "grading_notes", "graded_examples"):
        if not isinstance(entry.get(field, []), list):
            raise ValueError(field + " must be a list: " + path.name)

    for example in entry.get("graded_examples", []):
        if (
            not isinstance(example, dict)
            or not isinstance(example.get("student_answer"), str)
            or not isinstance(example.get("criterion_scores"), list)
        ):
            raise ValueError("Invalid graded example: " + path.name)


def check_domain_entry(entry, path):
    if not isinstance(entry.get("domain"), str) or not entry["domain"].strip():
        raise ValueError("General knowledge needs a domain: " + path.name)

    if not is_text_list(entry.get("detect")):
        raise ValueError("detect must be a list of keywords: " + path.name)

    if not is_text_list(entry.get("grading_guide", [])):
        raise ValueError("grading_guide must be a list: " + path.name)

    for concept in entry.get("concepts", []):
        if (
            not isinstance(concept, dict)
            or not isinstance(concept.get("text"), str)
            or not is_text_list(concept.get("keywords"))
        ):
            raise ValueError("Invalid concept: " + path.name)


# โหลดคลังความรู้ทั้งหมด และโหลดใหม่เมื่อไฟล์เปลี่ยน
# general/*.json = ความรู้ทั่วไปแยกตามหมวด, questions/*.json = ความรู้เฉพาะโจทย์
def load_knowledge():
    if not KNOWLEDGE_DIR.is_dir():
        return [], {}

    files = sorted(KNOWLEDGE_DIR.rglob("*.json"))
    key = tuple((str(f), f.stat().st_mtime_ns) for f in files)

    if _cache["key"] == key:
        return _cache["questions"], _cache["domains"]

    questions = []
    domains = {}

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))

        if "domain" in data:
            check_domain_entry(data, path)

            if data["domain"] in domains:
                raise ValueError("Duplicate domain: " + data["domain"])

            domains[data["domain"]] = data

        elif "question" in data:
            check_question_entry(data, path)
            data["_grams"] = trigrams(data["question"])
            questions.append(data)

        else:
            raise ValueError("Unknown knowledge file: " + path.name)

    ids = [entry["id"] for entry in questions]

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate question knowledge id.")

    _cache.update(key=key, questions=questions, domains=domains)

    return questions, domains


# หาโจทย์ในคลังความรู้ที่ตรงกับโจทย์ที่ส่งมา
def match_question(question_text, questions):
    grams = trigrams(question_text)
    best = None
    best_score = 0.0

    for entry in questions:
        score = cosine(grams, entry["_grams"])

        if score > best_score:
            best = entry
            best_score = score

    if best is None or best_score < QUESTION_MATCH_THRESHOLD:
        return None, best_score

    return best, best_score


def count_hits(keywords, text):
    lowered = text.lower()
    hits = 0

    for keyword in keywords:
        keyword = keyword.lower()

        if re.fullmatch(r"[a-z0-9_ ]+", keyword):
            # คำภาษาอังกฤษต้องตรงทั้งคำ
            pattern = (
                r"(?<![a-z0-9_])"
                + re.escape(keyword.strip())
                + r"(?![a-z0-9_])"
            )

            if re.search(pattern, lowered):
                hits += 1

        elif keyword in lowered:
            hits += 1

    return hits


# เลือกหมวดความรู้ (Programming, Algorithm, ER ฯลฯ) จากโจทย์และคำตอบ
def detect_domains(question_text, student_answer, domains):
    scores = []

    for name, entry in domains.items():
        score = (
            2 * count_hits(entry["detect"], question_text)
            + count_hits(entry["detect"], student_answer)
        )

        if score > 0:
            scores.append((score, name))

    scores.sort(reverse=True)

    if not scores:
        return []

    top = scores[0][0]

    return [
        name for score, name in scores[:MAX_DOMAINS]
        if score >= top / 2
    ]


def public_example(example):
    return {
        key: value
        for key, value in example.items()
        if key in (
            "label",
            "student_answer",
            "criterion_scores",
            "total",
            "max_total",
            "explanation",
        )
    }


def knowledge_size(knowledge):
    return len(json.dumps(knowledge, ensure_ascii=False))


# ดึงความรู้ที่เกี่ยวข้องกับคำตอบนี้ (Retrieval)
def retrieve_knowledge(data):
    questions, domains = load_knowledge()

    question_text = data["question_text"]
    student_answer = data["student_answer"]

    entry, match_score = match_question(question_text, questions)

    knowledge = {}
    selected = []

    if entry is not None:
        examples = sorted(
            entry.get("graded_examples", []),
            key=lambda item: similarity(student_answer, item["student_answer"]),
            reverse=True,
        )[:MAX_EXAMPLES]

        knowledge["question_id"] = entry["id"]
        knowledge["match_score"] = round(match_score, 2)
        knowledge["accepted_answers"] = entry.get("accepted_answers", [])
        knowledge["grading_notes"] = entry.get("grading_notes", [])
        knowledge["graded_examples"] = [public_example(e) for e in examples]

        selected = [d for d in entry.get("domains", []) if d in domains]

    # โจทย์ที่ไม่มีในคลังยังได้ความรู้ทั่วไปของหมวดที่ตรวจพบ
    for name in detect_domains(question_text, student_answer, domains):
        if name not in selected and len(selected) < MAX_DOMAINS:
            selected.append(name)

    if selected:
        knowledge["domains"] = selected
        knowledge["domain_guides"] = []

        for name in selected:
            knowledge["domain_guides"].extend(
                domains[name].get("grading_guide", [])
            )

        search_text = question_text + "\n" + student_answer
        scored = []

        for order, name in enumerate(selected):
            for concept in domains[name].get("concepts", []):
                hits = count_hits(concept["keywords"], search_text)

                if hits > 0:
                    scored.append((-hits, order, concept["text"]))

        scored.sort()

        concepts = []

        for _, _, text in scored:
            if text not in concepts:
                concepts.append(text)

        if concepts:
            knowledge["concepts"] = concepts[:MAX_CONCEPTS]

    # ตัดส่วนที่สำคัญน้อยออกก่อนถ้าความรู้ยาวเกิน
    while knowledge_size(knowledge) > MAX_KNOWLEDGE_CHARS and knowledge.get("concepts"):
        knowledge["concepts"].pop()

    while knowledge_size(knowledge) > MAX_KNOWLEDGE_CHARS and knowledge.get("graded_examples"):
        knowledge["graded_examples"].pop()

    return knowledge
