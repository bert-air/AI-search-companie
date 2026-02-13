"""LangGraph state definition with reducers for parallel execution."""

from __future__ import annotations

import operator
from typing import Annotated, Optional
from typing_extensions import TypedDict


def merge_dicts(left: dict, right: dict) -> dict:
    """Reducer that merges two dicts. Used for node_errors so parallel
    nodes can each report errors without overwriting each other."""
    merged = left.copy()
    merged.update(right)
    return merged


class AuditState(TypedDict):
    # -- Webhook input (set once by orchestrator) --
    deal_id: str
    stage_id: str
    company_name: str
    domain: str
    country: str  # default "France", used for Sales Navigator region filter

    # -- Optional sales team for connexions matching --
    sales_team: Optional[list[dict]]

    # -- Audit report row ID (set once by orchestrator) --
    audit_report_id: str

    # -- Ghost Genius data (set by ghost_genius_node, single writer) --
    linkedin_company_id: Optional[str]
    linkedin_company_url: Optional[str]
    ghost_genius_available: bool
    ghost_genius_employees_growth: Optional[dict]
    ghost_genius_executives: Optional[list]
    ghost_genius_posts: Optional[list]

    # -- Agent reports (PARALLEL — operator.add reducer) --
    agent_reports: Annotated[list[dict], operator.add]

    # -- Scoring & Synthesis (set sequentially) --
    scoring_result: Optional[dict]
    final_report: Optional[str]

    # -- Error tracking (PARALLEL — merge_dicts reducer) --
    node_errors: Annotated[dict, merge_dicts]

    # -- Final status --
    final_status: Optional[str]
