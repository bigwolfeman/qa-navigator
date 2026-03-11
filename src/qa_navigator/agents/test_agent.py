"""ADK agent factory for test execution.

Creates agents that execute single test items using the ComputerUseToolset.
Each item gets a fresh agent with a hyper-specific instruction to prevent
context pollution and ensure focused execution.
"""

from google.adk import Agent
from google.adk.tools.computer_use.computer_use_toolset import ComputerUseToolset
from google.adk.tools.computer_use.base_computer import BaseComputer

from ..config import settings

# ---------------------------------------------------------------------------
# Monkey-patch: add get_ui_tree() to BaseComputer so ComputerUseToolset
# exposes it as a tool.  ComputerUseToolset discovers tools by iterating
# dir(BaseComputer), so the method must exist on the ABC class itself.
# The default implementation returns an empty dict; computer subclasses
# override with real accessibility data.
# ---------------------------------------------------------------------------
if not hasattr(BaseComputer, "get_ui_tree"):
    async def _base_get_ui_tree(self) -> dict:
        """Return the UI element tree for the current window/application.

        Use this to discover interactive elements (buttons, inputs, labels)
        with their names, types, and screen coordinates before clicking.
        Returns a dict with an 'elements' list.
        """
        return {}

    BaseComputer.get_ui_tree = _base_get_ui_tree  # type: ignore[attr-defined]

if not hasattr(BaseComputer, "find_and_click"):
    async def _base_find_and_click(self, element_name: str):
        """Find a UI element by its name, text, role, or label and click it.

        Much more reliable than click_at(x, y) because it uses accessibility
        properties to locate the element precisely. Use get_ui_tree() first
        to discover element names.

        Args:
            element_name: The visible text, aria-label, placeholder, role name,
                         or CSS selector of the element to click.
        """
        raise NotImplementedError("find_and_click not available on this computer")

    BaseComputer.find_and_click = _base_find_and_click  # type: ignore[attr-defined]

if not hasattr(BaseComputer, "find_and_type"):
    async def _base_find_and_type(
        self,
        element_name: str,
        text: str,
        press_enter: bool = False,
        clear_first: bool = False,
    ):
        """Find an input element by name/placeholder/label and type text into it.

        More reliable than type_text_at(x, y, text) because it uses accessibility
        properties. Use for form inputs, search boxes, etc.

        Args:
            element_name: The placeholder text, aria-label, or name of the input.
            text: The text to type.
            press_enter: If True, press Enter after typing.
            clear_first: If True, clear existing text before typing.
        """
        raise NotImplementedError("find_and_type not available on this computer")

    BaseComputer.find_and_type = _base_find_and_type  # type: ignore[attr-defined]


def create_test_agent(
    computer: BaseComputer,
    item_instruction: str,
    agent_name: str = "qa_test_executor",
) -> Agent:
    """Create an ADK agent for executing a single test item.

    Args:
        computer: The BaseComputer implementation to use.
        item_instruction: Specific instruction for this test item.
        agent_name: Name for the agent instance.

    Returns:
        Configured ADK Agent ready to execute.
    """
    return Agent(
        model=settings.computer_use_model,
        name=agent_name,
        description="QA test executor that performs a single UI test action and reports results.",
        instruction=item_instruction,
        tools=[ComputerUseToolset(computer=computer)],
    )


def build_item_instruction(
    item_id: str,
    category: str,
    description: str,
    preconditions: list[str],
    action: str,
    expected_outcome: str,
) -> str:
    """Build a precise, single-item instruction for the test agent.

    The instruction is designed to be:
    - Specific: exactly one test item
    - Structured: clear steps to follow
    - Bounded: agent reports and stops, doesn't explore further
    """
    precondition_text = "\n".join(f"  - {p}" for p in preconditions) if preconditions else "  None"

    return f"""You are a QA tester executing ONE specific test. Do exactly what is asked.

TEST ITEM: {item_id}
CATEGORY: {category}

DESCRIPTION:
{description}

PRECONDITIONS:
{precondition_text}

ACTION TO PERFORM:
{action}

EXPECTED OUTCOME:
{expected_outcome}

CRITICAL RULES FOR WEB APP INTERACTION:
- ALWAYS prefer find_and_click(element_name) and find_and_type(element_name, text) over coordinate-based clicking. These use accessibility properties to locate elements precisely and are MUCH more reliable.
- Use get_ui_tree() to discover element names, then use find_and_click/find_and_type with those names.
- Only fall back to click_at(x, y) or type_text_at(x, y, text) if find_and_click/find_and_type fail for a specific element.
- When typing text into an input field to SUBMIT it (e.g. add a todo), set press_enter=True in find_and_type, or use key_combination("Return") afterward.
- After submitting, wait a moment and verify the result appeared.

INSTRUCTIONS:
1. Call get_ui_tree() to discover all interactive elements on the page.
2. If preconditions are not met, take steps to meet them first (e.g. navigate to URL).
3. Use find_and_click/find_and_type with element names from the UI tree.
4. Wait briefly after interactions to let the UI update.
5. Observe the actual result on screen via screenshot.
6. Compare what you see with the expected outcome.
7. Report your finding in this exact format:

RESULT: PASS or FAIL
OBSERVATION: [What you actually saw on screen after the action]
DETAIL: [Any additional context about what happened]

Be precise. Do exactly what is asked. Do not test anything else.
Do not skip this test. Do not summarize. Execute and report."""
