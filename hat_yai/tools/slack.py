"""Slack webhook notification.

Spec reference: Section 7.8 (Synthétiseur output).
"""

from __future__ import annotations

import logging

import httpx

from hat_yai.config import settings

logger = logging.getLogger(__name__)


async def send_slack_notification(
    company_name: str,
    score_total: int,
    score_max: int,
    verdict: str,
    data_quality_score: float,
    deal_id: str,
    status: str,
    slack_recap: str = "",
    score_profil: int = 0,
    score_intent: int = 0,
) -> bool:
    """Send a summary notification to Slack via webhook."""
    deal_url = f"https://app.hubspot.com/contacts/{settings.hubspot_portal_id}/record/0-3/{deal_id}/"

    emoji = {"GO": "🟢", "EXPLORE": "🟡", "PASS": "🔴"}.get(verdict, "⚪")
    status_text = "Audit terminé" if status == "completed" else f"Audit terminé ({status})"

    # Build message body: recap + KPIs
    lines = [f"{emoji} *[{company_name}] — {status_text}*", ""]
    if slack_recap:
        lines.append(slack_recap)
    else:
        lines.append("• _Aucun récapitulatif disponible_")
    lines.append("")
    lines.append(f"📊 Score : *{score_total}/{score_max}* — *{verdict}*")
    lines.append(f"   → Profil : *{score_profil}* pts | Intent : *{score_intent}* pts")
    lines.append(f"📋 Qualité données : *{data_quality_score:.0f}%*")
    lines.append(f"🔗 <{deal_url}|Voir le deal HubSpot>")

    body = "\n".join(lines)

    message = {
        "text": body,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": body},
            }
        ],
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(settings.slack_webhook_url, json=message)
        if resp.status_code != 200:
            logger.error(f"Slack webhook failed: {resp.status_code} {resp.text}")
            return False
        logger.info(f"Slack notification sent for {company_name}")
        return True
