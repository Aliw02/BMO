"""Run just the MCP SSE server standalone (without Telegram bot)."""
import logging
import os
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

logging.basicConfig(level=logging.INFO)

import uvicorn
from tools.mcp_server import mcp

print("Starting BMO MCP Server on port 4097 (standalone)...")
print(f"TELEGRAM_BOT_TOKEN: {'SET' if os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN') else 'NOT SET'}")
uvicorn.run(mcp.sse_app, host="127.0.0.1", port=4097, log_level="info")
