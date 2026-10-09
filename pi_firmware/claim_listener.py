"""Waits for the server to push this Pi's pairing credentials. The push includes the server's own address.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

CLAIM_PORT = 8765

_REQUIRED_FIELDS = ("server_url", "device_id", "session_token", "role", "name")


def wait_for_claim():
    """Blocks until claimed, then returns
    (server_url, device_id, session_token, role, name)."""
    result = {}
    claimed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/claim":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
                if any(field not in body for field in _REQUIRED_FIELDS):
                    raise ValueError("missing required field")
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            result.update(body)
            self.send_response(204)
            self.end_headers()
            claimed.set()

    server = HTTPServer(("0.0.0.0", CLAIM_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        claimed.wait()
    finally:
        server.shutdown()
        thread.join()

    return tuple(result[field] for field in _REQUIRED_FIELDS)
