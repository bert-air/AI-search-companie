"""Unipile API — primary source for employee growth + executive search fallback.

Plain async functions (NOT LangChain @tool). Called by linkedin_enrichment_node.
Priority chain: Evaboot → Unipile → Ghost Genius.

Endpoints used:
- GET  /linkedin/company/{slug}  — employee growth data
- POST /linkedin/search           — people search via Sales Navigator URL
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

from hat_yai.config import settings
from hat_yai.tools import supabase_db as db

logger = logging.getLogger(__name__)

# --- Account ID cache (fetched once from Supabase workspace_team) ---
_cached_account_id: Optional[str] = None


def _extract_linkedin_slug(linkedin_company_url: str) -> Optional[str]:
    """Extract company slug from LinkedIn URL.

    Examples:
        "https://www.linkedin.com/company/saint-gobain/" -> "saint-gobain"
        "https://www.linkedin.com/company/12345/" -> "12345"
    """
    if not linkedin_company_url:
        return None
    match = re.search(r"linkedin\.com/company/([a-zA-Z0-9_-]+)", linkedin_company_url)
    return match.group(1) if match else None


def _get_account_id() -> Optional[str]:
    """Get Unipile account_id from Supabase workspace_team (cached after first call)."""
    global _cached_account_id
    if _cached_account_id:
        return _cached_account_id

    try:
        client = db._get_client()
        result = (
            client.table("workspace_team")
            .select("unipile_account_id")
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if result.data:
            _cached_account_id = result.data[0].get("unipile_account_id")
            return _cached_account_id
    except Exception as e:
        logger.warning(f"Unipile: failed to fetch account_id from workspace_team: {e}")

    return None


def _headers() -> dict[str, str]:
    return {
        "X-API-KEY": settings.unipile_api_key,
    }


def _map_response_to_growth(data: dict) -> dict:
    """Map Unipile company response to the Ghost Genius growth format.

    Input: { "insights": { "employeesCount": { ... } } }
    Output: { "employees", "growth_6_months", "growth_1_year", "growth_2_years",
              "headcount_growth", "average_tenure", "_source": "unipile" }
    """
    insights = data.get("insights")
    if not insights:
        return {}

    emp_count = insights.get("employeesCount")
    if not emp_count:
        return {}

    result: dict = {"_source": "unipile"}

    # totalCount -> employees
    total = emp_count.get("totalCount")
    if total is not None:
        result["employees"] = total

    # averageTenure -> average_tenure
    tenure = emp_count.get("averageTenure")
    if tenure:
        result["average_tenure"] = tenure

    # growthGraph -> growth_6_months, growth_1_year, growth_2_years
    growth_graph = emp_count.get("growthGraph") or []
    month_to_key = {
        6: "growth_6_months",
        12: "growth_1_year",
        24: "growth_2_years",
    }
    for entry in growth_graph:
        month_range = entry.get("monthRange")
        pct = entry.get("growthPercentage")
        if month_range in month_to_key and pct is not None:
            result[month_to_key[month_range]] = pct

    # employeesCountGraph -> headcount_growth
    count_graph = emp_count.get("employeesCountGraph") or []
    if count_graph:
        result["headcount_growth"] = count_graph

    return result


async def get_employees_growth(linkedin_company_url: str) -> dict:
    """Fetch employee growth data from Unipile.

    Args:
        linkedin_company_url: Full LinkedIn company URL

    Returns:
        Dict matching Ghost Genius format:
        {growth_6_months, growth_1_year, growth_2_years, employees, headcount_growth}
        Returns {} if data unavailable.
    """
    slug = _extract_linkedin_slug(linkedin_company_url)
    if not slug:
        logger.warning(f"Unipile: could not extract slug from {linkedin_company_url}")
        return {}

    account_id = _get_account_id()
    if not account_id:
        logger.warning("Unipile: no account_id available")
        return {}

    if not settings.unipile_api_key:
        logger.warning("Unipile: API key not configured")
        return {}

    url = f"{settings.unipile_base_url}/linkedin/company/{slug}"
    params = {"account_id": account_id}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(3):  # initial + 2 retries
            try:
                resp = await client.get(url, params=params, headers=_headers())

                if resp.status_code == 429:
                    if attempt < 2:
                        logger.warning(f"Unipile: 429 rate limit, retry {attempt + 1}/2 in 5s")
                        await asyncio.sleep(5)
                        continue
                    else:
                        logger.error("Unipile: 429 after 2 retries, giving up")
                        return {}

                resp.raise_for_status()
                data = resp.json()
                growth = _map_response_to_growth(data)

                if growth and growth.get("growth_1_year") is not None:
                    logger.info(f"Unipile: got growth data for {slug}")
                else:
                    logger.info(f"Unipile: response had no insights for {slug}")

                return growth

            except httpx.HTTPStatusError as e:
                logger.warning(f"Unipile: HTTP {e.response.status_code} for {slug}")
                return {}
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"Unipile: error ({e}), retry {attempt + 1}/2 in 5s")
                    await asyncio.sleep(5)
                else:
                    logger.error(f"Unipile: failed after retries: {e}")
                    return {}

    return {}


# ---------------------------------------------------------------------------
# People search via Sales Navigator URL
# ---------------------------------------------------------------------------

def _map_person_to_exec(person: dict, is_current: bool) -> dict:
    """Convert a Unipile search result item to canonical exec_data format."""
    public_url = person.get("public_profile_url", "")
    return {
        "id": person.get("public_identifier") or person.get("id") or public_url,
        "full_name": person.get("name") or f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
        "url": public_url,
        "headline": person.get("headline", ""),
        "is_current_employee": is_current,
    }


async def search_linkedin(sales_nav_url: str) -> list[dict]:
    """Execute a Sales Navigator search via Unipile.

    POST /linkedin/search?account_id={id}  with body {"url": "<sales_nav_url>"}
    Returns the raw items list from Unipile response, or [].
    """
    account_id = _get_account_id()
    if not account_id:
        logger.warning("Unipile search: no account_id available")
        return []

    if not settings.unipile_api_key:
        logger.warning("Unipile search: API key not configured")
        return []

    endpoint = f"{settings.unipile_base_url}/linkedin/search"
    params = {"account_id": account_id}

    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    endpoint,
                    params=params,
                    headers=_headers(),
                    json={"url": sales_nav_url},
                )

                if resp.status_code == 429:
                    if attempt < 2:
                        logger.warning(f"Unipile search: 429 rate limit, retry {attempt + 1}/2 in 5s")
                        await asyncio.sleep(5)
                        continue
                    else:
                        logger.error("Unipile search: 429 after 2 retries, giving up")
                        return []

                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                total = data.get("paging", {}).get("total_count", len(items))
                logger.info(f"Unipile search: {len(items)} items returned (total={total})")
                return items

            except httpx.HTTPStatusError as e:
                logger.warning(f"Unipile search: HTTP {e.response.status_code}")
                return []
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"Unipile search: error ({e}), retry {attempt + 1}/2 in 5s")
                    await asyncio.sleep(5)
                else:
                    logger.error(f"Unipile search: failed after retries: {e}")
                    return []

    return []


async def search_executives(
    linkedin_company_id: str,
    company_name: str,
    region_id: str = "",
    region_name: str = "",
) -> tuple[list[dict], list[dict]]:
    """Search executives by seniority via Unipile (reuses Evaboot URL builders).

    Returns (current_executives, past_executives) in canonical exec_data format.
    """
    from hat_yai.tools.evaboot import _build_sales_nav_url

    url_current = _build_sales_nav_url(
        linkedin_company_id, company_name, "CURRENT_COMPANY", region_id, region_name,
    )
    url_past = _build_sales_nav_url(
        linkedin_company_id, company_name, "PAST_COMPANY", region_id, region_name,
    )

    items_current, items_past = await asyncio.gather(
        search_linkedin(url_current),
        search_linkedin(url_past),
    )

    current = [_map_person_to_exec(p, True) for p in items_current]
    past = [_map_person_to_exec(p, False) for p in items_past]

    logger.info(f"Unipile executives: {len(current)} current, {len(past)} past")
    return current, past


async def search_executives_by_keywords(
    linkedin_company_id: str,
    company_name: str,
    title_keywords: list[str],
    region_id: str = "",
    region_name: str = "",
) -> list[dict]:
    """Search current employees by title keywords via Unipile.

    Returns list of exec_data dicts in canonical format.
    """
    from hat_yai.tools.evaboot import _build_sales_nav_title_url

    url = _build_sales_nav_title_url(
        linkedin_company_id, company_name, title_keywords, region_id, region_name,
    )

    items = await search_linkedin(url)
    results = [_map_person_to_exec(p, True) for p in items]
    logger.info(f"Unipile keyword search: {len(results)} results for {title_keywords}")
    return results
