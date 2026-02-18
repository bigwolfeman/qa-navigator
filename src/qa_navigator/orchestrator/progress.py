"""Progress tracking and checkpointing for test runs.

Displays real-time progress and supports checkpoint/resume
for long-running test sessions.
"""

import json
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from ..checklist.models import Checklist, ChecklistItem, ItemStatus, TERMINAL_STATUSES

console = Console()


class ProgressTracker:
    """Tracks test execution progress with rich display and checkpointing."""

    def __init__(self, checkpoint_dir: Optional[Path] = None):
        self.checkpoint_dir = checkpoint_dir
        self._progress: Optional[Progress] = None
        self._task_id = None

    def initialize(self, checklist: Checklist) -> None:
        """Set up progress tracking for a checklist."""
        console.print(f"\n[bold cyan]Test Run: {checklist.id}[/]")
        console.print(f"  Target: {checklist.target_url or checklist.target_app or 'N/A'}")
        console.print(f"  Items: {checklist.total}")
        console.print()

    def log_item_start(self, item: ChecklistItem) -> None:
        """Log the start of a test item execution."""
        console.print(
            f"  [{item.id}] [bold]{item.description[:60]}[/] ... ",
            end="",
        )

    def log_item_result(self, item: ChecklistItem) -> None:
        """Log the result of a test item."""
        status_styles = {
            ItemStatus.PASSED: "[bold green]PASS[/]",
            ItemStatus.FAILED: "[bold red]FAIL[/]",
            ItemStatus.ERROR: "[bold yellow]ERROR[/]",
            ItemStatus.SKIPPED: "[dim]SKIP[/]",
        }
        style = status_styles.get(item.status, str(item.status))
        console.print(style)

    def log_progress(self, checklist: Checklist) -> None:
        """Print current progress summary."""
        s = checklist.summary()
        bar_width = 30
        done_ratio = checklist.completed / max(checklist.total, 1)
        filled = int(bar_width * done_ratio)
        bar = f"[{'#' * filled}{'-' * (bar_width - filled)}]"

        console.print(
            f"\n  {bar} {done_ratio:.0%} ({checklist.completed}/{checklist.total}) "
            f"| [green]Pass: {s['passed']}[/] "
            f"| [red]Fail: {s['failed']}[/] "
            f"| [yellow]Error: {s['errored']}[/]"
        )

    def log_summary(self, checklist: Checklist) -> None:
        """Print final summary table."""
        table = Table(title="Test Run Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold")

        s = checklist.summary()
        table.add_row("Total Items", str(s["total"]))
        table.add_row("Passed", f"[green]{s['passed']}[/]")
        table.add_row("Failed", f"[red]{s['failed']}[/]")
        table.add_row("Errors", f"[yellow]{s['errored']}[/]")
        table.add_row("Pass Rate", s["pass_rate"])

        console.print()
        console.print(table)

    def save_checkpoint(self, checklist: Checklist, path: Optional[Path] = None) -> Path:
        """Save checklist state for resume after crash."""
        save_path = path or (self.checkpoint_dir / f"checkpoint_{checklist.id}.json")
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize without binary screenshot data
        data = checklist.model_dump(mode="json")
        save_path.write_text(json.dumps(data, indent=2, default=str))
        return save_path

    def load_checkpoint(self, path: Path) -> Optional[Checklist]:
        """Load checklist state from checkpoint."""
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Checklist.model_validate(data)
