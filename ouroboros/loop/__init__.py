"""
Loop module - decomposed from the monolithic loop.py.

Public API:
- run_llm_loop: main entry point
- _StatefulToolExecutor: tool execution class
"""

from .core import run_llm_loop
from .tool_execution import _StatefulToolExecutor

__all__ = ["run_llm_loop", "_StatefulToolExecutor"]