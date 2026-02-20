"""Firecrawl tools for web search and scraping.

These are LangChain @tool decorated functions — they get bound to the LLM
so agents can call them via tool-calling protocol.
"""

from __future__ import annotations

from langchain_core.tools import tool
from firecrawl import FirecrawlApp

from hat_yai.config import settings

_SCRAPE_MAX_CHARS = 15_000


def _get_app() -> FirecrawlApp:
    return FirecrawlApp(api_key=settings.firecrawl_api_key)


@tool
def search_web(query: str) -> list[dict]:
    """Search the web for information. Returns a list of results with url, title, and content snippet.

    Args:
        query: The search query to execute.
    """
    app = _get_app()
    results = app.search(query, limit=5)
    return results.get("data", results) if isinstance(results, dict) else results


@tool
def scrape_page(url: str) -> str:
    """Scrape a web page and return its content as markdown text (truncated to ~15 000 chars).

    Args:
        url: The URL to scrape.
    """
    app = _get_app()
    result = app.scrape(url, formats=["markdown"])
    text = result.markdown or ""
    if len(text) > _SCRAPE_MAX_CHARS:
        text = text[:_SCRAPE_MAX_CHARS] + "\n\n[… contenu tronqué]"
    return text


def scrape_with_links(url: str) -> tuple[str, list[str]]:
    """Scrape a web page returning both markdown text and all page links.

    The "links" format captures footer/sidebar links that onlyMainContent misses,
    which is critical for finding LinkedIn URLs in site footers.

    Returns:
        (markdown_text, links_list) — markdown truncated to _SCRAPE_MAX_CHARS.
    """
    app = _get_app()
    result = app.scrape(url, formats=["markdown", "links"])
    text = result.markdown or ""
    if len(text) > _SCRAPE_MAX_CHARS:
        text = text[:_SCRAPE_MAX_CHARS] + "\n\n[… contenu tronqué]"
    links = result.links or []
    return text, links
