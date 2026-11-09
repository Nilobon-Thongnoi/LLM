import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hint_generator import (
    generate_hints,
    valid_text,
    contains_thai,
    validate_input
)


HOST = os.getenv("HINT_SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("HINT_SERVICE_PORT", "8765"))
TOKEN = os.getenv("HINT_SERVICE_TOKEN", "")


# ตรวจคำใบ้ที่สร้างก่อนส่งให้เว็บ
def check_hints(result, data):
    hints = result.get("hints")

    if not isinstance(hints, list) or len(hints) != 3:
        raise ValueError("Invalid hint count.")

    teachers = data.get("teacher_hints", [])
    used_texts = set()

    for level in range(1, 4):
        item = hints[level - 1]

        if not isinstance(item, dict):
            raise ValueError("Invalid hint.")

        if item.get("level") != level:
            raise ValueError("Invalid hint level.")

        text = item.get("text")
        source = item.get("source")

        if not valid_text(text):
            raise ValueError("Invalid hint text.")

        if source not in ("teacher", "ai"):
            raise ValueError("Invalid hint source.")

        teacher_text = None

        for teacher in teachers:
            if teacher["level"] == level:
                teacher_text = teacher["text"].strip()
                break

        if teacher_text is not None:
            if source != "teacher" or text != teacher_text:
                raise ValueError("Teacher hint was changed.")
        else:
            if source != "ai" or not contains_thai(text):
                raise ValueError("Invalid AI hint.")

        normalized = " ".join(text.casefold().split())

        if normalized in used_texts:
            raise ValueError("Duplicate hints.")

        used_texts.add(normalized)


class HintHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        content = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
            return

        self.send_json(404, {"error": "Not found."})

    def do_POST(self):
        if self.path != "/generate":
            self.send_json(404, {"error": "Not found."})
            return

        authorization = self.headers.get("Authorization", "")
        expected = "Bearer " + TOKEN

        if not hmac.compare_digest(authorization, expected):
            self.send_json(401, {"error": "Unauthorized."})
            return

        try:
            size = int(self.headers.get("Content-Length", "0"))

            if size <= 0 or size > 100000:
                raise ValueError("Invalid request size.")

            data = json.loads(
                self.rfile.read(size).decode("utf-8")
            )

            validate_input(data)

            question_id = data.get("question_id")

            if type(question_id) is not int or question_id <= 0:
                raise ValueError("Question ID is required.")

            # สร้าง Hint จากโจทย์ เฉลย Rubric และ Teacher Hint
            result = generate_hints(data)

            check_hints(result, data)

            # ส่งผลให้ Next.js นำไปบันทึกใน PostgreSQL
            self.send_json(200, {
                "question_id": question_id,
                "hints": result["hints"],
                "status": "generated"
            })

        except (
            ValueError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError
        ) as error:
            self.send_json(400, {"error": str(error)})

        except Exception as error:
            print("Hint generation error:", type(error).__name__)
            self.send_json(
                500,
                {"error": "Hint generation failed."}
            )


if __name__ == "__main__":
    if len(TOKEN) < 16:
        raise ValueError(
            "Set HINT_SERVICE_TOKEN to at least 16 characters."
        )

    server = ThreadingHTTPServer(
        (HOST, PORT),
        HintHandler
    )

    print(
        "AI Hint service running at http://"
        + HOST
        + ":"
        + str(PORT)
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping AI Hint service.")
    finally:
        server.server_close()
