"""Supabase database operations.

Plain async functions (NOT LangChain @tool). Called directly by nodes.
Spec reference: Section 8 + Section 9.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from supabase import create_client, Client

from hat_yai.config import settings

logger = logging.getLogger(__name__)


def _get_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_anon_key)


# --- enriched_companies (existing table, read + update growth) ---

def read_enriched_company(domain: str) -> Optional[dict]:
    """SELECT from enriched_companies WHERE domain LIKE '%{domain}%'"""
    client = _get_client()
    result = client.table("enriched_companies").select("*").ilike("domain", f"%{domain}%").limit(1).execute()
    return result.data[0] if result.data else None


def update_enriched_companies_growth(domain: str, growth_data: dict) -> None:
    """UPDATE enriched_companies SET employees_growth (JSONB) WHERE domain LIKE '%{domain}%'"""
    client = _get_client()
    client.table("enriched_companies").update({
        "employees_growth": growth_data,
    }).ilike("domain", f"%{domain}%").execute()


# --- enriched_contacts (existing table, read only) ---

def read_enriched_contact(linkedin_private_url: str) -> Optional[dict]:
    """SELECT from enriched_contacts WHERE linkedin_private_url = '{url}'"""
    client = _get_client()
    result = (
        client.table("enriched_contacts")
        .select("*")
        .eq("linkedin_private_url", linkedin_private_url)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def is_contact_fresh(contact: dict, max_age_days: int = 100) -> bool:
    """Check if enriched_contact is fresh enough (< max_age_days old)."""
    updated_at = contact.get("updated_at")
    if not updated_at:
        return False
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return updated_at > cutoff


# --- Supabase Edge Function: enrich ---

async def call_enrich_function(linkedin_url: str) -> bool:
    """POST to Supabase Edge Function /enrich.
    Returns True if call succeeded, False otherwise."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                settings.supabase_enrich_url,
                json={"contact_linkedin_url": linkedin_url},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.supabase_anon_key}",
                },
            )
            return resp.status_code < 400
        except Exception as e:
            logger.error(f"Enrich function failed for {linkedin_url}: {e}")
            return False


# --- ai_agent_company_audit_reports ---

def create_audit_report(
    deal_id: str,
    stage_id: str,
    company_name: str,
    domain: str,
) -> str:
    """INSERT into ai_agent_company_audit_reports. Returns the report UUID."""
    client = _get_client()
    result = client.table("ai_agent_company_audit_reports").insert({
        "deal_id": deal_id,
        "stage_id": stage_id,
        "company_name": company_name,
        "domain": domain,
        "status": "running",
    }).execute()
    return result.data[0]["id"]


def update_audit_report(report_id: str, updates: dict) -> None:
    """UPDATE ai_agent_company_audit_reports by id."""
    client = _get_client()
    client.table("ai_agent_company_audit_reports").update(updates).eq("id", report_id).execute()


# --- ai_agent_company_audit_executives ---

def insert_audit_executive(audit_id: str, deal_id: str, domain: str, exec_data: dict) -> str:
    """INSERT into ai_agent_company_audit_executives. Returns the executive UUID."""
    client = _get_client()
    result = client.table("ai_agent_company_audit_executives").insert({
        "audit_id": audit_id,
        "deal_id": deal_id,
        "domain": domain,
        "linkedin_private_url": exec_data.get("url"),
        "linkedin_profile_url": exec_data.get("url"),
        "full_name": exec_data.get("full_name"),
        "headline": exec_data.get("headline"),
        "is_current_employee": exec_data.get("is_current_employee", True),
        "enrichment_status": "pending",
    }).execute()
    return result.data[0]["id"]


def update_audit_executive(executive_id: str, updates: dict) -> None:
    """UPDATE ai_agent_company_audit_executives by id."""
    client = _get_client()
    client.table("ai_agent_company_audit_executives").update(updates).eq("id", executive_id).execute()


def read_audit_executives(audit_id: str) -> list[dict]:
    """SELECT all executives for a given audit."""
    client = _get_client()
    result = client.table("ai_agent_company_audit_executives").select("*").eq("audit_id", audit_id).execute()
    return result.data


# --- ai_agent_company_audit_linkedin_posts ---

def insert_audit_linkedin_post(audit_id: str, linkedin_private_url: str, full_name: str, post: dict) -> None:
    """INSERT a LinkedIn post into ai_agent_company_audit_linkedin_posts."""
    client = _get_client()
    client.table("ai_agent_company_audit_linkedin_posts").insert({
        "audit_id": audit_id,
        "linkedin_private_url": linkedin_private_url,
        "full_name": full_name,
        "post_id": post.get("id"),
        "post_url": post.get("url"),
        "post_text": post.get("text"),
        "published_at": post.get("published_at"),
        "total_reactions": post.get("total_reactions", 0),
        "total_comments": post.get("total_comments", 0),
        "total_reshares": post.get("total_reshares", 0),
        "is_reshare": post.get("is_reshare", False),
    }).execute()


def read_audit_linkedin_posts(audit_id: str) -> list[dict]:
    """SELECT all LinkedIn posts for a given audit."""
    client = _get_client()
    result = client.table("ai_agent_company_audit_linkedin_posts").select("*").eq("audit_id", audit_id).execute()
    return result.data
