"""
Autonomous task runner for BMO.
Decomposes a user objective into subtasks and executes each one via
isolated subagent sessions (separate OpenCode contexts).
Results are persisted to data/goals/ for later review via /goal results.
"""

import asyncio
import os
import re
import time
import json
import logging
from typing import Optional, List, Dict
from datetime import datetime
from uuid import uuid4

from core.bmo_engine import BMOEngine
from models.chat_models import ChatSession
from core.cli_renderer import print_info, print_success, print_error, console, bmo_spinner, render_markdown
from rich.rule import Rule
from config.settings import TELEGRAM_TOKEN

logger = logging.getLogger(__name__)


def _parse_subtasks(text: str) -> List[str]:
    """Extract numbered or bulleted subtasks from LLM output."""
    lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^(?:\d+[\.\)]|[-*]) ", line):
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
            cleaned = re.sub(r"^[-*]\s*", "", cleaned)
            lines.append(cleaned)
    return lines


def _fallback_parse_subtasks(text: str, objective: str) -> List[str]:
    """When no numbered list found, extract meaningful action lines."""
    text = text.strip()
    candidates = []
    for line in text.split("\n"):
        line = line.strip().rstrip(".")
        if not line or len(line) < 15:
            continue
        lower = line.lower()
        if any(lower.startswith(w) for w in ("objective", "here", "i'll", "let me", "sure", "okay", "note", "the", "this")):
            continue
        if any(kw in lower for kw in ("step", "first", "second", "third", "then", "next", "finally")):
            cleaned = re.sub(r"^(?:step\s*\d+[:\s.]*\s*|first[ly,\s]*|second[ly,\s]*|third[ly,\s]*|then[,\s]*|next[,\s]*|finally[,\s]*)", "", line, flags=re.IGNORECASE).strip()
            candidates.append(cleaned)
        elif len(candidates) < 5:
            candidates.append(line)
    if not candidates:
        return [objective]
    return candidates[:5]


