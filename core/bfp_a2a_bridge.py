"""Phase 5 — BFP A2A Compatibility Bridge.

Translates BFP Agent Cards to A2A Agent Card format (Google/Linux Foundation
standard), maps BFP RPC methods to A2A operations, and enables enterprise A2A
agents (Copilot, Salesforce Agentforce, Google ADK) to discover and communicate
with BMO.
"""

# ── Phase 1 — DID Identity ────────────────────────────────────────────────
from core.bfp_identity import get_did, sign, verify, get_agent_card as get_did_card

# ── Phase 2 — Agent Card ──────────────────────────────────────────────────
from core.bfp_agent_card import get_signed_card, generate_agent_card

# ── Shared Task Registry ───────────────────────────────────────────────────
from core.bfp_tasks import create_task, get_task, update_task

import json
import logging
import uuid
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── A2A Task State Mapping ────────────────────────────────────────────────
# A2A spec states:  submitted -> working -> input_required -> completed / failed / canceled

BFP_STATUS_TO_A2A: dict[str, str] = {
    "pending": "submitted",
    "processing": "working",
    "awaiting_input": "input_required",
    "done": "completed",
    "error": "failed",
    "cancelled": "canceled",
}


def _bfp_to_a2a_task_state(bfp_status: str) -> str:
    """Map a BFP task status string to the corresponding A2A task state."""
    return BFP_STATUS_TO_A2A.get(bfp_status, "working")


# ── Task ID Generator ─────────────────────────────────────────────────────

_task_id_counter: int = 0


def _generate_task_id() -> str:
    """Generate a unique task ID for A2A task tracking."""
    global _task_id_counter
    _task_id_counter += 1
    return f"a2a-task-{uuid.uuid4().hex[:8]}-{_task_id_counter}"


# ── Agent Card Conversion ─────────────────────────────────────────────────


def get_a2a_agent_card() -> dict:
    """Convert the signed BFP Agent Card to A2A ``/.well-known/agent.json`` format."""
    bfp_card: dict = get_signed_card()

    skills_raw = bfp_card.get("skills", [])
    skills_list: list[dict[str, str]] = []
    for s in skills_raw:
        if isinstance(s, str):
            skills_list.append({"id": s, "name": s, "description": f"Skill: {s}"})
        elif isinstance(s, dict):
            skills_list.append({
                "id": s.get("id", s.get("name", "unknown")),
                "name": s.get("name", s.get("id", "unknown")),
                "description": s.get("description", ""),
            })

    card = {
        "name": bfp_card.get("name", "BMO-Aliwey"),
        "description": (
            bfp_card.get("description")
            or f"Personal AI agent. Skills: {', '.join(s['id'] for s in skills_list)}"
        ),
        "url": (
            f"http://{bfp_card.get('host', '127.0.0.1')}"
            f":{bfp_card.get('a2a_port', 8766)}/a2a"
        ),
        "did": bfp_card.get("did", get_did() or ""),
        "agentVersion": bfp_card.get("version", "1.0.0"),
        "capabilities": {
            "skills": skills_list,
            "streaming": bfp_card.get("streaming", True),
            "pushNotifications": bfp_card.get("pushNotifications", False),
        },
        "authentication": {
            "schemes": [
                {"type": "bearer", "description": "BFP-signed bearer token"},
            ],
        },
        "defaultInputModes": bfp_card.get("defaultInputModes", ["text"]),
        "defaultOutputModes": bfp_card.get("defaultOutputModes", ["text"]),
    }
    return card


# ── JSON-RPC 2.0 Helpers ──────────────────────────────────────────────────


def _jsonrpc_request(method: str, params: dict, request_id: Any = 1) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}


def _jsonrpc_success(result: Any, request_id: Any = 1) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": request_id}


def _jsonrpc_error(code: int, message: str, request_id: Any = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": request_id,
    }


# ── A2A tasks/send Handler ────────────────────────────────────────────────


def handle_a2a_task_send(request: dict) -> dict:
    """Handle ``tasks/send`` -- maps to BFP ``bfp.delegate``."""
    try:
        params = request.get("params", {})
        task_id = params.get("id", _generate_task_id())
        message = params.get("message", {})

        if not message:
            return _jsonrpc_error(
                -32602, "Invalid params: 'message' is required", request.get("id"),
            )

        created_id = create_task(message, "a2a-client")
        task_data = get_task(created_id)

        a2a_state = _bfp_to_a2a_task_state(task_data.get("status", "pending")) if task_data else "failed"

        result: dict[str, Any] = {
            "id": created_id,
            "status": {
                "state": a2a_state,
                "stateHistory": [
                    {"state": "submitted", "timestamp": (task_data or {}).get("created_at", "")},
                ],
            },
        }

        if a2a_state == "completed" and task_data:
            result["status"]["stateHistory"].append(
                {"state": "completed", "timestamp": task_data.get("completed_at", "")},
            )
            parts = task_data.get("result", "")
            if parts:
                result["artifacts"] = [
                    {"parts": [{"text": parts}]},
                ]

        return _jsonrpc_success(result, request.get("id"))

    except Exception as exc:
        logger.exception("A2A tasks/send failed")
        return _jsonrpc_error(-32603, str(exc), request.get("id"))


# ── A2A tasks/get Handler ─────────────────────────────────────────────────


