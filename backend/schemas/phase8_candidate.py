"""Strict structured output returned by the Phase 8 candidate generator."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Phase8ModelCandidate(BaseModel):
    """Model-authored suggestions only; source identifiers are attached by code."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_type: Literal["explicit_requirement", "interpretation", "no_candidate"]
    candidate_obligation: str = Field(max_length=2000)
    supporting_quotation: str = Field(max_length=2000)
    uncertainty: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(max_length=2000)
    requires_human_review: Literal[True]
