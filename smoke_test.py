"""Smoke test: validates the full pipeline works end-to-end.

Requires:
  - GOOGLE_API_KEY environment variable set
  - Playwright chromium installed

Usage:
  source .venv/bin/activate
  export GOOGLE_API_KEY="your-key"
  python smoke_test.py
"""

import asyncio
import os
import sys

from rich.console import Console

console = Console()


async def smoke_test():
    """Run a minimal end-to-end test against example.com."""

    if not os.environ.get("GOOGLE_API_KEY"):
        console.print("[bold red]Set GOOGLE_API_KEY first![/]")
        sys.exit(1)

    from qa_navigator.checklist.generator import ChecklistGenerator
    from qa_navigator.computers.playwright_computer import QAPlaywrightComputer
    from qa_navigator.orchestrator.engine import TestOrchestrator

    console.print("\n[bold cyan]== QA Navigator Smoke Test ==[/]\n")

    # Step 1: Generate a small checklist
    console.print("[bold]Step 1: Generating checklist for example.com...[/]")
    generator = ChecklistGenerator()
    checklist = await generator.generate_from_instructions(
        instructions="Test the main page. Check all links and text content.",
        target_url="https://example.com",
    )
    console.print(f"  Generated {checklist.total} items")

    if checklist.total == 0:
        console.print("[bold red]No items generated - check API key and model access[/]")
        sys.exit(1)

    # Limit to first 3 items for smoke test
    checklist.items = checklist.items[:3]
    console.print(f"  Testing first {checklist.total} items (smoke test)")

    # Step 2: Set up computer
    console.print("\n[bold]Step 2: Launching browser...[/]")
    computer = QAPlaywrightComputer(
        screen_size=(1280, 936),
        initial_url="https://example.com",
        headless=True,
    )

    # Step 3: Run orchestrator
    console.print("\n[bold]Step 3: Running orchestrator...[/]")
    orchestrator = TestOrchestrator(computer=computer)

    try:
        result = await orchestrator.run(checklist)
    finally:
        await computer.close()

    # Step 4: Report
    console.print(f"\n[bold]Results:[/]")
    console.print(f"  Pass rate: {result.pass_rate:.0%}")
    console.print(f"  Passed: {result.passed}/{result.total}")

    for item in result.items:
        status_icon = {"passed": "✅", "failed": "❌", "error": "⚠️"}.get(item.status.value, "❓")
        console.print(f"  {status_icon} [{item.id}] {item.description[:60]}")

    console.print("\n[bold green]Smoke test complete![/]")


if __name__ == "__main__":
    asyncio.run(smoke_test())
