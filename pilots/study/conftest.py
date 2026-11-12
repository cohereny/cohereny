"""pytest conftest for study pilot unit tests.

Mocks heavy external dependencies (langchain, pypdf, dotenv) via sys.modules
BEFORE any test files are collected, so pure-function tests run without
installing the full LLM stack.
"""
from pathlib import Path
import sys
from unittest.mock import MagicMock

# Add the study pilot root to sys.path so tests can import the 'study' package
sys.path.insert(0, str(Path(__file__).parent))

# Mock all external dependencies that are imported at module level in study/
_HEAVY_DEPS = [
    "dotenv",
    "langchain_core",
    "langchain_core.messages",
    "langchain_openai",
    "pypdf",
    "langfuse",
    "psycopg",
    "psycopg.rows",
    "langgraph",
    "langgraph.graph",
    "langgraph.checkpoint",
    "langgraph.checkpoint.postgres",
    "langgraph.checkpoint.postgres.aio",
]
for _mod in _HEAVY_DEPS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
