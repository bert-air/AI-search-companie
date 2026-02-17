"""Router node — pure Python slicing of consolidated LinkedIn data.

No LLM. Reads consolidated_linkedin and produces agent-specific context slices.

Spec reference: Section 5.
"""

from __future__ import annotations

import logging

from hat_yai.state import AuditState

logger = logging.getLogger(__name__)

# Pre-signal IDs relevant to each agent
_DYNAMIQUE_SIGNALS = {
    "programme_transfo_annonce",
    "posts_linkedin_transfo",
    "verbatim_douleur_detecte",
    "turnover_comex_detecte",
}

_COMEX_ORGA_SIGNALS = {
    "nouveau_pdg_dg",
    "nouveau_dsi_dir_transfo",
    "direction_transfo_existe",
    "pmo_identifie",
    "dsi_en_poste_plus_5_ans",
    "evolution_roles_comex",
}


def _get_full_profiles(
    consolidated: dict,
    min_pertinence: int = 3,
    max_profiles: int = 8,
) -> list[dict]:
    """Select C-level profiles with pertinence_commerciale >= min_pertinence.

    Returns full dirigeant data (from dirigeants list) for matching C-levels,
    sorted by pertinence desc, capped at max_profiles.
    """
    c_levels = consolidated.get("c_levels") or []

    # Filter and sort by pertinence
    relevant = sorted(
        [c for c in c_levels if c.get("pertinence_commerciale", 0) >= min_pertinence],
        key=lambda c: c.get("pertinence_commerciale", 0),
        reverse=True,
    )[:max_profiles]

    relevant_names = {c["name"] for c in relevant}

    # Get full profile data from dirigeants list
    dirigeants = consolidated.get("dirigeants") or []
    full_profiles = [d for d in dirigeants if d.get("name") in relevant_names]

    # Attach pertinence_commerciale and role_deduit from c_levels
    c_level_map = {c["name"]: c for c in relevant}
    for profile in full_profiles:
        c_info = c_level_map.get(profile["name"], {})
        profile["pertinence_commerciale"] = c_info.get("pertinence_commerciale", 0)
        profile["role_deduit"] = c_info.get("role_deduit", "")

    return full_profiles


def _filter_posts_by_authors(consolidated: dict, author_names: set[str]) -> list[dict]:
    """Filter posts to only those authored by specific people."""
    return [
        p for p in (consolidated.get("posts_pertinents") or [])
        if p.get("auteur") in author_names
    ]


def _filter_pre_signals(consolidated: dict, signal_ids: set[str]) -> list[dict]:
    """Filter pre-detected signals to only those relevant to the agent."""
    return [
        s for s in (consolidated.get("signaux_pre_detectes") or [])
        if s.get("signal_id") in signal_ids
    ]


def route_to_agents(consolidated: dict) -> dict[str, dict]:
    """Slice consolidated LinkedIn JSON into agent-specific contexts."""
    return {
        "finance": {
            "croissance_effectifs": consolidated.get("croissance_effectifs"),
        },

        "entreprise": {
            "themes_transversaux": consolidated.get("themes_transversaux") or [],
        },

        "dynamique": {
            "posts_pertinents": consolidated.get("posts_pertinents") or [],
            "mouvements_consolides": consolidated.get("mouvements_consolides") or [],
            "croissance_effectifs": consolidated.get("croissance_effectifs"),
            "signaux_pre_detectes": _filter_pre_signals(
                consolidated, _DYNAMIQUE_SIGNALS
            ),
        },

        "comex_organisation": {
            "dirigeants": consolidated.get("dirigeants") or [],
            "c_levels": consolidated.get("c_levels") or [],
            "organigramme_probable": consolidated.get("organigramme_probable") or [],
            "mouvements_consolides": consolidated.get("mouvements_consolides") or [],
            "stack_consolidee": consolidated.get("stack_consolidee") or [],
            "signaux_pre_detectes": _filter_pre_signals(
                consolidated, _COMEX_ORGA_SIGNALS
            ),
        },

        "comex_profils": {
            "c_levels_details": _get_full_profiles(consolidated),
            "organigramme_probable": consolidated.get("organigramme_probable") or [],
            "posts_c_levels": _filter_posts_by_authors(
                consolidated,
                {p["name"] for p in _get_full_profiles(consolidated)},
            ),
        },

        "connexions": {
            "dirigeants_connexions": [
                {
                    "name": d.get("name", ""),
                    "current_title": d.get("current_title", ""),
                    "is_c_level": d.get("is_c_level", False),
                    "connected_with": d.get("connected_with"),
                    "entreprises_precedentes": d.get("entreprises_precedentes") or [],
                }
                for d in (consolidated.get("dirigeants") or [])
            ],
        },
    }


async def router_node(state: AuditState) -> dict:
    """Router: slice consolidated LinkedIn data for each agent."""
    consolidated = state.get("consolidated_linkedin") or {}

    if not consolidated or not consolidated.get("dirigeants"):
        logger.info("Router: No consolidated data, producing empty slices")
        return {"agent_context_slices": {
            "finance": {},
            "entreprise": {},
            "dynamique": {},
            "comex_organisation": {},
            "comex_profils": {},
            "connexions": {},
        }}

    slices = route_to_agents(consolidated)

    # Log slice sizes for monitoring
    for agent_name, slice_data in slices.items():
        import json
        size = len(json.dumps(slice_data, ensure_ascii=False))
        logger.info(f"Router: {agent_name} slice = {size:,} chars")

    return {"agent_context_slices": slices}
