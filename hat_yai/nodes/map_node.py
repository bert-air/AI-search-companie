"""MAP node — parallel LinkedIn profile extraction by lots.

Batches raw GG profiles into lots of 10, calls Sonnet in parallel
to extract structured JSON per lot (MapLotResult).

Spec reference: Section 3.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from hat_yai.models_mapreduce import MapLotResult
from hat_yai.state import AuditState
from hat_yai.utils.llm import get_fast_llm, load_prompt_template

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10
_MAX_TOKENS_PER_LOT = 250_000  # Safety: split lot if estimated tokens exceed this
_CHARS_PER_TOKEN = 4  # Rough estimate for token counting


def _pair_posts_to_profiles(
    executives: list[dict],
    posts: list[dict],
) -> list[dict]:
    """Attach each post to its author profile by full_name matching."""
    posts_by_name: dict[str, list[dict]] = {}
    for post in posts:
        name = (post.get("full_name") or "").strip().lower()
        if name:
            posts_by_name.setdefault(name, []).append(post)

    result = []
    for exec_data in executives:
        name = (exec_data.get("full_name") or "").strip().lower()
        profile = {**exec_data, "_posts": posts_by_name.get(name, [])}
        result.append(profile)
    return result


def create_batches(profiles: list[dict], batch_size: int = _BATCH_SIZE) -> list[list[dict]]:
    """Split profiles into lots. If a lot is too large, split further."""
    batches = []
    for i in range(0, len(profiles), batch_size):
        batch = profiles[i : i + batch_size]
        # Safety: estimate token size and split if too large
        estimated_chars = len(json.dumps(batch, ensure_ascii=False))
        estimated_tokens = estimated_chars // _CHARS_PER_TOKEN
        if estimated_tokens > _MAX_TOKENS_PER_LOT and len(batch) > 1:
            mid = len(batch) // 2
            batches.append(batch[:mid])
            batches.append(batch[mid:])
        else:
            batches.append(batch)
    return batches


async def _process_batch(
    batch: list[dict],
    lot_number: int,
    total_lots: int,
    company_name: str,
) -> Optional[MapLotResult]:
    """Process a single batch through Sonnet with structured output."""
    prompt = load_prompt_template(
        "map",
        lot_number=str(lot_number),
        total_lots=str(total_lots),
        batch_size=str(len(batch)),
        company_name=company_name,
    )

    # Build context: profiles + their posts
    context_parts = []
    for profile in batch:
        posts = profile.pop("_posts", [])
        context_parts.append(f"## Profil: {profile.get('full_name', 'Inconnu')}")
        context_parts.append(f"```json\n{json.dumps(profile, ensure_ascii=False, indent=2)}\n```")
        if posts:
            context_parts.append(f"### Posts LinkedIn ({len(posts)} posts)")
            for post in posts:
                text = post.get("text") or post.get("post_text") or ""
                if not isinstance(text, str):
                    text = str(text) if text else ""
                context_parts.append(
                    f"- [{post.get('published_at', '?')}] "
                    f"({post.get('total_reactions', 0)} reactions) "
                    f"{text}"
                )
        context_parts.append("")

    context = "\n".join(context_parts)

    llm = get_fast_llm(max_tokens=8192)
    structured_llm = llm.with_structured_output(MapLotResult)

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=context),
    ]

    result = await structured_llm.ainvoke(messages)
    return result


async def map_node(state: AuditState) -> dict:
    """MAP: batch profiles and extract structured data in parallel."""
    if not state.get("linkedin_available"):
        logger.info("MAP: LinkedIn data not available, skipping")
        return {"map_lot_results": []}

    executives = state.get("linkedin_executives") or []
    posts = state.get("linkedin_posts") or []

    if not executives:
        logger.info("MAP: No executives found, skipping")
        return {"map_lot_results": []}

    company_name = state["company_name"]

    # Pair posts to profiles
    profiles = _pair_posts_to_profiles(executives, posts)

    # Create batches
    batches = create_batches(profiles)
    total_lots = len(batches)

    logger.info(
        f"MAP: {len(profiles)} profiles -> {total_lots} lots "
        f"(batch_size={_BATCH_SIZE})"
    )

    # Process all lots in parallel
    tasks = [
        _process_batch(batch, i + 1, total_lots, company_name)
        for i, batch in enumerate(batches)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect successful results
    lot_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"MAP: Lot {i + 1}/{total_lots} failed: {result}")
        elif result is not None:
            lot_results.append(result.model_dump())
        else:
            logger.warning(f"MAP: Lot {i + 1}/{total_lots} returned None")

    logger.info(
        f"MAP: {len(lot_results)}/{total_lots} lots completed successfully"
    )

    return {"map_lot_results": lot_results}
