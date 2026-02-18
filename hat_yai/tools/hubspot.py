"""HubSpot API — create note on deal.

Spec reference: Section 7.8 (Synthétiseur output).
"""

from __future__ import annotations

import logging

import httpx

from hat_yai.config import settings

logger = logging.getLogger(__name__)

HUBSPOT_API_BASE = "https://api.hubapi.com"


async def create_deal_note(deal_id: str, note_body: str) -> bool:
    """Create a note associated with a deal in HubSpot.

    POST /crm/v3/objects/notes with hs_note_body = markdown report.
    Then associate the note with the deal.
    """
    headers = {
        "Authorization": f"Bearer {settings.hubspot_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=HUBSPOT_API_BASE, headers=headers, timeout=30.0) as client:
        # 1. Create the note
        create_resp = await client.post(
            "/crm/v3/objects/notes",
            json={
                "properties": {
                    "hs_note_body": note_body,
                }
            },
        )
        if create_resp.status_code >= 400:
            logger.error(
                f"HubSpot create note failed: {create_resp.status_code} "
                f"{create_resp.text[:500]}"
            )
            create_resp.raise_for_status()
        note_id = create_resp.json()["id"]

        # 2. Associate note with deal
        assoc_resp = await client.put(
            f"/crm/v3/objects/notes/{note_id}/associations/deals/{deal_id}/note_to_deal",
        )
        if assoc_resp.status_code >= 400:
            logger.error(
                f"HubSpot associate note failed: {assoc_resp.status_code} "
                f"{assoc_resp.text[:500]}"
            )
            assoc_resp.raise_for_status()

        logger.info(f"Created HubSpot note {note_id} on deal {deal_id}")
        return True
