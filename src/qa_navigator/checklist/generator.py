"""Gemini-powered exhaustive checklist generation.

Takes testing instructions (and optionally git diffs, live UI analysis)
and generates a massive, itemized checklist of every testable UI element.
The key is EXHAUSTIVENESS - every button, input, link, hover state, error case.
"""

import asyncio
import json
import uuid
from typing import Optional

from google import genai
from pydantic import BaseModel
from rich.console import Console

from ..config import settings
from .models import (
    Checklist,
    ChecklistItem,
    TestCategory,
    TestPriority,
    ItemStatus,
)

console = Console()

CHECKLIST_SYSTEM_PROMPT = """You are an extremely meticulous QA engineer generating an EXHAUSTIVE test checklist.

Your job is to enumerate EVERY SINGLE testable UI element and interaction. You must be thorough to the point of being obsessive.

RULES - YOU MUST FOLLOW ALL OF THESE:
1. Every visible button gets its own test item (click, verify result)
2. Every input field gets MULTIPLE test items:
   - Valid input → expected behavior
   - Empty input → error handling
   - Boundary input (max length, special chars) → graceful handling
3. Every link gets a test item (click, verify navigation)
4. Every dropdown gets test items: open menu, select each visible option, keyboard nav
5. Every checkbox/radio: check, uncheck, verify state, verify group behavior
6. Every hover state on interactive elements
7. Keyboard accessibility: Tab through all interactive elements, Enter to activate, Escape to dismiss
8. Error states: invalid form submission, required field missing
9. Navigation: page loads correctly, back/forward work, breadcrumbs work
10. Scroll behavior if page is scrollable
11. Modals/dialogs: open, close via X, close via Escape, close via backdrop click
12. Media elements: images load, videos play/pause

DO NOT:
- Summarize multiple tests into one item
- Skip "obvious" elements
- Group things together to save space
- Say "etc." or "and similar elements"

Each item must be specific enough that a tester can execute it without interpretation.

OUTPUT FORMAT: Return valid JSON matching this schema:
{
  "items": [
    {
      "id": "CAT-NNN",
      "category": "one of: navigation, form_input, button_click, link, dropdown, checkbox_radio, hover_state, scroll, keyboard, error_state, accessibility, responsive, visual, modal_dialog, media",
      "priority": "one of: critical, high, medium, low",
      "description": "Human-readable description of what to test",
      "preconditions": ["list of conditions that must be true"],
      "action": "Exact action to perform",
      "expected_outcome": "What should happen after the action",
      "page_or_section": "Which page or section this belongs to"
    }
  ]
}

Generate IDs using the category prefix:
- NAV-001, NAV-002... for navigation
- INP-001, INP-002... for form_input
- BTN-001, BTN-002... for button_click
- LNK-001, LNK-002... for link
- DRP-001, DRP-002... for dropdown
- CHK-001, CHK-002... for checkbox_radio
- HOV-001, HOV-002... for hover_state
- SCR-001, SCR-002... for scroll
- KEY-001, KEY-002... for keyboard
- ERR-001, ERR-002... for error_state
- ACC-001, ACC-002... for accessibility
- RSP-001, RSP-002... for responsive
- VIS-001, VIS-002... for visual
- MOD-001, MOD-002... for modal_dialog
- MED-001, MED-002... for media

REMEMBER: Your goal is EXHAUSTIVENESS. Generate as many items as needed. 50+ items for a simple page, 100+ for a complex one. DO NOT hold back."""


