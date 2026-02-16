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
_TWO_PASS_SUMMARY_MAX_TOKENS = 4096
_EXTRA_CONTEXT_PASS2_LIMIT = 10_000

# Fields to keep per agent when slimming executive data
_EXEC_BASE_FIELDS = {"full_name", "headline", "current_job_title", "is_current_employee", "url"}
_EXEC_FIELDS_BY_AGENT = {
    "comex_organisation": _EXEC_BASE_FIELDS | {"experiences"},
    "comex_profils": _EXEC_BASE_FIELDS | {"experiences", "skills"},
    "connexions": {"full_name", "headline", "connected_with"},
    "dynamique": _EXEC_BASE_FIELDS,
}

_MAX_EXECS = 25
_MAX_POSTS = 80
_POST_TEXT_LIMIT = 500

# Signal-relevant keywords for LinkedIn post filtering.
# Posts matching these keywords keep their full text (500 chars);
# non-matching posts keep only metadata (author, date, reactions).
# See docs/SIGNAL_KEYWORDS.md for the full reference.
_SIGNAL_KEYWORDS = [
    # Transformation / Digital
    "transformation", "transfo", "digitale", "digital", "innovation",
    "modernisation", "dématérialisation", "numérique", "industrie 4.0",
    # IT / DSI / Infrastructure
    "dsi", "cio", "cto", "directeur des systèmes", "directeur informatique",
    "directeur technique", "chief information", "chief technology",
    "erp", "sap", "salesforce", "cloud", "aws", "azure", "gcp",
    "migration", "cybersécurité", "cyber", "infra", "infrastructure",
    "servicenow", "jira", "monday", "planview", "ms project",
    "asana", "sciforma", "triskell",
    "devops", "saas", "data", "ia ", "intelligence artificielle",
    # PMO / Gestion de projets
    "pmo", "bureau de projets", "project management", "program manager",
    "programme manager", "portefeuille de projets", "gestion de projets",
    "chef de projet", "directeur de programme", "roadmap", "feuille de route",
    # Recrutement / RH
    "recrute", "recrutement", "hiring", "rejoindre", "rejoignez",
    "cdi", "embauche", "talent", "onboarding",
    # Stratégie / Plans
    "plan stratégique", "stratégie", "vision", "ambition",
    "plan directeur", "schéma directeur", "cap ", "objectif stratégique",
    # M&A / Restructuration
    "acquisition", "fusion", "rachat", "cession", "m&a",
    "pse", "licenciement", "plan social", "restructuration",
    "réorganisation", "plan de sauvegarde",
    # Finance / Croissance
    "chiffre d'affaires", "croissance", "résultats", "levée de fonds",
    "investissement", "budget it", "budget informatique",
]


def _slim_executive(exec_data: dict, agent_name: str) -> dict:
    """Keep only the fields relevant to the agent. Trim experience descriptions."""
    fields = _EXEC_FIELDS_BY_AGENT.get(agent_name, _EXEC_BASE_FIELDS)
    slim = {k: v for k, v in exec_data.items() if k in fields}

    # Keep only the 3 most recent experiences (list is ordered most recent first)
    if "experiences" in slim and isinstance(slim["experiences"], list):
        slim["experiences"] = slim["experiences"][:3]

    return slim


def _match_signal_keywords(text: str) -> list[str]:
    """Return signal keywords found in text (case-insensitive)."""
    text_lower = text.lower()
    return [kw for kw in _SIGNAL_KEYWORDS if kw in text_lower]


