"""Ghost Genius — Technical node (no LLM).

5 sequential sub-steps of HTTP API calls with Supabase read/write.
On failure: sets ghost_genius_available=False so downstream agents operate in degraded mode.

Spec reference: Section 5.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from hat_yai.state import AuditState
from hat_yai.tools import ghost_genius as gg
from hat_yai.tools import supabase_db as db
from hat_yai.tools.firecrawl import scrape_page

logger = logging.getLogger(__name__)


def _extract_linkedin_url_from_html(html: str) -> Optional[str]:
    """Parse HTML/markdown to find a linkedin.com/company/xxx URL."""
    match = re.search(r"https?://(?:www\.)?linkedin\.com/company/[a-zA-Z0-9_-]+/?", html)
    return match.group(0) if match else None


async def _step1_resolve_company(
    domain: str,
    company_name: str,
    audit_report_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Step 1: Domain → LinkedIn Company ID (4-level fallback).

    Returns (linkedin_company_id, linkedin_company_url) or (None, None).
    """
    # 1. Check Supabase cache
    company = db.read_enriched_company(domain)
    if company and company.get("linkedin_private_url"):
        url = company["linkedin_private_url"]
        cid = gg.extract_linkedin_company_id(url)
        if cid:
            logger.info(f"Step 1: Found company ID {cid} from Supabase cache")
            return cid, url

    # 2. Scrape homepage for LinkedIn URL
    try:
        homepage_content = scrape_page.invoke(f"https://{domain}")
        li_url = _extract_linkedin_url_from_html(homepage_content)
        if li_url:
            # Confirm via GG API
            gg_company = await gg.get_company_by_url(li_url)
            cid = str(gg_company.get("id", ""))
            full_url = gg_company.get("url", li_url)
            if cid:
                logger.info(f"Step 1: Found company ID {cid} from homepage scrape")
                return cid, full_url
    except Exception as e:
        logger.warning(f"Step 1: Homepage scrape failed: {e}")

    # 3. GG search by company name
    try:
        results = await gg.search_companies(company_name)
        if results:
            # Take first result whose name roughly matches
            for r in results:
                name = r.get("name", "").lower()
                if company_name.lower() in name or name in company_name.lower():
                    cid = str(r.get("id", ""))
                    url = r.get("url", "")
                    if cid:
                        logger.info(f"Step 1: Found company ID {cid} from GG search")
                        return cid, url
            # Fallback: take first result
            first = results[0]
            cid = str(first.get("id", ""))
            url = first.get("url", "")
            if cid:
                logger.info(f"Step 1: Using first GG search result, ID {cid}")
                return cid, url
    except Exception as e:
        logger.warning(f"Step 1: GG search failed: {e}")

    # 4. Nothing found
    logger.warning(f"Step 1: Could not resolve LinkedIn company ID for {domain}")
    return None, None


def _step2_employees_growth(
    domain: str,
) -> Optional[dict]:
    """Step 2: Check growth cache in enriched_companies.

    Returns cached growth data if all columns are non-NULL, else None.
    """
    company = db.read_enriched_company(domain)
    if not company:
        return None

    growth_fields = ["growth_1_year", "growth_6_months", "growth_2_years", "headcount_growth"]
    if all(company.get(f) is not None for f in growth_fields):
        logger.info("Step 2: Using cached growth data from enriched_companies")
        return {
            "growth_6_months": company["growth_6_months"],
            "growth_1_year": company["growth_1_year"],
            "growth_2_years": company["growth_2_years"],
            "headcount_growth": company["headcount_growth"],
        }
    return None


