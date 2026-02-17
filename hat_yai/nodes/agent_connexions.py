"""Agent Connexions — LinkedIn connection analysis and indirect vectors.

Reads pre-processed connexion data from router slice + sales team.
No web tools needed. Spec reference: Section 6.3 (Connexions).
"""

import logging

from hat_yai.models import AgentReport, Signal, DataQuality
from hat_yai.state import AuditState
from hat_yai.utils.agent_runner import run_agent

logger = logging.getLogger(__name__)

# Connexion signals owned by this agent
_CONNEXION_SIGNALS = [
    "connexion_c_level",
    "connexion_management",
    "vecteur_indirect_identifie",
    "reseau_alumni_commun",
]


async def agent_connexions_node(state: AuditState) -> dict:
    # Check if connected_with data is available (>20% of profiles)
    slices = state.get("agent_context_slices") or {}
    connexions_data = slices.get("connexions", {})
    dirigeants = connexions_data.get("dirigeants_connexions", [])

    if dirigeants:
        with_data = sum(1 for d in dirigeants if d.get("connected_with") is not None)
        if with_data / len(dirigeants) < 0.2:
            logger.info(
                f"Connexions: {with_data}/{len(dirigeants)} profiles have "
                f"connected_with data (<20%), skipping LLM call"
            )
            skip_report = AgentReport(
                agent_name="connexions",
                signals=[
                    Signal(
                        signal_id=sid,
                        status="UNKNOWN",
                        evidence="Données connected_with indisponibles",
                    )
                    for sid in _CONNEXION_SIGNALS
                ],
                data_quality=DataQuality(confidence_overall="low"),
            )
            return {"agent_reports": [skip_report.model_dump()]}

    return await run_agent(
        state=state,
        agent_name="connexions",
        tools=None,  # No external tools needed
    )
