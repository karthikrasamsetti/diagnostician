"""
schema.py (healer)
------------------
The DATA CONTRACT for the Healer's output: a HealProposal.

The Healer is the most safety-sensitive agent: its output concerns a CODE change
(a test locator). A wrong fix is worse than no fix, because a locator that matches
the WRONG element makes a test pass and MASKS a real bug. So the schema enforces
safety structurally:

  - requires_approval is ALWAYS True. The Healer never applies a fix; a human does.
  - When evidence of what the element BECAME is weak/absent, the Healer sets
    escalate=True and proposes NO locator (new_locator stays None). It shares
    observations to help the human, but never a fabricated guess — a persuasive
    guess gets rubber-stamped and is more dangerous than a blank escalation.

The Healer only fills new_locator when the DOM/diff clearly shows the element's
new form (grounded fix). Otherwise it escalates.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class LocatorStrategy(str, Enum):
    """How the proposed locator targets the element. Stable strategies preferred."""
    TEST_ID = "test_id"      # [data-testid=...] — most stable
    ROLE = "role"            # ARIA role/name — stable, semantic
    ID = "id"                # #id — stable if not auto-generated
    CSS = "css"              # CSS selector — ok if targeting stable attrs
    XPATH = "xpath"          # relative XPath — powerful; prefer relative over absolute


class HealProposal(BaseModel):
    # --- Always present ---
    old_locator: str = Field(
        description="The broken locator the test currently uses (what we'd replace)."
    )
    escalate: bool = Field(
        description="True when evidence of the element's NEW form is insufficient to "
                    "propose a safe fix. When True, new_locator MUST be null and a human "
                    "must handle it. Never guess a locator to avoid escalating."
    )
    requires_approval: bool = Field(
        default=True,
        description="ALWAYS True. The Healer proposes; a human approves and applies. "
                    "This is structural, not a decision the model makes."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence that the proposed fix targets the CORRECT, unique element. "
                    "Be conservative — a wrong locator masks real bugs. Low when unsure."
    )
    reasoning: str = Field(
        min_length=20,
        description="Explain what changed in the DOM/diff and why the proposal follows. "
                    "If escalating, explain what evidence was missing."
    )

    # --- Present only when a grounded fix is possible (escalate=False) ---
    new_locator: Optional[str] = Field(
        default=None,
        description="The proposed replacement locator. MUST be null when escalate=True. "
                    "Only set when the DOM/diff clearly shows the element's new form."
    )
    locator_strategy: Optional[LocatorStrategy] = Field(
        default=None,
        description="Strategy used by new_locator. Prefer stable (test_id/role/id) over "
                    "brittle (positional CSS / absolute XPath)."
    )
    grounded_in: Optional[str] = Field(
        default=None,
        description="The SPECIFIC evidence justifying the fix — e.g. the diff line showing "
                    "the rename, or the DOM change. Empty/weak evidence => escalate instead."
    )
    uniqueness_note: Optional[str] = Field(
        default=None,
        description="Why the new locator is UNIQUE (matches exactly one element). A locator "
                    "that could match multiple elements is not an acceptable fix."
    )

    # --- Present when escalating (helps the human without guessing) ---
    observations: list[str] = Field(
        default_factory=list,
        description="When escalating: useful, factual observations for the human (e.g. "
                    "'the diff touched login.html but no replacement selector was identifiable'). "
                    "NOT a locator guess — facts and context only."
    )


if __name__ == "__main__":
    # A grounded fix (evidence clear):
    good = HealProposal(
        old_locator="#login-btn",
        escalate=False,
        confidence=0.93,
        reasoning="The diff renamed the button id from #login-btn to #signin-btn in login.html.",
        new_locator="#signin-btn",
        locator_strategy="id",
        grounded_in="diff: renamed #login-btn -> #signin-btn in login.html",
        uniqueness_note="#signin-btn is a single id; ids are unique per page.",
    )
    print("GROUNDED FIX:\n", good.model_dump_json(indent=2), "\n")

    # An escalation (evidence weak): NO locator guess.
    esc = HealProposal(
        old_locator=".submit-btn",
        escalate=True,
        confidence=0.2,
        reasoning="No diff or updated DOM was provided showing what .submit-btn became, "
                  "so a safe replacement cannot be determined without guessing.",
        observations=["The test still references .submit-btn.",
                      "No commit diff touching the form was available to confirm the new selector."],
    )
    print("ESCALATION (no guess):\n", esc.model_dump_json(indent=2))
    # Sanity: escalation must not carry a locator.
    assert esc.new_locator is None
    assert esc.requires_approval is True
