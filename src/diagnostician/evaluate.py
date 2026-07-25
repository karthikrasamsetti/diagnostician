"""
evaluate.py
-----------
The EVALUATION HARNESS. Runs the Diagnostician over every fixture, compares each
verdict to the gold label, and reports metrics that reflect BOTH of our success
criteria:

  1. Correctness  -> overall accuracy, per-label confusion matrix, trap accuracy
  2. Calibration  -> is confidence HIGHER on correct answers than on wrong ones?
                     (An agent that is wrong but knows it -- low confidence -- is safe.
                      An agent that is wrong and confident is dangerous.)

This is the same discipline as DeepEval / LLM-as-judge work: define ground truth,
run the system, score against truth, inspect failures.

Special handling: the 'ambiguous_timeout' fixture is graded as a PASS if the agent
EITHER lands on application_bug OR escalates to human_review with low confidence,
because being honestly unsure is the correct behavior on a genuinely ambiguous case.
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
from collections import defaultdict

from diagnostician.schema import Verdict, Action
from diagnostician.agent import Diagnostician
from diagnostician.providers import get_provider
from diagnostician.fixtures import FIXTURES, Fixture
from diagnostician import config  # importing this loads .env

logger = logging.getLogger("diagnostician.evaluate")


def is_correct(fx: Fixture, verdict: Verdict) -> bool:
    """Grade one verdict against the gold label, with the ambiguous-case rule."""
    if fx.id == "ambiguous_timeout":
        # Ideal: honest escalation to a human on a genuinely 50/50 case.
        if verdict.recommended_action == Action.HUMAN_REVIEW:
            return True
        # Also acceptable: leaning to the safe side (a real bug) via file_bug.
        if (verdict.label.value == "application_bug"
                and verdict.recommended_action == Action.FILE_BUG):
            return True
        # Anything that MASKS a possible bug (quarantine/heal/fix_env) is a FAIL,
        # even if the label happens to match — the dangerous outcome we guard against.
        return False
    return verdict.label.value == fx.expected_label


def run_evaluation(provider_name: str) -> None:
    agent = Diagnostician(provider=get_provider(provider_name))

    results = []           # (fixture, verdict, correct?)
    confusion = defaultdict(lambda: defaultdict(int))  # gold -> predicted -> count
    conf_correct, conf_wrong = [], []

    print(f"\n{'='*72}\nEVALUATING with provider = {provider_name}\n{'='*72}")

    for fx in FIXTURES:
        verdict = agent.diagnose(fx.case_file)
        correct = is_correct(fx, verdict)
        results.append((fx, verdict, correct))
        confusion[fx.expected_label][verdict.label.value] += 1
        (conf_correct if correct else conf_wrong).append(verdict.confidence)

        mark = "PASS" if correct else "FAIL"
        trap = " [TRAP]" if fx.trap else ""
        print(f"\n[{mark}]{trap} {fx.id}")
        print(f"   expected : {fx.expected_label}")
        print(f"   predicted: {verdict.label.value}  (confidence {verdict.confidence:.2f}, "
              f"action {verdict.recommended_action.value})")
        if not correct:
            print(f"   WHY IT SHOULD BE {fx.expected_label}: {fx.why}")
        # Show a snippet of the agent's own reasoning so we can inspect its thinking.
        print(f"   agent reasoning: {verdict.reasoning[:160]}"
              + ("..." if len(verdict.reasoning) > 160 else ""))

    # ---- METRICS ----------------------------------------------------------
    total = len(results)
    n_correct = sum(1 for _, _, c in results if c)
    traps = [(fx, v, c) for fx, v, c in results if fx.trap]
    trap_correct = sum(1 for _, _, c in traps if c)

    print(f"\n{'='*72}\nRESULTS\n{'='*72}")
    print(f"Overall accuracy : {n_correct}/{total} = {n_correct/total:.0%}"
          f"   (target: >= 80%)")
    if traps:
        print(f"Trap accuracy    : {trap_correct}/{len(traps)} = {trap_correct/len(traps):.0%}"
              f"   (the cases that actually matter)")

    print("\nConfusion matrix (rows = truth, columns = predicted):")
    labels = ["application_bug", "broken_test", "flaky", "environment_issue"]
    header = "  " + "gold\\pred".ljust(18) + "".join(l[:9].ljust(11) for l in labels)
    print(header)
    for g in labels:
        row = "  " + g.ljust(18)
        for p in labels:
            row += str(confusion[g].get(p, 0)).ljust(11)
        print(row)

    # ---- CALIBRATION: the part naive evals skip ---------------------------
    print("\nConfidence calibration (are we humble when wrong?):")
    if conf_correct:
        print(f"  avg confidence when CORRECT : {statistics.mean(conf_correct):.2f}")
    if conf_wrong:
        print(f"  avg confidence when WRONG   : {statistics.mean(conf_wrong):.2f}"
              "   <-- should be LOWER than 'correct'")
        gap = (statistics.mean(conf_correct) if conf_correct else 0) - statistics.mean(conf_wrong)
        verdict_msg = ("GOOD: more confident when right than wrong."
                       if gap > 0 else
                       "WARNING: not more confident when right -- poor calibration / overconfidence.")
        print(f"  calibration gap (correct - wrong): {gap:+.2f}   {verdict_msg}")
    else:
        print("  (no wrong answers to measure calibration against)")


def main() -> None:
    """CLI entry point. Exposed as `diagnose-eval` via pyproject.toml scripts."""
    logging.basicConfig(level=logging.WARNING,  # keep INFO noise down during eval
                        format="%(levelname)s | %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default=None,
                        help="mock | anthropic | openai (default: read LLM_PROVIDER from .env)")
    args = parser.parse_args()
    # CLI flag wins if given; otherwise fall back to .env's LLM_PROVIDER, then 'mock'
    provider = args.provider or os.getenv("LLM_PROVIDER") or "mock"
    config.require_key_for(provider)
    run_evaluation(provider)


if __name__ == "__main__":
    main()