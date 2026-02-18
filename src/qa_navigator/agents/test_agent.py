"""ADK agent factory for test execution.

Creates agents that execute single test items using the ComputerUseToolset.
Each item gets a fresh agent with a hyper-specific instruction to prevent
context pollution and ensure focused execution.
"""

from google.adk import Agent
from google.adk.tools.computer_use.computer_use_toolset import ComputerUseToolset
from google.adk.tools.computer_use.base_computer import BaseComputer

from ..config import settings


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

INSTRUCTIONS:
1. Look at the current screen state.
2. If preconditions are not met, take steps to meet them first.
3. Perform EXACTLY the action described above.
4. Observe the actual result on screen.
5. Compare what you see with the expected outcome.
6. Report your finding in this exact format:

RESULT: PASS or FAIL
OBSERVATION: [What you actually saw on screen after the action]
DETAIL: [Any additional context about what happened]

Be precise. Do exactly what is asked. Do not test anything else.
Do not skip this test. Do not summarize. Execute and report."""
