"""Unit tests for checklist data models - no API key needed."""

from qa_navigator.checklist.models import (
    Checklist,
    ChecklistItem,
    TestCategory,
    TestPriority,
    ItemStatus,
    TERMINAL_STATUSES,
)


def _make_item(id: str, priority: TestPriority = TestPriority.MEDIUM, status: ItemStatus = ItemStatus.PENDING):
    return ChecklistItem(
        id=id,
        category=TestCategory.BUTTON_CLICK,
        priority=priority,
        description=f"Test {id}",
        action=f"Click {id}",
        expected_outcome=f"{id} activates",
    )


def test_checklist_summary():
    items = [
        _make_item("BTN-001", status=ItemStatus.PASSED),
        _make_item("BTN-002", status=ItemStatus.FAILED),
        _make_item("BTN-003", status=ItemStatus.PENDING),
        _make_item("BTN-004", status=ItemStatus.ERROR),
    ]
    items[0].status = ItemStatus.PASSED
    items[1].status = ItemStatus.FAILED
    items[3].status = ItemStatus.ERROR

    cl = Checklist(id="test-1", items=items)
    assert cl.total == 4
    assert cl.completed == 3  # PASSED + FAILED + ERROR
    assert cl.pending == 1
    assert cl.passed == 1
    assert cl.failed == 1
    assert cl.errored == 1


def test_get_next_pending_priority_order():
    items = [
        _make_item("BTN-001", priority=TestPriority.LOW),
        _make_item("BTN-002", priority=TestPriority.CRITICAL),
        _make_item("BTN-003", priority=TestPriority.HIGH),
    ]
    cl = Checklist(id="test-2", items=items)

    next_item = cl.get_next_pending()
    assert next_item.id == "BTN-002"  # Critical comes first


def test_get_next_pending_returns_none_when_empty():
    items = [_make_item("BTN-001", status=ItemStatus.PASSED)]
    items[0].status = ItemStatus.PASSED
    cl = Checklist(id="test-3", items=items)

    assert cl.get_next_pending() is None


def test_pass_rate():
    items = [
        _make_item("BTN-001"),
        _make_item("BTN-002"),
        _make_item("BTN-003"),
    ]
    items[0].status = ItemStatus.PASSED
    items[1].status = ItemStatus.PASSED
    items[2].status = ItemStatus.FAILED

    cl = Checklist(id="test-4", items=items)
    assert abs(cl.pass_rate - 2/3) < 0.01


def test_pass_rate_no_tested():
    cl = Checklist(id="test-5", items=[_make_item("BTN-001")])
    assert cl.pass_rate == 0.0


def test_items_by_category():
    items = [
        _make_item("BTN-001"),
        ChecklistItem(
            id="INP-001",
            category=TestCategory.FORM_INPUT,
            priority=TestPriority.HIGH,
            description="Test input",
            action="Type text",
            expected_outcome="Text appears",
        ),
    ]
    cl = Checklist(id="test-6", items=items)
    assert len(cl.get_items_by_category(TestCategory.BUTTON_CLICK)) == 1
    assert len(cl.get_items_by_category(TestCategory.FORM_INPUT)) == 1
