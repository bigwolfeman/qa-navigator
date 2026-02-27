"""Single-item test executor wrapping ADK agent.

Bridges the orchestrator state machine to the ADK computer-use agent.
For each checklist item, constructs a hyper-specific instruction,
runs the agent, and parses the result.
"""

import asyncio
import base64
import re
import time
from typing import Optional

_RETRY_DELAYS = [300, 600, 900, 1800]  # seconds to wait on 429 before each retry attempt (5m/10m/15m/30m)

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
    """Executes single checklist items through the ADK computer-use agent."""

    def __init__(self, computer: BaseComputer):
        self.computer = computer

    async def execute_item(self, item: ChecklistItem) -> ExecutionResult:
        """Execute a single test item and return the result.

        Creates a fresh ADK agent per item to avoid context pollution.
        The agent gets a hyper-specific instruction and limited turns.
        """
        start_time = time.monotonic()

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
        for attempt in range(len(_RETRY_DELAYS) + 1):  # up to 4 retries on 429
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
                    elif event.content:
                        console.print(f"  [dim]Event (no parts): {type(event).__name__}[/]")

                if not result_text.strip():
                    console.print(f"  [yellow]Agent returned no text (result_text={repr(result_text[:50])}). Using vision.[/]")
                else:
                    console.print(f"  [dim]Agent text: {repr(result_text[:100])}[/]")
                break  # success — exit retry loop

            except asyncio.TimeoutError:
                return ExecutionResult(
                    success=False,
                    status=ItemStatus.ERROR,
                    action_taken="Timed out",
                    observation="Agent exceeded time limit",
                    error="Timeout",
                    duration_ms=(time.monotonic() - start_time) * 1000,
                )
            except Exception as e:
                import traceback as _tb
                import sys as _sys
                _tb_str = _tb.format_exc()
                err_str = str(e)

                # 429 Resource Exhausted — retry with increasing backoff
                is_quota = "ResourceExhausted" in type(e).__name__ or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                if is_quota and attempt < len(_RETRY_DELAYS):
                    wait_match = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", err_str, re.IGNORECASE)
                    wait_secs = float(wait_match.group(1)) + 5 if wait_match else _RETRY_DELAYS[attempt]
                    console.print(f"  [yellow]429 quota hit (attempt {attempt+1}/{len(_RETRY_DELAYS)+1}) — waiting {wait_secs:.0f}s then retrying {item.id}...[/]")
                    await asyncio.sleep(wait_secs)
                    # Re-create session for the retry
                    session = await runner.session_service.create_session(
                        app_name="qa_navigator",
                        user_id="orchestrator",
                    )
                    result_text = ""
                    continue

                # Unrecoverable error
                console.print(f"  [red]Agent exception: {type(e).__name__}: {e}[/]")
                try:
                    with open("C:\\qa_tb.log", "a") as _f:
                        _f.write(f"\n=== {item.id} ===\n{_tb_str}\n")
                except Exception:
                    pass
                print(_tb_str, file=_sys.stderr, flush=True)
                return ExecutionResult(
                    success=False,
                    status=ItemStatus.ERROR,
                    action_taken="Agent error",
                    observation=err_str,
                    error=err_str,
                    duration_ms=(time.monotonic() - start_time) * 1000,
                )

        # Capture final screenshot
        final_state = await self.computer.current_state()
        duration = (time.monotonic() - start_time) * 1000

        # Parse agent's response
        # The computer-use model often doesn't output RESULT: PASS/FAIL format,
        # so we try text parsing first, then fall back to vision analysis.
        if result_text.strip():
            status, observation = self._parse_result(result_text)
        else:
            status = ItemStatus.ERROR
            observation = ""

        # If text parsing couldn't determine pass/fail, use vision analysis
        if status == ItemStatus.ERROR and final_state.screenshot:
            console.print("  [cyan]No PASS/FAIL in text — analyzing screenshot with Flash...[/]")
            status, observation = await self._analyze_with_vision(
                screenshot=final_state.screenshot,
                action=item.action,
                expected_outcome=item.expected_outcome,
            )

        return ExecutionResult(
            success=(status == ItemStatus.PASSED),
            status=status,
            action_taken=result_text[:500] if result_text else "Computer-use agent performed actions",
            observation=observation,
            after_screenshot=final_state.screenshot,
            duration_ms=duration,
        )

    @staticmethod
    async def _analyze_with_vision(
        screenshot: bytes,
        action: str,
        expected_outcome: str,
    ) -> tuple[ItemStatus, str]:
        """Use Flash to analyze a screenshot and determine PASS/FAIL.

        Called when the computer-use agent completes without producing text.
        Flash acts as the evaluator: it sees the final screen state and
        judges whether the expected outcome was achieved.
        """
        client = genai.Client()
        img_b64 = base64.b64encode(screenshot).decode("utf-8")

        prompt = f"""You are a QA evaluator. Look at this screenshot and determine if the test passed or failed.

ACTION THAT WAS PERFORMED:
{action}

EXPECTED OUTCOME:
{expected_outcome}

Look at the screenshot carefully. Did the action succeed and produce the expected outcome?

Respond in this EXACT format:
RESULT: PASS
OBSERVATION: [What you see that confirms the expected outcome]

OR:

RESULT: FAIL
OBSERVATION: [What you see that contradicts the expected outcome, or what is missing]

Be concise. One line for RESULT, one line for OBSERVATION."""

        for attempt in range(3):
          try:
            response = await client.aio.models.generate_content(
                model=settings.analysis_model,
                contents=[
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="image/png",
                            data=img_b64,
                        )
                    ),
                    types.Part(text=prompt),
                ],
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
            import traceback as _tb
            try:
                with open("C:\\qa_vision_tb.log", "a") as _f:
                    _f.write(f"\n=== Vision ===\n{_tb.format_exc()}\n")
            except Exception:
                pass
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
