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
- When typing text into an input field to SUBMIT it (e.g. add a todo), you MUST press Enter afterward using key_combination("Return") or key_combination("Enter"). Typing alone does not submit.
- Click an element before typing into it to ensure it has focus.
- After pressing Enter to submit a form or todo, wait a moment and verify the result appeared.
- If the action says "press Enter" or "submit", always use key_combination to press the Enter/Return key.

INSTRUCTIONS:
1. Look at the current screen state using a screenshot tool if needed.
2. If preconditions are not met, take steps to meet them first (e.g. navigate to URL).
3. Perform EXACTLY the action described above, including pressing Enter when submitting.
4. Wait briefly after interactions to let the UI update.
5. Observe the actual result on screen.
6. Compare what you see with the expected outcome.
7. Report your finding in this exact format:

RESULT: PASS or FAIL
OBSERVATION: [What you actually saw on screen after the action]
DETAIL: [Any additional context about what happened]

Be precise. Do exactly what is asked. Do not test anything else.
Do not skip this test. Do not summarize. Execute and report."""
