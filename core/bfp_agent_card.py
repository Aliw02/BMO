"""
BFP Agent Card system - Phase 2 of BMO Friendship Protocol
A2A-compatible agent card generation, caching, and serving
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from config.settings import DATA_DIR
from core.bfp_identity import get_did, sign, verify, get_agent_card as _get_did_card

logger = logging.getLogger(__name__)

BFP_DIR = DATA_DIR / "bfp"
CARD_FILE = BFP_DIR / "signed-card.json"

AGENT_NAME = "BMO-Aliwey"
AGENT_VERSION = "1.0.0"
AGENT_HUMAN = "Aliwey"

DEFAULT_CAPABILITIES = [
    {"id": "code", "name": "Code Assistant", "description": "Write, debug, refactor code"},
    {"id": "research", "name": "Research", "description": "Web search and analysis"},
    {"id": "files", "name": "File Operations", "description": "Read, write, edit files"},
    {"id": "web", "name": "Web Access", "description": "Fetch URLs and browse the web"},
    {"id": "terminal", "name": "Terminal", "description": "Execute shell commands"},
    {"id": "memory", "name": "Memory", "description": "Store and recall information"},
]

DEFAULT_ENDPOINTS = [
    {"type": "ws", "url": None, "description": "WebSocket for direct BFP communication"},
    {"type": "a2a", "url": None, "description": "A2A-compatible HTTP endpoint"},
]

_signed_card = None
_lock = threading.Lock()


def get_capabilities() -> list:
    return list(DEFAULT_CAPABILITIES)


def add_capability(cap_id: str, name: str, desc: str):
    caps = get_capabilities()
    for c in caps:
        if c["id"] == cap_id:
            c["name"] = name
            c["description"] = desc
            break
    else:
        caps.append({"id": cap_id, "name": name, "description": desc})
    global _signed_card
    _signed_card = None
    generate_agent_card()


def generate_agent_card(endpoints: list = None) -> dict:
    did = get_did()
    card = {
        "did": did,
        "name": AGENT_NAME,
        "version": AGENT_VERSION,
        "human": AGENT_HUMAN,
        "capabilities": get_capabilities(),
        "endpoints": endpoints or list(DEFAULT_ENDPOINTS),
        "publicKey": did.replace("did:bfp:", ""),
    }
    signed = sign(card)
    BFP_DIR.mkdir(parents=True, exist_ok=True)
    with open(CARD_FILE, "w") as f:
        json.dump(signed, f, indent=2)
    global _signed_card
    _signed_card = signed
    return signed


def get_signed_card() -> dict:
    global _signed_card
    if _signed_card is not None:
        return _signed_card
    with _lock:
        if _signed_card is not None:
            return _signed_card
        if CARD_FILE.exists():
            with open(CARD_FILE) as f:
                _signed_card = json.load(f)
            return _signed_card
        return generate_agent_card()


def to_a2a_format(card: dict) -> dict:
    return {
        "name": card.get("name", AGENT_NAME),
        "description": f"BFP Agent: {card.get('name', AGENT_NAME)}",
        "url": None,
        "skills": [
            {"id": c["id"], "name": c["name"], "description": c["description"]}
            for c in card.get("capabilities", [])
        ],
        "authentication": {"schemes": ["did:bfp"]},
        "version": card.get("version", AGENT_VERSION),
    }


class AgentCardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/.well-known/agent-card.json":
            card = get_signed_card()
            body = json.dumps(card).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, fmt, *args):
        logger.debug("AgentCard: %s", fmt % args)


class AgentCardServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.server = HTTPServer((host, port), AgentCardHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        logger.info("Agent Card server listening on %s:%s", self.host, self.port)

    def stop(self):
        self.server.shutdown()
        logger.info("Agent Card server stopped")


_server_instance = None


def serve_agent_card(host: str = "0.0.0.0", port: int = 8765):
    global _server_instance
    if _server_instance is not None:
        return _server_instance
    _server_instance = AgentCardServer(host, port)
    _server_instance.start()
    return _server_instance
