"""Sales Navigator search tool for LLM agents.

Synchronous LangChain @tool that searches LinkedIn Sales Navigator by title keywords
via Evaboot API (POST extraction + polling).

Called by agents during their ReAct loop — must be synchronous because
agent_runner.py calls tool_fn.invoke() from within an async event loop.
"""

from __future__ import annotations

import logging
import time

import httpx
from langchain_core.tools import tool

from hat_yai.config import settings
from hat_yai.tools.evaboot import (
    _build_sales_nav_title_url,
    _prospect_to_exec,
)

logger = logging.getLogger(__name__)

_EVABOOT_BASE = "https://api.evaboot.com/v1"
_EVABOOT_POLL_INTERVAL = 10  # seconds
_EVABOOT_MAX_POLLS = 18  # 3 minutes max


def _evaboot_search_sync(
    linkedin_company_id: str,
    company_name: str,
    title_keywords: list[str],
    region_id: str = "",
    region_name: str = "",
) -> list[dict]:
    """Synchronous Evaboot keyword search via Sales Navigator URL extraction."""
    if not settings.evaboot_api_key:
        raise RuntimeError("Evaboot API key not configured")

    url = _build_sales_nav_title_url(
        linkedin_company_id, company_name, title_keywords, region_id, region_name,
    )
    headers = {
        "Authorization": f"Token {settings.evaboot_api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        # Create extraction
        resp = client.post(
            f"{_EVABOOT_BASE}/extractions/url/",
            headers=headers,
            json={
                "linkedin_url": url,
                "search_name": f"{company_name}_agent_search",
                "enrich_email": "none",
            },
        )
        if resp.status_code != 202:
            raise RuntimeError(f"Evaboot create failed: {resp.status_code} {resp.text}")

        extraction_id = resp.json().get("extraction_id")
        if not extraction_id:
            raise RuntimeError("Evaboot: no extraction_id returned")

        count = resp.json().get("count", 0)
        logger.info(f"Sales Nav tool: Evaboot extraction {extraction_id} ({count} prospects)")

        # Poll until done
        for i in range(_EVABOOT_MAX_POLLS):
            time.sleep(_EVABOOT_POLL_INTERVAL)
            poll = client.get(
                f"{_EVABOOT_BASE}/extractions/{extraction_id}/",
                headers=headers,
            )
            if poll.status_code not in (200, 202):
                continue

            data = poll.json()
            status = data.get("status", "")

            if status == "EXECUTED":
                prospects = data.get("prospects", [])
                return [
                    _prospect_to_exec(p, True)
                    for p in prospects
                    if p.get("Matches Filters") == "YES"
                ]
            elif status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Evaboot extraction {status}")

        raise RuntimeError("Evaboot extraction timed out")


def _format_results(results: list[dict]) -> str:
    """Format search results for the agent."""
    if not results:
        return "Aucun résultat trouvé."

    lines = [f"**{len(results)} profil(s) trouvé(s) :**\n"]
    for r in results[:15]:
        name = r.get("full_name", "?")
        headline = r.get("headline", "")
        url = r.get("url", "")
        lines.append(f"- **{name}** — {headline}")
        if url:
            lines.append(f"  LinkedIn: {url}")
    return "\n".join(lines)


def make_search_sales_nav_tool(
    linkedin_company_id: str,
    company_name: str,
    region_id: str = "",
    region_name: str = "",
):
    """Factory: create a Sales Navigator search tool bound to a specific company.

    Args:
        linkedin_company_id: LinkedIn organization ID.
        company_name: Company display name.
        region_id: LinkedIn region ID (e.g. "105015875" for France).
        region_name: Region display name (e.g. "France").

    Returns:
        A LangChain @tool function that agents can call.
    """

    @tool
    def search_sales_navigator(title_keywords: str) -> str:
        """Search LinkedIn Sales Navigator for current employees by job title at the target company.

        Use this to find specific roles (PMO, IT Manager, etc.) that may not appear in the
        executive data. Returns name, headline, and LinkedIn URL for each match.

        Args:
            title_keywords: Comma-separated title keywords to search for (e.g. "PMO, manager IT, CIO office").
        """
        keywords_list = [kw.strip() for kw in title_keywords.split(",") if kw.strip()]

        if not linkedin_company_id:
            return "Erreur : pas de LinkedIn company ID disponible pour cette entreprise."

        try:
            results = _evaboot_search_sync(
                linkedin_company_id, company_name, keywords_list, region_id, region_name,
            )
            logger.info(f"Sales Nav tool: Evaboot returned {len(results)} results for '{title_keywords}'")
            return _format_results(results)
        except Exception as e:
            logger.error(f"Sales Nav tool: Evaboot failed ({e})")
            return f"Erreur : recherche Sales Navigator échouée. ({e})"

    return search_sales_navigator
