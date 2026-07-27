"""
api.py
------
FastAPI backend that exposes the triage crew over HTTP, so a UI (or any client)
can run triages and record human approve/reject decisions.

Design:
  - POST /triage        run the crew on a case_file (or a named fixture) -> result
  - GET  /fixtures      list the example failures a UI can offer
  - GET  /runs          list past runs (in-memory this session)
  - GET  /runs/{id}     fetch one run's full result
  - POST /runs/{id}/decision   record a human's approve/reject on a gated action

The crew returns rich Pydantic objects; the API converts the final state into
clean JSON. Runs are held in an in-memory store for now — a real database is a
later step. This is the honest MVP: get the shape right first, persist later.

Run locally:  uv run diagnose-api    (or: uvicorn diagnostician.api:app --reload)
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from diagnostician.core import config  # loads .env
from diagnostician.core.providers import get_provider
from diagnostician.crew.graph import build_crew
from diagnostician.agents.diagnostician.fixtures import FIXTURES

logger = logging.getLogger("diagnostician.api")

app = FastAPI(title="Diagnostician Crew API", version="0.1.0")

# Allow a local frontend (any origin in dev) to call the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # dev only; lock this down for real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-memory store (this session only). A DB replaces this later. ----------
_RUNS: dict[str, dict] = {}


# --- Request/response models --------------------------------------------------
class TriageRequest(BaseModel):
    case_file: Optional[dict] = None    # provide a raw case file...
    fixture_id: Optional[str] = None    # ...or name a built-in fixture
    provider: Optional[str] = None      # override provider; default from .env


class DecisionRequest(BaseModel):
    decision: str                        # "approve" or "reject"
    note: Optional[str] = None           # optional human note


# --- Helpers ------------------------------------------------------------------
def _state_to_json(state: dict) -> dict:
    """Convert a crew final-state (with Pydantic objects) into clean JSON."""
    out: dict = {}
    for key, val in state.items():
        if isinstance(val, BaseModel):
            out[key] = val.model_dump(mode="json")
        else:
            out[key] = val
    return out


def _needs_approval(result: dict) -> bool:
    """Does this run have a human-gated action pending?"""
    status = result.get("status", "")
    return status in ("awaiting_approval", "awaiting_human")


# --- Endpoints ----------------------------------------------------------------
@app.get("/fixtures")
def list_fixtures() -> list[dict]:
    """List the example failures a UI can offer in a dropdown."""
    return [
        {"id": f.id, "expected_label": f.expected_label,
         "trap": f.trap, "test_name": f.case_file.get("test_name")}
        for f in FIXTURES
    ]


@app.post("/triage")
def run_triage(req: TriageRequest) -> dict:
    """Run the crew on a case_file (or named fixture) and store the result."""
    # Resolve the case file.
    if req.case_file:
        case_file = req.case_file
    elif req.fixture_id:
        match = next((f for f in FIXTURES if f.id == req.fixture_id), None)
        if not match:
            raise HTTPException(404, f"No fixture '{req.fixture_id}'")
        case_file = match.case_file
    else:
        raise HTTPException(400, "Provide either case_file or fixture_id")

    provider_name = req.provider or os.getenv("LLM_PROVIDER") or "mock"
    try:
        config.require_key_for(provider_name)
    except EnvironmentError as e:
        raise HTTPException(400, str(e))

    logger.info("Triage run (provider=%s)", provider_name)
    crew = build_crew(get_provider(provider_name))
    final_state = crew.invoke({"case_file": case_file})
    result = _state_to_json(final_state)

    run_id = uuid.uuid4().hex[:12]
    _RUNS[run_id] = {
        "id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "result": result,
        "needs_approval": _needs_approval(result),
        "decision": None,   # filled when a human approves/rejects
    }
    return _RUNS[run_id]


@app.get("/runs")
def list_runs() -> list[dict]:
    """List past runs this session (most recent first)."""
    return sorted(_RUNS.values(), key=lambda r: r["created_at"], reverse=True)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = _RUNS.get(run_id)
    if not run:
        raise HTTPException(404, f"No run '{run_id}'")
    return run


@app.post("/runs/{run_id}/decision")
def record_decision(run_id: str, req: DecisionRequest) -> dict:
    """Record a human's approve/reject on a gated action (the human-in-the-loop)."""
    run = _RUNS.get(run_id)
    if not run:
        raise HTTPException(404, f"No run '{run_id}'")
    if req.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    if not run["needs_approval"]:
        raise HTTPException(400, "This run has no action awaiting a decision.")

    run["decision"] = {
        "decision": req.decision,
        "note": req.note,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    # NOTE: acting on an approval (opening the PR / filing the ticket) is a
    # separate, deliberately-unbuilt step. For now we only RECORD the decision.
    logger.info("Run %s decision: %s", run_id, req.decision)
    return run


@app.get("/")
def root() -> dict:
    return {"service": "Diagnostician Crew API", "runs_this_session": len(_RUNS)}


def main() -> None:
    """Entry point: `uv run diagnose-api`."""
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    uvicorn.run("diagnostician.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
