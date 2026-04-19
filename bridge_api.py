"""Minimal HTTP bridge for VaaniScribe live transcript sync.

Endpoints:
  GET  /health
  GET  /state
  POST /push

Auth:
  Set TRANSCRIPT_HTTP_TOKEN to require either:
  - Authorization: Bearer <token>
  - X-Bridge-Token: <token>

Usage:
  python bridge_api.py
"""

from __future__ import annotations

import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.getenv("PORT") or os.getenv("BRIDGE_PORT", "8787"))
TOKEN = os.getenv("TRANSCRIPT_HTTP_TOKEN", "").strip()
STATE_PATH = Path(os.getenv("TRANSCRIPT_BRIDGE_PATH", "live_transcript.json"))
MAX_LINES = int(os.getenv("TRANSCRIPT_MAX_LINES", "2000"))


DEFAULT_STATE: dict[str, Any] = {
    "connected": False,
    "device": "",
    "interim": "",
    "final_lines": [],
    "updated_at": 0,
}

STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _authorized(headers: Any) -> bool:
    if not TOKEN:
        return True

    auth_header = str(headers.get("Authorization", "")).strip()
    token_header = str(headers.get("X-Bridge-Token", "")).strip()

    if token_header and token_header == TOKEN:
        return True

    if auth_header.lower().startswith("bearer "):
        supplied = auth_header[7:].strip()
        if supplied == TOKEN:
            return True

    return False


def _safe_state_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    state = dict(DEFAULT_STATE)
    state["connected"] = bool(payload.get("connected", False))
    state["device"] = str(payload.get("device", "") or "")
    state["interim"] = str(payload.get("interim", "") or "")

    lines = payload.get("final_lines", [])
    if isinstance(lines, list):
        cleaned = [str(line).strip() for line in lines if str(line).strip()]
        state["final_lines"] = cleaned[-MAX_LINES:]
    else:
        state["final_lines"] = []

    try:
        state["updated_at"] = float(payload.get("updated_at", time.time()) or time.time())
    except (TypeError, ValueError):
        state["updated_at"] = time.time()

    return state


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return dict(DEFAULT_STATE)
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return _safe_state_from_payload(parsed)
    except Exception:
        pass
    return dict(DEFAULT_STATE)


def _write_state(state: dict[str, Any]) -> None:
    tmp_path = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(STATE_PATH)


class BridgeHandler(BaseHTTPRequestHandler):
    def _write_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _deny_if_unauthorized(self) -> bool:
        if _authorized(self.headers):
            return False
        self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"ok": True, "status": "up", "ts": time.time()})
            return

        if self.path == "/state":
            if self._deny_if_unauthorized():
                return
            self._write_json(HTTPStatus.OK, _read_state())
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/push":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return

        if self._deny_if_unauthorized():
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be object")
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid_json: {exc}"})
            return

        state = _safe_state_from_payload(payload)
        _write_state(state)
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "updated_at": state.get("updated_at", time.time()),
                "line_count": len(state.get("final_lines", [])),
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        # Keep logs concise in hosted environments.
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    print(f"[bridge-api] Serving on http://{HOST}:{PORT}")
    if TOKEN:
        print("[bridge-api] Auth enabled via TRANSCRIPT_HTTP_TOKEN")
    else:
        print("[bridge-api] Auth disabled (TRANSCRIPT_HTTP_TOKEN not set)")
    server.serve_forever()
