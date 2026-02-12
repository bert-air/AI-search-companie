"""Agent Entreprise — web search for company positioning and market.

Spec reference: Section 7.2.
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page
from hat_yai.tools import supabase_db as db


async def agent_entreprise_node(state: AuditState) -> dict:
    # Read enriched_companies from Supabase (available immediately, no GG dependency)
    extra = {}
    company = db.read_enriched_company(state["domain"], state["company_name"])
    if company:
        extra["enriched_company"] = {
            "linkedin_company_size": company.get("linkedin_company_size"),
            "company_size_range": company.get("company_size_range"),
            "industry": company.get("industry"),
            "description": company.get("description"),
            "specialities": company.get("specialities"),
            "hq_country": company.get("hq_country"),
            "hq_city": company.get("hq_city"),
            "founded_year": company.get("founded_year"),
        }

    return await run_agent(
        state=state,
        agent_name="entreprise",
        tools=[search_web, scrape_page],
        extra_context=extra or None,
    )
