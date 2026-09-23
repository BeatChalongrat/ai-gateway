"""FIX130 — a dummy alert endpoint for the off-site probe: prints one JSON line
per POST (path, ntfy headers, Authorization, body) and answers 200. Stands in
for ntfy/Resend so no probe ever reaches a real topic."""
import http.server
import json


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        print(json.dumps({
            "path": self.path,
            "title": self.headers.get("Title"),
            "priority": self.headers.get("Priority"),
            "tags": self.headers.get("Tags"),
            "authorization": self.headers.get("Authorization"),
            "body": body,
        }, ensure_ascii=False), flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"id":"fix130-sink"}')

    def log_message(self, *args):
        pass


http.server.HTTPServer(("", 8080), Handler).serve_forever()
