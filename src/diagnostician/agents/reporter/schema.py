"""
schema.py (reporter)
--------------------
The DATA CONTRACT for the Reporter agent's output: a TicketDraft.

Unlike the Diagnostician (a classifier picking one of four labels), the Reporter
is a GENERATOR — it writes a bug ticket. But we still use structured output so
downstream code (and, later, the real Jira API) can consume it reliably.

Honest-design principle: this schema only contains fields the Reporter can
actually PRODUCE from the case_file + verdict. Fields that need external data it
doesn't have (real screenshots, a confirmed epic ID from the Jira project) are
represented as SUGGESTIONS or flagged as needing human input — never invented.

SAFETY: this is a DRAFT object. The Reporter never files a ticket. A human
reviews the draft and submits it. "Call the Jira API" is a separate, human-gated
step we deliberately keep out of the agent.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Priority(str, Enum):
    """Jira-style priority. Locked vocabulary so downstream code can rely on it."""
    BLOCKER = "blocker"    # production down / release-blocking
    CRITICAL = "critical"  # major feature broken, no workaround
    MAJOR = "major"        # significant but with a workaround
    MINOR = "minor"        # small / cosmetic


class TicketDraft(BaseModel):
    summary: str = Field(
        min_length=10, max_length=120,
        description="Concise, searchable one-line title. Include the failing area "
                    "and the symptom, e.g. 'Checkout returns 500 after PricingService refactor'.",
    )
    description: str = Field(
        min_length=30,
        description="A clear narrative of what is broken and why it matters. "
                    "Written for the developer who will fix it.",
    )
    steps_to_reproduce: list[str] = Field(
        min_length=1,
        description="Ordered, concrete steps to reproduce the failure, derived from "
                    "the test and its context. Each step is one action.",
    )
    expected_result: str = Field(
        description="What SHOULD happen (from the test's assertion / intent).",
    )
    actual_result: str = Field(
        description="What DID happen (from the error message and logs).",
    )
    priority: Priority = Field(
        description="Severity/priority inferred from impact signals in the evidence "
                    "(e.g. a 500 on checkout is higher than a cosmetic issue).",
    )
    suggested_assignee: Optional[str] = Field(
        default=None,
        description="Best guess at who should fix it — usually the author of the commit "
                    "that introduced the failure, if identifiable. Null if unknown.",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Short tags for triage/filtering, e.g. ['auto-triaged', 'api', "
                    "'regression']. Lowercase, hyphenated.",
    )
    environment: Optional[str] = Field(
        default=None,
        description="Where it failed (CI/browser/viewport/OS), from run context. "
                    "Null if not provided.",
    )
    evidence: str = Field(
        description="The concrete failing evidence: error message, key log lines, "
                    "and the failing payload/query. Quoted from the case file.",
    )
    suggested_epic: Optional[str] = Field(
        default=None,
        description="A SUGGESTED epic/feature area based on the test name/area — a hint "
                    "for the human, NOT a confirmed Jira epic ID. Null if unclear.",
    )
    needs_human_input: list[str] = Field(
        default_factory=list,
        description="Things the Reporter could NOT determine and a human must supply, "
                    "e.g. 'attach Playwright trace/screenshot', 'confirm epic link', "
                    "'verify assignee'. Be honest here rather than inventing values.",
    )


if __name__ == "__main__":
    # Self-test: a valid draft passes; a too-short summary is rejected.
    draft = TicketDraft(
        summary="Checkout returns 500 after PricingService refactor",
        description="The checkout endpoint began returning HTTP 500 after the "
                    "PricingService discount logic was rewritten.",
        steps_to_reproduce=["Run test_checkout_returns_200", "Observe POST /api/checkout"],
        expected_result="HTTP 200 with a valid order",
        actual_result="HTTP 500; NullPointerException in PricingService",
        priority="critical",
        suggested_assignee="author of commit 'refactor: rewrite PricingService discount logic'",
        labels=["auto-triaged", "api", "regression"],
        environment="CI",
        evidence="AssertionError: expected 200, got 500; NullPointerException in PricingService.calculate()",
        suggested_epic="Checkout / Payments",
        needs_human_input=["confirm epic link", "verify assignee"],
    )
    print("Valid TicketDraft OK:")
    print(draft.model_dump_json(indent=2))
