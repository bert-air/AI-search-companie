"""Agent Dynamique — movements, changes, transformation signals.

Depends on Ghost Genius data. Spec reference: Section 7.3.
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page


async def agent_dynamique_node(state: AuditState) -> dict:
    return await run_agent(
        state=state,
        agent_name="dynamique",
        tools=[search_web, scrape_page],
    )
