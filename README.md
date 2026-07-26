# Diagnostician

An AI agent crew that triages CI/CD test failures. When an automated test fails,
the crew figures out *why* it failed, then routes to the right follow-up action —
drafting a bug ticket, quarantining a flaky test, or escalating to a human — so
engineers stop spending mornings on manual triage.

It is built as a set of small, single-purpose agents wired together with
LangGraph. Some agents use an LLM (where judgment is needed); others are plain
deterministic code (where arithmetic is enough). All actions that touch the real
world are draft-only and human-gated.

## The crew

```
                            ┌──────────────┐
   test failure ──────────► │ Diagnostician│  classify: bug / broken / flaky / env
   (case_file)              └──────┬───────┘
                                   │ router (by verdict + confidence gate)
             ┌─────────────┬───────┴────────┬──────────────┐
             ▼             ▼                ▼              ▼
        ┌─────────┐  ┌──────────┐    ┌─────────────┐ ┌──────────────┐
        │ Reporter│  │  Healer  │    │FlakeHandler │ │ Human Review │
        │ (LLM)   │  │ (stub)   │    │ (plain code)│ │  (escalate)  │
        └─────────┘  └──────────┘    └─────────────┘ └──────────────┘
        drafts a bug  proposes a      tracks flakes,   unsure / low
        ticket        locator fix     quarantines      confidence
```

| Agent | Kind | Job | Status |
|-------|------|-----|--------|
| Diagnostician | LLM | Classify the failure into `application_bug` / `broken_test` / `flaky` / `environment_issue`, with confidence + reasoning | ✅ done |
| Reporter | LLM | Draft an actionable bug ticket (summary, repro steps, expected/actual, priority, evidence) | ✅ done |
| FlakeHandler | plain code | Track how often a test flakes; quarantine at a threshold | ✅ done |
| Healer | LLM | Propose a fix for a stale locator (draft PR, human-approved) | ⏳ stub |
| Investigator | integration | Build real `case_file`s from live CI/GitHub Actions | ⏳ not built |

## Design principles

- **Right tool per node.** LLMs are used only where *judgment* is needed
  (classifying, ticket-writing). Counting flakes and comparing to a threshold is
  plain deterministic code — faster, free, and exact. A crew mixes both freely.
- **Structured output everywhere.** Every LLM agent is forced to emit a validated
  Pydantic object (a `Verdict`, a `TicketDraft`), so downstream code can rely on
  the shape. Format is locked even though LLM content is not.
- **Provider-agnostic.** Agents talk to an abstract `LLMProvider` with a generic
  `structured(system, user, schema)` method. Swap Anthropic ↔ OpenAI ↔ an offline
  Mock via one config value.
- **Calibrated humility + human gates.** Low confidence routes to human review.
  Actions that touch the real world (file a ticket, rewrite a test) are drafted,
  never executed — a human approves.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
# install deps into a local .venv
uv sync --extra dev

# configure a provider + key
cp .env.example .env
#   edit .env:  LLM_PROVIDER=anthropic   and   ANTHROPIC_API_KEY=sk-ant-...
#   (or LLM_PROVIDER=openai + OPENAI_API_KEY, or LLM_PROVIDER=mock for offline)

# run the eval harness over the Diagnostician's fixtures
uv run diagnose-eval                       # uses .env provider
uv run diagnose-eval --provider mock       # offline, no key

# run a failure through the whole CREW
uv run diagnose-crew                                  # all fixtures
uv run diagnose-crew --fixture migration_broke_data   # one case
uv run diagnose-crew --provider anthropic --fixture true_parallel_race

# run the tests (fully offline)
uv run pytest
```

## Project layout

```
src/diagnostician/
├── core/                     # shared plumbing, used by all agents
│   ├── schema.py             #   Verdict (Diagnostician's output contract)
│   ├── providers.py          #   LLM factory: anthropic / openai / mock
│   └── config.py             #   .env loading + key validation
├── agents/                   # one folder per agent
│   ├── diagnostician/        #   agent.py, prompt.py, fixtures.py
│   ├── reporter/             #   agent.py, prompt.py, schema.py (TicketDraft)
│   └── flake_handler/        #   agent.py (deterministic, no LLM)
├── crew/                     # orchestration
│   ├── state.py              #   CrewState (the accumulating shared state)
│   └── graph.py              #   LangGraph wiring + routing
└── evaluate.py               # eval harness (accuracy, confusion, calibration)
tests/                        # offline unit tests (27)
```

## How the pieces fit

- **State accumulates.** A `CrewState` flows through the graph like a case-file
  folder each node adds to: the Diagnostician adds a `verdict`, the Reporter adds
  a `ticket_draft`, the FlakeHandler adds a `flake_result`. The final state is a
  complete audit trail of what happened and why.
- **Routing is a conditional edge.** After the Diagnostician runs, a router reads
  the verdict: high-confidence `application_bug` → Reporter, `broken_test` →
  Healer, `flaky` → FlakeHandler, and anything low-confidence or ambiguous →
  Human Review.
- **Nodes are just functions.** Each takes the state and returns the fields it
  adds. The Diagnostician (LLM), the Reporter (LLM), and the FlakeHandler (plain
  code) are all the same shape to the graph.

## Evaluation

The Diagnostician is evaluated (not unit-tested) on a labeled fixture set that is
deliberately weighted toward "trap" cases — failures that look like one category
but are another (a date-dependent test that looks flaky but is a stale test
assumption; a migration that looks like a data glitch but is a real bug). The
harness reports accuracy, a confusion matrix, trap accuracy, and confidence
calibration (is the agent less confident when it's wrong?).

On the 8-fixture illustrative set, Anthropic scores 8/8 and OpenAI ~7/8, with
OpenAI wobbling near the confidence gate on the genuinely ambiguous case. Results
vary run-to-run because LLMs are non-deterministic; a production claim needs a
larger set mined from real pipeline history, run multiple times per case.

## Limitations

- The Healer is a stub; the Investigator is not built yet, so the crew currently
  runs on hand-made fixtures rather than live CI data.
- All real-world actions are drafts — nothing is auto-filed or auto-committed.
- The fixture set is small and illustrative, not a production benchmark.

## License

MIT.