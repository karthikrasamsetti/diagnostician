"""
agent.py (healer)
-----------------
The Healer AGENT. Given a broken-locator case + verdict, it either proposes a
grounded locator fix or escalates to a human. Same provider-agnostic machinery
as the Reporter (structured() with the HealProposal schema).

SAFETY: proposes only; never applies. On ANY error, the fallback ESCALATES
(never emits a guessed locator), consistent with "a wrong fix is worse than none".
"""

from __future__ import annotations

import logging
import time

from diagnostician.core.providers import LLMProvider, get_provider
from diagnostician.core.schema import Verdict
from diagnostician.agents.healer.schema import HealProposal
from diagnostician.agents.healer.prompt import (
    HEALER_SYSTEM_PROMPT, build_healer_message,
)

logger = logging.getLogger("diagnostician.healer")


class Healer:
    """Proposes a locator fix for a stale-locator (broken_test) failure."""

    def __init__(self, provider: LLMProvider | None = None,
                 max_retries: int = 2, backoff_seconds: float = 1.0):
        self._provider = provider or get_provider()
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        logger.info("Healer ready (provider=%s)", self._provider.name)

    def propose(self, case_file: dict, verdict: Verdict) -> HealProposal:
        """Propose a fix or escalate. Always returns a HealProposal; never raises."""
        test_name = case_file.get("test_name", "<unknown>")
        old_locator = self._extract_locator(case_file)
        user_message = build_healer_message(case_file, verdict)
        logger.info("Healer analyzing locator for: %s", test_name)

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                proposal = self._provider.structured(
                    HEALER_SYSTEM_PROMPT, user_message, HealProposal
                )
                # Safety invariant enforced in code, not just trusted from the model:
                # if it escalated, strip any locator that leaked through.
                if proposal.escalate:
                    proposal.new_locator = None
                    proposal.locator_strategy = None
                if proposal.escalate:
                    logger.info("Healer ESCALATED for %s (insufficient evidence)", test_name)
                else:
                    logger.info("Healer proposed fix for %s: %s -> %s (conf %.2f)",
                                test_name, proposal.old_locator, proposal.new_locator,
                                proposal.confidence)
                return proposal
            except Exception as e:  # noqa: BLE001
                last_error = e
                wait = self._backoff * (2 ** attempt)
                logger.warning("Healer attempt %d/%d failed for %s: %s. Retry in %.1fs",
                               attempt + 1, self._max_retries + 1, test_name, e, wait)
                if attempt < self._max_retries:
                    time.sleep(wait)

        # Fallback ALWAYS escalates — never a guessed locator.
        logger.error("Healer failed for %s after retries. Escalating. Last error: %s",
                     test_name, last_error)
        return HealProposal(
            old_locator=old_locator,
            escalate=True,
            confidence=0.0,
            reasoning=(f"The Healer could not analyze this failure after "
                       f"{self._max_retries + 1} attempts due to a provider error "
                       f"({type(last_error).__name__}). Escalating to a human — no "
                       f"locator is proposed, to avoid an unverified guess."),
            observations=[f"Automatic locator analysis failed for '{test_name}'.",
                          "A human should inspect the DOM change and update the locator."],
        )

    @staticmethod
    def _extract_locator(case_file: dict) -> str:
        """Best-effort pull of the broken locator from the error message/logs.
        This is only used to populate old_locator; it is NOT a fix."""
        text = f"{case_file.get('error_message','')} {case_file.get('logs','')}"
        # crude: look for a quoted selector-ish token
        import re
        m = re.search(r"['\"]([#.\[][^'\"]+)['\"]", text)
        return m.group(1) if m else "(locator not identified from case file)"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    from diagnostician.core.schema import Verdict as V
    healer = Healer(provider=get_provider("mock"))
    verdict = V(label="broken_test", confidence=0.95,
                reasoning="Element not found; diff shows the locator id was renamed.",
                recommended_action="heal_locator")
    case = {"test_name": "test_login_button_click",
            "error_message": "locator resolved to 0 elements for '#login-btn'",
            "diff_summary": "renamed #login-btn to #signin-btn in login.html"}
    proposal = healer.propose(case, verdict)
    print("\n--- HEAL PROPOSAL ---")
    print(proposal.model_dump_json(indent=2))
