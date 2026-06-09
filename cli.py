"""
BMO Interactive CLI Client
Provides a premium terminal experience inspired by high-end AI agents (Claude Code, Aider).
Integrates with the unified BMOEngine backend.
"""

import asyncio
import os
import sys
import threading
import logging

logger = logging.getLogger(__name__)
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter, Completer, Completion
from prompt_toolkit.styles import Style
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.widgets import Frame


from core.bmo_engine import BMOEngine
from rich.panel import Panel
from rich.rule import Rule
from core.goal_runner import GoalRunner
from core.budget_tracker import BudgetTracker
from core.cli_renderer import (
    console,
    print_banner,
    print_assistant_message,
    print_user_message,
    print_system_status,
    print_sessions_list,
    print_error,
    print_success,
    print_info,
    bmo_spinner
)

from config.settings import OWNER_ID
# Configuration: Default admin user chat ID
CHAT_ID = int(os.getenv("TELEGRAM_BOT_TOKEN_ADMIN_ID", str(OWNER_ID)))
USER_ID = CHAT_ID
USERNAME = "cli_user"

# Capture where the user launched bmo from — this is the project context
USER_CWD = os.getcwd()

# CLI Commands with descriptions for autocomplete proposals
COMMAND_DESCRIPTIONS = {
    "/agent": "Switch AI agent mode",
    "/bfp": "BMO Friendship Protocol commands",
    "/budget": "Track and manage token budget",
    "/clear": "Clear the terminal screen",
    "/compact": "Compact session history",
    "/diagnose": "Debug connectivity issues",
    "/env": "Show environment info",
    "/exit": "Exit BMO CLI",
    "/goal": "Create and run goals",
    "/help": "Show help information",
    "/interrupt": "Interrupt current request",
    "/model": "Switch AI model",
    "/new": "Start a new session",
    "/sessions": "List and manage sessions",
    "/share": "Share session context",
    "/status": "Show system status",
    "/stop": "Stop current operation",
    "/web": "Start webchat server",
    "!<command>": "Run a shell command (e.g. !dir, !git status)",
}

import re
from prompt_toolkit.completion import Completer, Completion

_PATH_COMMANDS_CACHE = None
def _get_path_commands():
    global _PATH_COMMANDS_CACHE
    if _PATH_COMMANDS_CACHE is None:
        dirs = os.environ.get("PATH", "").split(os.pathsep)
        exts = os.environ.get("PATHEXT", ".exe;.bat;.cmd").lower().split(";")
        _PATH_COMMANDS_CACHE = set()
        for d in dirs:
            if not os.path.isdir(d):
                continue
            try:
                for f in os.listdir(d):
                    name, ext = os.path.splitext(f)
                    if ext.lower() in exts:
                        _PATH_COMMANDS_CACHE.add(name.lower())
            except PermissionError:
                continue
    return sorted(_PATH_COMMANDS_CACHE)

class BMOCommandCompleter(Completer):
    """Completer that shows command proposals with descriptions."""
    def __init__(self):
        self.commands = COMMAND_DESCRIPTIONS
        self._slash_pattern = re.compile(r'/[a-zA-Z0-9_-]*')
        self._bang_pattern = re.compile(r'![a-zA-Z0-9_./\\\\-]*')

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # Complete /commands
        match = self._slash_pattern.match(text)
        if match:
            prefix = match.group()
            for cmd, desc in sorted(self.commands.items()):
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display=cmd,
                        display_meta=desc,
                    )
            return
        # Complete !shell commands from PATH
        match = self._bang_pattern.match(text)
        if match:
            prefix = match.group()
            for cmd in _get_path_commands():
                if cmd.startswith(prefix[1:]):
                    yield Completion(
                        "!" + cmd,
                        start_position=-len(prefix),
                        display="!" + cmd,
                        display_meta="Shell command",
                    )

completer = BMOCommandCompleter()

# Storage for collapsed pastes: placeholder -> full text
_paste_store: dict[str, str] = {}
_paste_counter = [0]
PASTE_COLLAPSE_THRESHOLD = 5  # lines

from prompt_toolkit.lexers import Lexer
from prompt_toolkit.document import Document

class PasteLexer(Lexer):
    def lex_document(self, document: Document):
        def get_line(lineno):
            text = document.lines[lineno]
            parts = []
            last_idx = 0
            # Matches pattern: [Pasted ~55 lines]
            for match in re.finditer(r"\[Pasted ~\d+ lines\]", text):
                start, end = match.span()
                if start > last_idx:
                    parts.append(("", text[last_idx:start]))
                parts.append(("class:pasted-placeholder", text[start:end]))
                last_idx = end
            if last_idx < len(text):
                parts.append(("", text[last_idx:]))
            return parts
        return get_line

# Styling for the prompt_toolkit interface
cli_style = Style.from_dict({
    "bottom-toolbar": "bg:#222222 #00ffff bold",
    "bottom-toolbar.text": "#ffffff",
    "prompt": "#00ffff bold",
})

from prompt_toolkit.filters import Condition

