from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-6"

    # Ghost Genius
    ghost_genius_api_key: str = ""
    ghost_genius_base_url: str = "https://api.ghostgenius.fr/v2"
    ghost_genius_account_ids: list[str] = field(default_factory=list)

    # Firecrawl
    firecrawl_api_key: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_enrich_url: str = ""

    # HubSpot
    hubspot_api_key: str = ""
    hubspot_portal_id: str = ""

    # Evaboot
    evaboot_api_key: str = ""

    # Slack
    slack_webhook_url: str = ""

    # Unipile
    unipile_api_key: str = ""
    unipile_base_url: str = "https://api25.unipile.com:15595/api/v1"

    # Enrich-CRM
    enrich_crm_api_key: str = ""


def load_settings() -> Settings:
    account_ids_raw = os.getenv("GHOST_GENIUS_ACCOUNT_IDS", "")
    account_ids = [a.strip() for a in account_ids_raw.split(",") if a.strip()]

    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        ghost_genius_api_key=os.getenv("GHOST_GENIUS_API_KEY", ""),
        ghost_genius_base_url=os.getenv("GHOST_GENIUS_BASE_URL", "https://api.ghostgenius.fr/v2"),
        ghost_genius_account_ids=account_ids,
        firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY", ""),
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", ""),
        supabase_enrich_url=os.getenv("SUPABASE_ENRICH_URL", ""),
        evaboot_api_key=os.getenv("EVABOOT_API_KEY", ""),
        hubspot_api_key=os.getenv("HUBSPOT_API_KEY", ""),
        hubspot_portal_id=os.getenv("HUBSPOT_PORTAL_ID", ""),
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
        unipile_api_key=os.getenv("UNIPILE_API_KEY", ""),
        unipile_base_url=os.getenv("UNIPILE_BASE_URL", "https://api25.unipile.com:15595/api/v1"),
        enrich_crm_api_key=os.getenv("ENRICH_CRM_API_KEY", ""),
    )


# LinkedIn region IDs for Sales Navigator location filter
LINKEDIN_REGION_IDS: dict[str, str] = {
    "France": "105015875",
    "United Kingdom": "101165590",
    "Germany": "101282230",
    "Spain": "105646813",
    "Italy": "103350119",
    "Belgium": "100565514",
    "Netherlands": "102890719",
    "Switzerland": "106693272",
    "USA": "103644278",
    "Canada": "101174742",
    "Australia": "101452733",
    "India": "102713980",
    "Brazil": "106057199",
    "Sweden": "105117694",
    "Denmark": "104514075",
    "Norway": "103819153",
}

# Title keywords for supplementary executive search (signal-relevant roles)
TITLE_SEARCH_KEYWORDS = ["PMO", "project management office", "CIO office", "manager IT", "chief of staff"]

# IT leadership keywords for targeted search (captures CTO IT, DSI, CISO, CDO...)
IT_LEADERSHIP_KEYWORDS = [
    "CTO", "CIO", "CISO", "CDO",
    "DSI", "Chief Technology", "Chief Information",
    "Chief Digital", "Chief Data", "Chief Security",
    "Directeur IT", "Directeur Systèmes",
    "VP IT", "VP Technology", "VP Digital",
]

settings = load_settings()
