"""Example application with intentional security, performance, and style issues.

This file exists purely for testing the code review agent. It contains
deliberate vulnerabilities and anti-patterns that the review agents should detect.
"""

from __future__ import annotations

import os
import pickle
import sqlite3
import subprocess

# Hardcoded credentials (security: hardcoded secret)
DATABASE_URL = "postgresql://admin:password123@prod-db.internal:5432/app"
API_SECRET_KEY = "sk-live-abc123def456ghi789"


def authenticate_user(username: str, password: str) -> dict | None:
    """Authenticate a user against the database.

    Contains: SQL injection, timing attack, information leakage.
    """
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # SQL injection vulnerability (security: injection)
    query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'"
    cursor.execute(query)
    user = cursor.fetchone()

    if user is None:
        # Information leakage: reveals whether username exists
        cursor.execute(f"SELECT 1 FROM users WHERE username = '{username}'")
        if cursor.fetchone():
            raise ValueError(f"Invalid password for user: {username}")
        raise ValueError(f"User not found: {username}")

    # Timing attack: string comparison is not constant-time
    if password == user[2]:
        return {"id": user[0], "username": user[1], "role": user[3]}
    return None


def run_system_command(user_input: str) -> str:
    """Execute a system command based on user input.

    Contains: command injection.
    """
    # Command injection vulnerability (security: injection)
    result = subprocess.run(
        f"echo {user_input}",
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def load_user_data(serialized_data: bytes) -> object:
    """Deserialize user-provided data.

    Contains: insecure deserialization.
    """
    # Pickle deserialization of untrusted data (security: deserialization)
    return pickle.loads(serialized_data)


def process_items(items: list[dict]) -> list[dict]:
    """Process a list of items with various issues.

    Contains: N+1 query pattern, no pagination, memory issues.
    """
    conn = sqlite3.connect("items.db")
    results = []

    # N+1 query problem (performance: database)
    for item in items:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM details WHERE item_id = {item['id']}")
        detail = cursor.fetchone()
        results.append({**item, "detail": detail})

    # Unbounded list growth (performance: memory)
    all_items = []
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items")
    for row in cursor.fetchall():
        all_items.append(dict(zip(["id", "name", "price"], row, strict=False)))

    # No connection close (performance: resource leak)
    return results


def calculate_fibonacci(n: int) -> int:
    """Calculate fibonacci number recursively.

    Contains: exponential time complexity.
    """
    # O(2^n) time complexity (performance: algorithm)
    if n <= 1:
        return n
    return calculate_fibonacci(n - 1) + calculate_fibonacci(n - 2)


def format_user_output(user_data: dict) -> str:
    """Format user data for display.

    Contains: XSS vulnerability in HTML output.
    """
    # XSS vulnerability: unsanitized user input in HTML (security: XSS)
    name = user_data.get("name", "")
    email = user_data.get("email", "")
    return f"<div><h1>Welcome {name}</h1><p>Email: {email}</p></div>"


# Unused imports at module level (style: dead code)
import json  # noqa: F401, E402
import re  # noqa: F401, E402
import sys  # noqa: F401, E402


def x(a, b, c):  # noqa: ANN001, ANN201
    """Bad function name and no type hints (style: naming, types)."""
    # Magic numbers (style: readability)
    if a > 42:
        return b * 3.14159 + c / 2.71828
    return None


class data_processor:
    """Class with wrong naming convention (style: PascalCase)."""

    def __init__(self):  # noqa: ANN204
        self.d = {}
        self.temp = []
        self.x = 0

    def do_stuff(self, input):  # noqa: ANN001, ANN201
        """Shadows built-in 'input', no types, vague name."""
        # 60+ line function would go here (style: function length)
        for i in range(len(input)):
            item = input[i]
            if item is not None:
                if isinstance(item, dict):
                    if "key" in item:
                        value = item["key"]
                        if value is not None:
                            if isinstance(value, str):
                                self.d[i] = value.strip().lower()
                            else:
                                self.d[i] = str(value)
                        else:
                            self.d[i] = ""
                    else:
                        self.d[i] = str(item)
                else:
                    self.d[i] = str(item)
            else:
                self.d[i] = ""
        return self.d


# Environment variable used without validation (security: config)
DEBUG_MODE = os.environ.get("DEBUG", "true").lower() == "true"
SECRET_TOKEN = os.environ.get("TOKEN", "default-insecure-token")