class ModelSelectorTUI:
    def __init__(self, raw_providers, favorites, save_fav_callback):
        self.raw_providers = raw_providers
        self.favorites = set(favorites)
        self.save_fav_callback = save_fav_callback
        
        self.provider_list = []
        for p in raw_providers:
            p_models = []
            raw_models = p.get("models", {})
            if isinstance(raw_models, dict):
                for m_id, m_data in raw_models.items():
                    p_models.append({
                        "id": m_id,
                        "name": m_data.get("name", m_id) if isinstance(m_data, dict) else m_id
                    })
            elif isinstance(raw_models, list):
                for m in raw_models:
                    p_models.append({
                        "id": m.get("id") if isinstance(m, dict) else m,
                        "name": m.get("name") or m.get("id") if isinstance(m, dict) else m
                    })
            if p_models:
                self.provider_list.append({
                    "id": p.get("id"),
                    "name": p.get("name") or p.get("id"),
                    "models": p_models
                })

        self.filtered_providers = list(self.provider_list)
        self.filtered_providers.sort(key=lambda p: (0 if p["id"] in self.favorites else 1, p["name"].lower()))
        
        self.selected_prov_idx = 0
        self.selected_model_idx = 0
        self.focus_pane = "search"  # "search", "providers", or "models"
        self.search_text = ""
        self.selected_model_id = None
        self.selected_provider_id = None
        
        # Build UI components
        self.search_buffer = Buffer()
        
        @self.search_buffer.on_text_changed.add_handler
        def _on_text_changed(buf):
            self.search_text = buf.text.lower()
            self.filtered_providers = []
            for p in self.provider_list:
                if self.search_text in p["name"].lower() or self.search_text in p["id"].lower():
                    self.filtered_providers.append(p)
            # Sort: favorites first, then alphabetically by name
            self.filtered_providers.sort(key=lambda p: (0 if p["id"] in self.favorites else 1, p["name"].lower()))
            self.selected_prov_idx = 0
            self.selected_model_idx = 0
            
        self.kb = KeyBindings()
        self._setup_keybindings()
        
    def _setup_keybindings(self):
        in_search = Condition(lambda: self.focus_pane == "search")
        in_providers = Condition(lambda: self.focus_pane == "providers")
        in_models = Condition(lambda: self.focus_pane == "models")

        # Global exit bindings
        @self.kb.add("c-c")
        def _global_exit(event):
            event.app.exit(result=None)

        # Search Pane Keybindings
        @self.kb.add("escape", filter=in_search)
        def _search_exit(event):
            event.app.exit(result=None)

        @self.kb.add("down", filter=in_search)
        @self.kb.add("enter", filter=in_search)
        @self.kb.add("tab", filter=in_search)
        def _search_go_to_providers(event):
            if self.filtered_providers:
                self.focus_pane = "providers"
                event.app.layout.focus(self.providers_window)

        # Providers Pane Keybindings
        @self.kb.add("escape", filter=in_providers)
        @self.kb.add("left", filter=in_providers)
        def _providers_exit(event):
            self.focus_pane = "search"
            event.app.layout.focus(self.search_window)

        @self.kb.add("up", filter=in_providers)
        def _providers_up(event):
            if self.filtered_providers:
                self.selected_prov_idx = (self.selected_prov_idx - 1) % len(self.filtered_providers)
                self.selected_model_idx = 0

        @self.kb.add("down", filter=in_providers)
        def _providers_down(event):
            if self.filtered_providers:
                self.selected_prov_idx = (self.selected_prov_idx + 1) % len(self.filtered_providers)
                self.selected_model_idx = 0

        @self.kb.add("space", filter=in_providers)
        def _providers_toggle_fav(event):
            if self.filtered_providers:
                prov = self.filtered_providers[self.selected_prov_idx]
                prov_id = prov["id"]
                if prov_id in self.favorites:
                    self.favorites.remove(prov_id)
                else:
                    self.favorites.add(prov_id)
                self.save_fav_callback(list(self.favorites))
                
                # Re-sort list and update index to stay focused on same item
                self.filtered_providers.sort(key=lambda p: (0 if p["id"] in self.favorites else 1, p["name"].lower()))
                for idx, p in enumerate(self.filtered_providers):
                    if p["id"] == prov_id:
                        self.selected_prov_idx = idx
                        break

        @self.kb.add("enter", filter=in_providers)
        @self.kb.add("right", filter=in_providers)
        @self.kb.add("tab", filter=in_providers)
        def _providers_select(event):
            if self.filtered_providers:
                prov = self.filtered_providers[self.selected_prov_idx]
                if prov["models"]:
                    self.focus_pane = "models"
                    self.selected_model_idx = 0
                    event.app.layout.focus(self.models_window)

        # Models Pane Keybindings
        @self.kb.add("escape", filter=in_models)
        @self.kb.add("left", filter=in_models)
        @self.kb.add("tab", filter=in_models)
        def _models_back(event):
            self.focus_pane = "providers"
            event.app.layout.focus(self.providers_window)

        @self.kb.add("up", filter=in_models)
        def _models_up(event):
            if self.filtered_providers:
                prov = self.filtered_providers[self.selected_prov_idx]
                if prov["models"]:
                    self.selected_model_idx = (self.selected_model_idx - 1) % len(prov["models"])

        @self.kb.add("down", filter=in_models)
        def _models_down(event):
            if self.filtered_providers:
                prov = self.filtered_providers[self.selected_prov_idx]
                if prov["models"]:
                    self.selected_model_idx = (self.selected_model_idx + 1) % len(prov["models"])

        @self.kb.add("enter", filter=in_models)
        def _models_select(event):
            if self.filtered_providers:
                prov = self.filtered_providers[self.selected_prov_idx]
                if prov["models"]:
                    model = prov["models"][self.selected_model_idx]
                    self.selected_provider_id = prov["id"]
                    self.selected_model_id = model["id"]
                    event.app.exit(result=(self.selected_provider_id, self.selected_model_id))

    def _get_visible_slice(self, items, selected_idx, max_visible=12):
        total = len(items)
        if total <= max_visible:
            return items, 0
        
        half = max_visible // 2
        start = selected_idx - half
        if start < 0:
            start = 0
        elif start + max_visible > total:
            start = total - max_visible
            
        end = start + max_visible
        return items[start:end], start

    def _render_providers(self):
        if not self.filtered_providers:
            return [("", " No matching providers found.\n")]
            
        visible_providers, start_idx = self._get_visible_slice(
            self.filtered_providers, 
            self.selected_prov_idx, 
            max_visible=15
        )
        
        lines = []
        if start_idx > 0:
            lines.append(("class:scroll-indicator", "   ▲ ... more providers above ...\n"))
            
        for idx, prov in enumerate(visible_providers):
            actual_idx = start_idx + idx
            is_selected = (actual_idx == self.selected_prov_idx)
            is_fav = prov["id"] in self.favorites
            
            prefix = " > " if is_selected else "   "
            fav_star = "⭐" if is_fav else "  "
            
            if is_selected:
                if self.focus_pane == "providers":
                    style = "class:selected"
                else:
                    style = "class:selected-inactive"
            else:
                style = ""
                
            lines.append((style, f"{prefix}{fav_star} {prov['name']} ({prov['id']})\n"))
            
        if start_idx + len(visible_providers) < len(self.filtered_providers):
            lines.append(("class:scroll-indicator", "   ▼ ... more providers below ...\n"))
            
        return lines

    def _render_models(self):
        if not self.filtered_providers:
            return [("", " No provider selected.\n")]
            
        prov = self.filtered_providers[self.selected_prov_idx]
        if not prov["models"]:
            return [("", " No models available.\n")]
            
        visible_models, start_idx = self._get_visible_slice(
            prov["models"], 
            self.selected_model_idx, 
            max_visible=15
        )
        
        lines = []
        if start_idx > 0:
            lines.append(("class:scroll-indicator", "   ▲ ... more models above ...\n"))
            
        for idx, model in enumerate(visible_models):
            actual_idx = start_idx + idx
            is_selected = (actual_idx == self.selected_model_idx)
            
            prefix = " > " if is_selected else "   "
            if is_selected:
                if self.focus_pane == "models":
                    style = "class:selected"
                else:
                    style = "class:selected-inactive"
            else:
                style = ""
                
            lines.append((style, f"{prefix}{model['id']} ({model['name']})\n"))
            
        if start_idx + len(visible_models) < len(prov["models"]):
            lines.append(("class:scroll-indicator", "   ▼ ... more models below ...\n"))
            
        return lines

    def _get_search_title(self):
        if self.focus_pane == "search":
            return [("class:frame.label.active", " Search Provider (Start typing...) ")]
        return [("class:frame.label", " Search Provider ")]

    def _get_providers_title(self):
        if self.focus_pane == "providers":
            return [("class:frame.label.active", " Providers List (⭐ Favorites) ")]
        return [("class:frame.label", " Providers List ")]

    def _get_models_title(self):
        if self.focus_pane == "models":
            return [("class:frame.label.active", " Available Models ")]
        return [("class:frame.label", " Available Models ")]

    def _render_status(self):
        if self.focus_pane == "search":
            return [("class:help", " [Down/Enter] Navigate Providers | [Esc] Cancel Selection")]
        elif self.focus_pane == "providers":
            return [("class:help", " [Up/Down] Choose Provider | [Space] Toggle Favorite ⭐ | [Enter/Right] Show Models | [Esc/Left] Back to Search")]
        else:
            return [("class:help", " [Up/Down] Choose Model | [Enter] Confirm Model Selection | [Esc/Left] Back to Providers")]

    def get_app(self):
        style = Style.from_dict({
            "selected": "bg:#00ffff #000000 bold",
            "selected-inactive": "bg:#333333 #ffffff italic",
            "help": "bg:#222222 #00ffff bold",
            "frame.label": "#888888",
            "frame.label.active": "#00ffff bold",
            "scroll-indicator": "#555555 italic",
        })
        
        self.search_window = Window(content=BufferControl(buffer=self.search_buffer), height=1)
        self.providers_window = Window(content=FormattedTextControl(self._render_providers, focusable=True))
        self.models_window = Window(content=FormattedTextControl(self._render_models, focusable=True))
        
        self.layout = Layout(
            HSplit([
                Frame(self.search_window, title=self._get_search_title),
                VSplit([
                    Frame(self.providers_window, title=self._get_providers_title),
                    Frame(self.models_window, title=self._get_models_title),
                ]),
                Window(content=FormattedTextControl(self._render_status), height=1),
            ]),
            focused_element=self.search_window
        )
        
        app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=style,
            full_screen=True
        )
        return app

def is_bot_running():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(("127.0.0.1", 4097))
            return True
    except Exception:
        return False

def start_bot_background():
    import subprocess
    import sys
    import os
    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x08000000
    try:
        subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True
        )
        return True
    except Exception:
        return False

def start_mcp_standalone():
    """Start just the MCP server (port 4097) in a daemon thread, no Telegram bot dependency."""
    import threading
    import uvicorn
    from tools.mcp_server import mcp
    t = threading.Thread(
        target=lambda: uvicorn.run(mcp.sse_app, host="127.0.0.1", port=4097, log_level="error"),
        daemon=True,
    )
    t.start()
    logger.info("Standalone MCP Server started on port 4097")
    return t

