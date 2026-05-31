"""
Quick test of opencode serve REST API:
1. POST /session   -> create session, get sessionID
2. POST /session/:id/message  -> send prompt
3. POST /session/:id/wait     -> wait for agent to finish
4. GET  /session/:id/message  -> read last reply
"""
import httpx
import json

BASE = "http://127.0.0.1:4096"

def main():
    client = httpx.Client(timeout=120)

    # Step 1: Create a new session
    print("Creating session...")
    r = client.post(f"{BASE}/session", json={})
    print(f"  Status: {r.status_code}")
    if r.status_code not in (200, 201):
        print(f"  Error: {r.text[:300]}")
        return
    session = r.json()
    session_id = session.get("id")
    print(f"  Session ID: {session_id}")

    # Step 2: Send a prompt
    print("\nSending prompt...")
    payload = {
        "parts": [
            {"type": "text", "text": "Say hello in one sentence."}
        ]
    }
    r = client.post(f"{BASE}/session/{session_id}/message", json=payload)
    print(f"  Status: {r.status_code}")
    if r.status_code not in (200, 201):
        print(f"  Error: {r.text[:500]}")
        return
    msg = r.json()
    print(f"  Message ID: {msg.get('info', {}).get('id')}")

    # Step 3: Wait for agent to finish
    print("\nWaiting for agent...")
    r = client.post(f"{BASE}/session/{session_id}/wait")
    print(f"  Status: {r.status_code}")

    # Step 4: Read last messages
    print("\nReading messages...")
    r = client.get(f"{BASE}/session/{session_id}/message", params={"limit": "5"})
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        messages = r.json()
        if isinstance(messages, list):
            for m in messages:
                role = m.get("info", {}).get("role", "?")
                parts = m.get("parts", [])
                text = " ".join(p.get("text","") for p in parts if p.get("type") == "text")
                print(f"  [{role}]: {text[:200]}")
        else:
            print(f"  Response: {json.dumps(messages)[:400]}")
    else:
        print(f"  Error: {r.text[:300]}")

if __name__ == "__main__":
    main()