def _slim_posts(posts: list[dict]) -> list[dict]:
    """Filter posts: keep full text for signal-relevant posts, metadata-only for others.

    - Sorted by date (recent first) instead of engagement
    - Capped at _MAX_POSTS (80)
    - Posts matching signal keywords → full text (500 chars) + matched keywords
    - Other posts → metadata only (author, date, reactions)
    """
    # Sort by date (recent first), fallback to empty string
    sorted_posts = sorted(
        (p for p in posts if isinstance(p, dict)),
        key=lambda p: p.get("published_at") or "",
        reverse=True,
    )
    result = []
    for post in sorted_posts[:_MAX_POSTS]:
        # GG API may return text as nested object — ensure we get a string
        raw_text = post.get("text") or post.get("post_text") or ""
        if not isinstance(raw_text, str):
            raw_text = str(raw_text) if raw_text else ""

        slim = {
            "full_name": post.get("full_name", ""),
            "published_at": post.get("published_at"),
            "total_reactions": post.get("total_reactions", 0),
            "total_comments": post.get("total_comments", 0),
        }

        matched = _match_signal_keywords(raw_text)
        if matched:
            slim["post_text"] = raw_text[:_POST_TEXT_LIMIT]
            slim["signal_keywords"] = matched

        result.append(slim)
    return result


def _build_context(state: AuditState, agent_name: str, extra_context: Optional[dict] = None) -> str:
    """Build the human message content with company info and relevant data.

    Prioritizes pre-processed router slices (from MAP/REDUCE pipeline).
    Falls back to raw GG data if router slices are not available.
    """
    parts = [
        f"# Entreprise à analyser",
        f"- Nom : {state['company_name']}",
        f"- Domaine : {state['domain']}",
        f"- Données LinkedIn disponibles : {state.get('ghost_genius_available', False)}",
    ]

    # NEW: Use pre-processed context slice from router if available
    slices = state.get("agent_context_slices")
    if slices and agent_name in slices:
        slice_data = slices[agent_name]
        if slice_data:
            parts.append(
                f"\n## Contexte LinkedIn pré-traité\n```json\n"
                f"{json.dumps(slice_data, ensure_ascii=False, indent=2)}\n```"
            )
    else:
        # LEGACY FALLBACK: raw GG data (when MAP/REDUCE pipeline is bypassed)
        gg_agents = {"comex_organisation", "comex_profils", "connexions", "dynamique"}
        if agent_name in gg_agents and state.get("ghost_genius_available"):
            if state.get("ghost_genius_executives"):
                execs = [_slim_executive(e, agent_name) for e in state["ghost_genius_executives"][:_MAX_EXECS]]
                parts.append(f"\n## Dirigeants LinkedIn\n```json\n{json.dumps(execs, ensure_ascii=False, indent=2)}\n```")
            if agent_name != "connexions" and state.get("ghost_genius_posts"):
                posts = _slim_posts(state["ghost_genius_posts"])
                parts.append(f"\n## Posts LinkedIn récents\n```json\n{json.dumps(posts, ensure_ascii=False, indent=2)}\n```")
            if state.get("ghost_genius_employees_growth"):
                parts.append(f"\n## Croissance effectifs\n```json\n{json.dumps(state['ghost_genius_employees_growth'], ensure_ascii=False, indent=2)}\n```")

    # Include sales team for connexions and comex_profils agents
    if agent_name in ("connexions", "comex_profils") and state.get("sales_team"):
        parts.append(f"\n## Équipe commerciale\n```json\n{json.dumps(state['sales_team'], ensure_ascii=False, indent=2)}\n```")

    if extra_context:
        parts.append(f"\n## Contexte additionnel\n```json\n{json.dumps(extra_context, ensure_ascii=False, indent=2)}\n```")

    return "\n".join(parts)


def _build_pass2_context(
    state: AuditState,
    agent_name: str,
    pass1_summary: str,
    extra_context: Optional[dict] = None,
) -> str:
    """Build slim context for Pass 2: company identity + Pass 1 summary only."""
    parts = [
        "# Entreprise à analyser",
        f"- Nom : {state['company_name']}",
        f"- Domaine : {state['domain']}",
        f"- Données LinkedIn disponibles : {state.get('ghost_genius_available', False)}",
        f"\n## Analyse des données internes (Pass 1)\n{pass1_summary}",
    ]

    if extra_context:
        ctx_str = json.dumps(extra_context, ensure_ascii=False, indent=2)
        if len(ctx_str) > _EXTRA_CONTEXT_PASS2_LIMIT:
            ctx_str = ctx_str[:_EXTRA_CONTEXT_PASS2_LIMIT] + "\n[… tronqué]"
        parts.append(f"\n## Contexte additionnel\n```json\n{ctx_str}\n```")

    return "\n".join(parts)


