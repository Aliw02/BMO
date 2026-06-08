import json
import os
from pathlib import Path

import base58
from nacl.encoding import RawEncoder
from nacl.signing import SigningKey, VerifyKey

from config.settings import DATA_DIR


BFP_DIR = DATA_DIR / "bfp"
IDENTITY_KEY_FILE = BFP_DIR / "identity.key"
AGENT_CARD_FILE = BFP_DIR / "agent-card.json"

_signing_key: SigningKey | None = None
_verify_key: VerifyKey | None = None
_did: str | None = None


def _ensure_identity():
    global _signing_key, _verify_key, _did
    if _signing_key is not None:
        return

    BFP_DIR.mkdir(parents=True, exist_ok=True)

    if IDENTITY_KEY_FILE.exists():
        seed = IDENTITY_KEY_FILE.read_bytes()
    else:
        seed = os.urandom(32)
        IDENTITY_KEY_FILE.write_bytes(seed)

    _signing_key = SigningKey(seed, encoder=RawEncoder)
    _verify_key = _signing_key.verify_key
    pub_bytes = _verify_key.encode(encoder=RawEncoder)
    _did = "did:bfp:" + base58.b58encode(pub_bytes).decode("ascii")


def get_did() -> str:
    _ensure_identity()
    return _did


def sign(data: dict) -> dict:
    _ensure_identity()
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signed = _signing_key.sign(payload, encoder=RawEncoder)
    return {**data, "signature": base58.b58encode(signed.signature).decode("ascii")}


def verify(did: str, data: dict) -> bool:
    if "signature" not in data:
        return False
    signature_b58 = data["signature"]
    remaining = {k: v for k, v in data.items() if k != "signature"}
    payload = json.dumps(remaining, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        if not did.startswith("did:bfp:"):
            return False
        pub_b58 = did[len("did:bfp:"):]
        pub_bytes = base58.b58decode(pub_b58)
        sig_bytes = base58.b58decode(signature_b58)
        verify_key = VerifyKey(pub_bytes)
        verify_key.verify(payload, sig_bytes)
        return True
    except Exception:
        return False


def get_agent_card() -> dict:
    _ensure_identity()
    if AGENT_CARD_FILE.exists():
        return json.loads(AGENT_CARD_FILE.read_text("utf-8"))
    pub_bytes = _verify_key.encode(encoder=RawEncoder)
    card = {
        "did": _did,
        "name": "BMO",
        "version": "1.0.0",
        "publicKey": base58.b58encode(pub_bytes).decode("ascii"),
    }
    AGENT_CARD_FILE.write_text(json.dumps(card, indent=2), "utf-8")
    return card
