import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from grading_engine import grade_answer


HOST = os.getenv("GRADING_SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("GRADING_SERVICE_PORT", "8766"))
TOKEN = os.getenv("GRADING_SERVICE_TOKEN", "")


class GradingHandler(BaseHTTPRequestHandler):

    # ส่งผลลัพธ์กลับเป็น JSON
    def send_json(self, status, data):
        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ตรวจว่า Service ยังทำงานอยู่
    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
            return

        self.send_json(404, {"error": "Not found."})

    # รับคำตอบข้อสอบจาก Next.js
    def do_POST(self):
        if self.path != "/grade":
            self.send_json(404, {"error": "Not found."})
            return

        authorization = self.headers.get("Authorization", "")

        if not hmac.compare_digest(
            authorization,
            "Bearer " + TOKEN
        ):
            self.send_json(401, {"error": "Unauthorized."})
            return

        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )

            if length <= 0 or length > 250000:
                raise ValueError("Invalid request size.")

            request_body = self.rfile.read(length)
            data = json.loads(
                request_body.decode("utf-8")
            )

            result = grade_answer(data)

            self.send_json(200, result)

        except (
            ValueError,
            KeyError,
            TypeError,
            UnicodeDecodeError
        ) as error:
            print(
                "Invalid grading request or result:",
                type(error).__name__,
                str(error),
                flush=True
            )
            self.send_json(
                400,
                {"error": str(error)}
            )

        except Exception as error:
            print(
                "Grading service error:",
                type(error).__name__,
                flush=True
            )

            self.send_json(
                500,
                {
                    "error": "AI grading failed. Please review manually."
                }
            )


if __name__ == "__main__":
    if len(TOKEN) < 16:
        raise ValueError(
            "Set GRADING_SERVICE_TOKEN to at least 16 characters."
        )

    server = ThreadingHTTPServer(
        (HOST, PORT),
        GradingHandler
    )

    print(
        f"AI Grading service running at http://{HOST}:{PORT}",
        flush=True
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print("Stopping AI Grading service.")

    finally:
        server.server_close()
