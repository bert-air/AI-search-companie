"""Agent COMEX Profils — deep dive on each C-level.

Depends on MAP/REDUCE router slice (c_levels_details + organigramme).
Spec reference: Section 6.3 (COMEX Profils).
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page


async def agent_comex_profils_node(state: AuditState) -> dict:
    return await run_agent(
        state=state,
        agent_name="comex_profils",
        tools=[search_web, scrape_page],
    )
