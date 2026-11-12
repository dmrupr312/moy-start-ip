"""Веб-сервер агента «Мой старт ИП». Только стандартная библиотека Python 3.10+.

Запуск:  python app.py            (по умолчанию http://0.0.0.0:8000)
API:
  POST /api/chat      {"session_id": "...", "message": "..."}
  POST /api/calc      {профиль}                 — сравнение режимов без диалога
  POST /api/okved     {"query": "..."}          — подбор ОКВЭД
  POST /api/precheck  {данные заявителя}        — проверка Р21001
  GET  /api/health
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")

from agent import checklist, okved, rules  # noqa: E402
from agent.core import Agent, GREETING  # noqa: E402

AGENT = Agent()
STATIC = ROOT / "static"
MAX_BODY = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "MoyStartIP/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._json(200, {"status": "ok", "mode": AGENT.mode, "greeting": GREETING})
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "слишком большой запрос"})
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "некорректный JSON"})
        path = self.path.split("?")[0]
        try:
            if path == "/api/chat":
                msg = str(data.get("message", ""))[:2000]
                return self._json(200, AGENT.handle(data.get("session_id"), msg))
            if path == "/api/calc":
                res = rules.compare_regimes(data)
                return self._json(200, {**res, "summary": rules.summary_text(res)})
            if path == "/api/okved":
                return self._json(200, {"codes": okved.search_okved(str(data.get("query", "")), 8)})
            if path == "/api/precheck":
                regime = data.get("regime", "usn6")
                return self._json(200, {"precheck": checklist.precheck(data),
                                        "draft": checklist.r21001_draft(data, regime),
                                        "plan": checklist.registration_plan(regime, data.get("channel", "gosuslugi"),
                                                                            bool(data.get("foreign")))})
        except (TypeError, ValueError) as e:
            return self._json(400, {"error": f"проверьте входные данные: {e}"})
        self._json(404, {"error": "not found"})


def main():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"«Мой старт ИП» запущен: http://{'localhost' if host == '0.0.0.0' else host}:{port}  | режим: {AGENT.mode}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
