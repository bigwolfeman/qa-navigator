"""Data models for QA test checklists.

These Pydantic models are the data backbone of QA Navigator.
Everything flows through ChecklistItem and Checklist.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TestCategory(str, Enum):
    """Categories of UI test items."""
    NAVIGATION = "navigation"
    FORM_INPUT = "form_input"
    BUTTON_CLICK = "button_click"
    LINK = "link"
    DROPDOWN = "dropdown"
    CHECKBOX_RADIO = "checkbox_radio"
    HOVER_STATE = "hover_state"
    SCROLL = "scroll"
    KEYBOARD = "keyboard"
    ERROR_STATE = "error_state"
    ACCESSIBILITY = "accessibility"
    RESPONSIVE = "responsive"
    VISUAL = "visual"
    MODAL_DIALOG = "modal_dialog"
    MEDIA = "media"


class TestPriority(str, Enum):
    """Priority levels for test items."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ItemStatus(str, Enum):
    """Status of a checklist item."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"  # Only with explicit justification


TERMINAL_STATUSES = {ItemStatus.PASSED, ItemStatus.FAILED, ItemStatus.ERROR, ItemStatus.SKIPPED}


class TestEvidence(BaseModel):
    """Evidence collected during test execution."""
    before_screenshot: Optional[bytes] = Field(default=None, exclude=True)
    after_screenshot: Optional[bytes] = Field(default=None, exclude=True)
    # Base64 versions for serialization
    before_screenshot_b64: Optional[str] = None
    after_screenshot_b64: Optional[str] = None
    action_description: str = ""
    observed_result: str = ""
    expected_result: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: Optional[float] = None


class ChecklistItem(BaseModel):
    """A single testable item in the checklist."""
    id: str                                    # e.g. "NAV-001", "BTN-012"
    category: TestCategory
    priority: TestPriority
    description: str                           # Human-readable test description
    preconditions: list[str] = []              # What must be true before testing
    action: str                                # What to do
    expected_outcome: str                      # What should happen
    page_or_section: str = ""                  # Which page/section this belongs to
    status: ItemStatus = ItemStatus.PENDING
    evidence: Optional[TestEvidence] = None
    retry_count: int = 0
    max_retries: int = 2
    error_message: Optional[str] = None
    skip_reason: Optional[str] = None


class Checklist(BaseModel):
    """The master checklist - source of truth for a test run."""
    id: str
    target_url: Optional[str] = None
    target_app: Optional[str] = None
    instructions: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    items: list[ChecklistItem] = []
    metadata: dict = {}

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def completed(self) -> int:
        return len([i for i in self.items if i.status in TERMINAL_STATUSES])

    @property
    def pending(self) -> int:
        return len([i for i in self.items if i.status == ItemStatus.PENDING])

    @property
    def passed(self) -> int:
        return len([i for i in self.items if i.status == ItemStatus.PASSED])

    @property
    def failed(self) -> int:
        return len([i for i in self.items if i.status == ItemStatus.FAILED])

    @property
    def errored(self) -> int:
        return len([i for i in self.items if i.status == ItemStatus.ERROR])

    @property
    def pass_rate(self) -> float:
        tested = [i for i in self.items if i.status in (ItemStatus.PASSED, ItemStatus.FAILED)]
        if not tested:
            return 0.0
        return len([i for i in tested if i.status == ItemStatus.PASSED]) / len(tested)

    def get_next_pending(self) -> Optional[ChecklistItem]:
        """Get next pending item, ordered by priority then ID."""
        pending = [i for i in self.items if i.status == ItemStatus.PENDING]
        if not pending:
            return None
        priority_order = {
            TestPriority.CRITICAL: 0,
            TestPriority.HIGH: 1,
            TestPriority.MEDIUM: 2,
            TestPriority.LOW: 3,
        }
        return sorted(pending, key=lambda i: (priority_order[i.priority], i.id))[0]

    def get_items_by_category(self, category: TestCategory) -> list[ChecklistItem]:
        return [i for i in self.items if i.category == category]

    def get_items_by_status(self, status: ItemStatus) -> list[ChecklistItem]:
        return [i for i in self.items if i.status == status]

    def summary(self) -> dict:
        """Return a summary dict of the checklist state."""
        return {
            "total": self.total,
            "completed": self.completed,
            "pending": self.pending,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "pass_rate": f"{self.pass_rate:.1%}",
        }
