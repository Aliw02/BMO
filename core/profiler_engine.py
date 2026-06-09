"""
Profiler engine — analyzes conversations and updates USER.md.
Runs every 7 messages to detect stable user traits, preferences, and constraints.
"""

import os
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

OBSERVATION_THRESHOLD = 7


class ProfilerEngine:
    """Analyzes conversation history and maintains USER.md with observed traits."""

    def __init__(self, user_file_path: str):
        self._user_file_path = user_file_path
        self._last_run_count: dict[int, int] = {}
        self._session_run: set[str] = set()

    def should_run(self, chat_id: int, message_count: int) -> bool:
        if message_count < OBSERVATION_THRESHOLD:
            return False
        last = self._last_run_count.get(chat_id, 0)
        if message_count - last >= OBSERVATION_THRESHOLD:
            self._last_run_count[chat_id] = message_count
            return True
        return False

    def get_profile_content(self) -> str:
        try:
            if os.path.exists(self._user_file_path):
                with open(self._user_file_path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception as e:
            logger.warning("Failed to read USER.md: %s", e)
        return ""

    async def analyze_and_update(
        self,
        chat_id: int,
        user_message: str,
        assistant_response: str,
        session_context: str,
        opencode_client,
    ) -> Optional[str]:
        prompt = (
            f"Based on this conversation exchange, identify if the user revealed any:\n"
            f"1. **Preferences** — communication style, format preferences, response preferences\n"
            f"2. **Habits** — recurring patterns in how they ask or direct\n"
            f"3. **Constraints** — limitations, restrictions, things they don't want\n"
            f"4. **Stable Traits** — personality traits, decision-making patterns, consistent behavior\n\n"
            f"User message: {user_message[:500]}\n"
            f"Assistant response: {assistant_response[:500]}\n\n"
            f"Existing profile:\n{session_context[:1000]}\n\n"
            f"If nothing notable, respond with 'NO_OBSERVATION'.\n"
            f"Otherwise, respond with brief observations, one per line, prefixed by the category:\n"
            f"PREFERENCE: ...\nHABIT: ...\nCONSTRAINT: ...\nTRAIT: ..."
        )
        try:
            result = await opencode_client.send_simple_query(prompt, timeout=60.0)
            if result.strip() == "NO_OBSERVATION":
                return None
            return result.strip()
        except Exception as e:
            logger.debug("Profiler analysis failed: %s", e)
            return None

    def write_observation(self, observation: str) -> bool:
        if not observation:
            return False
        try:
            content = self.get_profile_content()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            new_entry = f"- [{timestamp}] {observation}"

            marker = "## Recent Observations"
            if marker in content:
                lines = content.split("\n")
                insert_idx = None
                for i, line in enumerate(lines):
                    if line.strip() == marker:
                        insert_idx = i + 1
                        break
                if insert_idx is not None:
                    while insert_idx < len(lines) and (lines[insert_idx].strip() == "" or lines[insert_idx].startswith("- [")):
                        insert_idx += 1
                    lines.insert(insert_idx, new_entry)
                    found_updated = False
                    for i, line in enumerate(lines):
                        if line.startswith("*Last updated:"):
                            lines[i] = f"*Last updated: {timestamp[:10]}*"
                            found_updated = True
                            break
                    if not found_updated:
                        lines.append(f"*Last updated: {timestamp[:10]}*")
                    content = "\n".join(lines)
                else:
                    content += f"\n{new_entry}\n"
            else:
                content += f"\n\n{marker}\n\n{new_entry}\n\n---\n*Last updated: {timestamp[:10]}*\n"

            with open(self._user_file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error("Failed to write USER.md: %s", e)
            return False

    def extract_stable_observations(self, observation: str) -> dict:
        result = {"preferences": [], "habits": [], "constraints": [], "traits": []}
        for line in observation.split("\n"):
            line = line.strip()
            if line.startswith("PREFERENCE:"):
                result["preferences"].append(line[len("PREFERENCE:"):].strip())
            elif line.startswith("HABIT:"):
                result["habits"].append(line[len("HABIT:"):].strip())
            elif line.startswith("CONSTRAINT:"):
                result["constraints"].append(line[len("CONSTRAINT:"):].strip())
            elif line.startswith("TRAIT:"):
                result["traits"].append(line[len("TRAIT:"):].strip())
        return result
