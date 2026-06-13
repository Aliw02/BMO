import asyncio

# key: (chat_id, scope), value: {"event": asyncio.Event, "result": str}
pending_permissions = {}

# key: (chat_id, call_id), value: {"event": asyncio.Event, "result": list, "options": list, "is_multi": bool, "message_ids": list}
pending_questions = {}
