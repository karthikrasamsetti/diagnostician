"""
schema.py
---------
Defines the DATA CONTRACT for our Diagnostician agent.

A "contract" means: this is the single source of truth for what a valid
verdict looks like. The LLM must produce this shape, our code consumes this
shape, and a human reads this shape. Everyone agrees on one structure.
"""

from enum import Enum
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. The LABEL. We use an Enum instead of a free string.
#    WHY: if we allowed any string, the model could return "flakey", "FLAKY",
#    "possibly flaky", etc. — and our `if label == "flaky"` checks would silently
#    break. An Enum locks the vocabulary to EXACTLY three allowed values.
# ---------------------------------------------------------------------------
class Label(str, Enum):
    APPLICATION_BUG = "application_bug"   # the app is genuinely broken -> file a ticket (dev owns)
    BROKEN_TEST = "broken_test"           # app fine, test stale/badly written -> fix test (QA owns)
                                          #   includes hidden test dependencies & ordering problems:
                                          #   they PASS on retry but the root cause is a structural
                                          #   test flaw, so they are NOT 'flaky'.
    FLAKY = "flaky"                       # TRUE non-determinism: races, parallel collisions.
                                          #   same input -> different result by timing -> quarantine
    ENVIRONMENT_ISSUE = "environment_issue"  # viewport/CI config/resource limits -> fix pipeline
                                             #   config (DevOps owns). Deterministic given the env.


# ---------------------------------------------------------------------------
# 2. The recommended next action. Also an Enum, for the same reason.
#    This is what downstream agents (Reporter, Healer, Flake Handler) will
#    switch on later.
# ---------------------------------------------------------------------------
class Action(str, Enum):
    FILE_BUG = "file_bug"                       # -> Reporter agent (application_bug)
    HEAL_LOCATOR = "heal_locator"               # -> Healer agent (broken_test: stale locator)
    FIX_ENVIRONMENT = "fix_environment"         # -> DevOps (environment_issue: viewport/CI config)
    QUARANTINE_AND_TRACK = "quarantine_and_track"  # -> Flake Handler (flaky)
    HUMAN_REVIEW = "human_review"               # not confident / signals conflict -> escalate


# ---------------------------------------------------------------------------
# 3. The VERDICT itself — the full structured output of the Diagnostician.
#    Every Field(...) has a `description`. This is NOT just a comment:
#    when we wire this to the LLM, these descriptions are sent to the model
#    as instructions telling it how to fill each field. Good descriptions
#    here directly improve the model's answers. This is real prompt design.
# ---------------------------------------------------------------------------
class Verdict(BaseModel):
    label: Label = Field(
        description="The single classification of this test failure."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,  # ge = "greater or equal", le = "less or equal": force 0.0–1.0
        description=(
            "How confident you are, from 0.0 to 1.0. Be honest: use a LOW value "
            "when the evidence is ambiguous or conflicting. Do not inflate."
        ),
    )
    reasoning: str = Field(
        min_length=20,  # forces the model to actually justify, not say "flaky."
        description=(
            "Explain WHICH pieces of evidence led to this label. Cite the actual "
            "error type, logs, retry result, and code changes you relied on."
        ),
    )
    recommended_action: Action = Field(
        description="What should happen next, based on the label and confidence."
    )


# ---------------------------------------------------------------------------
# Quick self-test: prove the 'bouncer' accepts good data and rejects bad data.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("TEST 1 — valid verdict (should succeed):")
    good = Verdict(
        label="flaky",
        confidence=0.82,
        reasoning="TimeoutError waiting for selector; isolated retry passed with no code change.",
        recommended_action="quarantine_and_track",
    )
    print("  OK ->", good.model_dump(), "\n")

    print("TEST 2 — bad label (should be REJECTED):")
    try:
        Verdict(label="flakey", confidence=0.9,
                reasoning="a".ljust(20, "a"), recommended_action="file_bug")
    except Exception as e:
        print("  Rejected as expected. First error:", e.errors()[0]["msg"], "\n")

    print("TEST 3 — confidence out of range (should be REJECTED):")
    try:
        Verdict(label="flaky", confidence=1.7,
                reasoning="a".ljust(20, "a"), recommended_action="file_bug")
    except Exception as e:
        print("  Rejected as expected. First error:", e.errors()[0]["msg"])
