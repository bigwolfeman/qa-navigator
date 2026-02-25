"""CLI entry point for QA Navigator."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from .checklist.generator import ChecklistGenerator
from .computers.playwright_computer import QAPlaywrightComputer
from .config import settings
from .orchestrator.engine import TestOrchestrator

console = Console()


async def run_test(
    target_url: str,
    instructions: str,
    headless: bool = False,
    checkpoint_dir: Optional[Path] = None,
    recording_dir: Optional[str] = None,
) -> int:
    """Run a full QA test session.

    Returns:
        Exit code: 0 = all pass, 1 = failures, 2 = errors
    """
    # Step 1: Generate checklist
    generator = ChecklistGenerator()
    checklist = await generator.generate_from_instructions(
        instructions=instructions,
        target_url=target_url,
    )

    if checklist.total == 0:
        console.print("[bold red]No test items generated. Check your instructions.[/]")
        return 2

    # Step 2: Set up computer
    computer = QAPlaywrightComputer(
        screen_size=settings.screen_size,
        initial_url=target_url,
        headless=headless,
        recording_dir=recording_dir,
    )

    # Step 3: Run orchestrator
    orchestrator = TestOrchestrator(
        computer=computer,
        checkpoint_dir=checkpoint_dir,
    )

    try:
        result = await orchestrator.run(checklist)
    finally:
        await computer.close()

    # Log recording location if active
    if hasattr(computer, "video_path") and computer.video_path:
        console.print(f"[bold cyan]Screen recording: {computer.video_path}[/]")

    # Step 4: Determine exit code
    if result.failed > 0:
        return 1
    elif result.errored > 0:
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="qa-navigator",
        description="AI-powered exhaustive Visual QA Testing Agent",
    )
    parser.add_argument("--url", required=True, help="Target URL to test")
    parser.add_argument(
        "--instructions",
        default="Test this application thoroughly. Check every button, input, link, and interactive element.",
        help="Testing instructions",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--checkpoint-dir", type=Path, help="Directory for checkpointing")
    parser.add_argument("--recording-dir", default="recordings", help="Directory for screen recording (default: recordings/)")

    args = parser.parse_args()

    exit_code = asyncio.run(run_test(
        target_url=args.url,
        instructions=args.instructions,
        headless=args.headless,
        checkpoint_dir=args.checkpoint_dir,
        recording_dir=args.recording_dir,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
