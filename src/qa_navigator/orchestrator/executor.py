"""Single-item test executor wrapping ADK agent.

Bridges the orchestrator state machine to the ADK computer-use agent.
For each checklist item, constructs a hyper-specific instruction,
runs the agent, and parses the result.
"""

import asyncio
import re
import time
from typing import Optional

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
                        elif hasattr(part, "function_call"):
                            console.print(f"  [dim]Tool: {part.function_call.name}[/]")
                elif event.content:
                    console.print(f"  [dim]Event (no parts): {type(event).__name__}[/]")

            if not result_text:
                console.print(f"  [yellow]Agent returned no text. Events completed normally.[/]")

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
            console.print(f"  [red]Agent exception: {type(e).__name__}: {e}[/]")
            return ExecutionResult(
                success=False,
                status=ItemStatus.ERROR,
                action_taken="Agent error",
                observation=str(e),
                error=str(e),
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        # Capture final screenshot
        final_state = await self.computer.current_state()
        duration = (time.monotonic() - start_time) * 1000

        # Parse agent's response
        status, observation = self._parse_result(result_text)

        return ExecutionResult(
            success=(status == ItemStatus.PASSED),
            status=status,
            action_taken=result_text[:500],
            observation=observation,
            after_screenshot=final_state.screenshot,
            duration_ms=duration,
        )

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
