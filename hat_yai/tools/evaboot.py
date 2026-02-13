"""Evaboot API — fallback for Sales Navigator executive search.

Used when Ghost Genius /private/sales-navigator returns 403.
Async: POST extraction → poll until EXECUTED → return prospects.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

import httpx

from hat_yai.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.evaboot.com/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Token {settings.evaboot_api_key}",
        "Content-Type": "application/json",
    }


def _build_sales_nav_url(
    company_id: str,
    company_name: str,
    filter_type: str = "CURRENT_COMPANY",
) -> str:
    """Build a LinkedIn Sales Navigator search URL.

    Args:
        company_id: LinkedIn organization ID (same as GG company ID).
        company_name: Company name for display in the filter.
        filter_type: CURRENT_COMPANY or PAST_COMPANY.
    """
    search_id = random.randint(1000000000, 9999999999)

    # Seniority levels: 310=CXO, 300=VP, 320=Owner/Partner, 130=Strategic
    url = (
        "https://www.linkedin.com/sales/search/people?query="
        f"(recentSearchParam%3A(id%3A{search_id}%2CdoLogHistory%3Atrue)%2C"
        "filters%3AList("
        f"(type%3A{filter_type}%2Cvalues%3AList("
        f"(id%3Aurn%253Ali%253Aorganization%253A{company_id}%2C"
        f"text%3A{company_name}%2C"
        "selectionType%3AINCLUDED%2Cparent%3A(id%3A0))))%2C"
        "(type%3ASENIORITY_LEVEL%2Cvalues%3AList("
        "(id%3A310%2Ctext%3ACXO%2CselectionType%3AINCLUDED)%2C"
        "(id%3A300%2Ctext%3AVP%2CselectionType%3AINCLUDED)%2C"
        "(id%3A320%2Ctext%3AOwner%2CselectionType%3AINCLUDED)%2C"
        "(id%3A130%2Ctext%3AStrategic%2CselectionType%3AINCLUDED)))))"
        "&viewAllFilters=true"
    )
    return url


async def _create_extraction(linkedin_url: str, search_name: str) -> Optional[str]:
    """POST /v1/extractions/url/ — returns extraction_id or None."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_BASE_URL}/extractions/url/",
            headers=_headers(),
            json={
                "linkedin_url": linkedin_url,
                "search_name": search_name,
                "enrich_email": "none",
            },
        )
        if resp.status_code != 202:
            logger.error(f"Evaboot create extraction failed: {resp.status_code} {resp.text}")
            return None
        data = resp.json()
        extraction_id = data.get("extraction_id")
        count = data.get("count", 0)
        logger.info(f"Evaboot extraction created: {extraction_id} ({count} prospects)")
        return extraction_id


async def _poll_extraction(extraction_id: str, max_polls: int = 60, interval: float = 10.0) -> list[dict]:
    """GET /v1/extractions/{id}/ — poll until EXECUTED, return prospects."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(max_polls):
            resp = await client.get(
                f"{_BASE_URL}/extractions/{extraction_id}/",
                headers=_headers(),
            )
            if resp.status_code not in (200, 202):
                logger.warning(f"Evaboot poll failed: {resp.status_code}")
                await asyncio.sleep(interval)
                continue

            data = resp.json()
            status = data.get("status", "")

            if status == "EXECUTED":
                prospects = data.get("prospects", [])
                logger.info(f"Evaboot extraction complete: {len(prospects)} prospects")
                return prospects
            elif status in ("FAILED", "CANCELLED"):
                logger.error(f"Evaboot extraction {status}")
                return []

            logger.debug(f"Evaboot poll {i+1}/{max_polls}: {status}")
            await asyncio.sleep(interval)

    logger.error("Evaboot extraction timed out")
    return []


def _prospect_to_exec(prospect: dict, is_current: bool) -> dict:
    """Convert Evaboot prospect to the exec_data format used by the rest of the graph."""
    unique_id = prospect.get("Linkedin URL Unique ID", "")
    public_url = prospect.get("Linkedin URL Public", "")
    return {
        "id": unique_id or public_url,
        "full_name": f"{prospect.get('First Name', '')} {prospect.get('Last Name', '')}".strip(),
        "url": public_url or unique_id,
        "headline": prospect.get("Current Job", ""),
        "is_current_employee": is_current,
    }


async def search_executives(
    linkedin_company_id: str,
    company_name: str,
) -> tuple[list[dict], list[dict]]:
    """Search C-level executives via Evaboot (current + past).

    Returns (current_executives, past_executives) in the same format as GG.
    """
    if not settings.evaboot_api_key:
        logger.warning("Evaboot API key not configured, skipping fallback")
        return [], []

    # Launch both extractions in parallel
    current_url = _build_sales_nav_url(linkedin_company_id, company_name, "CURRENT_COMPANY")
    past_url = _build_sales_nav_url(linkedin_company_id, company_name, "PAST_COMPANY")

    current_id, past_id = await asyncio.gather(
        _create_extraction(current_url, f"{company_name}_current_execs"),
        _create_extraction(past_url, f"{company_name}_past_execs"),
    )

    # Poll both in parallel
    async def _empty() -> list[dict]:
        return []

    current_prospects, past_prospects = await asyncio.gather(
        _poll_extraction(current_id) if current_id else _empty(),
        _poll_extraction(past_id) if past_id else _empty(),
    )

    current = [_prospect_to_exec(p, True) for p in current_prospects if p.get("Matches Filters") == "YES"]
    past = [_prospect_to_exec(p, False) for p in past_prospects if p.get("Matches Filters") == "YES"]

    logger.info(f"Evaboot: {len(current)} current + {len(past)} past executives")
    return current, past