async def _step3_search_executives(
    linkedin_company_id: str,
    audit_id: str,
    deal_id: str,
    domain: str,
) -> list[dict]:
    """Step 3: Search C-levels via Sales Navigator.

    Current + past employees, dedupe by id, cap at 30 (current first).
    """
    current = await gg.search_executives_current(linkedin_company_id)
    past = await gg.search_executives_past(linkedin_company_id)

    # Deduplicate by LinkedIn ID
    seen_ids: set[str] = set()
    deduped: list[dict] = []

    for exec_data in current:
        eid = exec_data.get("id", "")
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            exec_data["is_current_employee"] = True
            deduped.append(exec_data)

    for exec_data in past:
        eid = exec_data.get("id", "")
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            exec_data["is_current_employee"] = False
            deduped.append(exec_data)

    # Cap at 30 (current employees prioritized since they're added first)
    deduped = deduped[:30]

    # Insert into Supabase
    for exec_data in deduped:
        exec_data["_db_id"] = db.insert_audit_executive(audit_id, deal_id, domain, exec_data)

    logger.info(f"Step 3: Found {len(deduped)} executives ({len(current)} current, {len(past)} past)")
    return deduped


async def _step4_enrich_profiles(
    executives: list[dict],
) -> list[dict]:
    """Step 4: Enrich each profile via Supabase Edge Function.

    Cadence: 1 call every 10 seconds.
    Cache: use enriched_contacts if updated_at < 100 days.
    """
    enriched = []

    for exec_data in executives:
        url = exec_data.get("url", "")
        db_id = exec_data.get("_db_id", "")

        if not url:
            enriched.append(exec_data)
            continue

        # Check enriched_contacts cache
        contact = db.read_enriched_contact(url)

        if contact and db.is_contact_fresh(contact):
            # Use cached data
            _copy_contact_fields(exec_data, contact)
            if db_id:
                db.update_audit_executive(db_id, {
                    **_contact_to_exec_updates(contact),
                    "enrichment_status": "cached",
                })
            logger.debug(f"Step 4: Cached enrichment for {exec_data.get('full_name')}")
        else:
            # Call enrich edge function, wait 10s, re-read
            success = await db.call_enrich_function(url)
            await asyncio.sleep(10)

            contact = db.read_enriched_contact(url)
            if contact:
                _copy_contact_fields(exec_data, contact)
                if db_id:
                    db.update_audit_executive(db_id, {
                        **_contact_to_exec_updates(contact),
                        "enrichment_status": "enriched",
                    })
                logger.debug(f"Step 4: Enriched {exec_data.get('full_name')}")
            else:
                if db_id:
                    db.update_audit_executive(db_id, {"enrichment_status": "failed"})
                logger.warning(f"Step 4: Enrichment failed for {exec_data.get('full_name')}")

        enriched.append(exec_data)

    logger.info(f"Step 4: Enriched {len(enriched)} profiles")
    return enriched


def _copy_contact_fields(exec_data: dict, contact: dict) -> None:
    """Copy enriched_contacts fields into exec_data dict."""
    exec_data["full_name"] = contact.get("full_name", exec_data.get("full_name"))
    exec_data["headline"] = contact.get("linkedin_headline", exec_data.get("headline"))
    exec_data["current_job_title"] = contact.get("linkedin_job_title")
    exec_data["company_name"] = contact.get("company_name")
    exec_data["experiences"] = contact.get("experiences")
    exec_data["educations"] = contact.get("educations")
    exec_data["skills"] = contact.get("linkedin_skills")
    exec_data["connected_with"] = contact.get("connected_with")


def _contact_to_exec_updates(contact: dict) -> dict:
    """Build update dict for ai_agent_company_audit_executives from enriched_contacts."""
    return {
        "full_name": contact.get("full_name"),
        "headline": contact.get("linkedin_headline"),
        "current_job_title": contact.get("linkedin_job_title"),
        "company_name": contact.get("company_name"),
        "experiences": contact.get("experiences"),
        "educations": contact.get("educations"),
        "skills": contact.get("linkedin_skills"),
        "connected_with": contact.get("connected_with"),
    }


