"""CI Runner — orchestrates the full CI/CD test flow.

Implements the three-phase playbook:
  1. Replay existing scripts (fast, validates current state)
  2. Explore uncovered UI elements and generate new scripts
  3. Report results and optionally commit new scripts

Usage:
  python -m qa_navigator --ci --url URL [--script-dir qa_scripts/]
"""

import asyncio
import base64
import re
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from rich.console import Console

from ..checklist.models import ChecklistItem, TestCategory, TestPriority, ItemStatus
from ..config import settings
from ..orchestrator.executor import TestExecutor, ExecutionResult
from ..scripts.manager import ScriptManager

console = Console()


class CoverageMap:
    """Tracks which UI elements are covered by existing scripts."""

    def __init__(self):
        self.covered_elements: set[str] = set()

    def parse_scripts(self, script_dir: Path) -> None:
        """Extract element names from all scripts in a directory."""
        for py_file in script_dir.glob("*.py"):
            code = py_file.read_text(encoding="utf-8")
            # find_and_click('Element Name') or find_and_click("Element Name")
            for match in re.finditer(r"find_and_click\(['\"]([^'\"]+)['\"]\)", code):
                self.covered_elements.add(match.group(1))
            for match in re.finditer(r"find_and_type\(['\"]([^'\"]+)['\"]", code):
                self.covered_elements.add(match.group(1))
            # click_at(x, y) / double_click_at(x, y) — track as coord markers
            for match in re.finditer(r"(?:double_click_at|click_at)\((\d+),\s*(\d+)\)", code):
                self.covered_elements.add(f"coord_{match.group(1)}_{match.group(2)}")
            # key_combination(["ctrl", "s"]) or key_combination(keys=["ctrl", "s"])
            for match in re.finditer(r"key_combination\([^\)]*\[([^\]]+)\]", code):
                keys = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
                if keys:
                    self.covered_elements.add("key_" + "+".join(keys))

    def find_uncovered(self, ui_tree: dict) -> list[dict]:
        """Compare live UI tree against covered elements.

        Returns list of uncovered element dicts with name, type, bounds.
        """
        uncovered = []
        self._walk(ui_tree.get("elements", []), uncovered)
        return uncovered

    def _walk(self, nodes: list, uncovered: list) -> None:
        _INTERACTIVE = {
            # Windows UIA control types
            "ButtonControl", "MenuItemControl", "MenuBarItemControl",
            "CheckBoxControl", "RadioButtonControl", "EditControl",
            "ComboBoxControl", "ListItemControl", "TabItemControl",
            "TreeItemControl", "HyperlinkControl",
            # Playwright accessibility roles (browser mode)
            "button", "link", "textbox", "checkbox", "radio",
            "combobox", "listitem", "tab", "menuitem", "treeitem",
            "menuitemcheckbox", "menuitemradio", "option",
        }
        for node in nodes:
            name = node.get("name", "")
            ctrl_type = node.get("type", "")
            enabled = node.get("enabled", True)

            if name and enabled and ctrl_type in _INTERACTIVE:
                if name not in self.covered_elements:
                    uncovered.append({
                        "name": name,
                        "type": ctrl_type,
                        "bounds": node.get("bounds"),
                    })

            children = node.get("children", [])
            if children:
                self._walk(children, uncovered)


class ScriptResult:
    """Result of replaying or running a single script."""
    def __init__(self, script_name: str, status: str, observation: str = ""):
        self.script_name = script_name
        self.status = status  # PASS, FAIL, BROKEN, NEW_PASS, NEW_FAIL
        self.observation = observation


