"""Test Orchestrator Engine - THE KEY DIFFERENTIATOR.

A deterministic state machine that forces exhaustive UI testing.
The agent CANNOT skip items. The orchestrator controls the loop.
Each item is fed to the agent one at a time with a specific instruction.
Every result is validated with evidence (before/after screenshots).

This is NOT an LLM. It is deterministic Python code that uses LLMs as tools.
"""

import asyncio
import base64
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from google.adk.tools.computer_use.base_computer import BaseComputer
from rich.console import Console

from ..checklist.models import (
    Checklist,
    ChecklistItem,
    ItemStatus,
    TestEvidence,
    TERMINAL_STATUSES,
)
from ..config import settings
from .executor import TestExecutor
from .progress import ProgressTracker

console = Console()


class OrchestratorState(str, Enum):
    """States of the orchestrator state machine."""
    INITIALIZING = "initializing"
    READY = "ready"
    EXECUTING_ITEM = "executing_item"
    VALIDATING = "validating"
    RETRYING = "retrying"
    ADVANCING = "advancing"
    COMPLETED = "completed"
    ERROR = "error"


class TestOrchestrator:
    """Deterministic state machine that forces exhaustive UI testing.

    The orchestrator is the controller. The ADK agent is the executor.
    The agent never decides what to test or when to stop.

    Flow:
        1. Load/receive a Checklist
        2. For each pending item (priority order):
           a. Capture before-screenshot
           b. Feed item to TestExecutor (ADK agent)
           c. Validate result
           d. If fail + retries left → retry with refined instruction
           e. Record evidence (before/after screenshots)
           f. Advance to next item
        3. Generate summary when all items addressed
    """

    def __init__(
        self,
        computer: BaseComputer,
        checkpoint_dir: Optional[Path] = None,
        reset_url: Optional[str] = None,
        native_desktop: bool = False,
    ):
        self.computer = computer
        self.executor = TestExecutor(computer, native_desktop=native_desktop)
        self.progress = ProgressTracker(checkpoint_dir=checkpoint_dir)
        self.state = OrchestratorState.INITIALIZING
        self.checklist: Optional[Checklist] = None
        self.reset_url = reset_url

    async def run(self, checklist: Checklist) -> Checklist:
        """Execute all items in the checklist. Returns the completed checklist.

        This is the main loop. It will not stop until every item
        has been addressed (passed, failed, errored, or skipped with reason).
        """
        self.checklist = checklist

        # Initialize the computer if it hasn't been already
        await self.computer.initialize()

        self.state = OrchestratorState.READY
        self.progress.initialize(checklist)

        items_executed = 0

        while True:
            item = checklist.get_next_pending()
            if item is None:
                break

            items_executed += 1
            item.status = ItemStatus.IN_PROGRESS
            self.state = OrchestratorState.EXECUTING_ITEM

            self.progress.log_item_start(item)

            # Reset page to known URL before each item so agents start from clean state.
            # Use reset_to_url() if available (clears localStorage/sessionStorage too)
            # so apps like TodoMVC don't carry state from previous test items.
            if self.reset_url:
                if hasattr(self.computer, "reset_to_url"):
                    await self.computer.reset_to_url(self.reset_url)  # type: ignore[union-attr]
                else:
                    await self.computer.navigate(self.reset_url)

            # Capture before state
            before_state = await self.computer.current_state()
            before_screenshot = before_state.screenshot

            # Execute the test item
            result = await self.executor.execute_item(item)

            # Handle retries
            if result.status == ItemStatus.ERROR and item.retry_count < item.max_retries:
                self.state = OrchestratorState.RETRYING
                item.retry_count += 1
                item.status = ItemStatus.PENDING
                console.print(f"[yellow]RETRY ({item.retry_count}/{item.max_retries})[/]")
                continue

            if result.status == ItemStatus.FAILED and item.retry_count < item.max_retries:
                self.state = OrchestratorState.RETRYING
                item.retry_count += 1
                item.status = ItemStatus.PENDING
                console.print(f"[yellow]RETRY ({item.retry_count}/{item.max_retries})[/]")
                continue

            # Record final result with evidence
            item.status = result.status
            item.error_message = result.error
            item.evidence = TestEvidence(
                before_screenshot=before_screenshot,
                after_screenshot=result.after_screenshot,
                before_screenshot_b64=_bytes_to_b64(before_screenshot),
                after_screenshot_b64=_bytes_to_b64(result.after_screenshot),
                action_description=result.action_taken,
                observed_result=result.observation,
                expected_result=item.expected_outcome,
                duration_ms=result.duration_ms,
            )

            self.state = OrchestratorState.ADVANCING
            self.progress.log_item_result(item)

            # Rate-limit: pause between items to stay under Gemini quota (2M token/min)
            if checklist.get_next_pending() is not None and settings.inter_item_delay_seconds > 0:
                console.print(f"[dim]  Pausing {settings.inter_item_delay_seconds:.0f}s before next item (quota guard)...[/]")
                await asyncio.sleep(settings.inter_item_delay_seconds)

            # Periodic progress update every 5 items
            if items_executed % 5 == 0:
                self.progress.log_progress(checklist)

            # Checkpoint every 10 items
            if items_executed % 10 == 0 and self.progress.checkpoint_dir:
                self.progress.save_checkpoint(checklist)

        # Final summary
        self.state = OrchestratorState.COMPLETED
        self.progress.log_progress(checklist)
        self.progress.log_summary(checklist)

        # Final checkpoint
        if self.progress.checkpoint_dir:
            self.progress.save_checkpoint(checklist)

        return checklist


def _bytes_to_b64(data: Optional[bytes]) -> Optional[str]:
    """Convert bytes to base64 string for JSON serialization."""
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")
