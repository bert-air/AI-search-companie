"""Agent COMEX Organisation — IT org structure, CIO, DSI, PMO.

Depends on LinkedIn enrichment data. Spec reference: Section 7.4.
"""

from hat_yai.config import LINKEDIN_REGION_IDS
from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page
from hat_yai.tools.sales_navigator import make_search_sales_nav_tool


async def agent_comex_organisation_node(state: AuditState) -> dict:
    country = state.get("country") or "France"
    search_nav = make_search_sales_nav_tool(
        linkedin_company_id=state.get("linkedin_company_id") or "",
        company_name=state["company_name"],
        region_id=LINKEDIN_REGION_IDS.get(country, ""),
        region_name=country,
    )
    return await run_agent(
        state=state,
        agent_name="comex_organisation",
        tools=[search_web, scrape_page, search_nav],
    )