async def get_boxed_input(session, model_id: str, is_online: bool, agent_names: list, engine) -> str:
    from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.application import Application

    kb = KeyBindings()
    
    @kb.add("c-c")
    def _cancel(event):
        event.app.exit(result="/exit")

    @kb.add("escape")
    def _clear(event):
        event.app.current_buffer.reset()

    @kb.add("enter")
    def _submit(event):
        text = event.app.current_buffer.text.strip()
        event.app.exit(result=text)

    from prompt_toolkit.keys import Keys
    @kb.add(Keys.BracketedPaste, eager=True)
    def _handle_paste_boxed(event):
        data = event.data
        lines = data.splitlines()
        if len(lines) >= PASTE_COLLAPSE_THRESHOLD:
            _paste_counter[0] += 1
            n = _paste_counter[0]
            placeholder = f"[Pasted ~{len(lines)} lines]"
            _paste_store[placeholder] = data
            event.app.current_buffer.insert_text(placeholder)
        else:
            event.app.current_buffer.insert_text(data)

    @kb.add("c-n", eager=True)
    def _cycle_agent_inner(event):
        current_agent = session.metadata.get("active_agent", "default")
        if current_agent not in agent_names and agent_names:
            current_agent = agent_names[0]
        
        if agent_names:
            try:
                curr_idx = agent_names.index(current_agent)
                next_idx = (curr_idx + 1) % len(agent_names)
            except ValueError:
                next_idx = 0
            next_agent = agent_names[next_idx]
            session.metadata["active_agent"] = next_agent
            session.metadata["opencode_session_id"] = None
            engine.storage.save_session(session)
            event.app.invalidate()

    buffer = Buffer(completer=completer, complete_while_typing=True)
    
    def get_status_text():
        curr_agent = session.metadata.get("active_agent", "default")
        return [
            ("class:brackets", "["),
            ("class:key", "Tab"),
            ("class:brackets", "]"),
            ("class:desc", " Commands "),
            ("class:separator", " | "),
            ("class:brackets", "["),
            ("class:key", "Enter"),
            ("class:brackets", "]"),
            ("class:desc", " Send "),
            ("class:separator", " | "),
            ("class:brackets", "["),
            ("class:key", "Esc"),
            ("class:brackets", "]"),
            ("class:desc", " Clear "),
            ("class:separator", " | "),
            ("class:brackets", "["),
            ("class:key", "Ctrl+N"),
            ("class:brackets", "]"),
            ("class:desc", f" Agent: {curr_agent} "),
        ]
    
    session_title = session.title or session.session_id[:8]
    title_text = f"── BMO Chat - Session {session_title} - {model_id} - {'Online' if is_online else 'Offline'} ──"
    def get_title_text():
        text = buffer.text
        if text.startswith("!"):
            return [("class:title-line-shell", title_text)]
        elif text.startswith("/"):
            return [("class:title-line-cmd", title_text)]
        else:
            return [("class:title-line", title_text)]
            
    from prompt_toolkit.layout.menus import CompletionsMenu
    
    layout = Layout(
        HSplit([
            Window(content=FormattedTextControl(get_title_text), height=1),
            Window(content=BufferControl(buffer=buffer, lexer=PasteLexer()), height=2, style="class:input-text"),
            CompletionsMenu(max_height=8),
            Window(content=FormattedTextControl(get_status_text), height=1, style="class:toolbar-bg"),
        ])
    )
    
    style = Style.from_dict({
        "title-line": "#4a5568 bold",
        "title-line-shell": "#38a169 bold",
        "title-line-cmd": "#d69e2e bold",
        "input-text": "#edf2f7",
        "brackets": "#718096 bg:#1a202c",
        "key": "#3182ce bg:#1a202c bold",
        "desc": "#a0aec0 bg:#1a202c",
        "separator": "#4a5568 bg:#1a202c",
        "toolbar-bg": "bg:#1a202c",
        "completion-menu": "bg:#2d3748 #edf2f7",
        "completion-menu.completion.current": "bg:#3182ce #ffffff bold",
        "completion-menu.completion.meta": "bg:#2d3748 #718096 italic",
        "completion-menu.completion.current.meta": "bg:#3182ce #bee3f8 italic",
        "pasted-placeholder": "bg:#ecc94b #1a202c bold",
    })
    
    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,
    )
    
    result = await app.run_async()
    return result if result is not None else ""

def print_session_history(session):
    """Renders the last 6 messages from the session history in the terminal."""
    if not session or not session.messages:
        return
    
    # Show last 6 messages (3 turns)
    history = session.messages[-6:]
    if not history:
        return
        
    print_info(f"  [dim]── Recent chat history ({len(history)} messages) ──[/dim]")
    for m in history:
        sender = m.get("sender") or m.get("role", "") if isinstance(m, dict) else getattr(m, "sender", getattr(m, "role", ""))
        content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        
        if sender == "user":
            print_user_message(content)
        elif sender == "assistant":
            active_agent = session.metadata.get("active_agent", "default")
            print_assistant_message(content, elapsed_secs=None, agent_name=active_agent)
    console.print()

_INTERACTIVE_SHELLS = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}

