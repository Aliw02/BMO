import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://127.0.0.1:4096/ws"
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Sending a message...")
            msg = {"action": "message", "args": {"content": "hello"}}
            await websocket.send(json.dumps(msg))
            print("Message sent, waiting for response...")
            
            for _ in range(5):
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                print(f"Received: {response[:200]}")
    except Exception as e:
        print(f"WebSocket error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
