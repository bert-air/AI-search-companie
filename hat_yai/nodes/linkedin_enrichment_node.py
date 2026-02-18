"""LinkedIn Enrichment — Technical node (no LLM).

5 sequential sub-steps of HTTP API calls with Supabase read/write.
On failure: sets linkedin_available=False so downstream agents operate in degraded mode.

Spec reference: Section 5.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from hat_yai.config import LINKEDIN_REGION_IDS, TITLE_SEARCH_KEYWORDS
from hat_yai.state import AuditState
from hat_yai.tools import ghost_genius as gg
from hat_yai.tools import evaboot
from hat_yai.tools import supabase_db as db
from hat_yai.tools import unipile
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
    company = db.read_enriched_company(domain, company_name)
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


def _is_growth_useful(growth: Optional[dict]) -> bool:
    """Check if growth data has actual values (not just null placeholders)."""
    if not growth or not isinstance(growth, dict):
        return False
    return growth.get("growth_1_year") is not None


def _step2_employees_growth(
    domain: str,
    company_name: str = "",
) -> Optional[dict]:
    """Step 2: Check growth cache in enriched_companies.

    Returns cached growth data from employees_growth JSONB column, else None.
    Rejects 'zombie' cache entries where all values are null.
    """
    company = db.read_enriched_company(domain, company_name)
    if not company:
        return None

    growth = company.get("employees_growth")
    if growth and isinstance(growth, dict) and _is_growth_useful(growth):
        logger.info("Step 2: Using cached growth data from enriched_companies")
        return growth
    return None


async def _step3_search_executives(
    linkedin_company_id: str,
    company_name: str,
    audit_id: str,
    deal_id: str,
    domain: str,
    region_id: str = "",
    region_name: str = "",
) -> list[dict]:
    """Step 3: Search executives via Sales Navigator.

    Two search passes:
      3a) Seniority-based (current + past, CXO/VP/Owner/Director)
      3b) Keyword-based (current only, title keywords like PMO, manager IT, etc.)

    Priority: Evaboot → Unipile → Ghost Genius.
    Both passes use region filter. Results are merged and deduped, cap at 50.
    """
    # --- 3a: Seniority search (current + past) ---
    # Cascade on both exceptions AND empty results (Evaboot returns [] on 429)
    current, past = [], []

    try:
        current, past = await evaboot.search_executives(
            linkedin_company_id, company_name, region_id, region_name,
        )
        if current or past:
            logger.info(f"Step 3a: Evaboot seniority search: {len(current)} current, {len(past)} past")
        else:
            logger.warning("Step 3a: Evaboot returned empty results")
    except Exception as e:
        logger.warning(f"Step 3a: Evaboot failed ({e})")

    if not current and not past:
        try:
            current, past = await unipile.search_executives(
                linkedin_company_id, company_name, region_id, region_name,
            )
            if current or past:
                logger.info(f"Step 3a: Unipile seniority search: {len(current)} current, {len(past)} past")
            else:
                logger.warning("Step 3a: Unipile returned empty results")
        except Exception as e:
            logger.warning(f"Step 3a: Unipile failed ({e})")

    if not current and not past:
        try:
            current = await gg.search_executives_current(linkedin_company_id, locations=region_id)
            past = await gg.search_executives_past(linkedin_company_id, locations=region_id)
            if current or past:
                logger.info(f"Step 3a: Ghost Genius fallback: {len(current)} current, {len(past)} past")
            else:
                logger.warning("Step 3a: Ghost Genius returned empty results")
        except Exception as e:
            logger.error(f"Step 3a: All 3 APIs failed ({e}), no executives found")

    # --- 3b: Keyword search (current only) ---
    # Same cascade logic: try next API if results are empty
    keyword_results: list[dict] = []

    try:
        keyword_results = await evaboot.search_executives_by_keywords(
            linkedin_company_id, company_name, TITLE_SEARCH_KEYWORDS, region_id, region_name,
        )
        if keyword_results:
            logger.info(f"Step 3b: Evaboot keyword search found {len(keyword_results)} profiles")
        else:
            logger.warning("Step 3b: Evaboot keywords returned empty results")
    except Exception as e:
        logger.warning(f"Step 3b: Evaboot keywords failed ({e})")

    if not keyword_results:
        try:
            keyword_results = await unipile.search_executives_by_keywords(
                linkedin_company_id, company_name, TITLE_SEARCH_KEYWORDS, region_id, region_name,
            )
            if keyword_results:
                logger.info(f"Step 3b: Unipile keyword search found {len(keyword_results)} profiles")
            else:
                logger.warning("Step 3b: Unipile keywords returned empty results")
        except Exception as e:
            logger.warning(f"Step 3b: Unipile keywords failed ({e})")

    if not keyword_results:
        try:
            keywords_str = " ".join(TITLE_SEARCH_KEYWORDS)
            keyword_results = await gg.search_executives_by_keywords(
                linkedin_company_id, keywords=keywords_str, locations=region_id,
            )
            if keyword_results:
                logger.info(f"Step 3b: Ghost Genius keyword search found {len(keyword_results)} profiles")
            else:
                logger.warning("Step 3b: Ghost Genius keywords returned empty results")
        except Exception as e:
            logger.error(f"Step 3b: All 3 APIs failed ({e}), no keyword results")

    # --- Merge and deduplicate ---
    seen_ids: set[str] = set()
    current_deduped: list[dict] = []
    past_deduped: list[dict] = []

    # Current seniority results first
    for exec_data in current:
        eid = exec_data.get("id", "")
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            exec_data["is_current_employee"] = True
            current_deduped.append(exec_data)

    # Keyword results (current employees, may overlap with seniority)
    for exec_data in keyword_results:
        eid = exec_data.get("id", "")
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            exec_data["is_current_employee"] = True
            current_deduped.append(exec_data)

    # Past seniority results (former C-levels — important for departure signals)
    for exec_data in past:
        eid = exec_data.get("id", "")
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            exec_data["is_current_employee"] = False
            past_deduped.append(exec_data)

    # Cap at 50: reserve up to 10 slots for former C-levels (departure signals),
    # fill remaining slots with current employees
    _MAX_PAST = 10
    past_kept = past_deduped[:_MAX_PAST]
    current_slots = 50 - len(past_kept)
    deduped = current_deduped[:current_slots] + past_kept

    # Insert into Supabase
    for exec_data in deduped:
        exec_data["_db_id"] = db.insert_audit_executive(audit_id, deal_id, domain, exec_data)

    logger.info(
        f"Step 3: Found {len(deduped)} executives "
        f"({len(current_deduped)} current, {len(past_kept)} past kept / {len(past_deduped)} past total)"
    )
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
            # Call enrich edge function, wait 2s, re-read
            success = await db.call_enrich_function(url)
            await asyncio.sleep(2)

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
    # LinkedIn "About" section — populated when enriched_contacts has linkedin_summary
    about = contact.get("linkedin_summary") or contact.get("about")
    if about:
        exec_data["linkedin_about"] = about


def _contact_to_exec_updates(contact: dict) -> dict:
    """Build update dict for ai_agent_company_audit_executives from enriched_contacts."""
    updates = {
        "full_name": contact.get("full_name"),
        "headline": contact.get("linkedin_headline"),
        "current_job_title": contact.get("linkedin_job_title"),
        "company_name": contact.get("company_name"),
        "experiences": contact.get("experiences"),
        "educations": contact.get("educations"),
        "skills": contact.get("linkedin_skills"),
        "connected_with": contact.get("connected_with"),
    }
    about = contact.get("linkedin_summary") or contact.get("about")
    if about:
        updates["linkedin_about"] = about
    return updates


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

            # Attach author info and insert into Supabase
            for post in posts:
                post["full_name"] = name
                post["linkedin_url"] = url
                db.insert_audit_linkedin_post(audit_id, url, name, post)

            all_posts.extend(posts)
        except Exception as e:
            logger.warning(f"Step 5: Failed to get posts for {name}: {e}")

    logger.info(f"Step 5: Collected {len(all_posts)} posts from {len(current_execs)} executives")
    return all_posts


# --- Main node function ---

async def linkedin_enrichment_node(state: AuditState) -> dict:
    """LinkedIn enrichment node — 5 sequential sub-steps.

    On any critical failure: returns linkedin_available=False.
    """
    domain = state["domain"]
    company_name = state["company_name"]
    audit_id = state["audit_report_id"]
    deal_id = state["deal_id"]
    country = state.get("country") or "France"

    # Resolve country → LinkedIn region ID
    region_id = LINKEDIN_REGION_IDS.get(country, "")
    region_name = country if region_id else ""
    if not region_id:
        logger.warning(f"No LinkedIn region ID for country '{country}', searches will not filter by region")

    # Reset account rotation for this run
    gg.reset_rotation()

    try:
        # Step 1: Domain → LinkedIn Company ID
        # Shortcut: if linkedin_company_url is pre-set in state, resolve from it
        pre_set_url = state.get("linkedin_company_url")
        if pre_set_url:
            logger.info(f"Step 1: Using pre-set LinkedIn URL: {pre_set_url}")
            gg_company = await gg.get_company_by_url(pre_set_url)
            linkedin_company_id = str(gg_company.get("id", ""))
            linkedin_company_url = gg_company.get("url", pre_set_url)
        else:
            linkedin_company_id, linkedin_company_url = await _step1_resolve_company(
                domain, company_name, audit_id
            )

        if not linkedin_company_id:
            logger.warning("LinkedIn enrichment: Could not resolve company, entering degraded mode")
            db.update_audit_report(audit_id, {"linkedin_available": False})
            return {
                "linkedin_company_id": None,
                "linkedin_company_url": None,
                "linkedin_available": False,
                "linkedin_employees_growth": None,
                "linkedin_executives": None,
                "linkedin_posts": None,
                "node_errors": {"linkedin_enrichment": "Could not resolve LinkedIn company ID"},
            }

        # Store LinkedIn info in audit report
        db.update_audit_report(audit_id, {
            "linkedin_company_id": linkedin_company_id,
            "linkedin_company_url": linkedin_company_url,
        })

        # Step 2: Employees Growth — always re-fetch (no cache)
        # Priority: Unipile → Ghost Genius
        growth = {}
        try:
            growth = await unipile.get_employees_growth(linkedin_company_url)
        except Exception as e:
            logger.warning(f"Step 2: Unipile failed: {e}")

        if not _is_growth_useful(growth):
            logger.info("Step 2: Unipile empty, trying Ghost Genius fallback")
            try:
                gg_growth = await gg.get_employees_growth(linkedin_company_url)
                if _is_growth_useful(gg_growth):
                    growth = gg_growth
                    logger.info("Step 2: Ghost Genius fallback succeeded")
            except Exception as e:
                logger.warning(f"Step 2: Ghost Genius fallback also failed: {e}")

        # Step 3: Search executives (seniority + keyword, with region filter)
        executives = await _step3_search_executives(
            linkedin_company_id, company_name, audit_id, deal_id, domain,
            region_id=region_id, region_name=region_name,
        )

        # Step 4: Enrich profiles
        executives = await _step4_enrich_profiles(executives)

        # Step 5: LinkedIn posts
        posts = await _step5_linkedin_posts(executives, audit_id)

        db.update_audit_report(audit_id, {"linkedin_available": True})

        return {
            "linkedin_company_id": linkedin_company_id,
            "linkedin_company_url": linkedin_company_url,
            "linkedin_available": True,
            "linkedin_employees_growth": growth,
            "linkedin_executives": executives,
            "linkedin_posts": posts,
        }

    except RuntimeError as e:
        # All accounts rate-limited
        logger.error(f"LinkedIn enrichment node failed: {e}")
        db.update_audit_report(audit_id, {"linkedin_available": False})
        return {
            "linkedin_company_id": None,
            "linkedin_company_url": None,
            "linkedin_available": False,
            "linkedin_employees_growth": None,
            "linkedin_executives": None,
            "linkedin_posts": None,
            "node_errors": {"linkedin_enrichment": str(e)},
        }
    except Exception as e:
        logger.error(f"LinkedIn enrichment node unexpected error: {e}")
        db.update_audit_report(audit_id, {"linkedin_available": False})
        return {
            "linkedin_company_id": None,
            "linkedin_company_url": None,
            "linkedin_available": False,
            "linkedin_employees_growth": None,
            "linkedin_executives": None,
            "linkedin_posts": None,
            "node_errors": {"linkedin_enrichment": str(e)},
        }