def handle_a2a_task_get(request: dict) -> dict:
    """Handle ``tasks/get`` -- maps to BFP ``bfp.status``."""
    try:
        params = request.get("params", {})
        task_id = params.get("id")
        if not task_id:
            return _jsonrpc_error(
                -32602, "Invalid params: 'id' is required", request.get("id"),
            )

        task_data = get_task(task_id)
        if task_data is None:
            return _jsonrpc_error(-32603, f"Task {task_id} not found", request.get("id"))

        a2a_state = _bfp_to_a2a_task_state(task_data.get("status", "pending"))

        result: dict[str, Any] = {
            "id": task_id,
            "status": {
                "state": a2a_state,
                "stateHistory": [
                    {"state": "submitted", "timestamp": task_data.get("created_at", "")},
                ],
            },
        }

        if a2a_state in ("completed", "failed", "canceled"):
            result["status"]["stateHistory"].append(
                {"state": a2a_state, "timestamp": task_data.get("updated_at", "")},
            )

        parts = task_data.get("result", "")
        if parts:
            result["artifacts"] = [
                {"parts": [{"text": parts}]},
            ]

        return _jsonrpc_success(result, request.get("id"))

    except Exception as exc:
        logger.exception("A2A tasks/get failed")
        return _jsonrpc_error(-32603, str(exc), request.get("id"))


# ── A2A tasks/sendSubscribe Handler ───────────────────────────────────────


def handle_a2a_task_send_subscribe(request: dict) -> dict:
    """Handle ``tasks/sendSubscribe`` -- same as tasks/send for now."""
    return handle_a2a_task_send(request)


# ── A2A tasks/cancel Handler ──────────────────────────────────────────────


def handle_a2a_task_cancel(request: dict) -> dict:
    """Handle ``tasks/cancel`` -- maps to BFP ``bfp.cancel``."""
    try:
        params = request.get("params", {})
        task_id = params.get("id")
        if not task_id:
            return _jsonrpc_error(
                -32602, "Invalid params: 'id' is required", request.get("id"),
            )

        task_data = get_task(task_id)
        if task_data is None:
            return _jsonrpc_error(-32603, f"Task {task_id} not found", request.get("id"))

        if task_data["status"] in ("pending", "processing"):
            update_task(task_id, "cancelled")
            new_status = "canceled"
        else:
            new_status = task_data["status"]

        return _jsonrpc_success(
            {
                "id": task_id,
                "status": {
                    "state": new_status,
                    "stateHistory": [
                        {"state": new_status, "timestamp": task_data.get("updated_at", "")},
                    ],
                },
            },
            request.get("id"),
        )

    except Exception as exc:
        logger.exception("A2A tasks/cancel failed")
        return _jsonrpc_error(-32603, str(exc), request.get("id"))


# ── A2A Method Router ─────────────────────────────────────────────────────

A2A_METHODS: dict[str, Callable[[dict], dict]] = {
    "tasks/send": handle_a2a_task_send,
    "tasks/get": handle_a2a_task_get,
    "tasks/sendSubscribe": handle_a2a_task_send_subscribe,
    "tasks/cancel": handle_a2a_task_cancel,
}


def dispatch_a2a(request: dict) -> dict:
    """Route an incoming A2A JSON-RPC 2.0 request to the appropriate handler."""
    method = request.get("method", "")
    handler = A2A_METHODS.get(method)
    if handler is None:
        return _jsonrpc_error(
            -32601, f"Method '{method}' not found", request.get("id"),
        )
    return handler(request)


# ── Error Mapping ─────────────────────────────────────────────────────────

JSONRPC_ERROR_TO_HTTP: dict[int, int] = {
    -32600: 400,  # Invalid Request
    -32601: 404,  # Method not found
    -32602: 400,  # Invalid params
    -32603: 500,  # Internal error
    -32000: 500,  # Server error
}


# ── HTTP Server ───────────────────────────────────────────────────────────


class A2ARequestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP server that serves A2A JSON-RPC 2.0 endpoints."""

    # Silence default per-request logging
    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug(fmt, *args)

    # ── helpers ────────────────────────────────────────────────────────

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, indent=2, default=str)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _send_agent_card(self) -> None:
        card = get_a2a_agent_card()
        body = json.dumps(card, indent=2, default=str)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    # ── Routes ─────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/agent.json":
            return self._send_agent_card()
        if parsed.path == "/a2a":
            return self._send_json({
                "jsonrpc": "2.0",
                "result": {"info": "BFP A2A endpoint active", "version": "1.0.0"},
                "id": None,
            })
        if parsed.path in ("/health", "/healthz"):
            return self._send_json({"status": "ok", "service": "bfp-a2a"})
        self._send_json(_jsonrpc_error(-32601, f"Not found: {parsed.path}"), 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_body()

        if parsed.path == "/a2a" or parsed.path.startswith("/a2a/"):
            result = dispatch_a2a(body)
            status = 200
            if "error" in result:
                code = result["error"].get("code", -32603)
                status = JSONRPC_ERROR_TO_HTTP.get(code, 500)
            return self._send_json(result, status)

        self._send_json(_jsonrpc_error(-32601, f"Not found: {parsed.path}"), 404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()


def start_a2a_endpoint(host: str = "0.0.0.0", port: int = 8766):
    """Start a minimal HTTP server that serves A2A endpoints in a background thread.

    Routes::

        GET  /.well-known/agent.json  -> A2A Agent Card
        GET  /a2a                     -> A2A endpoint info
        GET  /health                  -> Health check
        POST /a2a                     -> A2A JSON-RPC 2.0 dispatch
    """
    server = HTTPServer((host, port), A2ARequestHandler)
    
    def _run():
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

    import threading
    thread = threading.Thread(target=_run, daemon=True, name="bfp-a2a-server")
    thread.start()
    logger.info("BFP A2A endpoint listening on %s:%s", host, port)
    return server
