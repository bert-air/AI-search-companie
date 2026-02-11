"""Pydantic models matching the spec's output contract (Section 6)."""

from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class Source(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    date: str = ""
    snippet: str = ""


class Fact(BaseModel):
    category: str
    statement: str
    confidence: Literal["high", "medium", "low"]
    sources: list[Source] = Field(default_factory=list)


class Signal(BaseModel):
    signal_id: str
    status: Literal["DETECTED", "NOT_DETECTED", "UNKNOWN"]
    value: str = ""
    evidence: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    sources: list[str] = Field(default_factory=list)


class DataQuality(BaseModel):
    sources_count: int = 0
    ghost_genius_available: bool = False
    confidence_overall: Literal["high", "medium", "low"] = "medium"


class AgentReport(BaseModel):
    """Standard output contract for all LLM agents (spec Section 6)."""
    agent_name: str
    facts: list[Fact] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)


# --- Scoring ---

class ScoringSignal(BaseModel):
    signal_id: str
    status: Literal["DETECTED", "NOT_DETECTED", "UNKNOWN"]
    points: int
    agent_source: str
    value: str = ""
    evidence: str = ""


class ScoringResult(BaseModel):
    scoring_signals: list[ScoringSignal] = Field(default_factory=list)
    score_total: int = 0
    data_quality_score: float = 0.0
    data_missing_signals: list[str] = Field(default_factory=list)
    warning: Optional[str] = None
