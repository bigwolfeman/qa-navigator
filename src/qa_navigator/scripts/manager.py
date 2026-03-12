"""Script Manager — persistent automation script library.

Scripts are Python files that replay a sequence of harness tool calls.
They are generated once (when a new app or screen is first explored),
saved to disk, and reused on every subsequent run.

On a PR with UI changes:
  - Existing scripts are run first (fast)
  - Any that fail are flagged for regeneration
  - New screens get new scripts

Directory layout:
  {script_dir}/{app_slug}/{capability_slug}.py

Each script is async Python using the `computer` harness object in scope:
  await computer.find_and_click("File")
  await computer.key_combination(["escape"])
  await computer.find_and_type("", "Hello world")
"""

import re
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


def _slug(name: str) -> str:
    """Convert a name to a safe filename slug."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip())[:60]


def script_header(app: str, capability: str, description: str) -> str:
    return (
        f"# QA Navigator Auto-Script\n"
        f"# App: {app}\n"
        f"# Capability: {capability}\n"
        f"# Description: {description}\n"
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"# Replay with: ScriptManager.replay(path, computer)\n\n"
        f"import asyncio as _asyncio\n\n"
    )


class ScriptManager:
    """Manages the on-disk library of automation scripts for an app."""

    def __init__(self, script_dir: Path, app_name: str):
        self.app_dir = script_dir / _slug(app_name)
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.app_name = app_name

    def save(self, capability: str, description: str, tool_calls: list[dict]) -> Path:
        """Save a sequence of tool calls as a replayable Python script.

        Args:
            capability: Short name for what this script tests (e.g. "menu_exploration")
            description: Human-readable description
            tool_calls: List of {"tool": name, "args": {...}} dicts

        Returns:
            Path to the saved script file.
        """
        slug = _slug(capability)
        path = self.app_dir / f"{slug}.py"

        if path.exists():
            shutil.copy2(path, self.app_dir / f"{slug}.bak.py")

        lines = [script_header(self.app_name, capability, description)]
        for call in tool_calls:
            tool = call["tool"]
            args = call.get("args", {})
            if tool == "find_and_click":
                en = repr(args.get("element_name", ""))
                lines.append(f"await computer.find_and_click({en})")
            elif tool == "find_and_type":
                en = repr(args.get("element_name", ""))
                text = repr(args.get("text", ""))
                enter = args.get("press_enter", False)
                lines.append(f"await computer.find_and_type({en}, {text}, press_enter={enter})")
            elif tool == "key_combination":
                keys = args.get("keys", [])
                lines.append(f"await computer.key_combination({keys!r})")
            elif tool == "get_ui_tree":
                lines.append("await computer.get_ui_tree()  # discovery step")
            elif tool == "click_at":
                x, y = args.get("x", 0), args.get("y", 0)
                lines.append(f"await computer.click_at({x}, {y})")
            elif tool == "double_click_at":
                x, y = args.get("x", 0), args.get("y", 0)
                lines.append(f"await computer.double_click_at({x}, {y})")
            elif tool == "type_text":
                text = repr(args.get("text", ""))
                lines.append(f"await computer.type_text({text})")
            elif tool == "get_page_info":
                lines.append("await computer.get_page_info()  # verify state")
            elif tool == "screenshot":
                lines.append("await computer.current_state()  # screenshot")
            # add sleep after actions for UI settle
            lines.append("await _asyncio.sleep(0.4)")

        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def list_scripts(self) -> list[Path]:
        """List all saved scripts for this app."""
        return sorted(self.app_dir.glob("*.py"))

    def exists(self, capability: str) -> bool:
        return (self.app_dir / f"{_slug(capability)}.py").exists()

    def load(self, capability: str) -> Optional[str]:
        path = self.app_dir / f"{_slug(capability)}.py"
        return path.read_text(encoding="utf-8") if path.exists() else None

    async def replay(self, capability: str, computer) -> tuple[bool, str]:
        """Execute a saved script against the live computer.

        Returns:
            (success, output_log)
        """
        import io, sys as _sys
        code = self.load(capability)
        if not code:
            return False, f"No script found for '{capability}'"

        buf = io.StringIO()
        old = _sys.stdout
        _sys.stdout = buf
        try:
            import asyncio
            exec_globals = {
                "__builtins__": __builtins__,
                "computer": computer,
                "asyncio": asyncio,
            }
            # exec async code by wrapping in coroutine
            wrapped = f"async def _run():\n" + "\n".join(
                "    " + line for line in code.splitlines()
            ) + "\nimport asyncio as _a; _a.get_event_loop().run_until_complete(_run())"
            # Simpler: collect awaitable calls and run them
            await self._exec_async(code, exec_globals)
            return True, buf.getvalue()
        except Exception as e:
            return False, f"REPLAY ERROR: {e}"
        finally:
            _sys.stdout = old

    @staticmethod
    async def _exec_async(code: str, globs: dict) -> None:
        """Execute async Python code in the given globals dict."""
        import asyncio
        # Wrap the script body in an async function and await it
        indented = "\n".join("    " + line for line in code.splitlines())
        wrapper = f"async def __script__():\n{indented}\n"
        exec(wrapper, globs)  # noqa: S102
        await globs["__script__"]()
