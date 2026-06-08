"""Test MCP SSE connection and call send_telegram_message."""
import asyncio
import json
import anyio
from mcp.client.sse import sse_client
from mcp.types import JSONRPCMessage
from mcp.shared.message import SessionMessage


async def main():
    print("Connecting to MCP SSE server at http://127.0.0.1:4097/sse ...")
    async with sse_client(url="http://127.0.0.1:4097/sse") as (read, write):
        print("Connected!")

        # 1. Initialize
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bmo-bridge", "version": "1.0.0"},
            },
        }
        msg = JSONRPCMessage.model_validate(init)
        await write.send(SessionMessage(message=msg))

        async for msg in read:
            data = msg.message.model_dump()
            print(f"Initialize response: {json.dumps(data, indent=2)[:300]}")
            break

        # 2. Send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        msg = JSONRPCMessage.model_validate(notif)
        await write.send(SessionMessage(message=msg))

        # 3. List tools
        list_tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        msg = JSONRPCMessage.model_validate(list_tools)
        await write.send(SessionMessage(message=msg))

        async for msg in read:
            data = msg.message.model_dump()
            if "result" in data:
                tools = data["result"].get("tools", [])
                print(f"\nFound {len(tools)} tools:")
                for t in tools:
                    print(f"  - {t['name']}: {t.get('description', '')[:80]}")
                break

        # 4. Call send_telegram_message
        call = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "send_telegram_message",
                "arguments": {
                    "chat_id": 732356803,
                    "message": "TEST from BMO MCP bridge ✅ Server tools connected!",
                    "parse_mode": "HTML",
                },
            },
        }
        print("\nCalling send_telegram_message...")
        msg = JSONRPCMessage.model_validate(call)
        await write.send(SessionMessage(message=msg))

        async for msg in read:
            data = msg.message.model_dump()
            print(f"Result: {json.dumps(data, indent=2)[:500]}")
            break

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
