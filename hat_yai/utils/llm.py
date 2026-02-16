"""LLM helpers: model construction and prompt loading."""

from __future__ import annotations

from pathlib import Path

from langchain_anthropic import ChatAnthropic

from hat_yai.config import settings

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def get_llm(temperature: float = 0, max_tokens: int = 4096) -> ChatAnthropic:
    """Create a ChatAnthropic instance for Claude Opus."""
    return ChatAnthropic(
        model=settings.anthropic_model,
        anthropic_api_key=settings.anthropic_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_fast_llm(temperature: float = 0, max_tokens: int = 4096) -> ChatAnthropic:
    """Create a ChatAnthropic instance for Claude Sonnet (faster, cheaper)."""
    return ChatAnthropic(
        model="claude-sonnet-4-5-20250929",
        anthropic_api_key=settings.anthropic_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def load_prompt(agent_name: str) -> str:
    """Load a system prompt from prompts/{agent_name}.md"""
    path = PROMPTS_DIR / f"{agent_name}.md"
    return path.read_text(encoding="utf-8")


def load_prompt_template(agent_name: str, **kwargs: str) -> str:
    """Load a prompt and replace {{variable}} placeholders with provided values."""
    raw = load_prompt(agent_name)
    for key, value in kwargs.items():
        raw = raw.replace("{{" + key + "}}", str(value))
    return raw
