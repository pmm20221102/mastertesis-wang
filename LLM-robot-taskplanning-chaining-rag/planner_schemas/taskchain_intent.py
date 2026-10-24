"""Compact, benchmark-specific intent output before scene-graph grounding."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SimpleTaskStop(BaseModel):
    """One ordered destination and its surface-form object clues."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    order: int = Field(ge=1, le=5)
    destination: str = Field(min_length=1)
    objects: list[str]

    @field_validator("objects")
    @classmethod
    def object_terms_are_nonempty(cls, terms: list[str]) -> list[str]:
        if any(not term.strip() for term in terms):
            raise ValueError("object terms must be nonempty strings")
        return terms


class SimpleTaskChainIntent(BaseModel):
    """Small LLM contract for ordered destination planning tasks."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["navigate", "ask_clarification"]
    stops: list[SimpleTaskStop] = Field(max_length=5)
    clarification: str | None = None

    @model_validator(mode="after")
    def validate_action_and_stop_contract(self) -> "SimpleTaskChainIntent":
        if self.action == "navigate":
            if not self.stops:
                raise ValueError("navigate requires at least one stop")
            if [stop.order for stop in self.stops] != list(range(1, len(self.stops) + 1)):
                raise ValueError("stop order must be contiguous and match the requested sequence")
            if self.clarification:
                raise ValueError("navigate must not include a clarification request")
        else:
            if self.stops:
                raise ValueError("ask_clarification must not include destination stops")
            if not self.clarification:
                raise ValueError("ask_clarification requires a clarification message")
        return self
