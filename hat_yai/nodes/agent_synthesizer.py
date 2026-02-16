"""Agent Synthétiseur — compile all reports into a markdown executive summary.

Also handles the 3 output destinations: Supabase, HubSpot, Slack.
Spec reference: Section 7.8.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import SystemMessage, HumanMessage

from hat_yai.state import AuditState
from hat_yai.tools import supabase_db as db
from hat_yai.tools.hubspot import create_deal_note
from hat_yai.tools.slack import send_slack_notification
from hat_yai.utils.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)


async def agent_synthesizer_node(state: AuditState) -> dict:
    """Generate markdown report and push to 3 destinations."""
    audit_id = state["audit_report_id"]
    deal_id = state["deal_id"]
    company_name = state["company_name"]
    agent_reports = state.get("agent_reports", [])
    scoring = state.get("scoring_result", {})

    # --- Generate markdown report via LLM ---
    system_prompt = load_prompt("synthesizer")
    context_parts = [
        "# Rapports des agents\n",
        json.dumps(agent_reports, ensure_ascii=False, indent=2),
        "\n# Scoring\n",
        json.dumps(scoring, ensure_ascii=False, indent=2),
    ]

    llm = get_llm(max_tokens=8192)
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n".join(context_parts)),
    ])
    final_report = response.content

    # --- Determine final status ---
    has_errors = bool(state.get("node_errors"))
    final_status = "completed" if not has_errors else "completed_with_errors"

    # --- Build per-agent report fields for Supabase ---
    report_updates: dict = {
        "final_report": final_report,
        "scoring_signals": scoring.get("scoring_signals"),
        "score_total": scoring.get("score_total"),
        "score_max": scoring.get("score_max", 330),
        "data_quality_score": scoring.get("data_quality_score"),
        "verdict": scoring.get("verdict"),
        "status": final_status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Store MAP/REDUCE consolidated output for debug/replay
    consolidated = state.get("consolidated_linkedin")
    if consolidated:
        report_updates["consolidated_linkedin"] = consolidated

    # Store individual agent reports
    for report in agent_reports:
        name = report.get("agent_name", "")
        if name:
            report_updates[f"report_{name}"] = report

    # Store prompts used for this run (reproducibility / versioning)
    _AGENT_NAMES = ["finance", "entreprise", "dynamique", "comex_organisation", "comex_profils", "connexions", "scoring"]
    for agent_name in _AGENT_NAMES:
        try:
            report_updates[f"prompt_{agent_name}"] = load_prompt(agent_name)
        except Exception:
            logger.warning(f"Could not load prompt for {agent_name}")

    # Store MAP/REDUCE prompts for reproducibility
    for prompt_name in ("map", "reduce"):
        try:
            report_updates[f"prompt_{prompt_name}"] = load_prompt(prompt_name)
        except Exception:
            pass
    report_updates["prompt_synthesizer"] = system_prompt

    # Also store agent inputs for debug/replay
    report_updates["input_finance"] = {"company_name": company_name, "domain": state["domain"]}
    report_updates["input_entreprise"] = {"company_name": company_name, "domain": state["domain"]}
    report_updates["input_dynamique"] = {
        "company_name": company_name,
        "domain": state["domain"],
        "ghost_genius_available": state.get("ghost_genius_available"),
    }
    report_updates["input_comex_organisation"] = {
        "company_name": company_name,
        "domain": state["domain"],
        "ghost_genius_available": state.get("ghost_genius_available"),
    }
    report_updates["input_comex_profils"] = {
        "company_name": company_name,
        "domain": state["domain"],
        "ghost_genius_available": state.get("ghost_genius_available"),
    }
    report_updates["input_connexions"] = {
        "company_name": company_name,
        "domain": state["domain"],
        "ghost_genius_available": state.get("ghost_genius_available"),
    }

    # --- Output 1: Supabase ---
    try:
        db.update_audit_report(audit_id, report_updates)
        logger.info(f"Supabase: Updated audit report {audit_id}")
    except Exception as e:
        logger.error(f"Supabase update failed: {e}")

    # --- Output 2: HubSpot note ---
    try:
        await create_deal_note(deal_id, final_report)
        logger.info(f"HubSpot: Created note on deal {deal_id}")
    except Exception as e:
        logger.error(f"HubSpot note creation failed: {e}")

    # --- Output 3: Slack notification ---
    try:
        await send_slack_notification(
            company_name=company_name,
            score_total=scoring.get("score_total", 0),
            data_quality_score=scoring.get("data_quality_score", 0),
            deal_id=deal_id,
            status=final_status,
        )
        logger.info(f"Slack: Notification sent for {company_name}")
    except Exception as e:
        logger.error(f"Slack notification failed: {e}")

    return {
        "final_report": final_report,
        "final_status": final_status,
    }
