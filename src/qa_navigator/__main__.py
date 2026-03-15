"""CLI entry point for QA Navigator."""

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console

from .accessibility.auditor import WCAGAuditor
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
    chromium_executable: Optional[str] = None,
    run_wcag: bool = False,
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
            executable_path=chromium_executable,
        )
        reset_url = target_url

    # Step 3: Run orchestrator (reset_url ensures clean browser state per item)
    orchestrator = TestOrchestrator(
        computer=computer,
        checkpoint_dir=checkpoint_dir,
        reset_url=reset_url,
        native_desktop=(computer_type == "windows"),
    )

    wcag_report = None
    try:
        result = await orchestrator.run(checklist)

        # Step 3b: WCAG accessibility audit (while browser is still open)
        if run_wcag and computer_type == "browser" and hasattr(computer, 'page'):
            console.print("\n[bold cyan]Running WCAG 2.1 accessibility audit...[/]")
            try:
                # Navigate back to target for a clean audit
                await computer.page.goto(target_url, wait_until="networkidle", timeout=30000)
                auditor = WCAGAuditor()
                wcag_report = await auditor.audit(computer.page)
                console.print(
                    f"[bold]Accessibility score: {wcag_report.score:.0f}/100 "
                    f"({wcag_report.total_violations} violations, "
                    f"{len(wcag_report.passes)} checks passed)[/]"
                )
                if wcag_report.critical_count:
                    console.print(f"  [red]{wcag_report.critical_count} critical issues[/]")
                if wcag_report.serious_count:
                    console.print(f"  [yellow]{wcag_report.serious_count} serious issues[/]")
            except Exception as e:
                console.print(f"[yellow]WCAG audit failed: {e}[/]")
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
                wcag_report=wcag_report,
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


async def _run_wcag_only(args) -> int:
    """Run standalone WCAG 2.1 accessibility audit (no test suite)."""
    from .accessibility.auditor import WCAGAuditor, WCAGReport
    from .checklist.models import Checklist

    computer = QAPlaywrightComputer(
        screen_size=settings.screen_size,
        initial_url=args.url,
        headless=args.headless,
    )
    await computer.initialize()

    try:
        console.print(f"\n[bold cyan]WCAG 2.1 Accessibility Audit: {args.url}[/]\n")
        auditor = WCAGAuditor()
        wcag_report = await auditor.audit(computer.page)

        # Print results
        console.print(f"[bold]Score: {wcag_report.score:.0f}/100[/]")
        console.print(f"  Violations: {wcag_report.total_violations}")
        console.print(f"  Checks passed: {len(wcag_report.passes)}")

        if wcag_report.critical_count:
            console.print(f"\n  [red bold]{wcag_report.critical_count} CRITICAL[/]")
        if wcag_report.serious_count:
            console.print(f"  [yellow bold]{wcag_report.serious_count} SERIOUS[/]")

        for v in sorted(wcag_report.violations,
                       key=lambda x: {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}[x.severity.value]):
            sev_color = {"critical": "red", "serious": "yellow", "moderate": "yellow", "minor": "dim"}.get(v.severity.value, "")
            console.print(f"  [{sev_color}]{v.severity.value.upper():8s}[/] WCAG {v.wcag_criteria} — {v.description}")

        # Generate report if requested
        if args.report_dir:
            report_dir = Path(args.report_dir)
            report_file = report_dir / "wcag_audit.html"
            empty_checklist = Checklist(id="wcag-audit", target_url=args.url)
            generate_html_report(
                checklist=empty_checklist,
                output_path=report_file,
                wcag_report=wcag_report,
            )
            console.print(f"\n[bold green]Report: {report_file}[/]")

        return 0 if wcag_report.critical_count == 0 else 1
    finally:
        await computer.close()


async def _run_ci(args) -> int:
    """Run in CI mode: replay scripts, explore uncovered UI, report."""
    from .ci.runner import CIRunner

    computer, app_proc = _build_computer(args)
    script_dir = Path(args.script_dir)
    native = getattr(args, "computer", "browser") == "windows"

    try:
        await computer.initialize()
        runner = CIRunner(
            computer=computer,
            script_dir=script_dir,
            app_name=args.url,
            native_desktop=native,
        )
        return await runner.run()
    finally:
        await computer.close()
        if app_proc and app_proc.poll() is None:
            app_proc.terminate()


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
    parser.add_argument("--chromium-executable", default=None, help="Path to Chromium/Chrome binary (overrides Playwright default)")
    parser.add_argument("--checkpoint-dir", type=Path, help="Directory for checkpointing")
    parser.add_argument("--recording-dir", default="recordings", help="Directory for screen recording (default: recordings/)")
    parser.add_argument("--report-dir", type=Path, default=None, help="Directory to write HTML report (optional)")

    # CI mode
    parser.add_argument("--ci", action="store_true", help="Run in CI mode: replay scripts → explore → report")
    parser.add_argument("--script-dir", default="qa_scripts", help="Directory for saved test scripts (default: qa_scripts/)")

    # Accessibility
    parser.add_argument("--wcag", action="store_true", help="Run WCAG 2.1 accessibility audit and include in report")

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

    if args.wcag and not args.ci and args.instructions == parser.get_default("instructions"):
        # Standalone WCAG audit — no test suite, just accessibility check
        exit_code = asyncio.run(_run_wcag_only(args))
    elif args.ci:
        exit_code = asyncio.run(_run_ci(args))
    else:
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
            chromium_executable=args.chromium_executable,
            run_wcag=args.wcag,
        ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
