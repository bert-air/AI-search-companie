"""Agent COMEX Profils — deep dive on each C-level.

Depends on Ghost Genius + COMEX Organisation output. Spec reference: Section 7.5.
"""

from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent
from hat_yai.tools.firecrawl import search_web, scrape_page


def _get_comex_orga_report(state: AuditState) -> dict | None:
    """Extract the COMEX Organisation report from agent_reports."""
    for report in state.get("agent_reports", []):
        if report.get("agent_name") == "comex_organisation":
            return report
    return None


async def agent_comex_profils_node(state: AuditState) -> dict:
    comex_orga = _get_comex_orga_report(state)
    return await run_agent(
        state=state,
        agent_name="comex_profils",
        tools=[search_web, scrape_page],
        extra_context={"comex_organisation_report": comex_orga} if comex_orga else None,
        two_pass=True,
    )
