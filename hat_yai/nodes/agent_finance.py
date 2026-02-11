"""Agent Finance — web search for financial data.

Spec reference: Section 7.1.
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page


async def agent_finance_node(state: AuditState) -> dict:
    return await run_agent(
        state=state,
        agent_name="finance",
        tools=[search_web, scrape_page],
    )
