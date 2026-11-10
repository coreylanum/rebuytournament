"""Run the admin Lambda locally for development.

Usage:
  ADMIN_PASSWORD=dev SIGNING_KEY=dev-key python scripts/admin_local.py [port]

Translates plain HTTP requests into Lambda function-URL events, so the same
handler code runs here and in AWS. Emails land in data/store/outbox/ and the
website is written to build/site/ instead of S3.
"""

import base64
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("ADMIN_PASSWORD", "dev")
os.environ.setdefault("SIGNING_KEY", "dev-signing-key")

from pokerleague.handlers.admin import handler  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def _run(self, method: str) -> None:
        url = urlsplit(self.path)
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        event = {
            "rawPath": url.path,
            "rawQueryString": url.query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": base64.b64encode(body).decode(),
            "isBase64Encoded": True,
            "requestContext": {"http": {"method": method}},
        }
        result = handler(event)
        self.send_response(result["statusCode"])
        for k, v in result.get("headers", {}).items():
            self.send_header(k, v)
        for c in result.get("cookies", []):
            # Browsers drop Secure cookies on plain http other than localhost; fine here.
            self.send_header("set-cookie", c)
        self.end_headers()
        self.wfile.write(result.get("body", "").encode())

    def do_GET(self) -> None:  # noqa: N802
        self._run("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._run("POST")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    print(f"admin on http://localhost:{port}/admin (password: {os.environ['ADMIN_PASSWORD']})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
