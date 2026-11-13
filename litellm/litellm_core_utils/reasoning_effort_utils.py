from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from litellm.constants import (
    DEFAULT_REASONING_EFFORT_HIGH_THINKING_BUDGET,
    DEFAULT_REASONING_EFFORT_LOW_THINKING_BUDGET,
    DEFAULT_REASONING_EFFORT_MEDIUM_THINKING_BUDGET,
)

OpenAIStyleReasoningEffort = Literal["minimal", "low", "medium", "high"]


def is_gpt_6_astra(model: str) -> bool:
    return model.removeprefix("openai/").removeprefix("responses/") == "gpt-6-astra"


class _Reasoning(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    summary: Literal["auto", "concise", "detailed"] | None = None
    generate_summary: Literal["auto", "concise", "detailed"] | None = None


class _Thinking(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["enabled", "disabled", "adaptive"]
    budget_tokens: int | None = Field(default=None, ge=0)
    summary: Literal["auto", "concise", "detailed"] | None = None
    display: Literal["summarized", "omitted"] | None = None


def normalize_gpt6_reasoning(
    model: str, *, effort: object = None, thinking: object = None, reasoning: object = None,
) -> dict[str, str | None]:
    from litellm.exceptions import BadRequestError

    try:
        alias: Final = _Reasoning.model_validate(
            {"effort": effort} if isinstance(effort, str) else {} if effort is None else effort
        )
        native: Final = _Reasoning.model_validate(reasoning if reasoning is not None else {})
        hint: Final = _Thinking.model_validate(thinking) if thinking is not None else None
        if alias.effort is not None and native.effort is not None and alias.effort != native.effort:
            raise ValueError("Conflicting effort values")
        explicit: Final = native.effort or alias.effort
        inferred: Final = (
            None if hint is None else "none" if hint.type == "disabled"
            else reasoning_effort_from_thinking_budget(hint.budget_tokens)
            if hint.type == "enabled" and hint.budget_tokens is not None
            else explicit or "medium"
        )
        if explicit is not None and inferred is not None and explicit != inferred:
            raise ValueError("Conflicting thinking and effort values")
        if hint is not None and hint.type != "disabled" and explicit == "none":
            raise ValueError("Thinking is enabled but effort is none")
        summaries: Final = tuple(value for value in (
            native.summary, alias.summary, hint.summary if hint is not None else None,
            native.generate_summary, alias.generate_summary,
        ) if value is not None)
        if len(frozenset(summaries)) > 1:
            raise ValueError("Conflicting reasoning summaries")
        omitted: Final = hint is not None and hint.display == "omitted"
        if omitted and summaries:
            raise ValueError("Thinking display omits an explicitly requested summary")
        return {
            **{key: value for key, value in (
                ("effort", explicit or inferred),
                ("summary", native.summary or alias.summary or (hint.summary if hint is not None else None)
                 or ("auto" if hint is not None and hint.display == "summarized" else None)),
                ("generate_summary", native.generate_summary or alias.generate_summary),
            ) if value is not None},
            **({"summary": None} if omitted else {}),
        }
    except ValueError as error:
        raise BadRequestError(
            message="Invalid or conflicting reasoning_effort, thinking or reasoning parameters",
            model=model, llm_provider="openai",
        ) from error


def reasoning_effort_from_thinking_budget(
    budget_tokens: int,
) -> OpenAIStyleReasoningEffort:
    """Bucket an Anthropic ``thinking.budget_tokens`` into an OpenAI-style
    ``reasoning_effort`` using the shared ``DEFAULT_REASONING_EFFORT_*_THINKING_BUDGET``
    thresholds, so every backend that translates a budget into an effort label
    reads the same numbers.
    """
    if budget_tokens >= DEFAULT_REASONING_EFFORT_HIGH_THINKING_BUDGET:
        return "high"
    if budget_tokens >= DEFAULT_REASONING_EFFORT_MEDIUM_THINKING_BUDGET:
        return "medium"
    if budget_tokens >= DEFAULT_REASONING_EFFORT_LOW_THINKING_BUDGET:
        return "low"
    return "minimal"
