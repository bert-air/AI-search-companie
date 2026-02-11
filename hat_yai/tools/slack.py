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
    data_quality_score: float,
    deal_id: str,
    status: str,
) -> bool:
    """Send a summary notification to Slack via webhook."""
    deal_url = f"https://app.hubspot.com/contacts/undefined/deal/{deal_id}"

    emoji = "🟢" if score_total >= 50 else "🟡" if score_total >= 20 else "🔴"
    status_text = "Audit terminé" if status == "completed" else f"Audit terminé ({status})"

    message = {
        "text": f"{emoji} *{status_text} — {company_name}*",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *{status_text} — {company_name}*\n"
                        f"• Score : *{score_total}* points\n"
                        f"• Qualité données : *{data_quality_score:.0f}%*\n"
                        f"• <{deal_url}|Voir le deal HubSpot>"
                    ),
                },
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
