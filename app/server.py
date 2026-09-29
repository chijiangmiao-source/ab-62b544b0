"""页面、健康路径与审计接口的 HTTP 服务（仅用 Python 标准库）。"""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .bisim import audit
from .lts import parse_lts

INDEX_HTML = os.path.join(os.path.dirname(__file__), "static", "index.html")


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class AuditHandler(BaseHTTPRequestHandler):
    server_version = "TauBisimAudit/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 简洁日志
        if os.environ.get("QUIET") != "1":
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            try:
                with open(INDEX_HTML, "rb") as fh:
                    body = fh.read()
            except OSError:
                _send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "页面缺失"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/healthz":
            _send_json(self, HTTPStatus.OK, {"status": "ok"})
        else:
            _send_json(self, HTTPStatus.NOT_FOUND, {"error": "未找到", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/audit":
            _send_json(self, HTTPStatus.NOT_FOUND, {"error": "未找到", "path": self.path})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            _send_json(self, HTTPStatus.BAD_REQUEST, {"error": "请求体缺失或过大"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _send_json(self, HTTPStatus.BAD_REQUEST, {"error": f"请求 JSON 无效：{exc}"})
            return
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(k), str) for k in ("a", "b")
        ):
            _send_json(
                self,
                HTTPStatus.BAD_REQUEST,
                {"error": "请求须为对象，且字段 a、b 均为规程 JSON 文本"},
            )
            return

        # 两侧各自解析，问题一次全部汇总，绝不在首个错误处短路
        lts_a, problems_a = parse_lts("A", payload["a"])
        lts_b, problems_b = parse_lts("B", payload["b"])
        if problems_a or problems_b:
            _send_json(
                self,
                HTTPStatus.BAD_REQUEST,
                {
                    "valid": False,
                    "problems": [p.as_dict() for p in problems_a + problems_b],
                },
            )
            return

        assert lts_a is not None and lts_b is not None
        try:
            report = audit(lts_a, lts_b)
        except ValueError as exc:  # 防御：理论上校验阶段已穷尽
            _send_json(self, HTTPStatus.BAD_REQUEST, {"valid": False, "problems": [
                {"procedure": "-", "where": "-", "message": str(exc)}
            ]})
            return
        _send_json(self, HTTPStatus.OK, {"valid": True, "report": report})


def create_server(host: str = "0.0.0.0", port: int | None = None) -> ThreadingHTTPServer:
    port = port if port is not None else int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), AuditHandler)
    return httpd


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    httpd = create_server(host)
    print(f"审计服务监听 http://{host}:{httpd.server_address[1]}", flush=True)  # noqa: T201
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
