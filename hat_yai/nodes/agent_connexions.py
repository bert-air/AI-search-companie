"""Agent Connexions — LinkedIn connection analysis and indirect vectors.

Reads pre-processed connexion data from router slice + sales team.
No web tools needed. Spec reference: Section 6.3 (Connexions).
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent


async def agent_connexions_node(state: AuditState) -> dict:
    return await run_agent(
        state=state,
        agent_name="connexions",
        tools=None,  # No external tools needed
    )
