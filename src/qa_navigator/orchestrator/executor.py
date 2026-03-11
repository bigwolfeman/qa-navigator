"""Single-item test executor wrapping ADK agent.

Bridges the orchestrator state machine to the ADK computer-use agent.
For each checklist item, constructs a hyper-specific instruction,
runs the agent, and parses the result.

Two execution paths:
  - Browser (default): ADK InMemoryRunner with ComputerUseToolset
  - Native desktop: Raw genai.Client() function-calling loop with
    screenshot injection after every tool call. This bypasses ADK
    because ComputerUseToolset doesn't support Win32/UIA tools.
"""

import asyncio
import base64
import json
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

_RETRY_DELAYS = [300, 600, 900, 1800]  # seconds to wait on 429 before each retry attempt
_503_RETRY_DELAYS = [30, 60, 120]      # seconds to wait on 503 server overload

MAX_NATIVE_TURNS = 20  # hard cap on function-calling loop iterations

from google import genai
from google.adk.runners import InMemoryRunner
from google.adk.tools.computer_use.base_computer import BaseComputer
from google.genai import types
from pydantic import BaseModel
from rich.console import Console

from ..agents.test_agent import create_test_agent, build_item_instruction
from ..checklist.models import ChecklistItem, ItemStatus
from ..config import settings

console = Console()

# Function declarations for native desktop tools (used by genai.Client)
_NATIVE_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="screenshot",
        description="Take a screenshot of the current screen state.",
        parameters={"type": "OBJECT", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="get_ui_tree",
        description=(
            "Get the UI element tree (accessibility tree) for the current window. "
            "Returns element names, types, enabled state, and center coordinates. "
            "Use this FIRST to discover what elements exist before clicking."
        ),
        parameters={"type": "OBJECT", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="find_and_click",
        description=(
            "Find a UI element by its exact name from the accessibility tree and click it. "
            "Use get_ui_tree() first to discover available element names."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "element_name": {
                    "type": "STRING",
                    "description": "Exact name of the element from the UI tree",
                },
            },
            "required": ["element_name"],
        },
    ),
    types.FunctionDeclaration(
        name="find_and_type",
        description="Find a UI element by name and type text into it.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "element_name": {
                    "type": "STRING",
                    "description": "Exact name of the input element from the UI tree",
                },
                "text": {
                    "type": "STRING",
                    "description": "Text to type into the element",
                },
                "press_enter": {
                    "type": "BOOLEAN",
                    "description": "Whether to press Enter after typing (default false)",
                },
            },
            "required": ["element_name", "text"],
        },
    ),
    types.FunctionDeclaration(
        name="click_at",
        description="Click at specific pixel coordinates (x, y) on the screen.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "x": {"type": "INTEGER", "description": "X pixel coordinate"},
                "y": {"type": "INTEGER", "description": "Y pixel coordinate"},
            },
            "required": ["x", "y"],
        },
    ),
    types.FunctionDeclaration(
        name="key_combination",
        description="Press a key combination. E.g. ['ctrl', 's'] or ['escape'] or ['Return'].",
        parameters={
            "type": "OBJECT",
            "properties": {
                "keys": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "List of key names to press simultaneously",
                },
            },
            "required": ["keys"],
        },
    ),
    types.FunctionDeclaration(
        name="type_text",
        description="Type raw text at the current cursor position.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING", "description": "Text to type"},
            },
            "required": ["text"],
        },
    ),
    types.FunctionDeclaration(
        name="double_click_at",
        description="Double-click at specific pixel coordinates (x, y). Use for editing todo items.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "x": {"type": "INTEGER", "description": "X pixel coordinate"},
                "y": {"type": "INTEGER", "description": "Y pixel coordinate"},
            },
            "required": ["x", "y"],
        },
    ),
    types.FunctionDeclaration(
        name="get_page_info",
        description=(
            "Get page metadata: title, URL, focused element, and all visible text. "
            "Use to verify page state, read counters/labels, and check focus."
        ),
        parameters={"type": "OBJECT", "properties": {}},
    ),
]


class ExecutionResult(BaseModel):
    """Result from executing a single test item."""
    success: bool
    status: ItemStatus
    action_taken: str
    observation: str
    after_screenshot: Optional[bytes] = None
    error: Optional[str] = None
    duration_ms: float = 0


