"""REDUCE node — consolidate MAP lot results into a single enriched JSON.

Merges all lots, deduplicates, builds org chart, pre-detects signals.

Spec reference: Section 4.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from langchain_core.messages import SystemMessage, HumanMessage

from hat_yai.models_mapreduce import ConsolidatedLinkedIn
from hat_yai.state import AuditState
from hat_yai.utils.llm import get_llm, get_fast_llm, load_prompt_template

logger = logging.getLogger(__name__)

_LOTS_THRESHOLD_FOR_OPUS = 4  # Use Opus if > this many lots


def _empty_consolidated(company_name: str) -> dict:
    """Return a minimal consolidated result for degraded mode."""
    return ConsolidatedLinkedIn(
        company_name=company_name,
        extraction_date=date.today().isoformat(),
    ).model_dump()


async def reduce_node(state: AuditState) -> dict:
    """REDUCE: consolidate all MAP lot results into one JSON."""
    lot_results = state.get("map_lot_results") or []
    company_name = state["company_name"]

    if not lot_results:
        logger.info("REDUCE: No MAP results to consolidate")
        return {"consolidated_linkedin": _empty_consolidated(company_name)}

    total_lots = len(lot_results)

    # Build prompt
    prompt = load_prompt_template(
        "reduce",
        company_name=company_name,
        total_lots=str(total_lots),
    )

    # Build context: all lot JSONs + growth data
    context_parts = [
        f"# Extractions LinkedIn — {total_lots} lots\n",
    ]
    for lot in lot_results:
        lot_num = lot.get("lot_number", "?")
        context_parts.append(f"## Lot {lot_num}")
        context_parts.append(f"```json\n{json.dumps(lot, ensure_ascii=False, indent=2)}\n```\n")

    # Inject employee growth data (not part of MAP, comes from GG directly)
    growth = state.get("ghost_genius_employees_growth")
    if growth:
        context_parts.append("## Données de croissance effectifs (source LinkedIn)")
        context_parts.append(f"```json\n{json.dumps(growth, ensure_ascii=False, indent=2)}\n```\n")
        context_parts.append(
            "Intègre ces données dans le champ `croissance_effectifs` du JSON consolidé."
        )

    context = "\n".join(context_parts)

    # Model selection: Sonnet for <=4 lots, Opus for >4
    if total_lots > _LOTS_THRESHOLD_FOR_OPUS:
        logger.info(f"REDUCE: {total_lots} lots > {_LOTS_THRESHOLD_FOR_OPUS}, using Opus")
        llm = get_llm(max_tokens=8192)
    else:
        llm = get_fast_llm(max_tokens=8192)

    structured_llm = llm.with_structured_output(ConsolidatedLinkedIn)

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=context),
    ]

    try:
        result = await structured_llm.ainvoke(messages)
    except Exception as e:
        logger.error(f"REDUCE: Structured output failed: {e}, retrying with Opus")
        llm = get_llm(max_tokens=8192)
        structured_llm = llm.with_structured_output(ConsolidatedLinkedIn)
        result = await structured_llm.ainvoke(messages)

    consolidated = result.model_dump()

    # --- Post-LLM: merge posts in pure Python (LLM output budget too small) ---
    # Posts don't need LLM consolidation, just merging and deduplication
    all_posts = []
    seen_posts: set[str] = set()
    for lot in lot_results:
        for post in lot.get("posts_pertinents") or []:
            # Dedupe by (auteur, date, first 100 chars of text)
            key = (
                post.get("auteur", ""),
                post.get("date", ""),
                (post.get("texte_integral") or "")[:100],
            )
            key_str = str(key)
            if key_str not in seen_posts:
                seen_posts.add(key_str)
                all_posts.append(post)

    # Replace LLM's (likely truncated) posts with the complete merged set
    consolidated["posts_pertinents"] = all_posts

    # Ensure growth data is included even if LLM missed it
    if growth and not consolidated.get("croissance_effectifs"):
        consolidated["croissance_effectifs"] = growth

    # Ensure metadata
    consolidated["extraction_date"] = consolidated.get("extraction_date") or date.today().isoformat()
    consolidated["lots_fusionnes"] = total_lots

    logger.info(
        f"REDUCE: Consolidated {consolidated.get('profils_total', 0)} profiles, "
        f"{consolidated.get('profils_c_level', 0)} C-levels, "
        f"{len(all_posts)} posts, "
        f"{len(consolidated.get('signaux_pre_detectes', []))} pre-signals"
    )

    return {"consolidated_linkedin": consolidated}
