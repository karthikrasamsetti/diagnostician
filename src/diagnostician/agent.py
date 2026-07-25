"""
agent.py
--------
The Diagnostician AGENT. This is the glue that combines the three pieces:
    prompt (how to think)  +  provider (which model)  +  schema (answer shape)
into a single clean call:  diagnose(case_file) -> Verdict.

Design note for later: this whole class is effectively ONE NODE. When we build
the full crew with LangGraph, a thin wrapper will drop this in as the
"Diagnostician node". Building & testing it standalone now means that node is
already trusted before it ever enters a graph.

Production concerns handled here (per our requirements):
  - Logging: every diagnosis is traceable (what came in, what went out).
  - Error handling + RETRY: transient API failures (timeouts, rate limits) are
    retried with backoff; a persistent failure returns a SAFE fallback verdict
    (human_review) instead of crashing the pipeline. Failing safe > failing loud
    HERE, because a crashed triage agent would block the whole CI pipeline.
"""

from __future__ import annotations

import logging
import time

from diagnostician.schema import Verdict, Label, Action
from diagnostician.prompt import SYSTEM_PROMPT, build_user_message
from diagnostician.providers import LLMProvider, get_provider

logger = logging.getLogger("diagnostician.agent")


class Diagnostician:
    """A read-only agent that classifies one test failure into a Verdict."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
    ):
        # Depend on the ABSTRACT provider. If none supplied, the factory picks
        # one from env (LLM_PROVIDER), defaulting to the offline mock. The agent
        # genuinely does not know or care which model it got.
        self._provider = provider or get_provider()
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        logger.info("Diagnostician ready (provider=%s)", self._provider.name)

    def diagnose(self, case_file: dict) -> Verdict:
        """Classify one failure. Always returns a Verdict; never raises upward."""
        test_name = case_file.get("test_name", "<unknown>")
        user_message = build_user_message(case_file)
        logger.info("Diagnosing failure for test: %s", test_name)

        last_error: Exception | None = None
        # attempt 0 = first try; the rest are retries.
        for attempt in range(self._max_retries + 1):
            try:
                verdict = self._provider.classify(SYSTEM_PROMPT, user_message)
                logger.info(
                    "Verdict for %s: %s (confidence=%.2f, action=%s)",
                    test_name, verdict.label.value, verdict.confidence,
                    verdict.recommended_action.value,
                )
                return verdict
            except Exception as e:  # noqa: BLE001 - we deliberately catch all here
                last_error = e
                wait = self._backoff * (2 ** attempt)  # exponential backoff
                logger.warning(
                    "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                    attempt + 1, self._max_retries + 1, test_name, e, wait,
                )
                if attempt < self._max_retries:
                    time.sleep(wait)

        # All attempts failed. Return a SAFE fallback rather than crash the
        # pipeline. A human is explicitly put in the loop.
        logger.error("All attempts failed for %s. Returning safe fallback. Last error: %s",
                     test_name, last_error)
        return Verdict(
            label=Label.BROKEN_TEST,  # neutral placeholder; the action is what matters
            confidence=0.0,           # zero confidence signals "do not trust this"
            reasoning=(
                f"Diagnosis could not be completed after {self._max_retries + 1} "
                f"attempts due to a provider error ({type(last_error).__name__}). "
                f"Escalating to a human so no failure is silently mishandled."
            ),
            recommended_action=Action.HUMAN_REVIEW,
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )
    # Runs fully offline on the mock provider — no key needed.
    agent = Diagnostician(provider=get_provider("mock"))
    sample = {
        "test_name": "test_daily_tracking_report",
        "error_message": "AssertionError: expected 1+ rows, got 0",
        "error_type": "AssertionError",
        "retry_result": "failed again on retry; passes locally",
        "query": "SELECT * FROM tracking WHERE event_date = CURRENT_DATE",
        "run_context": "CI runs in UTC at 00:15; developer runs locally in IST",
        "history": "failures always cluster around midnight UTC",
    }
    result = agent.diagnose(sample)
    print("\n--- FINAL VERDICT ---")
    print(result.model_dump_json(indent=2))