async def run_shell_command(cmd: str) -> str:
    """Run a shell command, streaming output in real-time, returning full output with exit code.

    Standalone shell names (!cmd, !powershell, !pwsh) open a new terminal window.
    All other commands run inline with stdout captured and streamed.
    """
    if not cmd:
        print_error("Empty shell command. Usage: !<command>")
        return ""

    console.print(f"  [bold green]> {cmd}[/bold green]")

    # Detect standalone interactive shell — spawn a new terminal window
    first_word = cmd.strip().split(maxsplit=1)[0].lower()
    if first_word in _INTERACTIVE_SHELLS and len(cmd.strip().split()) == 1:
        import subprocess, shutil
        # Try shells in priority order: pwsh -> powershell -> cmd
        fallback_chain = [
            ("pwsh.exe", []),
            ("powershell.exe", []),
            ("cmd.exe", ["/k"]),
        ]
        launched = False
        for exe, args in fallback_chain:
            if shutil.which(exe):
                subprocess.Popen(
                    [exe] + args,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    close_fds=True,
                )
                msg = f"Opened {exe} in a new window."
                console.print(f"  [green]{msg}[/green]")
                launched = True
                break
        if not launched:
            err = "No shell found (tried pwsh.exe, powershell.exe, cmd.exe)"
            print_error(err)
            return f"$ {cmd}\n[Error] {err}"
        return f"$ {cmd}\n{msg}\n[Exit: 0]"

    buf = []
    shell = os.environ.get("COMSPEC", "cmd.exe")
    try:
        proc = await asyncio.create_subprocess_exec(
            shell, "/c", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print_error(f"Shell not found: {shell}")
        return f"$ {cmd}\n[Error] Shell not found: {shell}"

    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if text:
            console.print(f"  [dim]{text}[/dim]")
            buf.append(text)

    ret = await proc.wait()
    status = f"[Exit: {ret}]"
    if ret == 0:
        console.print(f"  [green]{status}[/green]")
    else:
        console.print(f"  [red]{status}[/red]")

    output = "\n".join(buf)
    return f"$ {cmd}\n{output}\n{status}"

async def main():
    # Ensure the BMO MCP Server (port 4097) is running — provides tools to OpenCode
    if not is_bot_running():
        print_info("BMO MCP Server is not running. Starting it...")
        if start_bot_background():
            for _ in range(10):
                await asyncio.sleep(0.3)
                if is_bot_running():
                    print_success("BMO Bot / MCP Server is online.")
                    break
            else:
                print_info("BMO Bot is loading in the background (check logs for TELEGRAM_TOKEN issues).")
                # Fall back to standalone MCP server if main.py didn't start
                if not is_bot_running():
                    print_info("Starting standalone MCP Server (no Telegram Bot)...")
                    start_mcp_standalone()
                    await asyncio.sleep(1)
                    if is_bot_running():
                        print_success("MCP Server is online (standalone mode).")
                    else:
                        print_error("MCP Server failed to start.")
        else:
            print_error("Failed to start BMO Bot. Starting standalone MCP Server instead...")
            start_mcp_standalone()
            await asyncio.sleep(1)
            if is_bot_running():
                print_success("MCP Server is online (standalone mode).")

    # Initialize Engine, GoalRunner, and BudgetTracker
    engine = BMOEngine(user_cwd=USER_CWD)
    # CLI has its own async loop — disable the worker subprocess so all queries
    # go through _send_direct. The worker is only needed for Telegram's sync handlers.
    engine.client._worker = None
    goal_runner = GoalRunner(engine)
    budget_tracker = BudgetTracker()

    # ── Start BFP Agent (independent of the worker, always start in CLI) ──
    # BFP is gated inside ensure_worker() which returns early when _worker=None,
    # so we start it explicitly here instead.
    if not engine._bfp_started:
        from config.settings import BFP_RELAY_URL, BFP_TRANSPORT_PORT, BFP_A2A_PORT
        _relay = BFP_RELAY_URL or "http://localhost:9753"
        asyncio.create_task(engine.bfp.start(
            bfp_port=BFP_TRANSPORT_PORT,
            a2a_port=BFP_A2A_PORT,
            relay_url=_relay,
        ))
        engine._bfp_started = True
        print_info(f"BFP Agent starting... (relay: {_relay})")

    # Check OpenCode Connection with a brief poll in case it's still booting
    status = await engine.get_status()
    is_connected = status.get("connected", False)
    if not is_connected:
        for _ in range(6):
            await asyncio.sleep(0.5)
            status = await engine.get_status()
            if status.get("connected", False):
                is_connected = True
                break
        else:
            # After retries exhausted, run full diagnostics
            err_detail = getattr(engine.client, '_last_alive_error', None)
            logger.warning("OpenCode connection failed after retries")
            try:
                diag = await engine.client.diagnose_connection()
                logger.warning("Diagnostics: port_open=%s, endpoints=%s",
                               diag['port_open'],
                               {ep: v.get('status', v.get('error', '?')) for ep, v in diag['endpoints'].items()})
                if diag.get("error"):
                    logger.warning("Root cause: %s", diag["error"])
            except Exception as de:
                logger.warning("Diagnostics failed: %s", de)
                
    connection_state = [is_connected]  # mutable so toolbar closure can reflect updates

    # Ensure session exists
    session = await engine.get_or_create_session(CHAT_ID, USER_ID, USERNAME)
    provider_id, model_id = await engine.get_model_info(CHAT_ID)

    # Fetch available agents for cycling
    agent_names = ["build", "plan"]  # default fallbacks
    try:
        available_agents = await engine.get_agents()
        if available_agents:
            agent_names = [a["name"] for a in available_agents]
    except Exception:
        pass

    # Print welcome banner
    print_banner(model_id, connection_state[0], workspace=USER_CWD)
    print_info(f"Workspace Path:  [bold white]{USER_CWD}[/bold white]")
    print_info(f"Connected DB:    [bold white]{engine.storage.db_path}[/bold white]")
    print_info(f"Active Session:  [bold cyan]{session.title or session.session_id[:8]}[/bold cyan]")
    print_info("Type /help to see all interactive commands.\n")
    print_session_history(session)

    # Define bottom toolbar function
    def get_toolbar_text():
        conn_indicator = "● Online" if connection_state[0] else "● Offline"
        curr_title = session.title or session.session_id[:8]
        curr_mode = session.metadata.get("active_mode", "execute")
        curr_agent = session.metadata.get("active_agent", "default")
        
        # Budget info
        limit = budget_tracker.get_limit(session.session_id)
        cost = budget_tracker.get_session_cost(session.session_id)
        budget_str = f"${cost:.2f}/${limit:.2f}"
        
        return [
            ("class:bottom-toolbar", f" BMO CLI | Server: {conn_indicator} | Session: {curr_title} | Model: {model_id} | Agent: {curr_agent} | Mode: {curr_mode} | Budget: {budget_str} ")
        ]

    prompt_kb = KeyBindings()

    @prompt_kb.add("c-c", eager=True)
    def _clear_input_ctrl_c(event):
        event.app.current_buffer.reset()

    @prompt_kb.add("escape", eager=True)
    def _clear_input_esc(event):
        event.app.current_buffer.reset()

    @prompt_kb.add("c-n", eager=True)
    def _cycle_agent(event):
        nonlocal session
        current_agent = session.metadata.get("active_agent", "default")
        if current_agent not in agent_names and agent_names:
            current_agent = agent_names[0]
        
        if agent_names:
            try:
                curr_idx = agent_names.index(current_agent)
                next_idx = (curr_idx + 1) % len(agent_names)
            except ValueError:
                next_idx = 0
            next_agent = agent_names[next_idx]
            session.metadata["active_agent"] = next_agent
            session.metadata["opencode_session_id"] = None
            engine.storage.save_session(session)
            
            sys.stdout.write(f"\r\n\x1b[36m[System] Agent switched to: {next_agent}\x1b[0m\n")
            sys.stdout.flush()
            event.app.invalidate()

    # Storage for collapsed pastes: placeholder → full text
    # Uses module-level _paste_store / _paste_counter / PASTE_COLLAPSE_THRESHOLD
    # defined at the top of this file. The get_boxed_input() handler also writes
    # to the module-level store — removing local shadowing so expansion finds it.

    _PS = PromptSession  # shorthand for sub-prompts — each call creates a fresh instance
    _pending_inject: str = None  # holds mid-run injection text to send on next iteration

    while True:
        try:
            # Sync active session from database (in case Telegram changed it)
            active_session = engine.storage.load_session(CHAT_ID)
            if active_session and active_session.session_id != session.session_id:
                session = active_session
                provider_id, model_id = await engine.get_model_info(CHAT_ID)

            # If an inject was requested during thinking, use it directly
            # instead of waiting for new user input from the prompt.
            if _pending_inject is not None:
                user_input = _pending_inject
                _pending_inject = None
            else:
                user_input = await get_boxed_input(session, model_id, connection_state[0], agent_names, engine)
                user_input = user_input.strip()
                if user_input:
                    # Move up 4 lines (height of the boxed prompt layout) and clear it
                    sys.stdout.write("\x1b[4A\x1b[J")
                    sys.stdout.flush()
                    if not user_input.startswith("/") and not user_input.startswith("!"):
                        print_user_message(user_input)

            # Check again after user input to ensure we don't send to a stale session
            active_session = engine.storage.load_session(CHAT_ID)
            if active_session and active_session.session_id != session.session_id:
                session = active_session
                provider_id, model_id = await engine.get_model_info(CHAT_ID)
                print_info(f"\n[System] Session synced with Telegram. Active session is now: {session.title}")
                print_session_history(session)

            if not user_input:
                continue

            # Expand paste placeholders back to full text before sending.
            # Handles two cases:
            #   1. user_input IS the placeholder (simple paste + Enter)
            #   2. user_input CONTAINS placeholder(s) embedded in longer text
            if _paste_store:
                if user_input in _paste_store:
                    # Simple case: the whole input is a placeholder
                    user_input = _paste_store.pop(user_input)
                else:
                    # Complex case: scan for embedded placeholders
                    for placeholder, content in list(_paste_store.items()):
                        if placeholder in user_input:
                            user_input = user_input.replace(placeholder, content)
                            _paste_store.pop(placeholder, None)

            # Command routing
            if user_input.startswith("/"):
                cmd_parts = user_input.split(" ", 1)
                cmd = cmd_parts[0].lower()

                if cmd in ("/exit", "/quit", "/q"):
                    print_success("Goodbye!")
                    break

                elif cmd in ("/help", "/h"):
                    print_info("Available Commands:")
                    print_info("  /help or /h      - Display this help message")
                    print_info("  /new or /n       - Start a fresh conversation session")
                    print_info("  /compact         - Summarize older messages, keep recent in full")
                    print_info("  /sessions or /s  - List recent conversation sessions and switch")
                    print_info("  /model or /m     - Interactively choose provider and model")
                    print_info("  /agent or /a     - Choose or switch active agent (plan, build, etc.)")
                    print_info("  /clear or /c     - Clear message history of current session")
                    print_info("  /status or /t    - Display server connection status and stats")
                    print_info("  /diagnose or /d  - Run full connection diagnostics (multi-endpoint)")
                    print_info("  /web             - Start/show webchat URL (Cloudflare tunnel)")
                    print_info("  /goal <task>     - Run autonomous goal loop (parallel subagents)")
                    print_info("  /goal list       - List past goal results")
                    print_info("  /goal results <id> - View full details of a saved goal")
                    print_info("  /budget <limit>  - Set session token cost budget limit in USD")
                    print_info("  /share           - Export current session as .txt or .json file")
                    print_info("  /bfp status      - Show BFP identity, capabilities, relay connection")
                    print_info("  /bfp find <cap>  - Find agents by capability via relay")
                    print_info("  /bfp delegate <did> <task> - Send a task to another agent")
                    print_info("  /bfp talk <did> <msg> - Send a message to another agent")
                    print_info("  /env             - View current environment variables")
                    print_info("  /env KEY=VALUE   - Update environment variable in .env file")
                    print_info("  /exit or /q      - Exit the CLI interface")
                    print_info("  ![command]       - Run a shell command (e.g. !dir, !git status)")
                    print_info("  [Hotkeys] Ctrl+N  - Cycle through active agents (plan -> build -> ...)")

                elif cmd in ("/new", "/n"):
                    print_info("Creating new session...")
                    session = await engine.create_new_session(CHAT_ID, USER_ID, USERNAME)
                    provider_id, model_id = await engine.get_model_info(CHAT_ID)
                    print_success(f"Started new session: {session.title}")

                elif cmd in ("/compact",):
                    print_info("Compacting session — summarizing older messages...")
                    result = await engine.compact_session(CHAT_ID)
                    print_info(result)

                elif cmd in ("/clear", "/c"):
                    print_info("Clearing current session history...")
                    if await engine.clear_session_history(CHAT_ID):
                        print_success("Session message history cleared successfully.")
                    else:
                        print_error("Failed to clear session history.")

                elif cmd in ("/status", "/t"):
                    status = await engine.get_status()
                    print_system_status(status, model_id)

                elif cmd in ("/diagnose", "/d"):
                    print_info("Running full connection diagnostics...")
                    diag = await engine.client.diagnose_connection()
                    overall = "[green]Connected[/green]" if diag["error"] is None else "[red]" + diag["error"] + "[/red]"
                    port_status = "[green]Yes[/green]" if diag["port_open"] else "[red]No[/red]"
                    ep_lines = []
                    for ep, v in diag["endpoints"].items():
                        if "status" in v:
                            ep_lines.append(f"  {ep}: [green]HTTP {v['status']}[/green]")
                        else:
                            ep_lines.append(f"  {ep}: [red]{v.get('error', 'Unknown')}[/red]")
                    console.print()
                    console.print(Panel.fit(
                        "[bold cyan]OpenCode Connection Diagnostics[/bold cyan]\n\n"
                        f"[white]Base URL:[/white] [bold]{diag['base_url']}[/bold]\n"
                        f"[white]Port Open:[/white] {port_status}\n"
                        f"[white]Overall:[/white] {overall}\n\n"
                        "[bold]Endpoint Tests:[/bold]\n" + "\n".join(ep_lines),
                        border_style="cyan",
                        width=72,
                    ))
                    console.print()

                elif cmd in ("/budget", "/b"):
                    if len(cmd_parts) < 2:
                        current_limit = budget_tracker.get_limit(session.session_id)
                        print_info(f"Current budget limit: ${current_limit:.2f}")
                        limit_input = (await _PS().prompt_async("Enter new budget limit in USD: ")).strip()
                    else:
                        limit_input = cmd_parts[1].strip()

                    try:
                        new_limit = float(limit_input)
                        budget_tracker.set_limit(session.session_id, new_limit)
                        print_success(f"Session budget set to: ${new_limit:.2f}")
                    except ValueError:
                        print_error("Invalid budget limit value.")

                elif cmd in ("/goal", "/g"):
                    sub = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                    sub_cmd = sub.split(maxsplit=1)[0] if sub else ""

                    # /goal --help, --h, help — show usage guide
                    if sub_cmd in ("--help", "-h", "--h", "help"):
                        console.print()
                        console.print(Rule("[bold cyan]/goal — Autonomous Goal Runner[/bold cyan]", style="cyan"))
                        console.print()
                        console.print("  [bold]Usage:[/bold]")
                        console.print("    [green]/goal <objective>[/green]    Run an autonomous goal")
                        console.print("    [green]/g <objective>[/green]       Short alias")
                        console.print("    [green]/goal list[/green]           List all saved goal results")
                        console.print("    [green]/goal results <id>[/green]   View full details of a saved goal")
                        console.print()
                        console.print("  [bold]Examples:[/bold]")
                        console.print("    [dim]/goal list all Python files in the project[/dim]")
                        console.print("    [dim]/goal check which ports are in use[/dim]")
                        console.print("    [dim]/goal list[/dim]")
                        console.print("    [dim]/goal results 20260607_093131[/dim]")
                        console.print()
                        console.print("  [bold]Behavior:[/bold]")
                        console.print("    • BMO decomposes your objective into 3-5 subtasks")
                        console.print("    • Each subtask runs in parallel in an isolated session")
                        console.print("    • On completion, results auto-send to Telegram")
                        console.print("    • All goals saved to [dim]data/goals/[/dim] as JSON")
                        console.print()
                        continue

                    # /goal list — show saved goal results
                    if sub_cmd == "list":
                        if sub != "list":
                            print_error("Unknown arguments after '/goal list'. Use '/goal list' alone, or '/goal <objective>' to run a new goal.")
                            continue
                        goals = goal_runner.list_saved_goals()
                        if not goals:
                            print_info("No saved goals found.")
                        else:
                            console.print(f"\n  [bold cyan]Saved Goals:[/bold cyan]")
                            for g in goals:
                                ts = g["timestamp"][:19]
                                ok_icon = "[green]OK[/green]" if g["completed_steps"] == g["total_steps"] else "[yellow]partial[/yellow]"
                                console.print(f"    [bold]{g['goal_id']}[/bold]  {ok_icon}  {g['completed_steps']}/{g['total_steps']} steps  [dim]{ts}[/dim]")
                                console.print(f"      [dim]{g['objective'][:120]}[/dim]")
                            console.print()
                        continue

                    # /goal results <id> — view full detail of a saved goal
                    if sub_cmd == "results":
                        parts = sub.split(maxsplit=1)
                        if len(parts) < 2:
                            print_error("Usage: /goal results <goal_id>")
                            continue
                        gid = parts[1].strip()
                        data = goal_runner.get_goal_result(gid)
                        if not data:
                            print_error(f"Goal '{gid}' not found.")
                        else:
                            console.print()
                            console.print(Rule(f"[bold cyan]Goal Result: {gid}[/bold cyan]", style="cyan"))
                            console.print(f"  [bold white]{data['objective']}[/bold white]")
                            console.print(f"  [dim]Ran at {data['timestamp'][:19]} — {data['completed_steps']}/{data['total_steps']} steps[/dim]")
                            console.print()
                            for r in data["results"]:
                                status = "BLOCKED" if r["blocked"] else "OK"
                                color = "red" if r["blocked"] else "green"
                                console.print(f"  [{color}]Step {r['step']}/{data['total_steps']} [{status}][/{color}] [white]{r['task']}[/white] [dim]{r['elapsed']:.1f}s[/dim]")
                                console.print(f"  [{color}]──[/{color}]")
                                from core.cli_renderer import render_markdown
                                render_markdown(r["response"])
                                console.print()
                        console.print()
                        continue

                    # No subcommand → new goal
                    if not sub:
                        objective = (await _PS().prompt_async("Enter autonomous objective goal: ")).strip()
                    else:
                        objective = sub

                    if not objective:
                        print_error("Objective cannot be empty.")
                        continue

                    await goal_runner.run_goal(CHAT_ID, USER_ID, objective)

                elif cmd in ("/sessions", "/s"):
                    sessions = await engine.list_sessions(CHAT_ID)
                    if not sessions:
                        print_info("No saved sessions found.")
                        continue

                    print_sessions_list(sessions)

                    choice = (await _PS().prompt_async("\nEnter session index number to switch (or press Enter to cancel): ")).strip()
                    if choice.isdigit():
                        idx = int(choice) - 1
                        if 0 <= idx < len(sessions):
                            selected_sid = sessions[idx]["session_id"]
                            new_sess = await engine.switch_session(CHAT_ID, selected_sid)
                            if new_sess:
                                session = new_sess
                                provider_id, model_id = await engine.get_model_info(CHAT_ID)
                                print_success(f"Switched active session to: {session.title}")
                                print_session_history(session)
                            else:
                                print_error("Failed to switch session.")
                        else:
                            print_error("Invalid session index.")

                elif cmd in ("/model", "/m"):
                    raw_providers = await engine.list_models()
                    if not raw_providers:
                        print_error("Could not fetch available models from server.")
                        continue

                    import time
                    from models.chat_models import UserMemory
                    memory = engine.storage.load_memory(CHAT_ID)
                    if not memory:
                        memory = UserMemory(
                            user_id=USER_ID,
                            chat_id=CHAT_ID,
                            preferences={"favorite_providers": []},
                            knowledge={},
                            created_at=time.time(),
                            updated_at=time.time()
                        )
                    if "favorite_providers" not in memory.preferences:
                        memory.preferences["favorite_providers"] = []

                    def save_fav_callback(fav_list):
                        memory.preferences["favorite_providers"] = fav_list
                        memory.updated_at = time.time()
                        engine.storage.save_memory(memory)

                    tui = ModelSelectorTUI(
                        raw_providers=raw_providers,
                        favorites=memory.preferences["favorite_providers"],
                        save_fav_callback=save_fav_callback
                    )

                    app = tui.get_app()
                    result = await app.run_async()

                    if result:
                        selected_provider_id, selected_model_id = result
                        if await engine.set_model(CHAT_ID, selected_provider_id, selected_model_id):
                            provider_id, model_id = await engine.get_model_info(CHAT_ID)
                            print_success(f"Active model set to: {model_id} ({provider_id})")
                        else:
                            print_error("Failed to switch model.")
                    else:
                        print_info("Cancelled model selection.")

                elif cmd in ("/agent", "/a"):
                    try:
                        available_agents = await engine.get_agents()
                    except Exception:
                        available_agents = []
                    if not available_agents:
                        print_error("Could not fetch available agents from server.")
                        continue
                    
                    print_info("Available Agents:")
                    curr_agent = session.metadata.get("active_agent", "default")
                    for idx, a in enumerate(available_agents):
                        name = a.get("name", "")
                        desc = a.get("description", "")
                        curr = " (active)" if curr_agent == name else ""
                        print_info(f"  [{idx + 1}] {name}{curr} - {desc}")
                    
                    choice = (await _PS().prompt_async("\nEnter agent index number to switch (or press Enter to cancel): ")).strip()
                    if choice.isdigit():
                        idx = int(choice) - 1
                        if 0 <= idx < len(available_agents):
                            selected_agent = available_agents[idx]["name"]
                            session.metadata["active_agent"] = selected_agent
                            session.metadata["opencode_session_id"] = None
                            engine.storage.save_session(session)
                            print_success(f"Active agent set to: {selected_agent}")
                        else:
                            print_error("Invalid agent index.")

                elif cmd in ("/share",):
                    current_session = engine.storage.get_session_by_id(session.session_id)
                    if not current_session or not current_session.messages:
                        print_error("No messages in current session to export.")
                        continue

                    print_info("Export format:")
                    print_info("  [1] Text file (.txt)")
                    print_info("  [2] JSON file (.json)")
                    fmt_choice = (await _PS().prompt_async("Choose [1/2] (default: 1): ")).strip()
                    fmt = "json" if fmt_choice == "2" else "txt"

                    import json as _json
                    from datetime import datetime as _dt

                    exports_dir = os.path.join(os.path.dirname(__file__), "data", "exports")
                    safe_title = (session.title or session.session_id[:8]).replace(" ", "_").replace("/", "-")
                    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"bmo_session_{safe_title}_{timestamp}.{fmt}"
                    out_path = os.path.join(exports_dir, filename)
                    os.makedirs(exports_dir, exist_ok=True)

                    # Messages are dicts with key "sender" (values: "user" or "assistant")
                    msgs = current_session.messages

                    def _sender(m):
                        if isinstance(m, dict):
                            return m.get("sender") or m.get("role", "")
                        return getattr(m, "sender", None) or getattr(m, "role", "")

                    def _content(m):
                        return m["content"] if isinstance(m, dict) else getattr(m, "content", "")

                    def _timestamp(m):
                        return m.get("timestamp") if isinstance(m, dict) else getattr(m, "timestamp", None)

                    if fmt == "json":
                        export_data = {
                            "session_id": session.session_id,
                            "title": session.title,
                            "exported_at": _dt.now().isoformat(),
                            "messages": [
                                {
                                    "sender": _sender(m),
                                    "content": _content(m),
                                    "timestamp": _timestamp(m),
                                }
                                for m in msgs
                            ]
                        }
                        with open(out_path, "w", encoding="utf-8") as _f:
                            _json.dump(export_data, _f, ensure_ascii=False, indent=2)
                    else:
                        lines = [
                            "BMO Session Export",
                            f"Title   : {session.title}",
                            f"ID      : {session.session_id}",
                            f"Exported: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
                            "=" * 60,
                            "",
                        ]
                        for m in msgs:
                            role_label = "You" if _sender(m) == "user" else "BMO"
                            lines.append(f"[{role_label}]")
                            lines.append(_content(m))
                            lines.append("")
                        with open(out_path, "w", encoding="utf-8") as _f:
                            _f.write("\n".join(lines))

                    print_success(f"Session exported → {out_path}")

                elif cmd == "/bfp":
                    parts = user_input.split(maxsplit=2)
                    subcmd = parts[1] if len(parts) > 1 else ""
                    if subcmd == "status":
                        s = engine.bfp.get_status()
                        print_info("── BFP Status ──")
                        print_info(f"  DID           : {s['did']}")
                        print_info(f"  Running       : {'Yes' if s['running'] else 'No'}")
                        print_info(f"  Relay         : {s['relay_url'] or 'Not connected'}")
                        print_info(f"  Relay Status  : {'Connected' if s['connected_to_relay'] else 'Disconnected'}")
                        print_info(f"  Transport Port: {s['bfp_port'] or 'N/A'}")
                        print_info(f"  Capabilities  : {', '.join(s['capabilities'])}")

                    elif subcmd == "find":
                        if len(parts) < 3:
                            print_error("Usage: /bfp find <capability>")
                            continue
                        capability = parts[2]
                        if not engine.bfp.is_running:
                            print_error("BFP not started")
                            continue
                        try:
                            agents = await engine.bfp.find_agents(capability)
                            if not agents:
                                print_info(f"No agents found with capability: {capability}")
                            else:
                                print_info(f"Agents with '{capability}' capability ({len(agents)}):")
                                for a in agents:
                                    name = a.get("name", a["did"][:30])
                                    did = a["did"]
                                    caps = ", ".join(a.get("capabilities", []))
                                    print_info(f"  {name}")
                                    print_info(f"    DID: {did}")
                                    print_info(f"    Capabilities: {caps}")
                        except Exception as e:
                            print_error(f"Discovery failed: {e}")

                    elif subcmd == "delegate":
                        if len(parts) < 4:
                            print_error("Usage: /bfp delegate <did> <task>")
                            continue
                        target_did = parts[2]
                        task_text = parts[3]
                        if not engine.bfp.is_running:
                            print_error("BFP not started")
                            continue
                        try:
                            print_info(f"Delegating task to {target_did[:40]}...")
                            result = await engine.bfp.delegate(target_did, {"query": task_text})
                            print_success(f"Result: {result.get('result', 'No result')}")
                        except Exception as e:
                            print_error(f"Delegation failed: {e}")

                    elif subcmd == "talk":
                        if len(parts) < 4:
                            print_error("Usage: /bfp talk <did> <message>")
                            continue
                        target_did = parts[2]
                        message = parts[3]
                        if not engine.bfp.is_running:
                            print_error("BFP not started")
                            continue
                        try:
                            print_info(f"Sending message to {target_did[:40]}...")
                            response = await engine.bfp.talk(target_did, message)
                            print_info(f"Response: {response}")
                        except Exception as e:
                            print_error(f"Talk failed: {e}")

                    else:
                        print_info("BFP Commands:")
                        print_info("  /bfp status               - Show BFP identity and connection status")
                        print_info("  /bfp find <capability>    - Find agents by capability via relay")
                        print_info("  /bfp delegate <did> <msg> - Send a task to another agent")
                        print_info("  /bfp talk <did> <msg>     - Chat with another agent")

                elif cmd == "/web":
                    # Check task registry for existing webchat/tunnel
                    import json as _json
                    from config.settings import BMO_HOME
                    _tasks_file = BMO_HOME / "data" / "background_tasks.json"
                    _existing_url = None
                    if _tasks_file.exists():
                        try:
                            _tasks = _json.loads(_tasks_file.read_text("utf-8"))
                            _existing_url = _tasks.get("webchat_tunnel_url")
                        except Exception:
                            pass

                    _session_query = f"?chat_id={CHAT_ID}&session_id={session.session_id}"
                    if _existing_url:
                        print_success(f"💬 Webchat is already running!")
                        print_info(f"   Local:  http://127.0.0.1:3456{_session_query}")
                        print_info(f"   Public: {_existing_url}{_session_query}")
                    else:
                        print_info("⏳ Starting webchat + Cloudflare tunnel...")
                        try:
                            from tools.task_registry import get_active_tasks
                            # Check if webchat is actually already running on port 3456
                            import socket as _sock
                            _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                            _s.settimeout(0.5)
                            _webchat_up = _s.connect_ex(("127.0.0.1", 3456)) == 0
                            _s.close()

                            if _webchat_up:
                                # Start only the tunnel
                                tunnel_result = await engine.client._call_mcp_tool(
                                    "tunnel_webchat", {"port": 3456}
                                ) if hasattr(engine.client, "_call_mcp_tool") else None
                                if tunnel_result:
                                    print_success(f"🌐 Tunnel started: {tunnel_result}")
                                else:
                                    print_info("   Webchat running locally: http://127.0.0.1:3456")
                                    print_info("   To get a public URL, run: bmo web")
                            else:
                                print_info("   Webchat not running. Start it with: bmo web")
                                print_info("   (Run `bmo web` from your terminal)")
                        except Exception as _e:
                            print_error(f"Web command failed: {_e}")
                            print_info("   Alternative: run `bmo web` from your terminal")

                elif cmd == "/env":
                    arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""
                    from config.settings import BMO_HOME
                    env_path = BMO_HOME / ".env"
                    if not env_path.exists():
                        from config.settings import BASE_DIR
                        env_path = BASE_DIR / ".env"
                    
                    if not arg:
                        if not env_path.exists():
                            print_info("No .env file found.")
                        else:
                            print_info(f"Current Environment Config ({env_path.name}):")
                            with open(env_path, "r", encoding="utf-8") as f:
                                for line in f:
                                    line_strip = line.strip()
                                    if not line_strip or line_strip.startswith("#"):
                                        continue
                                    if "=" in line_strip:
                                        k, v = line_strip.split("=", 1)
                                        k = k.strip()
                                        v = v.strip()
                                        if any(x in k.lower() for x in ("token", "key", "secret", "password")):
                                            masked_v = v[:3] + "..." + v[-3:] if len(v) > 6 else "..."
                                            console.print(f"  [bold cyan]{k}[/bold cyan]={masked_v}")
                                        else:
                                            console.print(f"  [bold cyan]{k}[/bold cyan]={v}")
                        continue
                    
                    if "=" not in arg:
                        print_error("Usage: /env KEY=VALUE to set a variable, or /env to list them.")
                        continue
                    
                    key, val = arg.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    
                    if not key:
                        print_error("Invalid key.")
                        continue
                    
                    lines = []
                    updated = False
                    if env_path.exists():
                        with open(env_path, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.strip().startswith("#"):
                                    lines.append(line)
                                    continue
                                if "=" in line:
                                    k, v = line.split("=", 1)
                                    if k.strip() == key:
                                        lines.append(f"{key}={val}\n")
                                        updated = True
                                        continue
                                lines.append(line)
                    
                    if not updated:
                        lines.append(f"{key}={val}\n")
                    
                    with open(env_path, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    
                    os.environ[key] = val
                    from dotenv import load_dotenv
                    load_dotenv(env_path)
                    
                    import config.settings as settings
                    if key == "TELEGRAM_BOT_TOKEN" or key == "TELEGRAM_TOKEN":
                        settings.TELEGRAM_TOKEN = val
                    elif key == "OPENCODE_SERVER_URL":
                        settings.OPENCODE_SERVER_URL = val
                        settings.OPENCODE_BASE_URL = val.rstrip("/")
                    
                    display_val = val
                    if any(x in key.lower() for x in ("token", "key", "secret", "password")):
                        display_val = val[:3] + "..." + val[-3:] if len(val) > 6 else "..."
                        
                    print_success(f"Updated config: {key}={display_val} (saved to {env_path})")
                    continue

                else:
                    print_error(f"Unknown command: {cmd}")
                continue

            elif user_input.startswith("!"):
                shell_cmd = user_input[1:].strip()
                if not shell_cmd:
                    print_error("Empty shell command. Usage: !<command>")
                    continue
                shell_output = await run_shell_command(shell_cmd)
                if shell_output:
                    _pending_inject = f"Shell command completed:\n{shell_output}"
                continue

            # Check budget limit before sending
            if budget_tracker.is_exceeded(session.session_id):
                print_error("⚠️ Session budget limit exceeded. Increase budget via /budget to continue.")
                continue

            # Offline guard — return immediately if server is unreachable
            if not connection_state[0]:
                alive = await engine.client.is_alive()
                connection_state[0] = alive
                if not alive:
                    err_detail = getattr(engine.client, '_last_alive_error', None)
                    msg = "BMO is offline — OpenCode server not reachable."
                    if err_detail:
                        msg += f"\n  Reason: {err_detail}"
                    msg += "\n  Start it with:  opencode serve --port 4800"
                    print_error(msg)
                    continue

            # Regular message — stream response
            import time as _time
            # ── Dual-channel input system ────────────────────────────────────
            # Channel 1: Ctrl+C / ESC        → hard cancel
            # Channel 2: keystroke + Enter   → mid-run injection (abort + new message)
            _cancel_event = threading.Event()
            _inject_event = threading.Event()
            _inject_buffer = []  # chars typed during thinking
            _spinner_active = [True]  # shared flag for permission prompt suspension

            def _key_watcher():
                """Reads keystrokes during thinking. Supports abort and mid-run injection."""
                try:
                    import msvcrt
                    while not _cancel_event.is_set() and not _inject_event.is_set():
                        if not _spinner_active[0]:
                            import time as _t; _t.sleep(0.1)
                            continue
                        if msvcrt.kbhit():
                            ch = msvcrt.getwch()
                            if ch in ('\x03', '\x1b'):  # Ctrl+C or ESC → hard cancel
                                _cancel_event.set()
                                return
                            elif ch in ('\r', '\n'):  # Enter → inject buffer as new message
                                if _inject_buffer:
                                    _inject_event.set()
                                    return
                            elif ch == '\x08':  # Backspace
                                if _inject_buffer:
                                    _inject_buffer.pop()
                                    sys.stdout.write('\b \b')
                                    sys.stdout.flush()
                            elif ch.isprintable():
                                _inject_buffer.append(ch)
                                sys.stdout.write(ch)
                                sys.stdout.flush()
                        import time as _t; _t.sleep(0.05)
                except Exception:
                    pass

            threading.Thread(target=_key_watcher, daemon=True).start()

            _current_activity = ["thinking..."]
            _last_printed_thinking = [""]
            _printed_thought_header = [False]
            _tool_start_times = {}
            _active_tool = [None]
            _tool_calls_count = {}

            def on_token(partial: str):
                pass  # no live display — kept for API compatibility

            def on_activity(kind: str, detail: str):
                _current_activity[0] = detail
                
                # Clear spinner line
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.flush()

                if kind == "thinking":
                    if not _printed_thought_header[0]:
                        sys.stdout.write(f"\n\x1b[2mThought:\x1b[0m\n")
                        sys.stdout.flush()
                        _printed_thought_header[0] = True
                    
                    new_thinking = detail[len(_last_printed_thinking[0]):]
                    if new_thinking:
                        sys.stdout.write(f"\x1b[2m{new_thinking}\x1b[0m")
                        sys.stdout.flush()
                        _last_printed_thinking[0] = detail
                else:
                    # If we transition away from thinking, ensure there's a newline
                    if _printed_thought_header[0]:
                        sys.stdout.write("\n\n")
                        sys.stdout.flush()
                        _printed_thought_header[0] = False
                        _last_printed_thinking[0] = ""

                    if kind == "tool_call":
                        try:
                            tool_name = detail.replace("🔧 ", "").split("(", 1)[0].strip()
                        except Exception:
                            tool_name = "tool"
                        
                        _active_tool[0] = tool_name
                        _tool_start_times[tool_name] = _time.monotonic()
                        _tool_calls_count[tool_name] = _tool_calls_count.get(tool_name, 0) + 1
                        
                        from core.cli_renderer import print_tool_start
                        print_tool_start("Execute Tool", tool_name)
                        
                    elif kind == "tool_result":
                        try:
                            parts = detail.split(" ", 2)
                            tool_name = parts[1].strip()
                        except Exception:
                            tool_name = _active_tool[0] or "tool"
                        
                        start_time = _tool_start_times.get(tool_name, _time.monotonic())
                        duration = _time.monotonic() - start_time
                        count = _tool_calls_count.get(tool_name, 1)
                        
                        from core.cli_renderer import print_tool_finish
                        print_tool_finish("Execute Tool", tool_name, tool_calls_count=count, elapsed_secs=duration)
                        _active_tool[0] = None


            async def on_permission(perm_id: str, perm_type: str, patterns: list) -> str:
                """Called when the OpenCode server requests permission. Pauses spinner and prompts user."""
                _spinner_active[0] = False
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.flush()
                panel = Panel(
                    f"[bold yellow]⚠️  BMO SECURITY PERMISSION REQUEST[/bold yellow]\n\n"
                    f"[white]Type:[/white] [bold]{perm_type}[/bold]\n"
                    f"[white]Scope:[/white] [code]{', '.join(patterns[:3]) if patterns else '*'}[/code]\n\n"
                    f"[dim]Choose an option (Auto-approves 'allow' after 7 minutes of inactivity):[/dim]\n"
                    f"  [bold cyan]a[/bold cyan] → Allow once\n"
                    f"  [bold cyan]d[/bold cyan] → Deny\n"
                    f"  [bold cyan]y[/bold cyan] → Yes, always (save to vault)",
                    border_style="yellow",
                    width=70,
                )
                console.print(panel)
                
                try:
                    # 7 minutes timeout (420 seconds)
                    choice = await asyncio.wait_for(
                        _PS().prompt_async("Your choice [a/d/y]: "),
                        timeout=420.0
                    )
                    choice = choice.strip().lower()
                except asyncio.TimeoutError:
                    from datetime import datetime as _dt
                    print_info("\n⚠️  Permission request timed out after 7 minutes of inactivity. Auto-approving once to avoid freezing.")
                    # Log the auto-approval to logs/permissions.log
                    try:
                        log_dir = os.path.join(os.path.dirname(__file__), "logs")
                        os.makedirs(log_dir, exist_ok=True)
                        with open(os.path.join(log_dir, "permissions.log"), "a", encoding="utf-8") as lf:
                            lf.write(f"[{_dt.now().isoformat()}] AUTO-APPROVED (Timeout): type={perm_type}, scope={patterns}\n")
                    except Exception:
                        pass
                    choice = "allow"

                if choice in ("a", "allow"):
                    reply = "allow"
                elif choice in ("y", "yes", "always"):
                    reply = "always"
                else:
                    reply = "deny"
                _spinner_active[0] = True
                sys.stdout.write(f"  {_DIM}BMO is thinking...{_RESET}\n")
                sys.stdout.flush()
                return reply

            async def on_question(call_id: str, question_state: dict) -> list:
                """Called when the OpenCode server requests answers to one or more multiple-choice questions."""
                _spinner_active[0] = False
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.flush()
                
                questions = question_state.get("questions", [])
                if not questions:
                    single_q = question_state.get("question")
                    single_opts = question_state.get("options", [])
                    is_multi = question_state.get("is_multi_select", False)
                    if single_q:
                        questions = [{"question": single_q, "options": single_opts, "is_multi_select": is_multi}]

                answers = []
                for idx, q_item in enumerate(questions):
                    q_text = q_item.get("question", "Question")
                    options = q_item.get("options", [])
                    is_multi = q_item.get("is_multi_select", False)
                    
                    options_lines = []
                    for o_idx, opt in enumerate(options):
                        options_lines.append(f"  [bold cyan]{o_idx + 1}[/bold cyan] → {opt}")
                    
                    options_str = "\n".join(options_lines)
                    multi_note = " (Select multiple separated by commas)" if is_multi else " (Select one option)"
                    
                    panel = Panel(
                        f"[bold yellow]❓  BMO INTERACTIVE QUESTION {idx + 1}/{len(questions)}[/bold yellow]\n\n"
                        f"[white]{q_text}[/white]\n\n"
                        f"{options_str}\n\n"
                        f"[dim]Please select by index number{multi_note}:[/dim]",
                        border_style="yellow",
                        width=70,
                    )
                    console.print(panel)
                    
                    while True:
                        try:
                            choice = await _PS().prompt_async(f"Your choice (1-{len(options)}): ")
                            choice = choice.strip()
                            if not choice:
                                continue
                            
                            if is_multi:
                                selected_indices = []
                                parts = [p.strip() for p in choice.split(",")]
                                for p in parts:
                                    if p.isdigit():
                                        val = int(p)
                                        if 1 <= val <= len(options):
                                            selected_indices.append(val - 1)
                                if selected_indices:
                                    answers.extend([options[i] for i in selected_indices])
                                    break
                            else:
                                if choice.isdigit():
                                    val = int(choice)
                                    if 1 <= val <= len(options):
                                        answers.append(options[val - 1])
                                        break
                            print_error("Invalid selection. Please enter a valid number within the range.")
                        except (KeyboardInterrupt, asyncio.CancelledError):
                            answers.append(options[0] if options else "")
                            break

                _spinner_active[0] = True
                sys.stdout.write(f"  {_DIM}BMO is thinking...{_RESET}\n")
                sys.stdout.flush()
                return answers

            stream_task = asyncio.ensure_future(
                engine.send_message_streaming(
                    CHAT_ID, USER_ID, user_input,
                    interface="cli", on_token=on_token, on_activity=on_activity,
                    on_permission=on_permission, on_question=on_question,
                )
            )
            start_t = _time.monotonic()
            response = None
            elapsed_total = 0.0
            _dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            _tick = 0
            _DIM = "\x1b[2m"
            _CYAN = "\x1b[36m"
            _RESET = "\x1b[0m"
            _inj_prompt_shown = False
            try:
                while not stream_task.done():
                    if _cancel_event.is_set():
                        raise asyncio.CancelledError("cancelled via Ctrl+C / ESC")
                    if _inject_event.is_set():
                        raise asyncio.CancelledError("inject")
                    await asyncio.sleep(0.1)
                    if _spinner_active[0] and not _printed_thought_header[0] and not _active_tool[0]:
                        elapsed_total = _time.monotonic() - start_t
                        _tick += 1
                        if _tick % 10 == 0:
                            injected_preview = ''.join(_inject_buffer)
                            act = _current_activity[0]
                            import re
                            act_clean = re.sub(r'<[^>]+>', '', act)
                            if len(act_clean) > 40:
                                act_clean = act_clean[:37] + "..."
                            if injected_preview:
                                line = f"\r  {_DIM}{_dots[(_tick // 10) % len(_dots)]} BMO is thinking... ({elapsed_total:.1f}s) {_CYAN}[{act_clean}]{_RESET}  {_CYAN}↩ {injected_preview}{_RESET}   "
                            else:
                                line = f"\r  {_DIM}{_dots[(_tick // 10) % len(_dots)]} BMO is thinking... ({elapsed_total:.1f}s) {_CYAN}[{act_clean}]{_RESET}   "
                            sys.stdout.write(line + " " * max(0, 80 - len(line)))
                            sys.stdout.flush()
                elapsed_total = _time.monotonic() - start_t
                sys.stdout.write("\r" + " " * 70 + "\r")
                sys.stdout.flush()
                response = await stream_task
            except (KeyboardInterrupt, asyncio.CancelledError) as exc:
                stream_task.cancel()
                _cancel_event.set()
                # ── Abort OpenCode server-side stream ──
                _oc_sid = session.metadata.get("opencode_session_id")
                if _oc_sid:
                    try:
                        await engine.client.abort_session(_oc_sid)
                        logger.info("CLI abort_session(%s) sent", _oc_sid)
                    except Exception:
                        pass
                elapsed_total = _time.monotonic() - start_t
                response = None
                sys.stdout.write("\r" + " " * 70 + "\r")
                sys.stdout.flush()

                # ── Mid-run injection: queue the typed text for next iteration ──
                injected_text = ''.join(_inject_buffer).strip()
                if injected_text and str(exc) == "inject":
                    await engine.reset_opencode_session(CHAT_ID)
                    console.print(f"\n  [bold cyan]↩ Injecting:[/bold cyan] [italic]{injected_text}[/italic]\n")
                    _pending_inject = injected_text  # picked up at top of next loop iteration
                    continue  # skip cancel message, go straight to sending
            finally:
                _cancel_event.set()

            connection_state[0] = engine.client.is_connected

            if response is None:
                console.print("\n  [bold yellow]⚡ Request cancelled.[/bold yellow]  [dim](New OpenCode session will be used for next message)[/dim]\n")
                await engine.reset_opencode_session(CHAT_ID)
                continue

            budget_tracker.record_usage(session.session_id, model_id, len(user_input) * 4, len(response) * 4)
            print_assistant_message(response, elapsed_total, agent_name=session.metadata.get("active_agent", "default"))

        except (KeyboardInterrupt, asyncio.CancelledError):
            continue
        except EOFError:
            print_success("\nGoodbye!")
            break
        except Exception as e:
            print_error(f"An unexpected error occurred: {e}")

    # Cleanup: ensure worker subprocess and httpx client are closed
    # before the event loop tears down. Prevents unclosed
    # _ProactorBasePipeTransport warnings on /exit.
    try:
        if engine.worker_manager and engine.worker_manager.is_alive:
            await engine.worker_manager.shutdown()
    except Exception:
        pass
    try:
        if hasattr(engine.client._http, 'aclose'):
            await engine.client._http.aclose()
    except Exception:
        pass

def main_run():
    import warnings
    warnings.filterwarnings("ignore", category=ResourceWarning)
    # Suppress Windows asyncio pipe ValueError on exit (closed pipe repr noise)
    warnings.filterwarnings("ignore", message=".*I/O operation on closed pipe.*")

    # Set up basic logging for the CLI TUI to write to ~/.bmo/logs/cli.log
    try:
        from config.settings import LOGS_DIR
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=LOGS_DIR / "cli.log",
            filemode="a",
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            level=logging.INFO
        )
    except Exception:
        pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main_run()
