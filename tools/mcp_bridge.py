"""
MCP Tool Bridge — calls BMO's MCP tools via SSE protocol.
Usage:
  python -m tools.mcp_bridge <tool_name> [json_args]

Examples:
  python -m tools.mcp_bridge send_telegram_message '{"chat_id": 732356803, "message": "Hello"}'
  python -m tools.mcp_bridge list_background_tasks '{}'
  python -m tools.mcp_bridge check_task_status '{"pid": 12345}'
  python -m tools.mcp_bridge stop_background_task '{"pid": 12345}'
"""
import asyncio
import json
import sys

from mcp.client.sse import sse_client
from mcp.types import JSONRPCMessage
from mcp.shared.message import SessionMessage


MCP_SERVER_URL = "http://127.0.0.1:4097/sse"


async def call_tool(tool_name: str, arguments: dict) -> dict:
    async with sse_client(url=MCP_SERVER_URL) as (read, write):
        # Initialize
        init = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-bridge", "version": "1.0.0"},
            },
        }
        msg = JSONRPCMessage.model_validate(init)
        await write.send(SessionMessage(message=msg))
        async for msg in read:
            init_resp = msg.message.model_dump()
            break

        # Initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        msg = JSONRPCMessage.model_validate(notif)
        await write.send(SessionMessage(message=msg))

        # Call tool
        call = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        msg = JSONRPCMessage.model_validate(call)
        await write.send(SessionMessage(message=msg))

        async for msg in read:
            result = msg.message.model_dump()
            return result


async def list_tools() -> list:
    async with sse_client(url=MCP_SERVER_URL) as (read, write):
        init = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-bridge", "version": "1.0.0"},
            },
        }
        msg = JSONRPCMessage.model_validate(init)
        await write.send(SessionMessage(message=msg))
        async for msg in read:
            break

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        msg = JSONRPCMessage.model_validate(notif)
        await write.send(SessionMessage(message=msg))

        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        msg = JSONRPCMessage.model_validate(req)
        await write.send(SessionMessage(message=msg))

        async for msg in read:
            result = msg.message.model_dump()
            if "result" in result:
                return result["result"].get("tools", [])


async def main():
    if len(sys.argv) < 2:
        print("Usage: python -m tools.mcp_bridge <tool_name> [json_args]")
        print("       python -m tools.mcp_bridge --list-tools")
        sys.exit(1)

    if sys.argv[1] == "--list-tools":
        tools = await list_tools()
        print(f"Available tools ({len(tools)}):")
        for t in tools:
            print(f"  {t['name']}")
        return

    tool_name = sys.argv[1]
    arguments = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    result = await call_tool(tool_name, arguments)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
