"""
crew_state.py
-------------
The shared STATE that flows through the crew graph.

Mental model: a case-file FOLDER that each node adds pages to. It does NOT get
passed-and-discarded relay-style; it ACCUMULATES. By the time the Reporter runs,
the folder still holds the Investigator's raw evidence AND the Diagnostician's
verdict AND whatever the Reporter itself adds. The final folder is the complete
audit trail of everything the crew did.

In LangGraph, when a node returns a dict, LangGraph MERGES those keys into this
state. So each node just returns the field(s) it produced; it never rebuilds the
whole state.

We use a TypedDict (LangGraph's most common state type). Every field is optional
in practice because different runs fill different fields — e.g. a run that routes
to the Healer fills `heal_result` and leaves `report_result` empty.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from diagnostician.core.schema import Verdict
from diagnostician.agents.reporter.schema import TicketDraft
from diagnostician.agents.healer.schema import HealProposal


class CrewState(TypedDict, total=False):
    # --- INPUT: what the Investigator gathered (for now, provided directly) ---
    case_file: dict            # logs, trace, error, error_type, commit, run_context...

    # --- Added by the Diagnostician node ---
    verdict: Verdict           # label + confidence + reasoning + recommended_action

    # --- Added by exactly ONE downstream node, depending on the verdict ---
    heal_result: Optional[str]     # Healer: human-readable summary
    heal_proposal: Optional[HealProposal]  # Healer: the full structured proposal
    flake_result: Optional[str]    # FlakeHandler: quarantine/track note (flaky)
    report_result: Optional[str]      # Reporter: human-readable summary line
    ticket_draft: Optional[TicketDraft]  # Reporter: the full structured draft
    human_note: Optional[str]      # HumanReview: why it was escalated

    # --- Control / audit ---
    route: Optional[str]       # which path the router chose (for observability)
    status: Optional[str]      # e.g. "done", "awaiting_human"
