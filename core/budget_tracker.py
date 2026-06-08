"""
Tracks API token usage and estimates session costs in USD.
Provides automatic warnings when a specified dollar budget is reached.
"""

from typing import Dict

# Approximate pricing per 1 million tokens (Input / Output USD)
MODEL_PRICING = {
    "qwen3.6-plus-free": {"input": 0.0, "output": 0.0},
    "big-pickle": {"input": 0.0, "output": 0.0},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
}

class BudgetTracker:
    def __init__(self, default_limit: float = 5.00):
        self.limits: Dict[str, float] = {}  # session_id -> budget limit in USD
        self.usage: Dict[str, Dict[str, float]] = {}  # session_id -> {"cost": float, "tokens": int}
        self.default_limit = default_limit

    def set_limit(self, session_id: str, limit: float):
        """Sets the budget limit for a specific session."""
        self.limits[session_id] = limit

    def get_limit(self, session_id: str) -> float:
        """Gets the budget limit for a session, fallback to default."""
        return self.limits.get(session_id, self.default_limit)

    def record_usage(self, session_id: str, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculates and stores cost for the current turn."""
        model_key = model_id.lower()
        pricing = {"input": 0.0, "output": 0.0}
        
        # Match substring for flexibility (e.g. claude-3-5-sonnet-latest -> claude-3-5-sonnet)
        for key, price in MODEL_PRICING.items():
            if key in model_key:
                pricing = price
                break
                
        cost = ((prompt_tokens / 1_000_000.0) * pricing["input"]) + \
               ((completion_tokens / 1_000_000.0) * pricing["output"])
               
        if session_id not in self.usage:
            self.usage[session_id] = {"cost": 0.0, "tokens": 0}
            
        self.usage[session_id]["cost"] += cost
        self.usage[session_id]["tokens"] += (prompt_tokens + completion_tokens)
        return cost

    def get_session_cost(self, session_id: str) -> float:
        """Gets total estimated cost for a session."""
        if session_id in self.usage:
            return self.usage[session_id]["cost"]
        return 0.0

    def is_exceeded(self, session_id: str) -> bool:
        """Checks if current session cost exceeds the set limit."""
        current_cost = self.get_session_cost(session_id)
        limit = self.get_limit(session_id)
        return current_cost >= limit
