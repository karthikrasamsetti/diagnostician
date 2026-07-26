# Diagnostician

A read-only AI agent that triages CI/CD test failures. Given the context of a
failed automated test (error, logs, retry outcome, commit diff, run context), it
classifies the failure into one of four categories, assigns a confidence score,
recommends a next action, and explains its reasoning from the evidence.

```
application_bug     the app is genuinely broken         -> file a bug (dev)
broken_test         the test is stale / bad assumption  -> fix the test (QA)
flaky               true non-determinism (races)        -> quarantine & track (QA)
environment_issue   viewport / CI config / resources    -> fix pipeline (DevOps)
```

It is **read-only**: it analyzes and recommends, but never edits code, files
tickets, or touches your repo. Acting on its verdicts is left to humans (or, later,
to separate action agents behind human approval).

## Why this exists

Triaging nightly test failures by hand is slow, repetitive detective work: for every
red test a human must decide *is the app broken, or is the test broken?* -- then write
the ticket or fix the locator. This agent does the first-pass diagnosis so engineers
only make the final call.

The hard part is that very different root causes share the **same symptom**. A test
that "passes on retry / fails only in CI / passes locally" can be a true race (flaky),
a hidden test dependency (broken_test), a date/timezone assumption (broken_test), or a
viewport mismatch (environment_issue). The agent's decision rules encode how an
experienced QA engineer tells these apart, rather than keyword-matching on "intermittent".

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
# 1. install dependencies into a local .venv
uv sync --extra dev

# 2. configure a provider + key
cp .env.example .env
#    then edit .env:
#      LLM_PROVIDER=anthropic          (or openai, or mock)
#      ANTHROPIC_API_KEY=sk-ant-...    (or OPENAI_API_KEY=sk-...)

# 3. run the evaluation over the built-in fixtures
uv run diagnose-eval                    # uses LLM_PROVIDER from .env
uv run diagnose-eval --provider mock    # offline, no key, no cost
uv run diagnose-eval --provider openai  # override .env for one run

# 4. run the unit tests (fully offline)
uv run pytest
```

`mock` needs no API key -- it returns a canned verdict and is used for offline
development and the test suite.

## How it works

```
case_file (dict)
      |
      v
build_user_message ---> SYSTEM_PROMPT (decision rules)
      |                        |
      +---------> LLMProvider.classify() ---> Verdict (schema-validated)
                  (anthropic / openai / mock)
```

- **schema.py** -- the data contract. A Pydantic `Verdict` (label, confidence 0-1,
  reasoning, recommended_action) with validation. Invalid model output is rejected
  at the door rather than crashing something downstream.
- **prompt.py** -- the system prompt: role, task, and the senior-QA decision rules,
  plus a confidence gate (confidence < 0.75 forces `human_review`) and an
  asymmetric-risk rule (never mask a possible real bug).
- **providers.py** -- a provider-agnostic layer. `LLMProvider` is an abstract
  interface; `AnthropicProvider`, `OpenAIProvider`, and `MockProvider` implement it.
  A `get_provider(name)` factory swaps models via one config value. Each provider
  forces structured output via its own mechanism (tool-calling / native structured
  output), hidden behind one interface.
- **agent.py** -- glue: renders the case, calls the provider, returns a `Verdict`.
  Adds logging, retry with exponential backoff, and a safe `human_review` fallback
  if the provider fails, so a model outage can't crash the pipeline.
- **config.py** -- loads `.env` and validates the chosen provider's key is present.
- **fixtures.py** -- the test set: realistic failure cases with gold labels, weighted
  toward "trap" cases that look like one thing but are another.
- **evaluate.py** -- the eval harness: runs the agent over the fixtures and reports
  accuracy, a confusion matrix, trap accuracy, and confidence calibration.

## Design decisions worth knowing

- **Structured output over free text.** The model is forced to emit a schema-valid
  `Verdict` so downstream code can branch on `label` reliably. Note this locks the
  output *format*, not the *content* -- LLMs remain non-deterministic even at
  temperature 0.
- **Provider-agnostic by design.** The agent depends only on the abstract
  `LLMProvider`. Swapping Anthropic / OpenAI / Mock is a one-line config change --
  useful for outages, cost/quality tuning, and offline testing.
- **Calibrated humility.** Success is not just accuracy; it is *being unsure when it
  should be*. The eval measures whether confidence is lower on wrong answers, and the
  confidence gate turns low confidence into safe escalation.
- **Evaluate, don't unit-test, the judgment.** Unit tests (offline, via the mock)
  cover the deterministic machinery. The LLM's reasoning quality is measured
  separately by the eval harness -- a different activity.

## Current results (8-fixture illustrative set)

| Provider  | Accuracy | Trap accuracy | Notes                                         |
|-----------|----------|---------------|-----------------------------------------------|
| Anthropic | 8/8      | 6/6           | Well-calibrated; escalates the ambiguous case |
| OpenAI    | 7/8      | 5/6           | Wobbles near the gate on the ambiguous case   |

Results vary run-to-run (LLMs are non-deterministic). The 8-fixture set is for
learning and smoke-testing; a production claim needs a larger set mined from real
pipeline history, ideally run multiple times per case to measure variance.

## Limitations

- Read-only: it recommends, it does not act.
- The fixture set is small and illustrative, not a production benchmark.
- The Investigator (which would build real case_files from live CI runs) is not
  implemented yet -- fixtures stand in for it.

## Project layout

```
diagnostician_pkg/
├── pyproject.toml        # project + deps (uv)
├── uv.lock               # pinned versions -- commit this
├── .env.example          # template; copy to .env (git-ignored)
├── README.md
├── src/diagnostician/
│   ├── schema.py
│   ├── prompt.py
│   ├── providers.py
│   ├── agent.py
│   ├── config.py
│   ├── fixtures.py
│   └── evaluate.py
└── tests/
    └── test_diagnostician.py
```

## License

MIT (or your choice).
