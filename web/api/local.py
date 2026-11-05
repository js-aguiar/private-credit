#!/usr/bin/env python3
"""Local HTTP wrapper around the catalog Lambda handler (no API Gateway).

    python web/api/local.py

Serves /api/* on 127.0.0.1:8081. Point the static UI at this origin when
developing with ``python -m http.server --directory web``.
"""

from __future__ import annotations

import json
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[2]
API_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(ROOT))

from handler import handler  # noqa: E402

HOST = "127.0.0.1"
PORT = 8081


class CatalogHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._write(handler(_event(self, "OPTIONS"), None))

    def do_GET(self) -> None:  # noqa: N802
        self._write(handler(_event(self, "GET"), None))

    def do_POST(self) -> None:  # noqa: N802
        self._write(handler(_event(self, "POST"), None))

    def do_PATCH(self) -> None:  # noqa: N802
        self._write(handler(_event(self, "PATCH"), None))

    def do_DELETE(self) -> None:  # noqa: N802
        self._write(handler(_event(self, "DELETE"), None))

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _write(self, result: dict) -> None:
        status = int(result.get("statusCode") or 200)
        headers = result.get("headers") or {}
        body = result.get("body") or ""
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if body and status != 204:
            self.wfile.write(body.encode("utf-8"))


def _event(request: BaseHTTPRequestHandler, method: str) -> dict:
    parsed = urlparse(request.path)
    length = int(request.headers.get("Content-Length") or 0)
    raw_body = request.rfile.read(length) if length > 0 else b""
    headers = {key: value for key, value in request.headers.items()}
    return {
        "requestContext": {"http": {"method": method, "path": parsed.path}},
        "rawPath": parsed.path,
        "rawQueryString": parsed.query,
        "queryStringParameters": None,
        "headers": headers,
        "body": raw_body.decode("utf-8") if raw_body else None,
        "isBase64Encoded": False,
    }


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), CatalogHandler)
    print(
        json.dumps(
            {
                "listening": f"http://{HOST}:{PORT}",
                "paths": [
                    "/api/filters",
                    "/api/documents",
                    "/api/series",
                    "/api/portfolios",
                ],
            }
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
