"""Orchestrator — entry point node.

Receives webhook payload (already in state from graph invocation).
Creates the audit report row in Supabase with status='running'.
Initializes state for downstream nodes.

Spec reference: Section 4 (flow d'execution).
"""

from __future__ import annotations

import logging

from hat_yai.state import AuditState
from hat_yai.tools import supabase_db as db

logger = logging.getLogger(__name__)


def _derive_company_name(domain: str) -> str:
    """Derive a human-readable company name from a clean domain.

    'saint-gobain.com' → 'Saint-Gobain'
    'acme.co.uk'       → 'Acme'
    """
    # Remove TLD(s): .com, .fr, .co.uk, etc.
    name = domain.split('.')[0]
    # Convert hyphens to spaces and title-case, then restore hyphens for names like Saint-Gobain
    return '-'.join(w.capitalize() for w in name.split('-'))


async def orchestrator_node(state: AuditState) -> dict:
    """Create audit report row and initialize state."""
    deal_id = state["deal_id"]
    stage_id = state["stage_id"]
    raw_domain = state["domain"]

    # Clean domain: strip ext., www., protocol, paths
    domain = db.clean_domain(raw_domain)
    if domain != raw_domain:
        logger.info(f"Domain normalized: {raw_domain} → {domain}")

    # Derive company name from domain (more reliable than deal title)
    company_name = _derive_company_name(domain)

    logger.info(f"Starting audit for {company_name} ({domain}), deal={deal_id}, stage={stage_id}")

    report_id = db.create_audit_report(
        deal_id=deal_id,
        stage_id=stage_id,
        company_name=company_name,
        domain=domain,
    )

    logger.info(f"Created audit report {report_id}")

    # Default country to France if not provided
    country = state.get("country") or "France"

    return {
        "audit_report_id": report_id,
        "domain": domain,
        "company_name": company_name,
        "country": country,
        "linkedin_available": False,
        "agent_reports": [],
        "node_errors": {},
    }
