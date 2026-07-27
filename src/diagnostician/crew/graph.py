"""
crew.py
-------
The CREW graph (walking skeleton). Wires the real Diagnostician together with
STUB action nodes via LangGraph, with conditional routing driven by the verdict.

Why stubs first: the genuinely new thing here is the ROUTING and STATE FLOW, not
the agents. By keeping Healer/Reporter/etc. as hollow stubs, the only real moving
part is the graph machinery — so when it works, you understand LangGraph cleanly.
Later slices replace each stub with a real agent, one at a time.

The graph shape:

    START -> diagnostician -> (router) -> healer      -> END
                                       -> flake_handler -> END
                                       -> reporter     -> END
                                       -> human_review -> END

The (router) is a CONDITIONAL EDGE: a function that reads the verdict from state
and returns the NAME of the next node.
"""

from __future__ import annotations

import logging

from langgraph.graph import StateGraph, START, END

from diagnostician.crew.state import CrewState
from diagnostician.agents.diagnostician.agent import Diagnostician
from diagnostician.core.providers import get_provider, LLMProvider
from diagnostician.core.schema import Action

logger = logging.getLogger("diagnostician.crew")


# ===========================================================================
# NODES. Each is a plain function: takes state, returns the field(s) it adds.
# LangGraph merges the returned dict into the shared CrewState.
# ===========================================================================

def make_diagnostician_node(provider: LLMProvider):
    """Factory so we can inject which provider the Diagnostician uses."""
    agent = Diagnostician(provider=provider)

    def diagnostician_node(state: CrewState) -> dict:
        verdict = agent.diagnose(state["case_file"])
        logger.info("Diagnostician -> %s (conf %.2f)",
                    verdict.label.value, verdict.confidence)
        return {"verdict": verdict}   # <-- merged into state as state["verdict"]

    return diagnostician_node


# --- STUB action nodes. Hollow on purpose; they just record what they WOULD do.
def make_healer_node(provider: LLMProvider):
    """Factory: injects the provider into the real Healer agent."""
    from diagnostician.agents.healer.agent import Healer
    healer = Healer(provider=provider)

    def healer_node(state: CrewState) -> dict:
        proposal = healer.propose(state["case_file"], state["verdict"])
        if proposal.escalate:
            summary = (f"Healer ESCALATED: could not safely determine a fix for "
                       f"'{proposal.old_locator}'. Human must update the locator. "
                       f"(No guess made — safer than a wrong locator.)")
            status = "awaiting_human"
        else:
            summary = (f"Healer PROPOSED: {proposal.old_locator} -> {proposal.new_locator} "
                       f"(confidence {proposal.confidence:.2f}). DRAFT ONLY — needs human "
                       f"approval before applying.")
            status = "awaiting_approval"
        logger.info("Healer: %s", "escalated" if proposal.escalate else "proposed a fix")
        return {"heal_result": summary, "heal_proposal": proposal,
                "route": "healer", "status": status}

    return healer_node


def make_flake_handler_node():
    """Factory for the deterministic FlakeHandler node (no provider needed)."""
    from diagnostician.agents.flake_handler.agent import FlakeHandler
    handler = FlakeHandler()  # default threshold + store

    def flake_handler_node(state: CrewState) -> dict:
        test_name = state["case_file"].get("test_name", "<unknown>")
        result = handler.handle(test_name)
        logger.info("FlakeHandler: %s (count=%d)", result.action, result.flake_count)
        return {"flake_result": result.message,
                "route": "flake_handler",
                "status": "quarantined" if result.quarantined else "done"}

    return flake_handler_node


def make_reporter_node(provider: LLMProvider):
    """Factory: injects the provider into the real Reporter agent."""
    from diagnostician.agents.reporter.agent import Reporter
    reporter = Reporter(provider=provider)

    def reporter_node(state: CrewState) -> dict:
        draft = reporter.draft(state["case_file"], state["verdict"])
        summary = (f"Drafted ticket: '{draft.summary}' (priority={draft.priority.value}). "
                   f"DRAFT ONLY — needs human review before filing.")
        logger.info("Reporter drafted a ticket (priority=%s)", draft.priority.value)
        return {"report_result": summary, "ticket_draft": draft,
                "route": "reporter", "status": "done"}

    return reporter_node


