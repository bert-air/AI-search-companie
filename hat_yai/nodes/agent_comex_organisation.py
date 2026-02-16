"""Agent COMEX Organisation — IT org structure, CIO, DSI, PMO.

Depends on Ghost Genius data. Spec reference: Section 7.4.
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page


async def agent_comex_organisation_node(state: AuditState) -> dict:
    return await run_agent(
        state=state,
        agent_name="comex_organisation",
        tools=[search_web, scrape_page],
        two_pass=True,
    )
