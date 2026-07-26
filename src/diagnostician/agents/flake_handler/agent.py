"""
agent.py (flake_handler)
------------------------
The FlakeHandler. Unlike the Diagnostician and Reporter, this node uses NO LLM —
it is pure deterministic logic. Its job (counting flakes, comparing to a
threshold) needs arithmetic, not judgment, so an LLM would only add latency,
cost, and non-determinism. Right tool for the job.

Behavior:
  - Record each flake for a test (count + last-seen timestamp) in a small JSON store.
  - Under the threshold  -> "track and keep watching".
  - At/over the threshold -> "quarantine": flag the test to stop blocking the
    pipeline, and escalate to a human to fix the root cause.

Persistence is a plain JSON file. A full DB (TinyDB, etc.) would be over-
engineering for a dict of counts — another "right tool" call.

We deliberately do NOT suggest code fixes here (that's an LLM/code-reading task,
a separate future capability). This node does one thing well.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("diagnostician.flake_handler")

DEFAULT_THRESHOLD = 5
DEFAULT_STORE = Path.home() / ".diagnostician" / "flake_history.json"


@dataclass
class FlakeResult:
    """Structured result of handling one flaky occurrence."""
    test_name: str
    flake_count: int          # total recorded flakes for this test
    threshold: int
    quarantined: bool         # did this occurrence push it to/over the threshold?
    action: str               # "track" or "quarantine"
    message: str              # human-readable summary

    def to_summary(self) -> str:
        return self.message


class FlakeHandler:
    """Tracks flakiness per test and quarantines chronically flaky tests."""

    def __init__(self, threshold: int = DEFAULT_THRESHOLD,
                 store_path: Path | str = DEFAULT_STORE):
        self.threshold = threshold
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._history = self._load()

    # --- persistence (plain JSON) ------------------------------------------
    def _load(self) -> dict:
        if self.store_path.exists():
            try:
                return json.loads(self.store_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not read flake store (%s); starting fresh.", e)
        return {}

    def _save(self) -> None:
        try:
            self.store_path.write_text(json.dumps(self._history, indent=2))
        except OSError as e:
            logger.error("Could not write flake store: %s", e)

    # --- core logic --------------------------------------------------------
    def handle(self, test_name: str) -> FlakeResult:
        """Record a flake for `test_name` and decide track vs. quarantine."""
        record = self._history.get(test_name, {"count": 0, "history": []})
        record["count"] += 1
        record["history"].append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        # Keep only the last 20 timestamps to bound file growth.
        record["history"] = record["history"][-20:]
        self._history[test_name] = record
        self._save()

        count = record["count"]
        quarantined = count >= self.threshold

        if quarantined:
            action = "quarantine"
            message = (
                f"Test '{test_name}' has flaked {count} times (threshold {self.threshold}). "
                f"QUARANTINED: isolate it so it stops blocking the pipeline, and escalate "
                f"to a human to fix the root cause (likely a race/timing/isolation issue). "
                f"This is NOT a code fix — it stops the bleeding while a human investigates."
            )
            logger.info("Quarantining '%s' (count=%d >= %d)", test_name, count, self.threshold)
        else:
            action = "track"
            remaining = self.threshold - count
            message = (
                f"Test '{test_name}' flaked (count {count}/{self.threshold}). "
                f"Tracking — {remaining} more flake(s) before auto-quarantine. "
                f"No action needed yet; monitoring for a pattern."
            )
            logger.info("Tracking '%s' (count=%d, %d to threshold)",
                        test_name, count, remaining)

        return FlakeResult(
            test_name=test_name, flake_count=count, threshold=self.threshold,
            quarantined=quarantined, action=action, message=message,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    # Demo: flake the same test until it quarantines. Uses a temp store so it
    # doesn't pollute the real history.
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "demo_flakes.json"
    handler = FlakeHandler(threshold=3, store_path=tmp)

    print("Simulating repeated flakes of 'test_wobbly':\n")
    for i in range(4):
        result = handler.handle("test_wobbly")
        print(f"  occurrence {i+1}: action={result.action:10s} count={result.flake_count} "
              f"quarantined={result.quarantined}")
    print("\nNote how it flips from 'track' to 'quarantine' at the threshold.")
