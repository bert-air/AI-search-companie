"""Agent Scoring — deterministic scoring from all agent signals.

Reads signals from all agent_reports, applies the 27-signal grille,
computes score_total with confidence weighting, data_quality_score,
and verdict (GO/EXPLORE/PASS).
Signal validators can override LLM status based on parsed value fields.

Spec reference: Annexe I.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from hat_yai.models import ScoringResult
from hat_yai.state import AuditState
from hat_yai.utils.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)

# Scoring grille — signal_id → points_bruts (27 signals)
SCORING_GRILLE: dict[str, int] = {
    # Positive signals
    "nouveau_dsi_dir_transfo": 30,
    "programme_transfo_annonce": 30,
    "nouveau_pdg_dg": 30,
    "verbatim_douleur_detecte": 25,
    "croissance_ca_forte": 20,
    "acquisition_recente": 20,
    "plan_strategique_annonce": 20,
    "direction_transfo_existe": 20,
    "cible_prioritaire_identifiee": 15,
    "croissance_effectifs_forte": 15,
    "dsi_plus_40": 15,
    "pmo_identifie": 15,
    "connexion_c_level": 15,
    "posts_linkedin_transfo": 15,
    "dirigeant_actif_linkedin": 10,
    "connexion_management": 10,
    "entreprise_plus_1000": 10,
    "reseau_alumni_commun": 10,
    "vecteur_indirect_identifie": 5,
    # Negative signals
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
    "verbatim_douleur_detecte": "dynamique",
    "croissance_ca_forte": "finance",
    "acquisition_recente": "dynamique",
    "plan_strategique_annonce": "dynamique",
    "direction_transfo_existe": "comex_organisation",
    "cible_prioritaire_identifiee": "comex_profils",
    "croissance_effectifs_forte": "dynamique",
    "dsi_plus_40": "comex_organisation",
    "pmo_identifie": "comex_organisation",
    "connexion_c_level": "connexions",
    "posts_linkedin_transfo": "dynamique",
    "dirigeant_actif_linkedin": "comex_profils",
    "connexion_management": "connexions",
    "entreprise_plus_1000": "finance",
    "reseau_alumni_commun": "comex_profils",
    "vecteur_indirect_identifie": "connexions",
    "entreprise_en_difficulte": "finance",
    "licenciements_pse": "dynamique",
    "entreprise_moins_500": "finance",
    "decroissance_effectifs": "dynamique",
    "dsi_moins_10": "comex_organisation",
    "aucune_info_dirigeants": "comex_organisation",
    "dsi_en_poste_plus_5_ans": "comex_organisation",
    "secteur_en_declin": "entreprise",
}

# Backward compatibility: old signal name → new signal name
_SIGNAL_ALIASES: dict[str, str] = {
    "sales_connecte_top_management": "connexion_c_level",
}

# Confidence multipliers for weighted scoring
CONFIDENCE_MULTIPLIERS: dict[str, float] = {
    "high": 1.0,
    "medium": 0.75,
    "low": 0.5,
}

# score_max = sum of all positive signals at full confidence
SCORE_MAX = sum(v for v in SCORING_GRILLE.values() if v > 0)  # 365

# Intent vs Profile signal classification
INTENT_SIGNALS = {
    "nouveau_dsi_dir_transfo", "programme_transfo_annonce", "nouveau_pdg_dg",
    "verbatim_douleur_detecte", "acquisition_recente", "plan_strategique_annonce",
    "posts_linkedin_transfo", "dirigeant_actif_linkedin", "licenciements_pse",
    "croissance_effectifs_forte", "decroissance_effectifs",
}

# Signals exempt from temporal decay (structural/permanent)
_NO_DECAY_SIGNALS = {
    "entreprise_plus_1000", "entreprise_moins_500", "dsi_plus_40", "dsi_moins_10",
    "secteur_en_declin", "entreprise_en_difficulte", "dsi_en_poste_plus_5_ans",
    "direction_transfo_existe", "pmo_identifie", "connexion_c_level",
    "connexion_management", "vecteur_indirect_identifie", "reseau_alumni_commun",
    "cible_prioritaire_identifiee", "dirigeant_actif_linkedin",
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


def _extract_event_date(text: str) -> Optional[date]:
    """Extract a date from signal evidence/value for temporal decay."""
    for pattern, fmt in [
        (r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"),
        (r"(\d{4}-\d{2})", "%Y-%m"),
        (r"\b(20\d{2})\b", "%Y"),
    ]:
        m = re.search(pattern, text)
        if m:
            try:
                return datetime.strptime(m.group(1), fmt).date()
            except ValueError:
                continue
    return None


def _decay_factor(signal: dict) -> float:
    """Events >18 months old lose 50% points."""
    for field in ("value", "evidence"):
        d = _extract_event_date(signal.get(field, ""))
        if d:
            months_ago = (date.today() - d).days / 30
            if months_ago > 18:
                return 0.5
    return 1.0


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
    """Deterministic scoring from agent signals with confidence weighting."""
    # Collect all signals from all agents, applying aliases
    all_signals: dict[str, dict] = {}
    for report in agent_reports:
        for signal in report.get("signals", []):
            sid = signal.get("signal_id")
            if sid:
                # Apply backward compatibility aliases
                sid = _SIGNAL_ALIASES.get(sid, sid)
                all_signals[sid] = signal

    scoring_signals = []
    score_total = 0
    detected_or_not = 0
    total_signals = len(SCORING_GRILLE)
    data_missing = []

    for signal_id, points_bruts in SCORING_GRILLE.items():
        signal = all_signals.get(signal_id)

        if signal:
            status = signal.get("status", "UNKNOWN")
            confidence = signal.get("confidence", "medium")
            value = signal.get("value", "")
            evidence = signal.get("evidence", "")
        else:
            status = "UNKNOWN"
            confidence = "medium"
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

        # Compute weighted points (with temporal decay for event-based signals)
        if status == "DETECTED":
            multiplier = CONFIDENCE_MULTIPLIERS.get(confidence, 0.75)
            decay = 1.0
            if signal and signal_id not in _NO_DECAY_SIGNALS:
                decay = _decay_factor(signal)
            points_ponderes = round(points_bruts * multiplier * decay)
            score_total += points_ponderes
            detected_or_not += 1
        elif status == "NOT_DETECTED":
            points_ponderes = 0
            detected_or_not += 1
        else:  # UNKNOWN
            points_ponderes = 0
            data_missing.append(signal_id)

        scoring_signals.append({
            "signal_id": signal_id,
            "status": status,
            "confidence": confidence,
            "points_bruts": points_bruts if status == "DETECTED" else 0,
            "points_ponderes": points_ponderes,
            "agent_source": SIGNAL_SOURCES.get(signal_id, ""),
            "value": value,
            "evidence": evidence,
        })

    data_quality_score = (detected_or_not / total_signals * 100) if total_signals > 0 else 0

    # Verdict
    if score_total >= 150:
        verdict = "GO"
        verdict_emoji = "🟢"
    elif score_total >= 80:
        verdict = "EXPLORE"
        verdict_emoji = "🟡"
    else:
        verdict = "PASS"
        verdict_emoji = "🔴"

    warning = None
    if data_quality_score < 50:
        warning = "Score peu fiable — données insuffisantes"

    # Sub-scores: profile (structural) vs intent (timing/buying signals)
    score_intent = sum(
        s["points_ponderes"] for s in scoring_signals
        if s["signal_id"] in INTENT_SIGNALS
    )
    score_profil = sum(
        s["points_ponderes"] for s in scoring_signals
        if s["signal_id"] not in INTENT_SIGNALS
    )

    return {
        "scoring_signals": scoring_signals,
        "score_total": score_total,
        "score_max": SCORE_MAX,
        "score_profil": score_profil,
        "score_intent": score_intent,
        "data_quality_score": round(data_quality_score, 1),
        "data_missing_signals": data_missing,
        "verdict": verdict,
        "verdict_emoji": verdict_emoji,
        "warning": warning,
    }


async def agent_scoring_node(state: AuditState) -> dict:
    """Compute scoring from all agent reports."""
    scoring = _compute_scoring(state.get("agent_reports", []))

    logger.info(
        f"Scoring: total={scoring['score_total']}/{scoring['score_max']}, "
        f"verdict={scoring['verdict']} {scoring['verdict_emoji']}, "
        f"profil={scoring['score_profil']}pts intent={scoring['score_intent']}pts, "
        f"quality={scoring['data_quality_score']}%, "
        f"missing={len(scoring['data_missing_signals'])} signals"
    )

    return {"scoring_result": scoring}
