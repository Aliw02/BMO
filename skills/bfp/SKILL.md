---
name: bfp
description: BMO Friendship Protocol — agent-to-agent discovery, cryptographic identity, and JSON-RPC communication over WebSocket
compatibility: bmo
---

# BFP — BMO Friendship Protocol

## What Is BFP

BFP (BMO Friendship Protocol) is a lightweight, DID-based protocol that enables personal AI agents to discover, establish trust with, and delegate work to each other. It is the ecosystem layer of the BMO project — designed so that any agent (BMO, Hermes, OpenClaw, or custom) can find peers and interoperate.

Before BFP, every agent was an island. BFP gives every agent a cryptographic identity, a discoverable capability profile, and a standard messaging channel.

## Why BFP Exists

- Different agents have different strengths (code, research, web, memory)
- Users want their agents to collaborate — not silo
- No lightweight, self-sovereign protocol existed for personal AI agents
- Enterprise protocols (A2A) are too heavy; ad-hoc solutions are too fragile

BFP bridges that gap: minimal enough for a single-file plugin, structured enough for production.

## Core Architecture (3 Layers)

### 1. Identity — DID + Ed25519

Every agent generates an Ed25519 keypair on first boot.

- **DID format**: `did:bfp:<base58(public_key)>` — self-certifying, no registry needed
- **Signatures** on every message — non-repudiation without PKI
- **Zero config** — keypair created on first launch, persisted to disk
- **Self-sovereign** — no central authority; trust is established per-session

### 2. Discovery — Agent Card + Relay

Every agent serves an Agent Card at `/.well-known/agent-card.json`:

```json
{
  "did": "did:bfp:8XQjH8qK8eFfAq...",
  "name": "BMO",
  "version": "1.0.0",
  "capabilities": ["code", "research", "files", "web", "terminal", "memory"],
  "endpoints": [{ "type": "bfp", "url": "ws://localhost:4097/bfp", "protocol": "bfp/1.0" }],
  "signature": "E5vq8x..."
}
```

An optional **relay server** acts as a directory:
- `POST /register` — announce presence with endpoint + capabilities
- `GET /resolve/{did}` — resolve a DID to reachable endpoint
- `GET /list` — list all registered agents
- `GET /find?capability={cap}` — search agents by capability

### 3. Transport — WebSocket + JSON-RPC 2.0

Persistent WebSocket connections with JSON-RPC 2.0 message format:

| Method | Purpose |
|--------|---------|
| `discover` | Request peer's Agent Card |
| `connect` | Establish trust handshake |
| `delegate` | Send a task and get result |
| `status` | Poll peer's status |
| `cancel` | Abort an in-flight task |
| `ping` | Keep-alive / liveness check |

Every message includes `sender_did` and `signature` for verification.

## How to Use BFP (CLI Commands)

In BMO's CLI, BFP auto-starts on boot. Use these commands:

| Command | Purpose |
|---------|---------|
| `/bfp status` | Show your DID, capabilities, relay connection |
| `/bfp find <capability>` | Discover agents by skill (e.g. `code`, `research`) |
| `/bfp delegate <did> <task>` | Send a task to another agent |
| `/bfp talk <did> <message>` | Chat with another agent |

**Example workflow:**

```
/bfp status
  → See your DID: did:bfp:8XQjH8qK8eFfAq...

/bfp find code
  → Found 2 agents with 'code' capability
    Agent Hermes — did:bfp:aBcD...
    Agent OpenClaw — did:bfp:XyZ...

/bfp delegate did:bfp:aBcD... "Review src/main.py for security issues"
  → Result: Found 2 potential issues...
```

## Configuration

Set in `.env`:

```
BFP_RELAY_URL=http://localhost:9753
BFP_TRANSPORT_PORT=4099
BFP_A2A_PORT=4100
```

Without a relay, BFP identity and direct connections still work.

## How Any Agent Can Adopt BFP

1. **Generate Ed25519 keypair** on first boot
2. **Derive DID**: `did:bfp:<base58(pubkey)>`
3. **Sign every message** with the private key
4. **Serve Agent Card** at `/.well-known/agent-card.json`
5. **Start WebSocket server** on a configurable port
6. **Implement JSON-RPC 2.0 methods**: discover, connect, delegate, status, cancel, ping
7. **Optionally register** with a relay for network discovery

Reference implementations:
- **BMO** — built-in (Python, reference implementation in `core/bfp_*.py`)
- **Hermes** — plugin template at `docs/bfp/plugins/hermes-bfp/plugin.py`
- **OpenClaw** — extension template at `docs/bfp/extensions/openclaw-bfp/extension.js`

## Architecture Diagram

```
┌─────────────────┐         ┌─────────────────┐
│   Agent A       │         │   Agent B       │
│  did:bfp:abc    │         │  did:bfp:xyz    │
│  Capabilities:  │         │  Capabilities:  │
│  code, memory   │         │  research, web  │
└────────┬────────┘         └────────┬────────┘
         │                           │
         │  ┌──────────────────┐     │
         │  │    BFP Relay     │     │
         ├──┤  localhost:9753  ├─────┤
         │  │  _ws_by_did map  │     │
         │  │  DID→WebSocket   │     │
         │  └──────────────────┘     │
         │                           │
         │  ── delegate ──────────►  │
         │  ◀── response ──────────  │
```

BFP Relay (from `tools/bfp_relay.py`) maintains a `_ws_by_did` mapping. When Agent A sends a `send` action targeting Agent B's DID, the relay forwards the message to B's WebSocket, awaits the response, and returns it to A.

## Running the Relay

```bash
python -m tools.bfp_relay
# Listens on port 9753 (default)
```

Or via MCP:
```
/bfp start-relay
```

## Testing

```bash
python -m pytest tests/test_bfp.py tests/test_bfp_discovery.py -v
# 20 tests: identity, transport, relay REST, WebSocket forwarding
```

## Future

- **P2P mesh**: mDNS/DHT discovery without relay
- **DID resolution**: universal resolver plugin
- **Capability negotiation**: handshake for shared capabilities
- **Task chaining**: multi-hop delegation
- **Encrypted transport**: Noise protocol or MLS
