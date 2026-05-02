"""
Data models for root cause analysis results.

RCAResult is produced by the RCAAnalyzer and attached to UnifiedResult.
It contains the LLM hypothesis, supporting evidence, recommended actions,
and GitHub code links.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CodeReference(BaseModel):
    """
    A link to a specific file (or line) in a GitHub repository
    that is likely relevant to the root cause.
    """

    repo: str                        # e.g. "my-org/payment-service"
    path: str                        # e.g. "src/payments/processor.py"
    url: str                         # full GitHub blob URL
    line_number: int | None = None   # specific line if known from a stack trace
    snippet: str | None = None       # short excerpt for context (≤ 3 lines)
    relevance: str = ""              # why this file was flagged


class RecommendedAction(BaseModel):
    priority: int = Field(ge=1, le=3, description="1 = immediate, 2 = soon, 3 = investigate")
    action: str
    rationale: str


class RCAResult(BaseModel):
    """
    The complete root cause analysis for one query.
    Produced by RCAAnalyzer and stored on UnifiedResult.
    """

    # The LLM's hypothesis
    summary: str = ""
    root_cause: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # What specific signals supported this conclusion
    supporting_evidence: list[str] = Field(default_factory=list)

    # What to do next, ordered by priority
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)

    # GitHub links
    code_references: list[CodeReference] = Field(default_factory=list)

    # Raw search terms extracted by the LLM, used for GitHub queries
    github_search_terms: list[str] = Field(default_factory=list)

    # Whether RCA ran at all (skipped if no signals, or API unavailable)
    performed: bool = False
    error: str | None = None