class ChecklistGenerator:
    """Generates exhaustive test checklists using Gemini."""

    def __init__(self):
        self.client = genai.Client()

    async def generate_from_instructions(
        self,
        instructions: str,
        target_url: Optional[str] = None,
        target_app: Optional[str] = None,
        ui_description: Optional[str] = None,
    ) -> Checklist:
        """Generate an exhaustive checklist from natural language instructions.

        Args:
            instructions: What to test (e.g., "test the login page thoroughly")
            target_url: URL of the web app to test
            target_app: Name of the desktop app to test
            ui_description: Optional description of UI elements discovered

        Returns:
            A Checklist with all generated items.
        """
        console.print("[bold cyan]Generating exhaustive test checklist...[/]")

        prompt = f"""Generate an exhaustive QA test checklist for the following:

TARGET: {target_url or target_app or 'Not specified'}
INSTRUCTIONS: {instructions}
"""
        if ui_description:
            prompt += f"\nDISCOVERED UI ELEMENTS:\n{ui_description}\n"

        prompt += "\nGenerate the checklist now. Be EXHAUSTIVE. Every element. Every interaction. Every edge case."

        _gen_retry_delays = [30, 60, 120]
        response = None
        for _attempt in range(len(_gen_retry_delays) + 1):
            try:
                response = await self.client.aio.models.generate_content(
                    model=settings.analysis_model,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=CHECKLIST_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                break
            except Exception as e:
                err = str(e)
                if ("503" in err or "UNAVAILABLE" in err) and _attempt < len(_gen_retry_delays):
                    wait = _gen_retry_delays[_attempt]
                    console.print(f"  [yellow]503 server overload (attempt {_attempt + 1}) — waiting {wait}s before retry...[/]")
                    await asyncio.sleep(wait)
                else:
                    raise

        # Parse the response
        checklist = self._parse_response(response.text, instructions, target_url, target_app)

        total_generated = checklist.total
        if total_generated > settings.max_checklist_items:
            checklist.items = checklist.items[:settings.max_checklist_items]
            console.print(f"[bold green]Generated {total_generated} items — using first {settings.max_checklist_items}.[/]")
        else:
            console.print(f"[bold green]Generated {total_generated} test items.[/]")
        return checklist

    async def generate_from_discovery(
        self,
        instructions: str,
        accessibility_tree: dict,
        screenshot_description: str,
        target_url: str,
    ) -> Checklist:
        """Generate checklist from live UI discovery data.

        Uses both the accessibility tree (structured) and screenshot analysis
        (visual) to find every testable element.
        """
        tree_summary = json.dumps(accessibility_tree, indent=2)[:8000]  # Truncate for context

        ui_description = f"""ACCESSIBILITY TREE (structured element data):
{tree_summary}

VISUAL ANALYSIS:
{screenshot_description}"""

        return await self.generate_from_instructions(
            instructions=instructions,
            target_url=target_url,
            ui_description=ui_description,
        )

    async def expand_checklist(self, checklist: Checklist, focus_area: str) -> Checklist:
        """Add more items to an existing checklist focusing on a specific area.

        Used when commit diffs indicate changes to a specific UI area.
        """
        existing_ids = {item.id for item in checklist.items}
        existing_summary = "\n".join(
            f"- {item.id}: {item.description}" for item in checklist.items[:50]
        )

        prompt = f"""An existing test checklist has {checklist.total} items. Here's a summary:
{existing_summary}

Now generate ADDITIONAL test items focusing specifically on:
{focus_area}

Do NOT duplicate existing items. Generate new items that cover untested aspects of this area.
Use the standard ID format but start numbering after the existing items."""

        response = await self.client.aio.models.generate_content(
            model=settings.analysis_model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=CHECKLIST_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

        new_items = self._parse_items(response.text)

        # Deduplicate by ID
        for item in new_items:
            if item.id in existing_ids:
                item.id = f"{item.id}-X{len(existing_ids)}"
            checklist.items.append(item)
            existing_ids.add(item.id)

        return checklist

    def _parse_response(
        self,
        text: str,
        instructions: str,
        target_url: Optional[str],
        target_app: Optional[str],
    ) -> Checklist:
        """Parse Gemini's JSON response into a Checklist."""
        items = self._parse_items(text)

        return Checklist(
            id=f"cl-{uuid.uuid4().hex[:8]}",
            target_url=target_url,
            target_app=target_app,
            instructions=instructions,
            items=items,
        )

    @staticmethod
    def _parse_items(text: str) -> list[ChecklistItem]:
        """Parse JSON text into ChecklistItem list with error tolerance."""
        try:
            # Handle potential markdown code blocks
            if "```" in text:
                text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]

            data = json.loads(text)
            raw_items = data.get("items", data) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            console.print("[yellow]Failed to parse checklist JSON, attempting recovery...[/]")
            import re
            # Try to find JSON array and parse as much as possible
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                try:
                    raw_items = json.loads(match.group())
                except json.JSONDecodeError:
                    # Truncated JSON — try to salvage complete objects
                    raw_items = ChecklistGenerator._salvage_truncated_json(match.group())
            else:
                # Try individual JSON objects
                raw_items = ChecklistGenerator._salvage_truncated_json(text)
            if not raw_items:
                return []

        items = []
        for raw in raw_items:
            try:
                item = ChecklistItem(
                    id=raw.get("id", f"ITEM-{len(items):03d}"),
                    category=TestCategory(raw.get("category", "visual")),
                    priority=TestPriority(raw.get("priority", "medium")),
                    description=raw.get("description", ""),
                    preconditions=raw.get("preconditions", []),
                    action=raw.get("action", ""),
                    expected_outcome=raw.get("expected_outcome", ""),
                    page_or_section=raw.get("page_or_section", ""),
                )
                items.append(item)
            except Exception:
                continue  # Skip malformed items

        return items

    @staticmethod
    def _salvage_truncated_json(text: str) -> list[dict]:
        """Extract as many complete JSON objects as possible from truncated text."""
        import re
        items = []
        # Find all complete {...} objects (greedy match balanced braces)
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        obj = json.loads(text[start:i+1])
                        if isinstance(obj, dict) and ("description" in obj or "action" in obj):
                            items.append(obj)
                    except json.JSONDecodeError:
                        pass
                    start = None
        console.print(f"[yellow]Salvaged {len(items)} items from truncated JSON[/]")
        return items