async def _run_pass1(
    system_prompt: str,
    context: str,
    agent_name: str,
) -> str:
    """Pass 1: analyse data-only (no tools). Returns a structured summary."""
    llm = get_llm(max_tokens=_TWO_PASS_SUMMARY_MAX_TOKENS)

    pass1_instruction = (
        "\n\n---\n\n"
        "PASS 1 — ANALYSE DES DONNÉES STRUCTURÉES\n\n"
        "Tu as accès à toutes les données LinkedIn enrichies (dirigeants, posts LinkedIn, "
        "croissance effectifs). Analyse-les en profondeur.\n\n"
        "Produis un résumé structuré couvrant :\n"
        "1. Tes conclusions principales pour chaque catégorie de ton mandat\n"
        "2. Les signaux que tu peux déjà émettre avec certitude\n"
        "3. Les LACUNES SPÉCIFIQUES : ce que tu n'as PAS trouvé dans les données "
        "et que tu auras besoin de chercher sur le web\n\n"
        "Format : texte structuré en sections, max 3000 mots.\n"
        "Sois exhaustif sur les noms, dates, rôles exacts que tu trouves dans les données.\n"
        "Sois spécifique sur les lacunes (ex: 'budget IT non mentionné', "
        "'stack technique inconnue', 'pas d'info PMO').\n"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context + pass1_instruction),
    ]

    response = await llm.ainvoke(messages)
    summary = response.content if isinstance(response.content, str) else str(response.content)

    logger.info(
        f"Agent {agent_name}: Pass 1 complete, summary={len(summary)} chars "
        f"(input context was {len(context)} chars)"
    )
    return summary


