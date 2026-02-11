"""Agent Connexions — check if AirSaas sales are connected to executives.

Simple agent: reads connected_with from state, no Firecrawl needed.
Spec reference: Section 7.6.
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent


async def agent_connexions_node(state: AuditState) -> dict:
    return await run_agent(
        state=state,
        agent_name="connexions",
        tools=None,  # No external tools needed
    )
