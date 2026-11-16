from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from litellm.constants import (
    DEFAULT_REASONING_EFFORT_HIGH_THINKING_BUDGET,
    DEFAULT_REASONING_EFFORT_LOW_THINKING_BUDGET,
    DEFAULT_REASONING_EFFORT_MEDIUM_THINKING_BUDGET,
)

OpenAIStyleReasoningEffort = Literal["minimal", "low", "medium", "high"]


_OPENAI_REASONING_MODELS: Final = frozenset((
    "gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-6.1-terra",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "codex-auto-review",
))


def is_openai_reasoning_model(model: str) -> bool:
    return model.removeprefix("openai/").removeprefix("responses/") in _OPENAI_REASONING_MODELS


class _Reasoning(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    summary: Literal["auto", "concise", "detailed"] | None = None
    generate_summary: Literal["auto", "concise", "detailed"] | None = None
    mode: Literal["standard", "pro"] | None = None
    context: Literal["auto", "current_turn", "all_turns"] | None = None


class _Thinking(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["enabled", "disabled", "adaptive"]
    budget_tokens: int | None = Field(default=None, ge=0)
    summary: Literal["auto", "concise", "detailed"] | None = None
    display: Literal["summarized", "omitted"] | None = None


def normalize_openai_reasoning(
    model: str, *, effort: object = None, thinking: object = None, reasoning: object = None,
    output_effort: object = None,
) -> dict[str, str | None]:
    from litellm.exceptions import BadRequestError

    try:
        alias: Final = _Reasoning.model_validate(
            {"effort": effort} if isinstance(effort, str) else {} if effort is None else effort
        )
        native: Final = _Reasoning.model_validate(reasoning if reasoning is not None else {})
        hint: Final = _Thinking.model_validate(thinking) if thinking is not None else None
        output: Final = _Reasoning.model_validate({"effort": output_effort})
        efforts: Final = frozenset(value for value in (alias.effort, native.effort, output.effort) if value is not None)
        if len(efforts) > 1:
            raise ValueError("Conflicting effort values")
        explicit: Final = native.effort or alias.effort or output.effort
        budget_effort: Final = (
            reasoning_effort_from_thinking_budget(hint.budget_tokens)
            if hint is not None and hint.type == "enabled" and hint.budget_tokens is not None else None
        )
        inferred: Final = (
            None if hint is None else "none" if hint.type == "disabled"
            else ("low" if budget_effort == "minimal" else budget_effort)
            if budget_effort is not None
            else explicit or "medium"
        )
        if explicit is not None and inferred is not None and explicit != inferred:
            raise ValueError("Conflicting thinking and effort values")
        if hint is not None and hint.type != "disabled" and explicit == "none":
            raise ValueError("Thinking is enabled but effort is none")
        selected_effort: Final = explicit or inferred
        bare_model: Final = model.removeprefix("openai/").removeprefix("responses/")
        if bare_model != "codex-auto-review" and (
            selected_effort == "minimal"
            or selected_effort == "none" and bare_model in ("gpt-6-astra", "gpt-6.1-sol")
        ):
            raise ValueError(f"Unsupported reasoning effort for {bare_model}: {selected_effort}")
        if (alias.mode is not None and native.mode is not None and alias.mode != native.mode
            or alias.context is not None and native.context is not None and alias.context != native.context):
            raise ValueError("Conflicting reasoning mode or context")
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
                ("effort", selected_effort),
                ("mode", native.mode or alias.mode),
                ("context", native.context or alias.context),
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
