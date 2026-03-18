"""Patch tool: apply code patches directly (no Claude CLI required).

Designed for DeepSeek and other non-Anthropic LLMs that generate patches
in the *** Begin Patch format. Includes retry-with-autofix: on failure,
re-asks the LLM with the error context so it can fix the patch.
"""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
from typing import Any, Dict, List

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import utc_now_iso, append_jsonl, truncate_for_log

log = logging.getLogger(__name__)

_MAX_RETRIES = 2  # How many times to re-ask LLM to fix a broken patch


def _apply_patch_subprocess(patch_text: str, repo_dir: pathlib.Path) -> tuple[bool, str]:
    """Run apply_patch via subprocess. Returns (success, output)."""
    from ouroboros.apply_patch import APPLY_PATCH_PATH

    try:
        proc = subprocess.run(
            [str(APPLY_PATCH_PATH)],
            input=patch_text,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return True, "Patch applied successfully."
        err = (proc.stderr or proc.stdout or "unknown error").strip()
        return False, f"exit {proc.returncode}: {err}"
    except subprocess.TimeoutExpired:
        return False, "apply_patch timed out (30s)"
    except FileNotFoundError:
        return False, "apply_patch binary not found — run install() first"
    except Exception as e:
        return False, f"apply_patch error: {type(e).__name__}: {e}"


def _ask_llm_to_fix_patch(
    ctx: ToolContext,
    original_prompt: str,
    patch_text: str,
    error_msg: str,
    file_contents: dict[str, str],
) -> str | None:
    """Ask the LLM to fix a broken patch. Returns corrected patch or None."""
    from ouroboros.llm import LLMClient

    model = os.environ.get("OUROBOROS_MODEL", "deepseek-chat")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    base_url = os.environ.get("OUROBOROS_LLM_BASE_URL", "https://openrouter.ai/api/v1")

    if not api_key:
        return None

    # Build context with file contents
    files_ctx = ""
    for fpath, content in file_contents.items():
        files_ctx += f"\n--- {fpath} ---\n{content}\n"

    fix_prompt = (
        f"You generated this patch but it FAILED to apply.\n\n"
        f"ORIGINAL REQUEST: {original_prompt}\n\n"
        f"YOUR PATCH:\n```\n{patch_text}\n```\n\n"
        f"ERROR: {error_msg}\n\n"
        f"CURRENT FILE CONTENTS:{files_ctx}\n\n"
        f"Generate a CORRECTED patch in the exact same format "
        f"(*** Begin Patch / *** Update File / *** End Patch). "
        f"Make sure context lines match the actual file content EXACTLY. "
        f"Output ONLY the patch, no explanations."
    )

    try:
        client = LLMClient(api_key=api_key, base_url=base_url)
        msg, usage = client.chat(
            messages=[{"role": "user", "content": fix_prompt}],
            model=model,
            max_tokens=8192,
        )

        # Track cost
        if usage.get("cost"):
            ctx.pending_events.append({
                "type": "llm_usage",
                "provider": "patch_autofix",
                "model": model,
                "usage": usage,
                "source": "apply_code_patch",
                "ts": utc_now_iso(),
                "category": "task",
            })

        content = msg.get("content", "")
        if not content:
            return None

        # Extract patch from response (might be wrapped in markdown code block)
        if "*** Begin Patch" in content:
            start = content.index("*** Begin Patch")
            # Find end
            end_markers = ["*** End Patch", "```"]
            end = len(content)
            for marker in end_markers:
                idx = content.find(marker, start + 15)
                if idx >= 0:
                    if marker == "*** End Patch":
                        idx += len(marker)
                    end = min(end, idx)
            return content[start:end].strip()

        return None

    except Exception as e:
        log.warning("Patch autofix LLM call failed: %s", e)
        return None


def _extract_patch_files(patch_text: str) -> list[str]:
    """Extract file paths referenced in a patch."""
    files = []
    for line in patch_text.splitlines():
        for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:"):
            if line.startswith(prefix):
                path = line.split(":", 1)[1].strip()
                files.append(path)
    return files


def _read_file_contents(repo_dir: pathlib.Path, files: list[str]) -> dict[str, str]:
    """Read current contents of files referenced in patch."""
    contents = {}
    for fpath in files:
        full = repo_dir / fpath
        if full.exists():
            try:
                contents[fpath] = full.read_text(encoding="utf-8")
            except Exception:
                contents[fpath] = "<read error>"
    return contents


def _apply_code_patch(ctx: ToolContext, patch: str, description: str = "") -> str:
    """Apply a code patch with retry-on-failure.

    Args:
        patch: The patch text in *** Begin Patch format.
        description: Optional description of what the patch does (used for autofix context).
    """
    from ouroboros.tools.git import _acquire_git_lock, _release_git_lock
    from ouroboros.utils import run_cmd

    # Validate patch format
    if "*** Begin Patch" not in patch and "*** Update File" not in patch and "*** Add File" not in patch:
        return (
            "⚠️ INVALID_PATCH_FORMAT: patch must use the *** Begin Patch format.\n"
            "Expected format:\n"
            "*** Begin Patch\n"
            "*** Update File: path/to/file.py\n"
            "@@ context @@\n"
            " unchanged line\n"
            "-removed line\n"
            "+added line\n"
            "*** End Patch"
        )

    lock = _acquire_git_lock(ctx)
    try:
        # Ensure we're on the right branch
        try:
            run_cmd(["git", "checkout", ctx.branch_dev], cwd=ctx.repo_dir)
        except Exception as e:
            return f"⚠️ GIT_ERROR (checkout): {e}"

        # Attempt 1: apply directly
        ok, msg = _apply_patch_subprocess(patch, ctx.repo_dir)
        if ok:
            # Check for uncommitted changes
            warning = _check_changes(ctx.repo_dir)
            return f"✅ Patch applied successfully.{warning}"

        log.warning("Patch failed (attempt 1): %s", msg)

        # Retry with autofix
        files = _extract_patch_files(patch)
        file_contents = _read_file_contents(ctx.repo_dir, files)

        current_patch = patch
        for attempt in range(2, 2 + _MAX_RETRIES):
            ctx.emit_progress_fn(f"Patch failed, asking LLM to fix (attempt {attempt})...")

            fixed = _ask_llm_to_fix_patch(
                ctx,
                description or "apply code changes",
                current_patch,
                msg,
                file_contents,
            )
            if not fixed:
                break

            ok2, msg2 = _apply_patch_subprocess(fixed, ctx.repo_dir)
            if ok2:
                warning = _check_changes(ctx.repo_dir)
                return f"✅ Patch applied (fixed on attempt {attempt}).{warning}"

            log.warning("Patch autofix failed (attempt %d): %s", attempt, msg2)
            current_patch = fixed
            msg = msg2

        # All attempts failed
        try:
            append_jsonl(ctx.drive_logs() / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "patch_failure",
                "error": truncate_for_log(msg, 1000),
                "files": files,
                "attempts": 1 + _MAX_RETRIES,
            })
        except Exception:
            pass

        return (
            f"⚠️ PATCH_FAILED after {1 + _MAX_RETRIES} attempts.\n"
            f"Last error: {msg}\n\n"
            f"Fallback: use repo_write_commit to write the file directly, "
            f"or fix the patch context lines to match the actual file content."
        )

    finally:
        _release_git_lock(lock)


def _check_changes(repo_dir: pathlib.Path) -> str:
    """Return a reminder about uncommitted changes if any."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_dir),
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return "\n\n⚠️ UNCOMMITTED CHANGES — run repo_commit_push to commit."
    except Exception:
        pass
    return ""


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("apply_code_patch", {
            "name": "apply_code_patch",
            "description": (
                "Apply a code patch in *** Begin Patch format. "
                "Preferred for targeted edits (add/remove/modify lines). "
                "Auto-retries with LLM fix on failure. "
                "Follow with repo_commit_push to commit changes."
            ),
            "parameters": {"type": "object", "properties": {
                "patch": {
                    "type": "string",
                    "description": (
                        "The patch text. Format:\n"
                        "*** Begin Patch\n"
                        "*** Update File: path/to/file.py\n"
                        "@@ optional context @@\n"
                        " unchanged context line\n"
                        "-line to remove\n"
                        "+line to add\n"
                        "*** End Patch"
                    ),
                },
                "description": {
                    "type": "string",
                    "default": "",
                    "description": "Brief description of the change (helps autofix on failure).",
                },
            }, "required": ["patch"]},
        }, _apply_code_patch, is_code_tool=True, timeout_sec=120),
    ]
