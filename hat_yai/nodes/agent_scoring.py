"""Agent Scoring — deterministic scoring from all agent signals.

Reads signals from all agent_reports, applies the 20-signal grille,
computes score_total and data_quality_score.
Signal validators can override LLM status based on parsed value fields.

Spec reference: Section 7.7.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from hat_yai.models import ScoringResult
from hat_yai.state import AuditState
from hat_yai.utils.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)

# Scoring grille — signal_id → points (spec Section 7.7)
SCORING_GRILLE: dict[str, int] = {
    "nouveau_dsi_dir_transfo": 30,
    "programme_transfo_annonce": 30,
    "nouveau_pdg_dg": 30,
    "croissance_ca_forte": 20,
    "acquisition_recente": 20,
    "plan_strategique_annonce": 20,
    "direction_transfo_existe": 20,
    "croissance_effectifs_forte": 15,
    "dsi_plus_40": 15,
    "pmo_identifie": 15,
    "sales_connecte_top_management": 15,
    "posts_linkedin_transfo": 15,
    "entreprise_plus_1000": 10,
    "entreprise_en_difficulte": -30,
    "licenciements_pse": -20,
    "entreprise_moins_500": -20,
    "decroissance_effectifs": -15,
    "dsi_moins_10": -15,
    "aucune_info_dirigeants": -10,
    "dsi_en_poste_plus_5_ans": -10,
    "secteur_en_declin": -10,
}

# signal_id → agent_source
SIGNAL_SOURCES: dict[str, str] = {
    "nouveau_dsi_dir_transfo": "comex_organisation",
    "programme_transfo_annonce": "dynamique",
    "nouveau_pdg_dg": "comex_organisation",
    "croissance_ca_forte": "finance",
    "acquisition_recente": "dynamique",
    "plan_strategique_annonce": "dynamique",
    "direction_transfo_existe": "comex_organisation",
    "croissance_effectifs_forte": "dynamique",
    "dsi_plus_40": "comex_organisation",
    "pmo_identifie": "comex_organisation",
    "sales_connecte_top_management": "connexions",
    "posts_linkedin_transfo": "dynamique",
    "entreprise_plus_1000": "finance",
    "entreprise_en_difficulte": "finance",
    "licenciements_pse": "dynamique",
    "entreprise_moins_500": "finance",
    "decroissance_effectifs": "dynamique",
    "dsi_moins_10": "comex_organisation",
    "aucune_info_dirigeants": "comex_organisation",
    "dsi_en_poste_plus_5_ans": "comex_organisation",
    "secteur_en_declin": "entreprise",
}


def _parse_months(text: str) -> Optional[float]:
    """Best-effort: extract a duration in months from value/evidence text."""
    text = text.lower().strip()
    # "16 mois", "12 mois", "~30 mois"
    m = re.search(r"(\d+(?:\.\d+)?)\s*mois", text)
    if m:
        return float(m.group(1))
    # "2 ans", "1.5 ans"
    m = re.search(r"(\d+(?:\.\d+)?)\s*an[s]?", text)
    if m:
        return float(m.group(1)) * 12
    return None


def _parse_number(text: str) -> Optional[float]:
    """Best-effort: extract a number from value text (handles spaces, K, etc.)."""
    text = text.lower().strip().replace("\u00a0", "").replace(" ", "")
    # "10500", "10.500", "10k"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*k", text)
    if m:
        return float(m.group(1).replace(",", ".")) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
    if m:
        return float(m.group(1))
    return None


def _validate_recency(signal: dict, max_months: float) -> Optional[bool]:
    """Validate that a 'new in role' signal is within the threshold."""
    for field in ("value", "evidence"):
        months = _parse_months(signal.get(field, ""))
        if months is not None:
            return months <= max_months
    return None  # couldn't parse → keep LLM status


def _validate_min_employees(signal: dict, threshold: int) -> Optional[bool]:
    """Validate entreprise_plus_X: employees > threshold."""
    for field in ("value", "evidence"):
        n = _parse_number(signal.get(field, ""))
        if n is not None:
            return n > threshold
    return None


def _validate_max_employees(signal: dict, threshold: int) -> Optional[bool]:
    """Validate entreprise_moins_X: employees < threshold."""
    for field in ("value", "evidence"):
        n = _parse_number(signal.get(field, ""))
        if n is not None:
            return n < threshold
    return None


def _validate_min_months(signal: dict, min_months: float) -> Optional[bool]:
    """Validate dsi_en_poste_plus_5_ans: tenure > threshold."""
    for field in ("value", "evidence"):
        months = _parse_months(signal.get(field, ""))
        if months is not None:
            return months > min_months
    return None


# signal_id → validator function
# Returns True (signal confirmed), False (override to NOT_DETECTED), or None (can't parse)
SIGNAL_VALIDATORS = {
    "nouveau_pdg_dg": lambda s: _validate_recency(s, 12),
    "nouveau_dsi_dir_transfo": lambda s: _validate_recency(s, 12),
    "entreprise_plus_1000": lambda s: _validate_min_employees(s, 1000),
    "entreprise_moins_500": lambda s: _validate_max_employees(s, 500),
    "dsi_en_poste_plus_5_ans": lambda s: _validate_min_months(s, 60),
}


def _compute_scoring(agent_reports: list[dict]) -> dict:
    """Deterministic scoring from agent signals."""
    # Collect all signals from all agents
    all_signals: dict[str, dict] = {}
    for report in agent_reports:
        for signal in report.get("signals", []):
            sid = signal.get("signal_id")
            if sid:
                all_signals[sid] = signal

    scoring_signals = []
    score_total = 0
    detected_or_not = 0
    total_signals = len(SCORING_GRILLE)
    data_missing = []

    for signal_id, points in SCORING_GRILLE.items():
        signal = all_signals.get(signal_id)

        if signal:
            status = signal.get("status", "UNKNOWN")
            value = signal.get("value", "")
            evidence = signal.get("evidence", "")
        else:
            status = "UNKNOWN"
            value = ""
            evidence = ""

        # Threshold validation: override DETECTED if value fails check
        if status == "DETECTED" and signal and signal_id in SIGNAL_VALIDATORS:
            valid = SIGNAL_VALIDATORS[signal_id](signal)
            if valid is False:
                logger.warning(
                    f"Scoring: {signal_id} overridden DETECTED→NOT_DETECTED "
                    f"(value={value!r} failed threshold check)"
                )
                status = "NOT_DETECTED"

        if status == "DETECTED":
            score_total += points
            detected_or_not += 1
        elif status == "NOT_DETECTED":
            detected_or_not += 1
        else:  # UNKNOWN
            data_missing.append(signal_id)

        scoring_signals.append({
            "signal_id": signal_id,
            "status": status,
            "points": points if status == "DETECTED" else 0,
            "agent_source": SIGNAL_SOURCES.get(signal_id, ""),
            "value": value,
            "evidence": evidence,
        })

    data_quality_score = (detected_or_not / total_signals * 100) if total_signals > 0 else 0
    warning = None
    if data_quality_score < 50:
        warning = "Score peu fiable — données insuffisantes"

    return {
        "scoring_signals": scoring_signals,
        "score_total": score_total,
        "data_quality_score": round(data_quality_score, 1),
        "data_missing_signals": data_missing,
        "warning": warning,
    }


async def agent_scoring_node(state: AuditState) -> dict:
    """Compute scoring from all agent reports."""
    scoring = _compute_scoring(state.get("agent_reports", []))

    logger.info(
        f"Scoring: total={scoring['score_total']}, "
        f"quality={scoring['data_quality_score']}%, "
        f"missing={len(scoring['data_missing_signals'])} signals"
    )

    return {"scoring_result": scoring}
