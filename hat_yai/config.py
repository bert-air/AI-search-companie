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

    # Slack
    slack_webhook_url: str = ""


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
        hubspot_api_key=os.getenv("HUBSPOT_API_KEY", ""),
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
    )


settings = load_settings()
