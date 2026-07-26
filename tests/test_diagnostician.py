"""
tests/test_diagnostician.py
---------------------------
Unit tests for the DETERMINISTIC parts of the system.

Key idea in AI testing: you cannot assert an exact LLM output (it's
non-deterministic). So we test everything AROUND the model — schema validation,
the provider factory, agent retry/fallback logic, prompt rendering, and the
eval grading function — using the MockProvider to stand in for a real model.

The LLM's *judgment quality* is measured separately by evaluate.py (that's
evaluation, not unit testing — a different activity).

Run with:  uv run pytest   (or just: pytest)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from diagnostician.core.schema import Verdict, Label, Action
from diagnostician.core.providers import (
    get_provider, MockProvider, LLMProvider, AnthropicProvider, OpenAIProvider,
)
from diagnostician.agents.diagnostician.agent import Diagnostician
from diagnostician.agents.diagnostician.prompt import build_user_message, SYSTEM_PROMPT
from diagnostician.agents.diagnostician.fixtures import FIXTURES
from diagnostician.evaluate import is_correct


# ---------------------------------------------------------------------------
# SCHEMA — the validation "bouncer" must accept valid data and reject invalid.
# ---------------------------------------------------------------------------
class TestSchema:
    def test_valid_verdict_is_accepted(self):
        v = Verdict(label="flaky", confidence=0.8,
                    reasoning="A sufficiently long reasoning string for the test.",
                    recommended_action="quarantine_and_track")
        assert v.label == Label.FLAKY
        assert v.confidence == 0.8

    def test_invalid_label_is_rejected(self):
        with pytest.raises(ValidationError):
            Verdict(label="flakey",  # typo — not an allowed enum value
                    confidence=0.8, reasoning="x" * 20,
                    recommended_action="file_bug")

    @pytest.mark.parametrize("bad_conf", [-0.1, 1.1, 2.0])
    def test_confidence_out_of_range_is_rejected(self, bad_conf):
        with pytest.raises(ValidationError):
            Verdict(label="flaky", confidence=bad_conf, reasoning="x" * 20,
                    recommended_action="file_bug")

    def test_reasoning_too_short_is_rejected(self):
        with pytest.raises(ValidationError):
            Verdict(label="flaky", confidence=0.5, reasoning="short",
                    recommended_action="file_bug")


# ---------------------------------------------------------------------------
# FACTORY — resolves names to providers; the abstract contract holds.
# ---------------------------------------------------------------------------
class TestFactory:
    def test_mock_by_name(self):
        assert isinstance(get_provider("mock"), MockProvider)

    def test_all_providers_are_llmproviders(self):
        # We can construct the classes without keys? No — anthropic/openai read
        # keys at init. So we only assert the registry maps to the right TYPES.
        from diagnostician.core.providers import _REGISTRY
        assert _REGISTRY["mock"] is MockProvider
        assert _REGISTRY["anthropic"] is AnthropicProvider
        assert _REGISTRY["openai"] is OpenAIProvider

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError):
            get_provider("gemini")  # not registered

    def test_case_insensitive(self):
        assert isinstance(get_provider("MOCK"), MockProvider)


# ---------------------------------------------------------------------------
# AGENT — machinery tested via the mock (no API calls).
# ---------------------------------------------------------------------------
class TestAgent:
    def test_diagnose_returns_verdict(self):
        agent = Diagnostician(provider=MockProvider())
        result = agent.diagnose({"test_name": "t", "error_message": "boom"})
        assert isinstance(result, Verdict)

    def test_diagnose_returns_injected_canned_verdict(self):
        canned = Verdict(label="application_bug", confidence=0.99,
                         reasoning="Injected canned verdict for testing purposes.",
                         recommended_action="file_bug")
        agent = Diagnostician(provider=MockProvider(canned=canned))
        result = agent.diagnose({"test_name": "t"})
        assert result.label == Label.APPLICATION_BUG
        assert result.recommended_action == Action.FILE_BUG

    def test_agent_never_raises_returns_safe_fallback_on_provider_error(self):
        # A provider that always fails should NOT crash the agent — it must
        # return the safe human_review fallback with confidence 0.
        class ExplodingProvider(LLMProvider):
            name = "exploding"
            def classify(self, system_prompt, user_message):
                raise RuntimeError("simulated API outage")

        agent = Diagnostician(provider=ExplodingProvider(),
                              max_retries=1, backoff_seconds=0)  # 0 backoff = fast test
        result = agent.diagnose({"test_name": "t"})
        assert result.recommended_action == Action.HUMAN_REVIEW
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# PROMPT RENDERING — deterministic string building.
# ---------------------------------------------------------------------------
class TestPromptRendering:
    def test_missing_fields_show_not_provided(self):
        msg = build_user_message({"test_name": "only_name"})
        assert "only_name" in msg
        assert "(not provided)" in msg  # absent fields are explicit, not dropped

    def test_all_fields_render(self):
        msg = build_user_message({
            "test_name": "t", "error_message": "e", "error_type": "TimeoutError",
            "query": "SELECT 1", "history": "flaked before",
        })
        assert "TimeoutError" in msg and "SELECT 1" in msg

    def test_system_prompt_has_all_four_labels(self):
        for label in ["application_bug", "broken_test", "flaky", "environment_issue"]:
            assert label in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# GRADING — the eval's is_correct logic, incl. the ambiguous-case rule.
# ---------------------------------------------------------------------------
class TestGrading:
    def _fx(self, fixture_id):
        return next(f for f in FIXTURES if f.id == fixture_id)

    def test_exact_label_match_passes(self):
        fx = self._fx("bug_api_500")
        v = Verdict(label="application_bug", confidence=0.9, reasoning="x" * 20,
                    recommended_action="file_bug")
        assert is_correct(fx, v) is True

    def test_wrong_label_fails(self):
        fx = self._fx("bug_api_500")
        v = Verdict(label="flaky", confidence=0.9, reasoning="x" * 20,
                    recommended_action="quarantine_and_track")
        assert is_correct(fx, v) is False

    def test_ambiguous_human_review_passes(self):
        fx = self._fx("ambiguous_timeout")
        v = Verdict(label="flaky", confidence=0.5, reasoning="x" * 20,
                    recommended_action="human_review")
        assert is_correct(fx, v) is True  # honest escalation is ideal

    def test_ambiguous_masking_action_fails_even_with_matching_label(self):
        fx = self._fx("ambiguous_timeout")
        # label matches expected (application_bug) BUT action masks -> must FAIL
        v = Verdict(label="application_bug", confidence=0.9, reasoning="x" * 20,
                    recommended_action="quarantine_and_track")
        assert is_correct(fx, v) is False


# ---------------------------------------------------------------------------
# FIXTURES — sanity checks on the test set itself.
# ---------------------------------------------------------------------------
class TestFixtures:
    def test_fixtures_exist(self):
        assert len(FIXTURES) >= 8

    def test_all_expected_labels_are_valid(self):
        valid = {l.value for l in Label}
        for fx in FIXTURES:
            assert fx.expected_label in valid, f"{fx.id} has invalid label"

    def test_ids_are_unique(self):
        ids = [f.id for f in FIXTURES]
        assert len(ids) == len(set(ids))
