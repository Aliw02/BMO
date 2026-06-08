import json
import logging

logger = logging.getLogger(__name__)

MSG_QUERY = "query"
MSG_RESULT = "result"
MSG_UPDATE = "update"
MSG_ERROR = "error"
MSG_CANCELLED = "cancelled"
MSG_CONNECT = "connect"
MSG_PING = "ping"
MSG_PONG = "pong"
MSG_SHUTDOWN = "shutdown"


def encode_msg(msg_id: int, msg_type: str, **kwargs) -> str:
    data = {"id": msg_id, "type": msg_type, **kwargs}
    return json.dumps(data, default=str) + "\n"


def decode_msg(line: str) -> dict | None:
    line = line.strip().lstrip("\ufeff")
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        logger.warning("Failed to decode worker message: %s", e)
        return None