def human_review_node(state: CrewState) -> dict:
    v = state["verdict"]
    note = (f"[HumanReview] Escalated: label={v.label.value}, "
            f"confidence={v.confidence:.2f}. A human must decide.")
    logger.info("HumanReview: escalated to a person")
    return {"human_note": note, "route": "human_review", "status": "awaiting_human"}


# ===========================================================================
# THE ROUTER. A conditional-edge function: reads state, returns the NEXT node's
# NAME (a string). This is where the verdict decides the path.
# ===========================================================================

def route_by_verdict(state: CrewState) -> str:
    v = state["verdict"]

    # Safety gate first: if the agent escalated OR is not confident, a human
    # decides — regardless of label. (Mirrors the confidence gate in the prompt.)
    if v.recommended_action == Action.HUMAN_REVIEW or v.confidence < 0.75:
        return "human_review"

    # Otherwise route by label to the matching action node.
    return {
        "application_bug": "reporter",
        "environment_issue": "reporter",   # env issues also get a report (to DevOps)
        "broken_test": "healer",
        "flaky": "flake_handler",
    }.get(v.label.value, "human_review")   # unknown -> be safe, ask a human


# ===========================================================================
# BUILD THE GRAPH. Nodes + edges assembled into a runnable graph.
# ===========================================================================

def build_crew(provider: LLMProvider | None = None):
    provider = provider or get_provider()

    graph = StateGraph(CrewState)

    # Register nodes (name -> function).
    graph.add_node("diagnostician", make_diagnostician_node(provider))
    graph.add_node("healer", make_healer_node(provider))
    graph.add_node("flake_handler", make_flake_handler_node())
    graph.add_node("reporter", make_reporter_node(provider))
    graph.add_node("human_review", human_review_node)

    # Edges (the wiring):
    graph.add_edge(START, "diagnostician")          # entry -> diagnostician

    # CONDITIONAL edge: after diagnostician, the router picks the next node.
    graph.add_conditional_edges(
        "diagnostician",
        route_by_verdict,
        # map the router's return string -> the actual node name
        {
            "healer": "healer",
            "flake_handler": "flake_handler",
            "reporter": "reporter",
            "human_review": "human_review",
        },
    )

    # Each action node then goes to END (the run is complete).
    for node in ("healer", "flake_handler", "reporter", "human_review"):
        graph.add_edge(node, END)

    return graph.compile()


def _print_final_state(final_state: dict) -> None:
    print("\n--- FINAL STATE (the audit trail) ---")
    for k, val in final_state.items():
        if k == "verdict":
            print(f"  verdict: {val.label.value} (conf {val.confidence:.2f}, "
                  f"action {val.recommended_action.value})")
        else:
            print(f"  {k}: {val}")


def main() -> None:
    """CLI entry point. Exposed as `diagnose-crew` via pyproject.toml scripts."""
    import argparse
    import os
    from diagnostician.core import config  # loads .env
    from diagnostician.agents.diagnostician.fixtures import FIXTURES

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s | %(name)s | %(message)s")

    parser = argparse.ArgumentParser(description="Run a failure through the triage crew.")
    parser.add_argument("--provider", default=None,
                        help="mock | anthropic | openai (default: LLM_PROVIDER from .env)")
    parser.add_argument("--fixture", default=None,
                        help="fixture id to run (default: run ALL fixtures)")
    args = parser.parse_args()

    provider_name = args.provider or os.getenv("LLM_PROVIDER") or "mock"
    config.require_key_for(provider_name)
    crew = build_crew(get_provider(provider_name))

    # Pick which fixtures to run.
    if args.fixture:
        selected = [f for f in FIXTURES if f.id == args.fixture]
        if not selected:
            raise SystemExit(f"No fixture with id '{args.fixture}'. "
                             f"Available: {[f.id for f in FIXTURES]}")
    else:
        selected = FIXTURES

    for fx in selected:
        print(f"\n{'='*60}\nRunning crew on: {fx.id}  (expected: {fx.expected_label})")
        final_state = crew.invoke({"case_file": fx.case_file})
        _print_final_state(final_state)


if __name__ == "__main__":
    main()
