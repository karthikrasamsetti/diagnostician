"""
providers.py
------------
The provider-agnostic LLM layer. The agent talks to an ABSTRACT interface
(LLMProvider) and never imports a vendor SDK directly. This is the Strategy
pattern (swappable algorithm) delivered via a Factory (get_provider by name).

WHY: swap Anthropic <-> OpenAI <-> a free offline Mock by changing ONE string.
Vendor outages, price changes, cost/quality tuning, and offline testing all
become trivial. The agent's reasoning code stays identical.

Each provider's job: take a system prompt + user message + our Pydantic schema,
force the model to return data matching that schema, and hand back a validated
Verdict object. HOW it forces structure differs per vendor; the agent doesn't care.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

from diagnostician.core.schema import Verdict

logger = logging.getLogger("diagnostician.providers")


# ---------------------------------------------------------------------------
# THE CONTRACT. Every provider MUST implement classify(). The agent depends
# only on this abstract type — never on anthropic/openai concretely.
# ---------------------------------------------------------------------------
class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def classify(self, system_prompt: str, user_message: str) -> Verdict:
        """Return a schema-valid Verdict for the given prompts. Raises on failure."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ANTHROPIC. Uses tool-calling to FORCE structured output: we hand Claude the
# Verdict schema as a tool, and require it to "call" that tool — the arguments
# it produces are guaranteed to match our schema shape.
# ---------------------------------------------------------------------------
class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-6", temperature: float = 0.0):
        from anthropic import Anthropic
        self._client = Anthropic()          # reads ANTHROPIC_API_KEY from env
        self._model = model
        self._temperature = temperature     # 0.0 -> consistency, not creativity

    def classify(self, system_prompt: str, user_message: str) -> Verdict:
        tool = {
            "name": "submit_verdict",
            "description": "Submit the final triage verdict.",
            "input_schema": Verdict.model_json_schema(),  # Pydantic -> JSON Schema
        }
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            temperature=self._temperature,
            system=system_prompt,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_verdict"},  # MUST use the tool
            messages=[{"role": "user", "content": user_message}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                return Verdict.model_validate(block.input)  # schema bouncer runs here
        raise ValueError("Anthropic returned no tool_use block")


# ---------------------------------------------------------------------------
# OPENAI. Uses its native structured-output feature (response_format) which
# accepts a Pydantic model directly and guarantees a matching parsed object.
# ---------------------------------------------------------------------------
class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str = "gpt-4o-2024-08-06", temperature: float = 0.0):
        from openai import OpenAI
        self._client = OpenAI()             # reads OPENAI_API_KEY from env
        self._model = model
        self._temperature = temperature

    def classify(self, system_prompt: str, user_message: str) -> Verdict:
        completion = self._client.beta.chat.completions.parse(
            model=self._model,
            temperature=self._temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format=Verdict,        # OpenAI enforces the schema for us
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError("OpenAI returned no parsed object (possible refusal)")
        return parsed


# ---------------------------------------------------------------------------
# MOCK. No network, no key, no cost. Returns a canned Verdict (or one you inject).
# This is what lets us build and TEST the whole pipeline offline & deterministically.
# Notice: the agent can't tell this apart from a real provider — that's the point.
# ---------------------------------------------------------------------------
class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, canned: Verdict | None = None):
        self._canned = canned or Verdict(
            label="flaky",
            confidence=0.5,
            reasoning="Mock provider default response for offline pipeline testing.",
            recommended_action="human_review",
        )

    def classify(self, system_prompt: str, user_message: str) -> Verdict:
        logger.info("MockProvider returning canned verdict (no API call made)")
        return self._canned


# ---------------------------------------------------------------------------
# THE FACTORY. Give it a name, get a ready provider. This is the single place
# that knows about concrete classes; everything else uses the abstract type.
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
    # Prove the factory + mock work with zero API keys.
    provider = get_provider("mock")
    print("Provider name:", provider.name)
    verdict = provider.classify("system", "user")
    print("Returned a valid Verdict:", verdict.model_dump())
    # Prove it's polymorphic: the type is the abstract contract.
    print("Is an LLMProvider?", isinstance(provider, LLMProvider))
