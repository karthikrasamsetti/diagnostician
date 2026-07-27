"""
providers.py
------------
The provider-agnostic LLM layer. Agents talk to an ABSTRACT interface
(LLMProvider) and never import a vendor SDK directly. Strategy pattern (swappable
algorithm) delivered via a Factory (get_provider by name).

GENERALIZED for multiple agents: the core method is `structured(system, user,
schema)` — it forces the model to return data matching ANY Pydantic schema and
returns a validated instance. The Diagnostician uses it with Verdict; the Reporter
uses it with TicketDraft; future agents use it with their own schemas.

`classify()` is kept as a thin backward-compatible wrapper (Verdict-specific) so
existing Diagnostician code and tests keep working unchanged.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel

from diagnostician.core.schema import Verdict

logger = logging.getLogger("diagnostician.providers")

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# THE CONTRACT. Every provider implements structured() for ANY schema.
# classify() is a Verdict-specific convenience built on top of it.
# ---------------------------------------------------------------------------
class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def structured(self, system_prompt: str, user_message: str,
                   schema: Type[T]) -> T:
        """Return a schema-valid instance of `schema`. Raises on failure."""
        raise NotImplementedError

    def classify(self, system_prompt: str, user_message: str) -> Verdict:
        """Backward-compatible convenience: structured output as a Verdict."""
        return self.structured(system_prompt, user_message, Verdict)


# ---------------------------------------------------------------------------
# ANTHROPIC. Tool-calling forces structured output: hand Claude the schema as a
# tool and require it to "call" it, so the arguments match the schema.
# ---------------------------------------------------------------------------
class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-6", temperature: float = 0.0):
        from anthropic import Anthropic
        self._client = Anthropic()          # reads ANTHROPIC_API_KEY from env
        self._model = model
        self._temperature = temperature     # 0.0 -> consistency, not creativity

    def structured(self, system_prompt: str, user_message: str,
                   schema: Type[T]) -> T:
        tool = {
            "name": "submit",
            "description": f"Submit the result as a {schema.__name__}.",
            "input_schema": schema.model_json_schema(),
        }
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            temperature=self._temperature,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit"},
            messages=[{"role": "user", "content": user_message}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return schema.model_validate(block.input)  # schema bouncer runs here
        raise ValueError("Anthropic returned no tool_use block")


# ---------------------------------------------------------------------------
# OPENAI. Native structured-output feature (response_format) accepts a Pydantic
# model directly and guarantees a matching parsed object.
# ---------------------------------------------------------------------------
class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str = "gpt-4o-2024-08-06", temperature: float = 0.0):
        from openai import OpenAI
        self._client = OpenAI()             # reads OPENAI_API_KEY from env
        self._model = model
        self._temperature = temperature

    def structured(self, system_prompt: str, user_message: str,
                   schema: Type[T]) -> T:
        completion = self._client.beta.chat.completions.parse(
            model=self._model,
            temperature=self._temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format=schema,         # OpenAI enforces the schema for us
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError("OpenAI returned no parsed object (possible refusal)")
        return parsed


# ---------------------------------------------------------------------------
# MOCK. No network, no key, no cost. Returns a canned object per schema so the
# whole pipeline is testable offline & deterministically.
# ---------------------------------------------------------------------------
class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, canned: BaseModel | None = None):
        # Default canned Verdict (keeps existing Diagnostician tests working).
        self._canned = canned or Verdict(
            label="flaky",
            confidence=0.5,
            reasoning="Mock provider default response for offline pipeline testing.",
            recommended_action="human_review",
        )

    def structured(self, system_prompt: str, user_message: str,
                   schema: Type[T]) -> T:
        logger.info("MockProvider returning canned %s (no API call made)",
                    schema.__name__)
        # If an injected canned object matches the requested schema, return it.
        if isinstance(self._canned, schema):
            return self._canned
        # Otherwise synthesize a minimal valid instance of the requested schema
        # so mock-based tests work for ANY agent without hand-injecting one.
        return _minimal_instance(schema)


def _minimal_instance(schema: Type[T]) -> T:
    """Build a minimal valid instance of a Pydantic schema for offline mocking.
    Fills required fields with schema-appropriate placeholder values."""
    from pydantic_core import PydanticUndefined
    values = {}
    for name, field in schema.model_fields.items():
        if field.default is not PydanticUndefined or field.default_factory is not None:
            continue  # optional / has default -> let Pydantic fill it
        ann = field.annotation
        if ann is bool:
            values[name] = True
        elif ann is str:
            values[name] = f"[mock {name}]".ljust(30, ".")
        elif ann is float:
            values[name] = 0.5
        elif ann is int:
            values[name] = 1
        elif getattr(ann, "__origin__", None) is list:
            values[name] = ["[mock item]"]
        else:
            # enum or other -> pick the first allowed value if possible
            try:
                values[name] = list(ann)[0]
            except TypeError:
                values[name] = "[mock]".ljust(30, ".")
    return schema.model_validate(values)


# ---------------------------------------------------------------------------
# THE FACTORY.
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "mock": MockProvider,
}


def get_provider(name: str | None = None, **kwargs) -> LLMProvider:
    """Factory. `name` defaults to env LLM_PROVIDER, then 'mock' if unset."""
    name = (name or os.getenv("LLM_PROVIDER") or "mock").lower()
    if name not in _REGISTRY:
        raise ValueError(f"Unknown provider '{name}'. Choose from {list(_REGISTRY)}.")
    logger.info("Instantiating provider: %s", name)
    return _REGISTRY[name](**kwargs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    provider = get_provider("mock")
    print("Provider name:", provider.name)
    verdict = provider.classify("system", "user")
    print("Returned a valid Verdict:", verdict.model_dump())
    print("Is an LLMProvider?", isinstance(provider, LLMProvider))