async def run_agent(
    state: AuditState,
    agent_name: str,
    tools: Optional[list] = None,
    extra_context: Optional[dict] = None,
    two_pass: bool = False,
) -> dict:
    """Run an LLM agent with optional tool-calling loop.

    Returns a dict with `agent_reports` key containing the AgentReport.
    On failure, returns a degraded report.
    """
    try:
        system_prompt = load_prompt(agent_name)
        context = _build_context(state, agent_name, extra_context)

        llm = get_llm(max_tokens=8192)

        if two_pass and tools:
            # --- TWO-PASS MODE ---
            # Pass 1: full GG data, no tools → structured summary
            pass1_summary = await _run_pass1(system_prompt, context, agent_name)

            # Pass 2: slim context + summary, WITH tools → web research
            pass2_context = _build_pass2_context(state, agent_name, pass1_summary, extra_context)

            pass2_system = system_prompt + (
                "\n\n---\n"
                "MODE PASS 2 — RECHERCHE WEB COMPLÉMENTAIRE\n\n"
                "Tu as déjà analysé toutes les données internes (voir 'Analyse des données "
                "internes (Pass 1)' dans le contexte). Concentre-toi UNIQUEMENT sur :\n"
                "1. Combler les lacunes identifiées dans l'analyse Pass 1\n"
                "2. Vérifier/confirmer les conclusions clés avec des sources web\n"
                "3. Trouver des informations que les données internes ne contenaient pas\n"
                "4. Exécuter les RECHERCHES OBLIGATOIRES définies dans ton prompt "
                "(ex: Sales Navigator pour les profils mid-management comme PMO, IT Manager)\n\n"
                "Ne répète PAS ce qui est déjà dans l'analyse Pass 1. "
                "Ajoute UNIQUEMENT de nouvelles informations.\n"
            )

            messages = [
                SystemMessage(content=pass2_system),
                HumanMessage(content=pass2_context),
            ]

            # Standard tool loop on slim context
            for iteration in range(MAX_TOOL_ITERATIONS):
                ctx_size = _estimate_context_chars(messages)
                if ctx_size > _MAX_CONTEXT_CHARS:
                    logger.warning(f"Agent {agent_name}: Pass 2 context {ctx_size} exceeds limit, stopping")
                    break

                response = await llm.bind_tools(tools).ainvoke(messages)
                messages.append(response)

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

                        result_str = str(result)
                        if len(result_str) > _MAX_TOOL_RESULT_CHARS:
                            result_str = result_str[:_MAX_TOOL_RESULT_CHARS] + "\n\n[… résultat tronqué]"

                        messages.append(ToolMessage(
                            content=result_str,
                            tool_call_id=tc["id"],
                        ))
                    continue

                break

            # Inject Pass 1 summary into message history for extraction
            messages.append(HumanMessage(content=(
                "## Rappel — Analyse des données internes (Pass 1)\n\n"
                f"{pass1_summary}\n\n"
                "Combine les données internes (Pass 1) et tes recherches web (Pass 2) "
                "pour ton analyse finale."
            )))

        else:
            # --- STANDARD SINGLE-PASS MODE ---
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=context),
            ]

            for iteration in range(MAX_TOOL_ITERATIONS):
                ctx_size = _estimate_context_chars(messages)
                if ctx_size > _MAX_CONTEXT_CHARS:
                    logger.warning(f"Agent {agent_name}: context size {ctx_size} exceeds limit, stopping tool loop")
                    break

                if tools:
                    response = await llm.bind_tools(tools).ainvoke(messages)
                else:
                    response = await llm.ainvoke(messages)

                messages.append(response)

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

                        result_str = str(result)
                        if len(result_str) > _MAX_TOOL_RESULT_CHARS:
                            result_str = result_str[:_MAX_TOOL_RESULT_CHARS] + "\n\n[… résultat tronqué]"

                        messages.append(ToolMessage(
                            content=result_str,
                            tool_call_id=tc["id"],
                        ))
                    continue

                break

        # Two-step extraction with automatic retry
        signals_section = _extract_signals_section(system_prompt)
        signal_ids = _extract_signal_ids(system_prompt)

        async def _run_extraction(use_opus: bool = False) -> AgentReport:
            """Execute Step A (analysis) + Step B (structured extraction).

            Args:
                use_opus: Force Opus for both steps (used on retry).
            """
            # Step A — concise analysis with explicit signal verdicts
            step_a_llm = get_llm(max_tokens=8192) if use_opus else llm
            analysis_prompt = (
                "Résume ton analyse en 2000 mots max.\n\n"
                "OBLIGATION : termine TOUJOURS ton analyse par cette section exacte :\n\n"
                "## Verdict des signaux\n"
                "Une ligne par signal au format : signal_id → DETECTED / NOT_DETECTED / UNKNOWN | evidence courte\n\n"
                "Base-toi sur TOUTES les données (contexte fourni + résultats web). "
                "Ne mets UNKNOWN que si tu n'as vraiment aucune donnée pertinente.\n\n"
            )
            if signals_section:
                analysis_prompt += signals_section + "\n"

            analysis_response = await step_a_llm.ainvoke(
                messages + [HumanMessage(content=analysis_prompt)]
            )
            analysis_text = (
                analysis_response.content
                if isinstance(analysis_response.content, str)
                else str(analysis_response.content)
            )
            logger.info(f"Agent {agent_name}: analysis step produced {len(analysis_text)} chars (opus={use_opus})")
            logger.debug(f"Agent {agent_name}: analysis text:\n{analysis_text[:5000]}")

            # Step B — structured extraction
            if use_opus or agent_name == "comex_organisation":
                ext_llm = get_llm(max_tokens=4096).with_structured_output(AgentReport)
            else:
                ext_llm = get_fast_llm(max_tokens=4096).with_structured_output(AgentReport)

            extraction_instruction = (
                "Convertis l'analyse ci-dessous en AgentReport JSON structuré.\n\n"
                "PRIORITÉ ABSOLUE — le champ `signals` est le plus important :\n"
            )
            if signal_ids:
                extraction_instruction += (
                    f"- Tu DOIS inclure exactement ces signal_id : {signal_ids}\n"
                    "- Pour chaque signal, extrais le status (DETECTED/NOT_DETECTED/UNKNOWN), "
                    "l'evidence et la confidence depuis l'analyse.\n"
                    "- Ne mets UNKNOWN que si l'analyse ne contient AUCUNE information sur ce signal.\n"
                    "- VÉRIFIE que la liste signals contient bien {n} éléments avant de terminer.\n".format(n=len(signal_ids))
                )
            else:
                extraction_instruction += "- Cet agent n'émet aucun signal. Le champ signals doit être [].\n"
            extraction_instruction += (
                "\nPour les facts : garde-les concis (5 maximum, 1-2 phrases par fact).\n"
                f"\n---\n\n{analysis_text}"
            )

            report: AgentReport = await ext_llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=extraction_instruction),
            ])
            report.agent_name = agent_name
            return report

        # --- Attempt 1: default models ---
        retry_reason = None
        try:
            report = await _run_extraction(use_opus=False)
            if _needs_retry(report, signal_ids):
                retry_reason = "all signals UNKNOWN with empty evidence"
        except Exception as e:
            retry_reason = f"extraction failed: {e}"
            logger.warning(f"Agent {agent_name}: extraction failed ({e}), will retry with Opus")

        # --- Attempt 2: retry with Opus if needed ---
        if retry_reason:
            logger.warning(f"Agent {agent_name}: retrying extraction with Opus (reason: {retry_reason})")
            report = await _run_extraction(use_opus=True)

        _validate_sources(report)
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
    # Try both "Signaux à émettre" (old) and "Signaux" (new spec)
    match = re.search(r"(## Signaux(?:\s+à émettre)?.*?)(?=\n## |\Z)", prompt, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_signal_ids(prompt: str) -> list[str]:
    """Extract signal_id values from the signals table in the prompt."""
    signals_section = _extract_signals_section(prompt)
    if not signals_section:
        return []
    # Match signal_ids: backtick-wrapped `signal_id` or plain signal_id in table rows
    backtick = re.findall(r"\|\s*`(\w+)`", signals_section)
    if backtick:
        return backtick
    # Fallback: match plain signal_id patterns in table rows (word_word format)
    return re.findall(r"\|\s*(\w+(?:_\w+)+)\s*\|", signals_section)


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


_FAKE_PUBLISHERS = {
    "document interne", "analyse sectorielle", "étude sectorielle",
    "rapport interne", "source interne", "analyse interne",
}


def _validate_sources(report: AgentReport) -> None:
    """Post-extraction validation: degrade facts with no verifiable URL."""
    for fact in report.facts:
        has_real_url = any(s.url.strip() for s in fact.sources)
        if not has_real_url:
            fact.confidence = "low"
        for source in fact.sources:
            if source.publisher.lower().strip() in _FAKE_PUBLISHERS:
                source.publisher = "model_knowledge"


def _needs_retry(report: AgentReport, expected_signal_ids: list[str]) -> bool:
    """Check if extraction produced no useful signal data and should be retried.

    Returns True if ALL expected signals have status UNKNOWN with empty evidence.
    Returns False if there are no expected signals or at least one signal has data.
    """
    if not expected_signal_ids:
        return False
    report_signals = {s.signal_id: s for s in report.signals}
    for sid in expected_signal_ids:
        sig = report_signals.get(sid)
        if sig and (sig.status != "UNKNOWN" or (sig.evidence and sig.evidence.strip())):
            return False
    return True
