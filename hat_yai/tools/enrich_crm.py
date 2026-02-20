"""Enrich-CRM API — company LinkedIn resolution by domain.

Plain sync function (NOT LangChain @tool). Called by linkedin_enrichment_node.

Endpoint used:
- GET /api/ingress/v4/full?apiId=...&data=<domain>&firmographic=true
  Returns company.firmographics.linkedinUrl + linkedinId when found.

Docs: https://enrich-crm.readme.io/reference/gethomepagecontentv4
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from hat_yai.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://gateway.enrich-crm.com/api/ingress/v4/full"


def resolve_company_linkedin(domain: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a domain to its LinkedIn company URL and numeric ID.

    Returns (linkedin_url, linkedin_id) or (None, None).
    Costs 1 API credit per successful lookup.
    """
    api_key = settings.enrich_crm_api_key
    if not api_key:
        logger.warning("Enrich-CRM: no API key configured, skipping")
        return None, None

    try:
        resp = httpx.get(
            _BASE_URL,
            params={"apiId": api_key, "data": domain, "firmographic": "true"},
            timeout=15,
        )
        if resp.status_code == 404:
            logger.info(f"Enrich-CRM: domain {domain} not found (404)")
            return None, None

        resp.raise_for_status()
        data = resp.json()

        # API error codes (e.g. code=5 "Not found")
        if data.get("code") and not data.get("company"):
            logger.info(f"Enrich-CRM: {domain} -> code {data['code']}: {data.get('message')}")
            return None, None

        company = data.get("company")
        if not company:
            return None, None

        firmographics = company.get("firmographics") or {}
        linkedin_url = firmographics.get("linkedinUrl", "")
        linkedin_id = firmographics.get("linkedinId", "")

        # Fallback: companySearch sometimes contains a LinkedIn URL
        if not linkedin_url:
            cs = company.get("companySearch", "")
            if "linkedin.com" in cs:
                linkedin_url = cs

        if linkedin_url and "linkedin.com" in linkedin_url:
            logger.info(
                f"Enrich-CRM: {domain} -> {firmographics.get('name', '?')} "
                f"| {linkedin_url} (id={linkedin_id}, "
                f"credits={data.get('creditBurn')}, remaining={data.get('currentCredit')})"
            )
            return linkedin_url, str(linkedin_id) if linkedin_id else None

        return None, None

    except httpx.TimeoutException:
        logger.warning(f"Enrich-CRM: timeout for {domain}")
        return None, None
    except Exception as e:
        logger.warning(f"Enrich-CRM: error for {domain}: {e}")
        return None, None
