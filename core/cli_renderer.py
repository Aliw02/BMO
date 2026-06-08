"""
Formatting and rendering utilities for the BMO CLI using the rich library.
Provides colored text, panels, status tables, and markdown rendering.
"""

from typing import List, Dict, Optional
from datetime import datetime
from contextlib import asynccontextmanager
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.markdown import Markdown
from rich.box import ROUNDED, DOUBLE_EDGE
from rich.live import Live
from rich.spinner import Spinner
from rich.columns import Columns
from rich.align import Align

console = Console()

@asynccontextmanager
async def bmo_spinner(message: str = "BMO is thinking..."):
    """Async context manager that shows a live animated spinner while awaiting something."""
    import asyncio
    spinner = Spinner("dots", text=f"[bold cyan] {message}[/bold cyan]", style="cyan")
    done = asyncio.Event()

    async def _animate():
        with Live(spinner, console=console, refresh_per_second=12, transient=True):
            await done.wait()

    task = asyncio.ensure_future(_animate())
    try:
        yield
    finally:
        done.set()
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except Exception:
            pass

BANNER_TEXT = """\
 ██████╗ ███╗   ███╗ ██████╗ 
 ██╔══██╗████╗ ████║██╔═══██╗
 ██████╔╝██╔████╔██║██║   ██║  [dim]v2.0[/dim]
 ██╔══██╗██║╚██╔╝██║██║   ██║  [dim]CLI Client[/dim]
 ██████╔╝██║ ╚═╝ ██║╚██████╔╝  [dim]OpenCode Integration[/dim]
 ╚═════╝ ╚═╝     ╚═╝ ╚═════╝ \
"""

def print_banner(active_model: str, is_connected: bool, workspace: Optional[str] = None):
    """Renders the startup banner inside a neat rounded panel."""
    status_indicator = "[bold green]● Connected[/bold green]" if is_connected else "[bold red]● Offline[/bold red]"

    content = (
        f"[bold cyan]{BANNER_TEXT}[/bold cyan]\n\n"
        f"  [dim]Model:[/dim]      [bold white]{active_model}[/bold white]\n"
        f"  [dim]Status:[/dim]     {status_indicator}\n"
    )
    if workspace:
        content += f"  [dim]Workspace:[/dim]  [bold white]{workspace}[/bold white]\n"

    panel = Panel(
        content,
        box=ROUNDED,
        title="[bold cyan]BMO Terminal User Interface[/bold cyan]",
        border_style="cyan",
        padding=(1, 4),
        expand=False,
    )
    console.print()
    console.print(panel)
    console.print()

def _html_to_md(text: str) -> str:
    """Convert Telegram HTML tags to Markdown and strip remaining HTML."""
    import re
    # Code blocks before inline code (order matters)
    text = re.sub(r'<pre><code[^>]*>(.*?)</code></pre>', r'```\n\1\n```', text, flags=re.DOTALL)
    text = re.sub(r'<pre>(.*?)</pre>', r'```\n\1\n```', text, flags=re.DOTALL)
    text = re.sub(r'<code>(.*?)</code>', r'`\1`', text, flags=re.DOTALL)
    # Inline formatting
    text = re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<i>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<em>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<u>(.*?)</u>', r'__\1__', text, flags=re.DOTALL)
    text = re.sub(r'<s>(.*?)</s>', r'~~\1~~', text, flags=re.DOTALL)
    # Links
    text = re.sub(r'<a href="(.*?)">(.*?)</a>', r'[\2](\1)', text, flags=re.DOTALL)
    # Line breaks
    text = text.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
    # Strip remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    return text


def render_markdown(text: str):
    """Renders markdown text inside the terminal."""
    console.print(Markdown(_html_to_md(text), code_theme="monokai"))


AGENT_THEMES = {
    "plan": {
        "name": "Plan",
        "style": "bold yellow",
        "border": "yellow",
        "hex": "#d69e2e",
    },
    "build": {
        "name": "Build",
        "style": "bold cyan",
        "border": "cyan",
        "hex": "#00ffff",
    },
    "research": {
        "name": "Research",
        "style": "bold purple",
        "border": "magenta",
        "hex": "#9f7aea",
    },
    "default": {
        "name": "BMO",
        "style": "bold cyan",
        "border": "cyan",
        "hex": "#00ffff",
    }
}

def get_agent_theme(agent_name: Optional[str]) -> dict:
    if not agent_name:
        return AGENT_THEMES["default"]
    name_lower = agent_name.lower()
    if "plan" in name_lower:
        return AGENT_THEMES["plan"]
    elif "build" in name_lower:
        return AGENT_THEMES["build"]
    elif "research" in name_lower or "search" in name_lower:
        return AGENT_THEMES["research"]
    return AGENT_THEMES["default"]


