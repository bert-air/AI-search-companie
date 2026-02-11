"""Agent Entreprise — web search for company positioning and market.

Spec reference: Section 7.2.
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page


async def agent_entreprise_node(state: AuditState) -> dict:
    return await run_agent(
        state=state,
        agent_name="entreprise",
        tools=[search_web, scrape_page],
    )