async def _step5_linkedin_posts(
    executives: list[dict],
    audit_id: str,
) -> list[dict]:
    """Step 5: Fetch LinkedIn posts for top 15 current employees, 2 pages each."""
    current_execs = [e for e in executives if e.get("is_current_employee")][:15]
    all_posts: list[dict] = []

    for exec_data in current_execs:
        url = exec_data.get("url", "")
        name = exec_data.get("full_name", "")
        if not url:
            continue

        try:
            # Page 1
            page1 = await gg.get_profile_posts(url, page=1)
            posts = page1.get("data", [])

            # Page 2 if pagination token exists
            token = page1.get("pagination_token")
            if token:
                page2 = await gg.get_profile_posts(url, page=2, pagination_token=token)
                posts.extend(page2.get("data", []))

            # Insert into Supabase
            for post in posts:
                db.insert_audit_linkedin_post(audit_id, url, name, post)

            all_posts.extend(posts)
        except Exception as e:
            logger.warning(f"Step 5: Failed to get posts for {name}: {e}")

    logger.info(f"Step 5: Collected {len(all_posts)} posts from {len(current_execs)} executives")
    return all_posts


# --- Main node function ---

async def ghost_genius_node(state: AuditState) -> dict:
    """Ghost Genius technical node — 5 sequential sub-steps.

    On any critical failure: returns ghost_genius_available=False.
    """
    domain = state["domain"]
    company_name = state["company_name"]
    audit_id = state["audit_report_id"]
    deal_id = state["deal_id"]

    # Reset account rotation for this run
    gg.reset_rotation()

    try:
        # Step 1: Domain → LinkedIn Company ID
        linkedin_company_id, linkedin_company_url = await _step1_resolve_company(
            domain, company_name, audit_id
        )

        if not linkedin_company_id:
            logger.warning("Ghost Genius: Could not resolve company, entering degraded mode")
            db.update_audit_report(audit_id, {"ghost_genius_available": False})
            return {
                "linkedin_company_id": None,
                "linkedin_company_url": None,
                "ghost_genius_available": False,
                "ghost_genius_employees_growth": None,
                "ghost_genius_executives": None,
                "ghost_genius_posts": None,
                "node_errors": {"ghost_genius": "Could not resolve LinkedIn company ID"},
            }

        # Store LinkedIn info in audit report
        db.update_audit_report(audit_id, {
            "linkedin_company_id": linkedin_company_id,
            "linkedin_company_url": linkedin_company_url,
        })

        # Step 2: Employees Growth
        growth = _step2_employees_growth(domain)
        if growth is None:
            try:
                growth = await gg.get_employees_growth(linkedin_company_url)
                # Cache in enriched_companies
                db.update_enriched_companies_growth(domain, growth)
            except Exception as e:
                logger.warning(f"Step 2 failed: {e}")
                growth = {}

        # Step 3: Search C-levels
        executives = await _step3_search_executives(
            linkedin_company_id, audit_id, deal_id, domain
        )

        # Step 4: Enrich profiles
        executives = await _step4_enrich_profiles(executives)

        # Step 5: LinkedIn posts
        posts = await _step5_linkedin_posts(executives, audit_id)

        db.update_audit_report(audit_id, {"ghost_genius_available": True})

        return {
            "linkedin_company_id": linkedin_company_id,
            "linkedin_company_url": linkedin_company_url,
            "ghost_genius_available": True,
            "ghost_genius_employees_growth": growth,
            "ghost_genius_executives": executives,
            "ghost_genius_posts": posts,
        }

    except RuntimeError as e:
        # All accounts rate-limited
        logger.error(f"Ghost Genius node failed: {e}")
        db.update_audit_report(audit_id, {"ghost_genius_available": False})
        return {
            "linkedin_company_id": None,
            "linkedin_company_url": None,
            "ghost_genius_available": False,
            "ghost_genius_employees_growth": None,
            "ghost_genius_executives": None,
            "ghost_genius_posts": None,
            "node_errors": {"ghost_genius": str(e)},
        }
    except Exception as e:
        logger.error(f"Ghost Genius node unexpected error: {e}")
        db.update_audit_report(audit_id, {"ghost_genius_available": False})
        return {
            "linkedin_company_id": None,
            "linkedin_company_url": None,
            "ghost_genius_available": False,
            "ghost_genius_employees_growth": None,
            "ghost_genius_executives": None,
            "ghost_genius_posts": None,
            "node_errors": {"ghost_genius": str(e)},
        }
