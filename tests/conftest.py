"""
Shared pytest configuration and fixtures.
Sets required environment variables before any module imports happen,
so pydantic-settings can build `Settings` without a real .env file.
"""
import os

# ── Minimal env vars required by Settings before any import ──────────────────
os.environ.setdefault("SYSTEM_PROMPT", "test-prompt")
os.environ.setdefault("OUTPUT_DIR", "/tmp/test-outputs")
os.environ.setdefault("SLIDE_DECK_INSTRUCTION", "test-instruction")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pw@localhost/testdb")