class GoalRunner:
    def __init__(self, engine: BMOEngine):
        self.engine = engine
        self.is_running = False
        self.goals_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "goals"
        )
        os.makedirs(self.goals_dir, exist_ok=True)

    def _save_goal_result(self, objective: str, subtasks: List[str], results: List[Dict]) -> str:
        now = datetime.now()
        goal_id = now.strftime("%Y%m%d_%H%M%S")
        data = {
            "goal_id": goal_id,
            "objective": objective,
            "timestamp": now.isoformat(),
            "total_steps": len(subtasks),
            "completed_steps": sum(1 for r in results if not r["blocked"]),
            "subtasks": subtasks,
            "results": results,
        }
        with open(os.path.join(self.goals_dir, f"{goal_id}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return goal_id

    def list_saved_goals(self) -> List[Dict]:
        if not os.path.isdir(self.goals_dir):
            return []
        goals = []
        for fname in sorted(os.listdir(self.goals_dir), reverse=True):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(self.goals_dir, fname), "r", encoding="utf-8") as f:
                        data = json.load(f)
                        goals.append({
                            "goal_id": data["goal_id"],
                            "objective": data["objective"],
                            "timestamp": data["timestamp"],
                            "total_steps": data["total_steps"],
                            "completed_steps": data["completed_steps"],
                        })
                except Exception:
                    continue
        return goals

    def get_goal_result(self, goal_id: str) -> Optional[Dict]:
        filepath = os.path.join(self.goals_dir, f"{goal_id}.json")
        if not os.path.isfile(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    async def run_goal(self, chat_id: int, user_id: int, objective: str, max_steps: int = 10):
        self.is_running = True

        console.print()
        console.print(Rule(f"[bold cyan]BMO AUTONOMOUS GOAL[/bold cyan]", style="cyan"))
        console.print(f"  [bold white]{objective}[/bold white]")
        console.print(Rule(style="dim"))
        console.print()

        # 1. Plan: create a fresh session for the planning prompt so it doesn't pollute the main session
        plan_prompt = (
            f"Objective: {objective}\n\n"
            "Break this objective into 3-5 specific, actionable subtasks.\n"
            "Respond ONLY with a numbered list. Example:\n"
            "1. Scan directory structure\n"
            "2. Read config files\n"
            "3. Summarize findings\n"
            "Do NOT include greetings, explanations, or any text before/after the list."
        )

        async with bmo_spinner("Decomposing objective into subtasks..."):
            plan_result = await self.engine.send_message(chat_id, user_id, plan_prompt, interface="cli")

        subtasks = _parse_subtasks(plan_result)
        if not subtasks:
            subtasks = _fallback_parse_subtasks(plan_result, objective)
            console.print(f"  [dim yellow]Parsed {len(subtasks)} action items from plan response[/dim yellow]")

        total = min(len(subtasks), max_steps)
        subtasks = subtasks[:total]

        console.print(f"  [bold cyan]Plan:[/bold cyan]")
        for i, task in enumerate(subtasks, 1):
            console.print(f"    [dim]{i}. {task}[/dim]")
        console.print()

        # 2. Dispatch each subtask to its own isolated subagent session
        main_session = self.engine.storage.load_session(chat_id)
        main_agent = "default"
        main_provider = None
        main_model = None
        main_mode = "execute"
        if main_session:
            main_agent = main_session.metadata.get("active_agent", "default")
            main_provider = main_session.metadata.get("provider_id")
            main_model = main_session.metadata.get("model_id")
            main_mode = main_session.metadata.get("active_mode", "execute")

        async def _run_step(step_num: int, task: str) -> Dict:
            step_prompt = (
                f"Subtask {step_num}/{total}: {task}\n"
                f"Overall objective: {objective}\n\n"
                "Execute this step independently. Report back with results."
            )
            await self._notify_telegram(chat_id, f"Subtask {step_num}/{total}: {task}")
            t0 = time.time()

            # Create an isolated session for this subtask
            now = time.time()
            sub_session = ChatSession(
                chat_id=chat_id,
                user_id=user_id,
                username=f"goal_subtask_{step_num}",
                created_at=now,
                updated_at=now,
                messages=[],
                metadata={
                    "opencode_session_id": None,
                    "provider_id": main_provider,
                    "model_id": main_model,
                    "active_mode": main_mode,
                    "active_agent": main_agent,
                },
                title=f"Goal subtask {step_num}",
                session_id=str(uuid4()),
            )
            self.engine.storage.save_session(sub_session)

            async with bmo_spinner(f"Subtask {step_num}/{total}..."):
                response = await self.engine.send_message_in_session(
                    sub_session, step_prompt, interface="cli"
                )

            elapsed = time.time() - t0
            lower_resp = response.lower()
            blocked = "error" in lower_resp or "fail" in lower_resp or "blocked" in lower_resp
            return {
                "step": step_num,
                "task": task,
                "response": response,
                "elapsed": elapsed,
                "blocked": blocked,
            }

        results = []
        for i, t in enumerate(subtasks):
            res = await _run_step(i + 1, t)
            results.append(res)

        # 3. Persist and display results
        goal_id = self._save_goal_result(objective, subtasks, results)

        console.print()
        console.print(Rule("[bold cyan]Results[/bold cyan]", style="cyan"))
        console.print()
        for r in results:
            status = "BLOCKED" if r["blocked"] else "OK"
            color = "red" if r["blocked"] else "green"
            console.print(f"  [{color}]Step {r['step']}/{total} [{status}][/{color}] [white]{r['task']}[/white]")
            console.print(f"    [dim]Completed in {r['elapsed']:.1f}s[/dim]")
            render_markdown(r["response"])
            console.print()

            if r["blocked"]:
                await self._notify_telegram(chat_id, f"Goal Blocked: {r['task']}\n{r['response'][:150]}...")

        ok_count = sum(1 for r in results if not r["blocked"])
        summary_icon = "✅" if ok_count == total else "⚠️"
        console.print(Rule(f"[bold green]Goal: {ok_count}/{total} subtasks completed[/bold green]", style="green"))
        console.print(f"  [dim]Saved as [bold white]goal_{goal_id}[/bold white] — run [bold]/goal results {goal_id}[/bold] to view full details[/dim]")
        console.print()
        await self._send_goal_to_telegram(chat_id, goal_id, objective, subtasks, results, ok_count, total)
        self.is_running = False

    async def _send_goal_to_telegram(self, chat_id: int, goal_id: str, objective: str, subtasks: list, results: list, ok_count: int, total: int):
        token = TELEGRAM_TOKEN
        if not token:
            return
        import httpx

        async def _send(text: str, parse_mode: str = "HTML"):
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode}, timeout=10)
            except Exception as e:
                logger.error("Telegram send failed: %s", e)

        status_icon = "✅" if ok_count == total else "⚠️"
        total_elapsed = sum(r["elapsed"] for r in results)

        header = (
            f"🎯 <b>Goal: {objective[:150]}</b>\n"
            f"{status_icon} {ok_count}/{total} steps  —  {total_elapsed:.1f}s"
        )
        await _send(header)

        last = results[-1] if results else None
        if last and not last["blocked"]:
            body = last["response"].strip()
            if len(body) > 3900:
                body = body[:3897] + "..."
            await _send(f"📋 <b>Results:</b>\n\n{body}")

        else:
            parts = []
            for r in results:
                icon = "✅" if not r["blocked"] else "❌"
                snippet = r["response"].strip().split("\n")[0][:150]
                parts.append(f"{icon} <b>Step {r['step']}:</b> {snippet}")
            text = "\n\n".join(parts)
            if len(text) > 3900:
                text = text[:3897] + "..."
            await _send(text)

        await _send(f"<code>goal_id: {goal_id}  |  /goal results {goal_id}</code>")

    async def _notify_telegram(self, chat_id: int, message: str):
        token = TELEGRAM_TOKEN
        if not token:
            logger.info("[Goal Bridge] %s", message)
            return
        import httpx
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": f"BMO Goal:\n{message}", "parse_mode": "HTML"}
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.error("Telegram bridge failed: %s", e)
