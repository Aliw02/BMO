import asyncio

# key: (chat_id, scope), value: {"event": asyncio.Event, "result": str}
pending_permissions = {}
