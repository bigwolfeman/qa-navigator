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
from .report.html import generate_html_report

console = Console()


async def run_test(
    target_url: str,
    instructions: str,
    headless: bool = False,
    checkpoint_dir: Optional[Path] = None,
    recording_dir: Optional[str] = None,
    report_dir: Optional[Path] = None,
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

    # Step 3: Run orchestrator (reset_url ensures clean browser state per item)
    orchestrator = TestOrchestrator(
        computer=computer,
        checkpoint_dir=checkpoint_dir,
        reset_url=target_url,
    )

    try:
        result = await orchestrator.run(checklist)
    finally:
        await computer.close()

    # Log recording location if active
    video_path = None
    if hasattr(computer, "video_path") and computer.video_path:
        video_path = computer.video_path
        console.print(f"[bold cyan]Screen recording: {video_path}[/]")

    # Step 4: Generate HTML report
    if report_dir:
        report_file = report_dir / f"{result.id}.html"
        try:
            generate_html_report(
                checklist=result,
                recording_path=str(video_path) if video_path else None,
                output_path=report_file,
            )
            console.print(f"[bold green]HTML report: {report_file}[/]")
        except Exception as e:
            console.print(f"[yellow]Report generation failed: {e}[/]")

    # Step 5: Determine exit code
    if result.failed > 0:
        return 1
    elif result.errored > 0:
        return 2
    return 0


def main():
    # Detect serve mode: first arg is "serve"
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        parser = argparse.ArgumentParser(prog="qa-navigator serve")
        parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
        parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
        args = parser.parse_args(sys.argv[2:])
        try:
            import uvicorn
            from .server.app import app
            uvicorn.run(app, host=args.host, port=args.port)
        except ImportError:
            console.print("[red]uvicorn not installed. Run: pip install uvicorn[/]")
            sys.exit(1)
        return

    # Default: run mode (backwards-compatible with existing bat files)
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
    parser.add_argument("--report-dir", type=Path, default=None, help="Directory to write HTML report (optional)")

    args = parser.parse_args()

    exit_code = asyncio.run(run_test(
        target_url=args.url,
        instructions=args.instructions,
        headless=args.headless,
        checkpoint_dir=args.checkpoint_dir,
        recording_dir=args.recording_dir,
        report_dir=args.report_dir,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
