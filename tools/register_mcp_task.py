"""Register the MCP server as a background task."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.task_registry import register_task, get_active_tasks, format_task_list

# Register the MCP server (PID 22584, port 4097)
register_task(
    pid=22584,
    command="python -m tools.run_mcp_standalone",
    port=4097,
    task_type="server",
    description="BMO MCP SSE Server (standalone) for tool access",
    log_file="logs/mcp_standalone.log",
)

print("Registered MCP server in task registry.")
print("\nActive tasks:")
print(format_task_list())
