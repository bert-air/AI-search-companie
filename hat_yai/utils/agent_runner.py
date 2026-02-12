"""Shared ReAct loop for LLM agents.

All 6 research agents and the scoring/synthesis agents use this runner.
It handles: prompt loading, context building, tool-calling loop, structured output.

Spec reference: Section 6 (output contract), Section 7 (agent specs).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from hat_yai.models import AgentReport
from hat_yai.state import AuditState
from hat_yai.utils.llm import get_llm, get_fast_llm, load_prompt

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
_MAX_TOOL_RESULT_CHARS = 20_000
_MAX_CONTEXT_CHARS = 150_000

# Fields to keep per agent when slimming executive data
_EXEC_BASE_FIELDS = {"full_name", "headline", "current_job_title", "is_current_employee", "url"}
_EXEC_FIELDS_BY_AGENT = {
    "comex_organisation": _EXEC_BASE_FIELDS | {"experiences"},
    "comex_profils": _EXEC_BASE_FIELDS | {"experiences", "skills"},
    "connexions": {"full_name", "headline", "connected_with"},
    "dynamique": _EXEC_BASE_FIELDS,
}

_MAX_EXECS = 25
_MAX_POSTS = 30
_POST_TEXT_LIMIT = 500


def _slim_executive(exec_data: dict, agent_name: str) -> dict:
    """Keep only the fields relevant to the agent. Trim experience descriptions."""
    fields = _EXEC_FIELDS_BY_AGENT.get(agent_name, _EXEC_BASE_FIELDS)
    slim = {k: v for k, v in exec_data.items() if k in fields}

    # Keep only the 3 most recent experiences (list is ordered most recent first)
    if "experiences" in slim and isinstance(slim["experiences"], list):
        slim["experiences"] = slim["experiences"][:3]

    return slim


def _slim_posts(posts: list[dict]) -> list[dict]:
    """Keep top posts by engagement, truncate text."""
    sorted_posts = sorted(posts, key=lambda p: p.get("total_reactions", 0) if isinstance(p, dict) else 0, reverse=True)
    result = []
    for post in sorted_posts[:_MAX_POSTS]:
        if not isinstance(post, dict):
            continue
        # GG API may return text as nested object — ensure we get a string
        raw_text = post.get("text") or post.get("post_text") or ""
        if not isinstance(raw_text, str):
            raw_text = str(raw_text) if raw_text else ""
        slim = {
            "full_name": post.get("full_name", ""),
            "post_text": raw_text[:_POST_TEXT_LIMIT],
            "published_at": post.get("published_at"),
            "total_reactions": post.get("total_reactions", 0),
            "total_comments": post.get("total_comments", 0),
        }
        result.append(slim)
    return result


def _build_context(state: AuditState, agent_name: str, extra_context: Optional[dict] = None) -> str:
    """Build the human message content with company info and relevant GG data."""
    parts = [
        f"# Entreprise à analyser",
        f"- Nom : {state['company_name']}",
        f"- Domaine : {state['domain']}",
        f"- Ghost Genius disponible : {state.get('ghost_genius_available', False)}",
    ]

    # Include GG data for agents that need it
    gg_agents = {"comex_organisation", "comex_profils", "connexions", "dynamique"}
    if agent_name in gg_agents and state.get("ghost_genius_available"):
        if state.get("ghost_genius_executives"):
            execs = [_slim_executive(e, agent_name) for e in state["ghost_genius_executives"][:_MAX_EXECS]]
            parts.append(f"\n## Dirigeants (Ghost Genius)\n```json\n{json.dumps(execs, ensure_ascii=False, indent=2)}\n```")
        if agent_name != "connexions" and state.get("ghost_genius_posts"):
            posts = _slim_posts(state["ghost_genius_posts"])
            parts.append(f"\n## Posts LinkedIn récents\n```json\n{json.dumps(posts, ensure_ascii=False, indent=2)}\n```")
        if state.get("ghost_genius_employees_growth"):
            parts.append(f"\n## Croissance effectifs\n```json\n{json.dumps(state['ghost_genius_employees_growth'], ensure_ascii=False, indent=2)}\n```")

    if extra_context:
        parts.append(f"\n## Contexte additionnel\n```json\n{json.dumps(extra_context, ensure_ascii=False, indent=2)}\n```")

    return "\n".join(parts)


async def run_agent(
    state: AuditState,
    agent_name: str,
    tools: Optional[list] = None,
    extra_context: Optional[dict] = None,
) -> dict:
    """Run an LLM agent with optional tool-calling loop.

    Returns a dict with `agent_reports` key containing the AgentReport.
    On failure, returns a degraded report.
    """
    try:
        system_prompt = load_prompt(agent_name)
        context = _build_context(state, agent_name, extra_context)

        llm = get_llm(max_tokens=8192)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=context),
        ]

        # Tool-calling loop
        for iteration in range(MAX_TOOL_ITERATIONS):
            # Context size guard — stop if accumulated context is too large
            ctx_size = _estimate_context_chars(messages)
            if ctx_size > _MAX_CONTEXT_CHARS:
                logger.warning(f"Agent {agent_name}: context size {ctx_size} exceeds limit, stopping tool loop")
                break

            if tools:
                response = await llm.bind_tools(tools).ainvoke(messages)
            else:
                response = await llm.ainvoke(messages)

            messages.append(response)

            # If the LLM made tool calls, execute them and loop
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tc in response.tool_calls:
                    tool_fn = _find_tool(tc["name"], tools or [])
                    if tool_fn:
                        try:
                            result = tool_fn.invoke(tc["args"])
                        except Exception as e:
                            result = f"Error: {e}"
                    else:
                        result = f"Error: unknown tool {tc['name']}"

                    # Truncate oversized tool results
                    result_str = str(result)
                    if len(result_str) > _MAX_TOOL_RESULT_CHARS:
                        result_str = result_str[:_MAX_TOOL_RESULT_CHARS] + "\n\n[… résultat tronqué]"

                    messages.append(ToolMessage(
                        content=result_str,
                        tool_call_id=tc["id"],
                    ))
                continue

            # No tool calls — agent is done
            break

        # Two-step extraction:
        # Step A — ask the LLM to produce a concise analysis with explicit signal verdicts
        signals_section = _extract_signals_section(system_prompt)
        analysis_prompt = (
            "Résume ton analyse en 2000 mots max. Pour chaque signal ci-dessous, "
            "indique explicitement : signal_id → DETECTED / NOT_DETECTED / UNKNOWN + justification courte.\n"
            "Base-toi sur TOUTES les données (contexte fourni + résultats web). "
            "Ne mets UNKNOWN que si tu n'as vraiment aucune donnée pertinente.\n\n"
        )
        if signals_section:
            analysis_prompt += signals_section + "\n"

        analysis_response = await llm.ainvoke(messages + [HumanMessage(content=analysis_prompt)])
        analysis_text = analysis_response.content if isinstance(analysis_response.content, str) else str(analysis_response.content)
        logger.info(f"Agent {agent_name}: analysis step produced {len(analysis_text)} chars")

        # Step B — extract structured output from the concise analysis only
        signal_ids = _extract_signal_ids(system_prompt)
        extraction_llm = get_fast_llm(max_tokens=4096).with_structured_output(AgentReport)
        extraction_instruction = (
            "Convertis l'analyse ci-dessous en AgentReport JSON structuré.\n\n"
            "RÈGLES CRITIQUES pour le champ signals :\n"
        )
        if signal_ids:
            extraction_instruction += (
                f"- Tu DOIS inclure exactement ces signal_id : {signal_ids}\n"
                "- Pour chaque signal, extrais le status (DETECTED/NOT_DETECTED/UNKNOWN), "
                "l'evidence et la confidence depuis l'analyse.\n"
                "- Ne mets UNKNOWN que si l'analyse ne contient AUCUNE information sur ce signal.\n"
            )
        else:
            extraction_instruction += "- Cet agent n'émet aucun signal. Le champ signals doit être [].\n"
        extraction_instruction += f"\n---\n\n{analysis_text}"

        extraction_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=extraction_instruction),
        ]
        report: AgentReport = await extraction_llm.ainvoke(extraction_messages)
        report.agent_name = agent_name

        return {"agent_reports": [report.model_dump()]}

    except Exception as e:
        logger.error(f"Agent {agent_name} failed: {e}", exc_info=True)
        fallback = AgentReport(
            agent_name=agent_name,
            data_quality={"sources_count": 0, "ghost_genius_available": False, "confidence_overall": "low"},
        )
        return {
            "agent_reports": [fallback.model_dump()],
            "node_errors": {agent_name: str(e)},
        }


def _extract_signals_section(prompt: str) -> str:
    """Extract the signals table section from an agent's system prompt."""
    match = re.search(r"(## Signaux à émettre.*?)(?=\n## |\Z)", prompt, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_signal_ids(prompt: str) -> list[str]:
    """Extract signal_id values from the signals table in the prompt."""
    signals_section = _extract_signals_section(prompt)
    if not signals_section:
        return []
    # Match backtick-wrapped signal_ids in markdown table rows (preceded by |)
    return re.findall(r"\|\s*`(\w+)`", signals_section)


def _estimate_context_chars(messages: list) -> int:
    """Rough estimate of total context size in characters."""
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block))
    return total


def _find_tool(name: str, tools: list):
    """Find a tool by name in the tools list."""
    for tool in tools:
        if tool.name == name:
            return tool
    return None
