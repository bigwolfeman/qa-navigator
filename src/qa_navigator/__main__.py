"""CLI entry point for QA Navigator."""

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console

from .checklist.generator import ChecklistGenerator
from .computers.playwright_computer import QAPlaywrightComputer
from .config import settings
from .orchestrator.engine import TestOrchestrator
from .report.html import generate_html_report

console = Console()


def _build_computer(args):
    """Construct the right BaseComputer from CLI args."""
    computer_type = getattr(args, "computer", "browser")

    if computer_type == "windows":
        # Windows desktop automation — only works on Windows
        if sys.platform != "win32":
            console.print("[bold red]--computer windows only works on Windows.[/]")
            sys.exit(2)

        from .computers.windows_computer import WindowsComputer

        app_proc = None
        if args.app_exe:
            console.print(f"[bold yellow]Launching app: {args.app_exe}[/]")
            app_proc = subprocess.Popen(args.app_exe)
            time.sleep(args.app_launch_wait)

        screen_w, screen_h = settings.screen_size
        computer = WindowsComputer(
            screen_size=(screen_w, screen_h),
            target_window_title=args.app_title or None,
            recording_dir=args.recording_dir,
        )
        return computer, app_proc
    else:
        # Default: browser via Playwright
        computer = QAPlaywrightComputer(
            screen_size=settings.screen_size,
            initial_url=args.url,
            headless=args.headless,
            recording_dir=args.recording_dir,
        )
        return computer, None


async def run_test(
    target_url: str,
    instructions: str,
    headless: bool = False,
    checkpoint_dir: Optional[Path] = None,
    recording_dir: Optional[str] = None,
    report_dir: Optional[Path] = None,
    computer_type: str = "browser",
    app_exe: Optional[str] = None,
    app_title: Optional[str] = None,
    app_launch_wait: float = 3.0,
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
    app_proc = None
    if computer_type == "windows":
        if sys.platform != "win32":
            console.print("[bold red]--computer windows only works on Windows.[/]")
            return 2
        from .computers.windows_computer import WindowsComputer

        if app_exe:
            console.print(f"[bold yellow]Launching app: {app_exe}[/]")
            app_proc = subprocess.Popen(app_exe)
            time.sleep(app_launch_wait)

        computer = WindowsComputer(
            screen_size=settings.screen_size,
            target_window_title=app_title or None,
            recording_dir=recording_dir,
        )
        reset_url = None  # No URL reset for desktop apps
    else:
        computer = QAPlaywrightComputer(
            screen_size=settings.screen_size,
            initial_url=target_url,
            headless=headless,
            recording_dir=recording_dir,
        )
        reset_url = target_url

    # Step 3: Run orchestrator (reset_url ensures clean browser state per item)
    orchestrator = TestOrchestrator(
        computer=computer,
        checkpoint_dir=checkpoint_dir,
        reset_url=reset_url,
    )

    try:
        result = await orchestrator.run(checklist)
    finally:
        await computer.close()
        if app_proc and app_proc.poll() is None:
            app_proc.terminate()

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
    parser.add_argument("--url", required=True, help="Target URL or app description (used for checklist generation)")
    parser.add_argument(
        "--instructions",
        default="Test this application thoroughly. Check every button, input, link, and interactive element.",
        help="Testing instructions",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode (browser only)")
    parser.add_argument("--checkpoint-dir", type=Path, help="Directory for checkpointing")
    parser.add_argument("--recording-dir", default="recordings", help="Directory for screen recording (default: recordings/)")
    parser.add_argument("--report-dir", type=Path, default=None, help="Directory to write HTML report (optional)")

    # Computer selection
    parser.add_argument(
        "--computer",
        choices=["browser", "windows"],
        default="browser",
        help="Computer backend: 'browser' (Playwright, default) or 'windows' (Win32 desktop)",
    )
    parser.add_argument(
        "--app-exe",
        default=None,
        help="Windows only: path to executable to launch before testing (e.g. C:\\Windows\\System32\\calc.exe)",
    )
    parser.add_argument(
        "--app-title",
        default=None,
        help="Windows only: window title substring to target (e.g. 'Calculator')",
    )
    parser.add_argument(
        "--app-launch-wait",
        type=float,
        default=3.0,
        help="Windows only: seconds to wait after launching --app-exe (default: 3)",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(run_test(
        target_url=args.url,
        instructions=args.instructions,
        headless=args.headless,
        checkpoint_dir=args.checkpoint_dir,
        recording_dir=args.recording_dir,
        report_dir=args.report_dir,
        computer_type=args.computer,
        app_exe=args.app_exe,
        app_title=args.app_title,
        app_launch_wait=args.app_launch_wait,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