def print_assistant_message(content: str, elapsed_secs: float = None, agent_name: str = "default"):
    """Displays the assistant's message with full Markdown rendering — tables, code, headings, bold."""
    theme = get_agent_theme(agent_name)
    cleaned = _html_to_md(content)
    time_label = f"  [dim]⏱ {elapsed_secs:.1f}s[/dim]" if elapsed_secs is not None else ""
    panel = Panel(
        Markdown(cleaned, code_theme="monokai", justify="left"),
        border_style=theme["border"],
        title=f"[{theme['style']}]{theme['name']}[/{theme['style']}]{time_label}",
        title_align="left",
        padding=(1, 3),
    )
    console.print()
    console.print(panel)
    console.print()


def print_thought(thought_text: str, duration_ms: Optional[int] = None):
    """Prints a thinking block in the modern, dimmed OpenCode style."""
    duration_label = f" {duration_ms}ms" if duration_ms is not None else ""
    console.print(f"[dim]Thought:{duration_label}[/dim]")
    if thought_text.strip():
        # Render the thought text as dimmed raw lines
        for line in thought_text.splitlines():
            if line.strip():
                console.print(f"  [dim]{line}[/dim]")
    console.print()


def print_tool_start(task_name: str, tool_name: str):
    """Renders the start of a tool call in the OpenCode style."""
    console.print(f"  [bold yellow]⠋[/bold yellow] [yellow]Running Task - {task_name}[/yellow] [dim]({tool_name})[/dim]")


def print_tool_finish(task_name: str, tool_name: str, tool_calls_count: int = 1, elapsed_secs: float = 0.0):
    """Renders the completion of a tool call in the OpenCode style."""
    console.print(f"  [bold green]✓[/bold green] [green]Finished Task - {task_name}[/green]")
    console.print(f"    [dim]↳ {tool_calls_count} toolcalls - {elapsed_secs:.1f}s[/dim]")
    console.print()



def print_user_message(content: str):
    """Displays the user's message inside a neat right-aligned rounded panel with transparent background."""
    panel = Panel(
        content,
        border_style="blue",
        title="[bold blue]You[/bold blue]",
        title_align="right",
        padding=(1, 3),
        expand=False,
    )
    console.print()
    console.print(Align.right(panel))
    console.print()

def print_system_status(status: dict, active_model: str):
    """Prints the status of BMO and the OpenCode server in a table."""
    table = Table(title="BMO System Status", box=ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 2))
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")

    conn_status = "[bold green]● Connected[/bold green]" if status.get("connected") else "[bold red]● Disconnected[/bold red]"
    table.add_row("OpenCode Connection", conn_status)
    table.add_row("OpenCode Endpoint", status.get("base_url", "Unknown"))
    table.add_row("Active Model", f"[bold white]{active_model}[/bold white]")
    
    db_stats = status.get("stats", {})
    table.add_row("Total Conversations", str(db_stats.get("sessions", 0)))
    table.add_row("Total Messages", str(db_stats.get("messages", 0)))
    
    console.print()
    console.print(table)
    console.print()

def print_sessions_list(sessions: List[dict]):
    """Renders the list of available sessions in a table."""
    table = Table(title="Conversation Sessions History", box=ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 2))
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Session Title", style="bold")
    table.add_column("Messages", justify="right")
    table.add_column("Created", style="dim")
    table.add_column("Last Updated", style="dim")

    for idx, s in enumerate(sessions):
        active_marker = "[bold green]★ Active[/bold green]" if s.get("is_active") else "[dim]○[/dim]"
        title = s.get("title") or s.get("session_id")[:8]
        msg_count = str(s.get("msg_count", 0))
        
        created_time = "Unknown"
        if s.get("created_at"):
            created_time = datetime.fromtimestamp(s["created_at"]).strftime("%Y-%m-%d %H:%M")

        updated_time = "Unknown"
        if s.get("updated_at"):
            updated_time = datetime.fromtimestamp(s["updated_at"]).strftime("%Y-%m-%d %H:%M")

        table.add_row(
            str(idx + 1),
            active_marker,
            title,
            msg_count,
            created_time,
            updated_time
        )

    console.print()
    console.print(table)
    console.print()

def print_error(message: str):
    """Prints an error message in red."""
    console.print(f"\n  [bold red]❌  {message}[/bold red]\n")

def print_success(message: str):
    """Prints a success message in green."""
    console.print(f"\n  [bold green]✅  {message}[/bold green]\n")

def print_info(message: str):
    """Prints an informational message in yellow/dim."""
    console.print(f"[yellow]ℹ️ {message}[/yellow]")
