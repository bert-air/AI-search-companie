"""Shared ReAct loop for LLM agents.

All 6 research agents and the scoring/synthesis agents use this runner.
It handles: prompt loading, context building, tool-calling loop, structured output.

Spec reference: Section 6 (output contract), Section 7 (agent specs).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from hat_yai.models import AgentReport
from hat_yai.state import AuditState
from hat_yai.utils.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10


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
            parts.append(f"\n## Dirigeants (Ghost Genius)\n```json\n{json.dumps(state['ghost_genius_executives'], ensure_ascii=False, indent=2)}\n```")
        if state.get("ghost_genius_posts"):
            parts.append(f"\n## Posts LinkedIn récents\n```json\n{json.dumps(state['ghost_genius_posts'], ensure_ascii=False, indent=2)}\n```")
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

                    messages.append(ToolMessage(
                        content=str(result),
                        tool_call_id=tc["id"],
                    ))
                continue

            # No tool calls — agent is done
            break

        # Extract structured output
        extraction_llm = get_llm(max_tokens=4096).with_structured_output(AgentReport)
        extraction_messages = messages + [
            HumanMessage(content="Maintenant, produis ton rapport final au format JSON structuré AgentReport."),
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


def _find_tool(name: str, tools: list):
    """Find a tool by name in the tools list."""
    for tool in tools:
        if tool.name == name:
            return tool
    return None
