from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from aggregator.models.result import UnifiedResult

FollowUpRole = Literal["user", "assistant"]
FollowUpProvider = Literal["hermes", "anthropic"]


class FollowUpMessage(BaseModel):
    role: FollowUpRole
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content must not be blank")
        return cleaned


class FollowUpRequest(BaseModel):
    incident: UnifiedResult
    question: str = Field(min_length=1, max_length=2000)
    history: list[FollowUpMessage] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must not be blank")
        return cleaned


class FollowUpResponse(BaseModel):
    answer: str = ""
    provider: FollowUpProvider | None = None
    fallback_used: bool = False
    error: str | None = None
