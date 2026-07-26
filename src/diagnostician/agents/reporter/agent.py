"""
agent.py (reporter)
-------------------
The Reporter AGENT. Given a case_file + a Verdict, it drafts a bug ticket
(TicketDraft). Mirrors the Diagnostician's shape: prompt + provider + schema,
with logging, retry, and a safe fallback.

Uses the provider's generic structured() method with the TicketDraft schema —
the SAME provider-agnostic machinery the Diagnostician uses, just a different
output type. That reuse is the payoff of generalizing providers.

SAFETY: produces a DRAFT only. Never files to Jira. A human reviews and submits.
"""

from __future__ import annotations

import logging
import time

from diagnostician.core.providers import LLMProvider, get_provider
from diagnostician.core.schema import Verdict
from diagnostician.agents.reporter.schema import TicketDraft, Priority
from diagnostician.agents.reporter.prompt import (
    REPORTER_SYSTEM_PROMPT, build_reporter_message,
)

logger = logging.getLogger("diagnostician.reporter")


class Reporter:
    """Drafts a bug ticket from a case file and a triage verdict."""

    def __init__(self, provider: LLMProvider | None = None,
                 max_retries: int = 2, backoff_seconds: float = 1.0):
        self._provider = provider or get_provider()
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        logger.info("Reporter ready (provider=%s)", self._provider.name)

    def draft(self, case_file: dict, verdict: Verdict) -> TicketDraft:
        """Draft a ticket. Always returns a TicketDraft; never raises upward."""
        test_name = case_file.get("test_name", "<unknown>")
        user_message = build_reporter_message(case_file, verdict)
        logger.info("Drafting ticket for: %s", test_name)

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                draft = self._provider.structured(
                    REPORTER_SYSTEM_PROMPT, user_message, TicketDraft
                )
                logger.info("Ticket drafted for %s: '%s' (priority=%s)",
                            test_name, draft.summary, draft.priority.value)
                return draft
            except Exception as e:  # noqa: BLE001
                last_error = e
                wait = self._backoff * (2 ** attempt)
                logger.warning("Reporter attempt %d/%d failed for %s: %s. Retry in %.1fs",
                               attempt + 1, self._max_retries + 1, test_name, e, wait)
                if attempt < self._max_retries:
                    time.sleep(wait)

        # Safe fallback: a minimal draft that explicitly flags it needs a human.
        logger.error("Reporter failed for %s after retries. Returning fallback draft. "
                     "Last error: %s", test_name, last_error)
        return TicketDraft(
            summary=f"[NEEDS HUMAN] Could not auto-draft ticket for {test_name}",
            description=(f"The Reporter failed to draft a ticket after "
                         f"{self._max_retries + 1} attempts due to a provider error "
                         f"({type(last_error).__name__}). Please draft manually."),
            steps_to_reproduce=["See the original test failure logs."],
            expected_result="(unavailable — drafting failed)",
            actual_result=str(case_file.get("error_message", "(unknown)")),
            priority=Priority.MAJOR,
            evidence=str(case_file.get("error_message", "(unknown)")),
            needs_human_input=["Draft this ticket manually — automatic drafting failed."],
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s | %(name)s | %(message)s")
    from diagnostician.core.schema import Verdict as V
    # Offline demo on the mock (produces a placeholder TicketDraft).
    reporter = Reporter(provider=get_provider("mock"))
    fake_verdict = V(label="application_bug", confidence=0.95,
                     reasoning="A 500 with an app-side stacktrace after a refactor.",
                     recommended_action="file_bug")
    case = {"test_name": "test_checkout_returns_200",
            "error_message": "expected 200, got 500",
            "logs": "NullPointerException in PricingService"}
    draft = reporter.draft(case, fake_verdict)
    print("\n--- DRAFT TICKET ---")
    print(draft.model_dump_json(indent=2))
