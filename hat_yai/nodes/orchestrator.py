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


async def orchestrator_node(state: AuditState) -> dict:
    """Create audit report row and initialize state."""
    deal_id = state["deal_id"]
    stage_id = state["stage_id"]
    company_name = state["company_name"]
    domain = state["domain"]

    logger.info(f"Starting audit for {company_name} ({domain}), deal={deal_id}, stage={stage_id}")

    report_id = db.create_audit_report(
        deal_id=deal_id,
        stage_id=stage_id,
        company_name=company_name,
        domain=domain,
    )

    logger.info(f"Created audit report {report_id}")

    return {
        "audit_report_id": report_id,
        "ghost_genius_available": False,
        "agent_reports": [],
        "node_errors": {},
    }