class CIRunner:
    """Orchestrates the full CI test run per the playbook."""

    def __init__(
        self,
        computer,
        script_dir: Path,
        app_name: str,
        native_desktop: bool = False,
    ):
        self.computer = computer
        self.script_dir = script_dir
        self.app_name = app_name
        self.native_desktop = native_desktop
        self.manager = ScriptManager(script_dir, app_name)
        self.executor = TestExecutor(computer, native_desktop=native_desktop)
        self.results: list[ScriptResult] = []

    async def run(self) -> int:
        """Execute the full 3-phase CI playbook.

        Returns exit code: 0=all pass, 1=failures, 2=errors.
        """
        console.print("\n[bold cyan]== Phase 1: Replay Existing Scripts ==[/]\n")
        broken_scripts = await self._phase_replay()

        console.print("\n[bold cyan]== Phase 2: Explore Uncovered UI ==[/]\n")
        await self._phase_explore(broken_scripts)

        console.print("\n[bold cyan]== Phase 3: Report ==[/]\n")
        return self._phase_report()

    # ── Phase 1: Replay ──────────────────────────────────────────────────

    async def _phase_replay(self) -> list[str]:
        """Replay all existing scripts. Returns list of broken script names."""
        scripts = self.manager.list_scripts()
        if not scripts:
            console.print("  No existing scripts found. Skipping to Phase 2.")
            return []

        console.print(f"  Found {len(scripts)} saved scripts.")
        broken: list[str] = []

        for script_path in scripts:
            name = script_path.stem
            console.print(f"\n  [bold]{name}[/]")
            code = script_path.read_text(encoding="utf-8")

            # Build a ChecklistItem from the script header comments
            desc = self._extract_header(code, "Description") or name
            item = ChecklistItem(
                id=f"SCR-{name[:8].upper()}",
                category=TestCategory.BUTTON_CLICK,
                priority=TestPriority.HIGH,
                description=desc,
                action=str(script_path),
                expected_outcome=f"Script '{name}' completes successfully",
            )

            result = await self.executor.execute_item(item)

            if result.status == ItemStatus.PASSED:
                console.print(f"  [green]PASS[/] — {result.observation[:100]}")
                self.results.append(ScriptResult(name, "PASS", result.observation))
            else:
                # Check if it's BROKEN (elements not found) vs FAIL (elements found but wrong state)
                is_broken = "not found" in result.observation.lower() or "element" in result.observation.lower()
                status = "BROKEN" if is_broken else "FAIL"
                console.print(f"  [red]{status}[/] — {result.observation[:100]}")
                self.results.append(ScriptResult(name, status, result.observation))
                if status == "BROKEN":
                    broken.append(name)

            # Inter-item delay
            await asyncio.sleep(settings.inter_item_delay_seconds)

        return broken

    # ── Phase 2: Explore ─────────────────────────────────────────────────

    async def _phase_explore(self, broken_scripts: list[str]) -> None:
        """Detect uncovered UI elements and generate new scripts."""
        # Capture current UI state
        state = await self.computer.current_state()
        ui_tree = {}
        if hasattr(self.computer, "get_ui_tree"):
            try:
                ui_tree = await self.computer.get_ui_tree()
            except Exception as e:
                console.print(f"  [yellow]UI tree capture failed: {e}[/]")

        if not ui_tree:
            console.print("  No UI tree available. Skipping exploration.")
            return

        # Build coverage map
        coverage = CoverageMap()
        if self.manager.app_dir.exists():
            coverage.parse_scripts(self.manager.app_dir)

        uncovered = coverage.find_uncovered(ui_tree)
        console.print(
            f"  Coverage: {len(coverage.covered_elements)} elements covered, "
            f"{len(uncovered)} uncovered"
        )

        if not uncovered and not broken_scripts:
            console.print("  [green]Full coverage. Nothing to explore.[/]")
            return

        # Generate scripts for uncovered elements
        if uncovered:
            console.print(f"\n  Generating scripts for {len(uncovered)} uncovered elements...")
            await self._generate_scripts_for(uncovered, state.screenshot, ui_tree)

        # Regenerate broken scripts
        for name in broken_scripts:
            console.print(f"\n  Regenerating broken script: {name}")
            # Use the original description
            old_code = self.manager.load(name)
            desc = self._extract_header(old_code or "", "Description") or name
            await self._regenerate_script(name, desc, state.screenshot, ui_tree)

    async def _generate_scripts_for(
        self,
        uncovered: list[dict],
        screenshot: bytes,
        ui_tree: dict,
    ) -> None:
        """Ask Gemini to design scripts for uncovered UI elements."""
        client = genai.Client()
        scr_b64 = base64.b64encode(screenshot).decode()

        # Group uncovered elements into logical test scripts (max 5 per batch)
        element_desc = "\n".join(
            f"  - {e['name']} ({e['type']})" for e in uncovered[:30]
        )

        prompt = f"""You are a QA test designer. Given a screenshot and a list of uncovered UI elements,
design automation test scripts.

UNCOVERED ELEMENTS (not yet tested):
{element_desc}

For each logical group of related elements, design ONE test script.
Each script should test a complete workflow (not just click a single button).

Output JSON array:
[
  {{
    "capability": "short_name_for_this_test",
    "description": "What this script tests",
    "steps": [
      {{"tool": "get_ui_tree", "args": {{}}}},
      {{"tool": "find_and_click", "args": {{"element_name": "exact UIA name"}}}},
      {{"tool": "key_combination", "args": {{"keys": ["ctrl", "s"]}}}},
      {{"tool": "find_and_type", "args": {{"element_name": "field name", "text": "test input"}}}}
    ]
  }}
]

Rules:
- Use EXACT element names from the uncovered list above
- Start every script with get_ui_tree to discover current state
- Design 1-5 scripts max — group related elements into workflows
- Each script should be 5-20 steps
- Output ONLY valid JSON, no markdown fences"""

        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.analysis_model,
                    contents=[types.Content(role="user", parts=[
                        types.Part(inline_data=types.Blob(mime_type="image/png", data=scr_b64)),
                        types.Part(text=prompt),
                    ])],
                    config=types.GenerateContentConfig(temperature=0.2),
                ),
                timeout=90.0,
            )
            text = (resp.text or "").strip()
            # Strip markdown fences
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

            import json
            script_defs = json.loads(text)
        except Exception as e:
            console.print(f"  [red]Script generation failed: {e}[/]")
            return

        # Save and immediately test each generated script
        for sd in script_defs:
            cap = sd.get("capability", "unknown")
            desc = sd.get("description", cap)
            steps = sd.get("steps", [])

            if not steps:
                continue

            path = self.manager.save(cap, desc, steps)
            console.print(f"  Saved: {path.name} ({len(steps)} steps)")

            # Run it through the function-calling loop
            item = ChecklistItem(
                id=f"NEW-{cap[:8].upper()}",
                category=TestCategory.BUTTON_CLICK,
                priority=TestPriority.MEDIUM,
                description=desc,
                action=str(path),
                expected_outcome=f"New test '{cap}' completes successfully",
            )
            result = await self.executor.execute_item(item)

            if result.status == ItemStatus.PASSED:
                console.print(f"  [green]NEW PASS[/] — {result.observation[:80]}")
                self.results.append(ScriptResult(cap, "NEW_PASS", result.observation))
            else:
                console.print(f"  [yellow]NEW FAIL[/] — {result.observation[:80]}")
                self.results.append(ScriptResult(cap, "NEW_FAIL", result.observation))
                # Don't delete — keep for review

            await asyncio.sleep(settings.inter_item_delay_seconds)

    async def _regenerate_script(
        self,
        name: str,
        description: str,
        screenshot: bytes,
        ui_tree: dict,
    ) -> None:
        """Regenerate a broken script with current UI state."""
        # Use the same explore flow but targeted at one capability
        element_names = []
        self._walk_tree_names(ui_tree.get("elements", []), element_names)

        client = genai.Client()
        scr_b64 = base64.b64encode(screenshot).decode()

        prompt = f"""You are a QA test designer. A previously working test script is now broken
because the UI has changed. Redesign the script for the current UI.

ORIGINAL TEST: {description}

AVAILABLE UI ELEMENTS:
{chr(10).join(f'  - {n}' for n in element_names[:40])}

Design a replacement script as a JSON object:
{{
  "capability": "{name}",
  "description": "{description}",
  "steps": [...]
}}

Output ONLY valid JSON, no markdown fences."""

        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.analysis_model,
                    contents=[types.Content(role="user", parts=[
                        types.Part(inline_data=types.Blob(mime_type="image/png", data=scr_b64)),
                        types.Part(text=prompt),
                    ])],
                    config=types.GenerateContentConfig(temperature=0.2),
                ),
                timeout=90.0,
            )
            text = (resp.text or "").strip()
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

            import json
            sd = json.loads(text)
            steps = sd.get("steps", [])
            if steps:
                self.manager.save(name, description, steps)
                console.print(f"  Regenerated: {name} ({len(steps)} steps)")
        except Exception as e:
            console.print(f"  [red]Regeneration failed for {name}: {e}[/]")

    # ── Phase 3: Report ──────────────────────────────────────────────────

    def _phase_report(self) -> int:
        """Print summary and return exit code."""
        passed = sum(1 for r in self.results if r.status in ("PASS", "NEW_PASS"))
        failed = sum(1 for r in self.results if r.status in ("FAIL", "NEW_FAIL"))
        broken = sum(1 for r in self.results if r.status == "BROKEN")
        total = len(self.results)

        console.print(f"\n  Total: {total} | Pass: {passed} | Fail: {failed} | Broken: {broken}")

        for r in self.results:
            icon = {"PASS": "[green]PASS[/]", "NEW_PASS": "[green]NEW[/]",
                    "FAIL": "[red]FAIL[/]", "NEW_FAIL": "[yellow]NEW_FAIL[/]",
                    "BROKEN": "[red]BROKEN[/]"}.get(r.status, r.status)
            console.print(f"  {icon} {r.script_name}: {r.observation[:80]}")

        if failed > 0 or broken > 0:
            return 1
        return 0

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_header(code: str, field: str) -> Optional[str]:
        """Extract a value from script header comments like '# Description: ...'"""
        match = re.search(rf"^# {field}:\s*(.+)$", code, re.MULTILINE)
        return match.group(1).strip() if match else None

    @staticmethod
    def _walk_tree_names(nodes: list, out: list[str]) -> None:
        for node in nodes:
            name = node.get("name", "")
            if name and node.get("enabled", True):
                out.append(name)
            children = node.get("children", [])
            if children:
                CIRunner._walk_tree_names(children, out)