class TestExecutor:
    """Executes single checklist items through the ADK computer-use agent.

    Two modes:
      - Browser (native_desktop=False): ADK InMemoryRunner + ComputerUseToolset
      - Native desktop (native_desktop=True): Raw genai function-calling loop
    """

    def __init__(
        self,
        computer: BaseComputer,
        native_desktop: bool = False,
        use_element_tools: bool = True,
    ):
        self.computer = computer
        self.native_desktop = native_desktop
        # When use_element_tools=True and the computer supports find_and_click,
        # use the raw genai function-calling loop instead of ADK's coordinate-based
        # ComputerUseToolset. This is MUCH more reliable for clicking buttons,
        # checkboxes, and typing into inputs because it uses accessibility selectors.
        self.use_element_tools = use_element_tools and hasattr(computer, "find_and_click")

    async def execute_item(
        self,
        item: ChecklistItem,
        script_hint: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute a single test item and return the result.

        Creates a fresh agent per item to avoid context pollution.

        Args:
            item: The checklist item to execute.
            script_hint: Optional saved script source to include as context
                         in the initial prompt (CI replay mode).
        """
        if self.native_desktop or self.use_element_tools:
            return await self._execute_native_item(item, script_hint=script_hint)
        return await self._execute_browser_item(item)

    # ── Browser path (ADK InMemoryRunner) ─────────────────────────────────

    async def _execute_browser_item(self, item: ChecklistItem) -> ExecutionResult:
        """Execute via ADK agent + ComputerUseToolset (browser mode)."""
        start_time = time.monotonic()

        before_screenshot: Optional[bytes] = None
        try:
            before_state = await self.computer.current_state()
            before_screenshot = before_state.screenshot
        except Exception:
            pass

        instruction = build_item_instruction(
            item_id=item.id,
            category=item.category.value,
            description=item.description,
            preconditions=item.preconditions,
            action=item.action,
            expected_outcome=item.expected_outcome,
        )

        agent = create_test_agent(
            computer=self.computer,
            item_instruction=instruction,
            agent_name=f"qa_executor_{item.id.replace('-', '_')}",
        )

        runner = InMemoryRunner(agent=agent, app_name="qa_navigator")
        session = await runner.session_service.create_session(
            app_name="qa_navigator",
            user_id="orchestrator",
        )

        result_text = ""
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                async for event in runner.run_async(
                    user_id="orchestrator",
                    session_id=session.id,
                    new_message=types.Content(
                        role="user",
                        parts=[types.Part(text=f"Execute test item {item.id}: {item.action}")]
                    ),
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, "text") and part.text:
                                result_text += part.text
                            elif hasattr(part, "function_call") and part.function_call is not None:
                                console.print(f"  [dim]Tool: {part.function_call.name}[/]")

                if not result_text.strip():
                    console.print(f"  [yellow]Agent returned no text. Using vision.[/]")
                else:
                    console.print(f"  [dim]Agent text: {repr(result_text[:100])}[/]")
                break

            except asyncio.TimeoutError:
                return ExecutionResult(
                    success=False, status=ItemStatus.ERROR,
                    action_taken="Timed out", observation="Agent exceeded time limit",
                    error="Timeout", duration_ms=(time.monotonic() - start_time) * 1000,
                )
            except Exception as e:
                tb_str = traceback.format_exc()
                err_str = str(e)

                if self._should_retry_quota(err_str, attempt, _RETRY_DELAYS):
                    wait_secs = self._get_retry_wait(err_str, attempt, _RETRY_DELAYS)
                    console.print(f"  [yellow]429 (attempt {attempt+1}) — waiting {wait_secs:.0f}s[/]")
                    await asyncio.sleep(wait_secs)
                    session = await runner.session_service.create_session(
                        app_name="qa_navigator", user_id="orchestrator",
                    )
                    result_text = ""
                    continue

                if self._should_retry_503(err_str, attempt):
                    wait_secs = _503_RETRY_DELAYS[attempt]
                    console.print(f"  [yellow]503 (attempt {attempt+1}) — waiting {wait_secs}s[/]")
                    await asyncio.sleep(wait_secs)
                    session = await runner.session_service.create_session(
                        app_name="qa_navigator", user_id="orchestrator",
                    )
                    result_text = ""
                    continue

                console.print(f"  [red]Agent exception: {type(e).__name__}: {e}[/]")
                self._log_error(item.id, tb_str)
                print(tb_str, file=sys.stderr, flush=True)
                return ExecutionResult(
                    success=False, status=ItemStatus.ERROR,
                    action_taken="Agent error", observation=err_str,
                    error=err_str, duration_ms=(time.monotonic() - start_time) * 1000,
                )

        # Capture final screenshot and evaluate
        final_state = await self.computer.current_state()
        duration = (time.monotonic() - start_time) * 1000

        if result_text.strip():
            status, observation = self._parse_result(result_text)
        else:
            status = ItemStatus.ERROR
            observation = ""

        if status == ItemStatus.ERROR and final_state.screenshot:
            console.print("  [cyan]No PASS/FAIL in text — analyzing with Flash...[/]")
            status, observation = await self._analyze_with_vision(
                screenshot=final_state.screenshot,
                action=item.action,
                expected_outcome=item.expected_outcome,
                before_screenshot=before_screenshot,
            )

        return ExecutionResult(
            success=(status == ItemStatus.PASSED),
            status=status,
            action_taken=result_text[:500] if result_text else "Agent performed actions",
            observation=observation,
            after_screenshot=final_state.screenshot,
            duration_ms=duration,
        )

    # ── Native desktop path (raw genai function-calling) ──────────────────

    async def _execute_native_item(
        self,
        item: ChecklistItem,
        script_hint: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute via raw genai.Client() multi-turn function-calling loop.

        This bypasses ADK entirely. The model gets tool declarations for
        the computer's native methods (get_ui_tree, find_and_click, etc.)
        and a screenshot is injected after every tool call so the model
        can see the result of its action and adapt.
        """
        start_time = time.monotonic()

        before_screenshot: Optional[bytes] = None
        try:
            before_state = await self.computer.current_state()
            before_screenshot = before_state.screenshot
        except Exception:
            pass

        # Build the initial user message
        app_type = "Windows desktop application" if self.native_desktop else "web application"
        user_text = (
            f"You are a QA tester on a {app_type}.\n\n"
            f"TEST: {item.id}\n"
            f"DESCRIPTION: {item.description}\n"
            f"ACTION: {item.action}\n"
            f"EXPECTED: {item.expected_outcome}\n\n"
        )
        if script_hint:
            user_text += (
                f"REFERENCE SCRIPT (adapt to current UI, don't follow blindly):\n"
                f"```\n{script_hint}\n```\n\n"
            )
        user_text += (
            "INSTRUCTIONS:\n"
            "1. Call get_ui_tree() to discover interactive elements.\n"
            "2. Perform the test action using the tools.\n"
            "3. Call get_page_info() to read text, counters, focus state, etc.\n"
            "4. Take a screenshot to visually verify the result.\n"
            "5. Report RESULT: PASS or FAIL with an OBSERVATION.\n\n"
            "Start by calling get_ui_tree()."
        )

        # Initial screenshot as context
        initial_parts: list[types.Part] = []
        if before_screenshot:
            scr_b64 = base64.b64encode(before_screenshot).decode()
            initial_parts.append(
                types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=scr_b64))
            )
        initial_parts.append(types.Part(text=user_text))

        contents: list[types.Content] = [
            types.Content(role="user", parts=initial_parts),
        ]

        client = genai.Client()
        tool_config = types.Tool(function_declarations=_NATIVE_TOOL_DECLARATIONS)
        gen_config = types.GenerateContentConfig(
            tools=[tool_config],
            temperature=0.1,
        )

        result_text = ""
        actions_log: list[str] = []

        for turn in range(MAX_NATIVE_TURNS):
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=settings.computer_use_model,
                        contents=contents,
                        config=gen_config,
                    ),
                    timeout=120.0,
                )
            except Exception as e:
                err_str = str(e)
                if self._should_retry_quota(err_str, 0, _RETRY_DELAYS):
                    wait = self._get_retry_wait(err_str, 0, _RETRY_DELAYS)
                    console.print(f"  [yellow]429 in native loop — waiting {wait:.0f}s[/]")
                    await asyncio.sleep(wait)
                    continue
                err_msg = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__} (no message)"
                console.print(f"  [red]Native loop error: {err_msg}[/]")
                self._log_error(item.id, traceback.format_exc())
                return ExecutionResult(
                    success=False, status=ItemStatus.ERROR,
                    action_taken="\n".join(actions_log), observation=err_msg,
                    error=err_msg, duration_ms=(time.monotonic() - start_time) * 1000,
                )

            # Filter out thought parts (thinking models embed signatures)
            model_parts = [
                p for p in (response.candidates[0].content.parts or [])
                if not getattr(p, "thought", False)
            ]

            # Check for text response (model is done)
            has_function_call = any(
                hasattr(p, "function_call") and p.function_call
                for p in model_parts
            )

            if not has_function_call:
                # Model finished — extract text
                for p in model_parts:
                    if hasattr(p, "text") and p.text:
                        result_text += p.text
                break

            # Add model response to conversation (without thought parts)
            contents.append(types.Content(role="model", parts=model_parts))

            # Execute each function call
            fn_response_parts: list[types.Part] = []
            screenshot_after: Optional[bytes] = None

            for part in model_parts:
                if not (hasattr(part, "function_call") and part.function_call):
                    continue

                fc = part.function_call
                fn_name = fc.name
                fn_args = dict(fc.args) if fc.args else {}
                # Sanitize args for Windows console (cp1252 can't handle Unicode)
                safe_args = str(fn_args).encode("ascii", errors="replace").decode("ascii")
                console.print(f"  [dim]Tool: {fn_name}({safe_args})[/]")
                actions_log.append(f"{fn_name}({fn_args})")

                # Dispatch to computer methods
                try:
                    fn_result = await self._dispatch_native_tool(fn_name, fn_args)
                    fn_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fn_name,
                            response=fn_result,
                        )
                    ))
                except Exception as e:
                    fn_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fn_name,
                            response={"error": str(e)},
                        )
                    ))

            # Inject a screenshot after tool calls so the model sees the result
            try:
                post_state = await self.computer.current_state()
                screenshot_after = post_state.screenshot
                if screenshot_after:
                    scr_b64 = base64.b64encode(screenshot_after).decode()
                    fn_response_parts.append(
                        types.Part(inline_data=types.Blob(
                            mime_type="image/jpeg", data=scr_b64
                        ))
                    )
            except Exception:
                pass

            contents.append(types.Content(role="user", parts=fn_response_parts))

            # Trim conversation history to prevent context growth from screenshots.
            # Keep: first user message (instruction) + last 6 turns (3 model + 3 user).
            # This prevents the 120s timeout from accumulating screenshot data.
            if len(contents) > 8:
                contents = [contents[0]] + contents[-6:]

        # Evaluate result
        final_state = await self.computer.current_state()
        duration = (time.monotonic() - start_time) * 1000

        if result_text.strip():
            status, observation = self._parse_result(result_text)
        else:
            status = ItemStatus.ERROR
            observation = ""

        if status == ItemStatus.ERROR and final_state.screenshot:
            console.print("  [cyan]No PASS/FAIL in text — analyzing with Flash...[/]")
            status, observation = await self._analyze_with_vision(
                screenshot=final_state.screenshot,
                action=item.action,
                expected_outcome=item.expected_outcome,
                before_screenshot=before_screenshot,
            )

        return ExecutionResult(
            success=(status == ItemStatus.PASSED),
            status=status,
            action_taken="\n".join(actions_log) if actions_log else result_text[:500],
            observation=observation,
            after_screenshot=final_state.screenshot,
            duration_ms=duration,
        )

    async def _dispatch_native_tool(self, name: str, args: dict) -> dict:
        """Route a function call to the appropriate computer method."""
        if name == "screenshot":
            state = await self.computer.current_state()
            return {"status": "ok", "description": "Screenshot captured"}

        elif name == "get_ui_tree":
            if hasattr(self.computer, "get_ui_tree"):
                tree = await self.computer.get_ui_tree()
                if not isinstance(tree, dict):
                    return {"elements": []}
                # Return ONLY structured elements (compact). Skip aria_snapshot
                # to keep context small — each screenshot already eats tokens.
                if "elements" in tree:
                    return {"elements": tree["elements"][:40]}
                elif "aria_snapshot" in tree:
                    return {"aria_snapshot": tree["aria_snapshot"][:1500]}
                return tree
            return {"error": "get_ui_tree not available on this computer"}

        elif name == "find_and_click":
            el_name = args.get("element_name", "")
            if hasattr(self.computer, "find_and_click"):
                result = await self.computer.find_and_click(el_name)
                return {"status": "clicked", "element": el_name}
            return {"error": f"find_and_click not available"}

        elif name == "find_and_type":
            el_name = args.get("element_name", "")
            text = args.get("text", "")
            press_enter = args.get("press_enter", False)
            if hasattr(self.computer, "find_and_type"):
                await self.computer.find_and_type(el_name, text, press_enter=press_enter)
                return {"status": "typed", "element": el_name, "text": text}
            return {"error": "find_and_type not available"}

        elif name == "click_at":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            await self.computer.click_at(x, y)
            return {"status": "clicked", "x": x, "y": y}

        elif name == "double_click_at":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            if hasattr(self.computer, "double_click_at"):
                await self.computer.double_click_at(x, y)
            elif hasattr(self.computer, "page"):
                await self.computer.page.mouse.dblclick(x, y)
            else:
                await self.computer.click_at(x, y)
                await asyncio.sleep(0.05)
                await self.computer.click_at(x, y)
            return {"status": "double_clicked", "x": x, "y": y}

        elif name == "key_combination":
            keys = args.get("keys", [])
            await self.computer.key_combination(keys)
            return {"status": "pressed", "keys": keys}

        elif name == "type_text":
            text = args.get("text", "")
            await self.computer.type_text(text)
            return {"status": "typed", "text": text}

        elif name == "get_page_info":
            if hasattr(self.computer, "page"):
                page = self.computer.page
                info = {}
                try:
                    info["title"] = await page.title()
                    info["url"] = page.url
                    info["focused_element"] = await page.evaluate(
                        "() => { const el = document.activeElement; "
                        "return el ? {tag: el.tagName, placeholder: el.placeholder || '', "
                        "className: el.className || '', id: el.id || ''} : null; }"
                    )
                    # Get all visible text (counters, labels, headings)
                    info["visible_text"] = await page.evaluate(
                        "() => { const walk = document.createTreeWalker("
                        "document.body, NodeFilter.SHOW_TEXT, null); "
                        "const texts = []; let n; "
                        "while ((n = walk.nextNode()) && texts.length < 50) { "
                        "const t = n.textContent.trim(); "
                        "if (t && t.length < 200) texts.push(t); } "
                        "return texts; }"
                    )
                except Exception as e:
                    info["error"] = str(e)
                return info
            return {"error": "get_page_info requires browser computer"}

        else:
            return {"error": f"Unknown tool: {name}"}

    @staticmethod
    async def _analyze_with_vision(
        screenshot: bytes,
        action: str,
        expected_outcome: str,
        before_screenshot: Optional[bytes] = None,
    ) -> tuple[ItemStatus, str]:
        """Use Flash to analyze a screenshot and determine PASS/FAIL.

        Called when the computer-use agent completes without producing text.
        Flash acts as the evaluator: it sees the final screen state and
        judges whether the expected outcome was achieved.

        If before_screenshot is provided, Flash compares the before and after
        states to detect changes — much more accurate than after-only analysis.
        """
        client = genai.Client()
        after_b64 = base64.b64encode(screenshot).decode("utf-8")

        if before_screenshot:
            before_b64 = base64.b64encode(before_screenshot).decode("utf-8")
            prompt = f"""You are a QA evaluator comparing the state of a web app BEFORE and AFTER an action.

The FIRST image is BEFORE the action. The SECOND image is AFTER the action.

ACTION THAT WAS PERFORMED:
{action}

EXPECTED OUTCOME:
{expected_outcome}

EVALUATION GUIDELINES:
- Focus on FUNCTIONAL changes: did new content appear? Did state change correctly?
- IGNORE CSS styling differences (colors, backgrounds, fonts may vary between environments)
- IGNORE minor layout variations
- For "add a todo" tests: PASS if any new list item appeared after the action
- For "empty/whitespace input" tests: PASS if no new item appeared (correct rejection)
- For "page load" tests: PASS if the page content is visible and not blank/error
- For "input field" tests: PASS if the field exists and is interactable
- Compare BEFORE vs AFTER — look for meaningful functional change

Did the action achieve the expected functional outcome?

Respond in this EXACT format:
RESULT: PASS
OBSERVATION: [Brief description of the functional change you observed]

OR:

RESULT: FAIL
OBSERVATION: [What specifically is missing or wrong functionally]

Be concise. One line for RESULT, one line for OBSERVATION."""

            contents = [
                types.Part(inline_data=types.Blob(mime_type="image/png", data=before_b64)),
                types.Part(inline_data=types.Blob(mime_type="image/png", data=after_b64)),
                types.Part(text=prompt),
            ]
        else:
            prompt = f"""You are a QA evaluator. Look at this screenshot and determine if the test passed or failed.

ACTION THAT WAS PERFORMED:
{action}

EXPECTED OUTCOME:
{expected_outcome}

EVALUATION GUIDELINES:
- Focus on FUNCTIONAL correctness, not visual styling
- IGNORE CSS color/background differences — they vary between environments
- For "add a todo" tests: PASS if a todo item is visible in the list
- For "page load" tests: PASS if the page content is visible

Did the action succeed and produce the expected outcome?

Respond in this EXACT format:
RESULT: PASS
OBSERVATION: [What you see that confirms the expected outcome]

OR:

RESULT: FAIL
OBSERVATION: [What you see that contradicts the expected outcome, or what is missing]

Be concise. One line for RESULT, one line for OBSERVATION."""

            contents = [
                types.Part(inline_data=types.Blob(mime_type="image/png", data=after_b64)),
                types.Part(text=prompt),
            ]

        for attempt in range(3):
          try:
            response = await client.aio.models.generate_content(
                model=settings.analysis_model,
                contents=contents,
            )
            analysis_text = response.text or ""
            console.print(f"  [dim]Flash response: {repr(analysis_text[:120])}[/]")
            return TestExecutor._parse_result(analysis_text)
          except Exception as e:
            is_quota = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            wait = 15 * (attempt + 1) if is_quota else 0
            console.print(f"  [yellow]Vision attempt {attempt+1} failed: {type(e).__name__}. Wait {wait}s[/]")
            if attempt < 2:
                if wait:
                    await asyncio.sleep(wait)
                continue
            self._log_error("vision", traceback.format_exc())
            return ItemStatus.ERROR, f"Vision analysis failed: {e}"

    @staticmethod
    def _parse_result(text: str) -> tuple[ItemStatus, str]:
        """Parse the agent's text response into a status and observation."""
        if not text:
            return ItemStatus.ERROR, "Agent returned no response"

        text_upper = text.upper()

        # Look for explicit RESULT: PASS/FAIL markers
        result_match = re.search(r"RESULT:\s*(PASS|FAIL)", text_upper)
        if result_match:
            status = ItemStatus.PASSED if result_match.group(1) == "PASS" else ItemStatus.FAILED
        elif "PASS" in text_upper and "FAIL" not in text_upper:
            status = ItemStatus.PASSED
        elif "FAIL" in text_upper:
            status = ItemStatus.FAILED
        else:
            status = ItemStatus.ERROR

        # Extract observation
        obs_match = re.search(r"OBSERVATION:\s*(.+?)(?:DETAIL:|$)", text, re.DOTALL | re.IGNORECASE)
        observation = obs_match.group(1).strip() if obs_match else text[:300]

        return status, observation

    # ── Shared helpers ────────────────────────────────────────────────────

    @staticmethod
    def _should_retry_quota(err_str: str, attempt: int, delays: list) -> bool:
        return (
            ("ResourceExhausted" in err_str or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str)
            and attempt < len(delays)
        )

    @staticmethod
    def _should_retry_503(err_str: str, attempt: int) -> bool:
        return ("503" in err_str or "UNAVAILABLE" in err_str) and attempt < len(_503_RETRY_DELAYS)

    @staticmethod
    def _get_retry_wait(err_str: str, attempt: int, delays: list) -> float:
        wait_match = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", err_str, re.IGNORECASE)
        return float(wait_match.group(1)) + 5 if wait_match else delays[attempt]

    @staticmethod
    def _log_error(item_id: str, tb_str: str) -> None:
        """Write traceback to a log file (cross-platform)."""
        try:
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            log_path = log_dir / "qa_errors.log"
            with open(log_path, "a") as f:
                f.write(f"\n=== {item_id} ===\n{tb_str}\n")
        except Exception:
            pass
