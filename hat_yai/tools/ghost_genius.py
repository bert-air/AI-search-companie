"""Ghost Genius API functions.

Plain async functions (NOT LangChain @tool). Called directly by ghost_genius_node.
Handles account_id rotation with round-robin and rate-limit tracking.

Spec reference: Section 5 + Section 9.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

from hat_yai.config import settings

logger = logging.getLogger(__name__)

# --- Account rotation ---

_exhausted_accounts: set[str] = set()
_rotation_index: int = 0


def reset_rotation() -> None:
    """Reset rotation state at the start of each audit run."""
    global _exhausted_accounts, _rotation_index
    _exhausted_accounts = set()
    _rotation_index = 0


def _next_account_id() -> Optional[str]:
    """Get next available account_id via round-robin. Returns None if all exhausted."""
    global _rotation_index
    ids = settings.ghost_genius_account_ids
    if not ids:
        return None

    for _ in range(len(ids)):
        candidate = ids[_rotation_index % len(ids)]
        _rotation_index += 1
        if candidate not in _exhausted_accounts:
            return candidate

    return None


def _mark_exhausted(account_id: str) -> None:
    _exhausted_accounts.add(account_id)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.ghost_genius_api_key}",
        "Content-Type": "application/json",
    }


async def _get_with_rotation(
    path: str,
    params: dict,
    needs_account: bool = True,
    timeout: float = 30.0,
) -> dict:
    """Make a GET request with account rotation and retry on rate limit.
    Retries once on 5xx after 30 seconds per spec Section 5.6.
    """
    async with httpx.AsyncClient(
        base_url=settings.ghost_genius_base_url,
        headers=_headers(),
        timeout=timeout,
    ) as client:
        if needs_account:
            account_id = _next_account_id()
            if account_id is None:
                raise RuntimeError("All Ghost Genius accounts are rate-limited")
            params["account_id"] = account_id

        resp = await client.get(path, params=params)

        # Rate limit → mark exhausted, retry with next account
        if resp.status_code == 429 and needs_account:
            _mark_exhausted(params["account_id"])
            next_id = _next_account_id()
            if next_id is None:
                raise RuntimeError("All Ghost Genius accounts are rate-limited")
            params["account_id"] = next_id
            resp = await client.get(path, params=params)

        # 5xx → retry once after 30s (spec 5.6)
        if resp.status_code >= 500:
            logger.warning(f"GG 5xx on {path}, retrying in 30s")
            await asyncio.sleep(30)
            resp = await client.get(path, params=params)

        resp.raise_for_status()
        return resp.json()


# --- Step 1: Domain → LinkedIn Company ID ---

def extract_linkedin_company_id(url: str) -> Optional[str]:
    """Extract numeric company ID from a LinkedIn company URL."""
    if not url:
        return None
    match = re.search(r"linkedin\.com/company/(\d+)", url)
    return match.group(1) if match else None


async def get_company_by_url(linkedin_url: str) -> dict:
    """GET /company?url={linkedin_url}"""
    return await _get_with_rotation(
        "/company",
        params={"url": linkedin_url},
        needs_account=False,
    )


async def search_companies(keywords: str) -> list[dict]:
    """GET /search/companies?keywords={name}"""
    result = await _get_with_rotation(
        "/search/companies",
        params={"keywords": keywords},
        needs_account=False,
    )
    return result if isinstance(result, list) else result.get("data", [])


# --- Step 2: Employees Growth ---

async def get_employees_growth(linkedin_company_url: str) -> dict:
    """GET /private/employees-growth?account_id={id}&url={url}

    Returns: {growth_6_months, growth_1_year, growth_2_years, employees, headcount_growth}
    """
    return await _get_with_rotation(
        "/private/employees-growth",
        params={"url": linkedin_company_url},
        needs_account=True,
    )


# --- Step 3: Search C-levels via Sales Navigator ---

async def search_executives_current(linkedin_company_id: str, locations: str = "") -> list[dict]:
    """Search current employees at C-level via Sales Navigator.

    GET /private/sales-navigator?account_id={id}&current_company={id}&seniority_level=310,320,300,220&locations={id}
    """
    params: dict = {
        "current_company": linkedin_company_id,
        "seniority_level": "310,320,300,220",
    }
    if locations:
        params["locations"] = locations
    result = await _get_with_rotation(
        "/private/sales-navigator",
        params=params,
        needs_account=True,
    )
    return result if isinstance(result, list) else result.get("data", [])


async def search_executives_past(linkedin_company_id: str, locations: str = "") -> list[dict]:
    """Search past employees at C-level via Sales Navigator.

    GET /private/sales-navigator?account_id={id}&past_company={id}&seniority_level=310,320,300,220&locations={id}
    """
    params: dict = {
        "past_company": linkedin_company_id,
        "seniority_level": "310,320,300,220",
    }
    if locations:
        params["locations"] = locations
    result = await _get_with_rotation(
        "/private/sales-navigator",
        params=params,
        needs_account=True,
    )
    return result if isinstance(result, list) else result.get("data", [])


async def search_executives_by_keywords(
    linkedin_company_id: str,
    keywords: str,
    locations: str = "",
) -> list[dict]:
    """Search current employees by title keywords via Sales Navigator (no seniority filter).

    GET /private/sales-navigator?account_id={id}&current_company={id}&keywords={kw}&locations={id}
    """
    params: dict = {
        "current_company": linkedin_company_id,
        "keywords": keywords,
    }
    if locations:
        params["locations"] = locations
    result = await _get_with_rotation(
        "/private/sales-navigator",
        params=params,
        needs_account=True,
    )
    return result if isinstance(result, list) else result.get("data", [])


# --- Step 5: LinkedIn Posts ---

async def get_profile_posts(linkedin_url: str, page: int = 1, pagination_token: str = "") -> dict:
    """GET /profile/posts?url={url}&page={n}&pagination_token={token}

    Returns: {data: [...posts], pagination_token: "..."}
    """
    params: dict = {"url": linkedin_url, "page": str(page)}
    if pagination_token:
        params["pagination_token"] = pagination_token

    return await _get_with_rotation(
        "/profile/posts",
        params=params,
        needs_account=False,
    )
