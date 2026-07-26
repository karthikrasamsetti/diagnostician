"""
fixtures.py
-----------
Our TEST SET (the "answer key"). Each fixture is a realistic case_file paired
with the gold (correct) label + expected action, plus a note on WHY.

These are deliberately weighted toward the HARD trap cases we identified together
— the ones that look like one thing but are another. An agent that only handles
obvious cases is untested where it matters.

`expected_label` is the ground truth we grade against in Step 7 (Evaluation).
`trap` flags cases specifically designed to fool a naive keyword-matcher.
"""

from dataclasses import dataclass, field


@dataclass
class Fixture:
    id: str
    case_file: dict
    expected_label: str
    why: str
    trap: bool = False
    tags: list[str] = field(default_factory=list)


FIXTURES: list[Fixture] = [

    # ---- EASY / BASELINE CASES (should be straightforward) ------------------
    Fixture(
        id="bug_api_500",
        case_file={
            "test_name": "test_checkout_returns_200",
            "error_message": "AssertionError: expected status 200, got 500",
            "error_type": "AssertionError",
            "logs": "POST /api/checkout -> 500; app stacktrace: NullPointerException in PricingService",
            "retry_result": "failed identically on all 3 retries",
            "commit_message": "refactor: rewrite PricingService discount logic",
            "diff_summary": "PricingService.calculate() heavily modified",
            "history": "test has passed reliably for 6 months",
        },
        expected_label="application_bug",
        why="Value mismatch (500), app-side stacktrace, consistent on retry, and the "
            "commit changed exactly the failing component. Textbook real bug.",
        tags=["api", "clear"],
    ),
    Fixture(
        id="broken_locator",
        case_file={
            "test_name": "test_login_button_click",
            "error_message": "Error: locator resolved to 0 elements for '#login-btn'",
            "error_type": "TimeoutError",
            "logs": "waiting for selector '#login-btn' ... 0 matches",
            "retry_result": "failed identically on all retries",
            "commit_message": "feat: redesign auth page",
            "diff_summary": "renamed #login-btn to #signin-btn in login.html",
            "history": "passed until this commit",
        },
        expected_label="broken_test",
        why="Element genuinely absent (0 matches), consistent on retry, and the diff "
            "shows the exact locator was renamed. App works; test is stale.",
        tags=["ui", "locator", "clear"],
    ),

    # ---- TRAP CASES (look like one thing, are another) ----------------------
    Fixture(
        id="date_dependent_tracking",
        case_file={
            "test_name": "test_daily_tracking_report",
            "error_message": "AssertionError: expected 1+ rows, got 0",
            "error_type": "AssertionError",
            "logs": "query returned empty result set",
            "retry_result": "fails in CI; passes when run locally during the day",
            "commit_message": "chore: update logging config",
            "diff_summary": "no change to tracking feature or DB schema",
            "query": "SELECT * FROM tracking WHERE event_date = CURRENT_DATE",
            "run_context": "CI runs in UTC at 00:15; developer runs locally in IST daytime",
            "history": "passed 40 times, 3 failures — all clustered around midnight UTC",
        },
        expected_label="broken_test",
        why="LOOKS flaky (passes locally, fails in CI, intermittent) but is DETERMINISTIC "
            "given the date: query filters CURRENT_DATE, failures cluster at midnight UTC, "
            "no app/schema change. Temporal data assumption -> broken_test.",
        trap=True,
        tags=["data", "date", "timezone", "looks-flaky"],
    ),
    Fixture(
        id="true_parallel_race",
        case_file={
            "test_name": "test_update_shared_cart",
            "error_message": "AssertionError: expected cart total 50, got 30",
            "error_type": "AssertionError",
            "logs": "two workers modified cart id=SHARED_TEST_CART concurrently",
            "retry_result": "passes when run in isolation; fails ~30% during parallel suite",
            "commit_message": "ci: increase parallel workers from 2 to 8",
            "diff_summary": "only CI concurrency changed; no app or test-data change",
            "history": "started failing intermittently right after parallelism increased",
        },
        expected_label="flaky",
        why="TRUE non-determinism: two workers race on the SAME shared cart. Passes in "
            "isolation, ~30% fail in parallel, onset correlates with more workers. "
            "Genuine race -> flaky.",
        trap=True,
        tags=["race", "parallel", "genuinely-flaky"],
    ),
    Fixture(
        id="test_dependency_ordering",
        case_file={
            "test_name": "test_view_created_order",
            "error_message": "AssertionError: order not found",
            "error_type": "AssertionError",
            "logs": "GET /orders/latest -> 404; expected order created by prior test",
            "retry_result": "passes on 3rd retry once the create-order test has finished",
            "commit_message": "test: split order tests into separate files",
            "diff_summary": "test execution order changed; app unchanged",
            "history": "began failing after tests were reorganized",
        },
        expected_label="broken_test",
        why="LOOKS flaky (passes on retry) but the cause is a HIDDEN DEPENDENCY: this test "
            "needs data a prior test creates, and reorg changed the order. Structural test "
            "flaw, not non-determinism -> broken_test.",
        trap=True,
        tags=["dependency", "ordering", "looks-flaky"],
    ),
    Fixture(
        id="viewport_environment",
        case_file={
            "test_name": "test_mobile_menu_visible",
            "error_message": "Error: element '.hamburger' is not visible",
            "error_type": "TimeoutError",
            "logs": "element exists in DOM but has display:none at 1920px width",
            "retry_result": "fails every time in CI; passes on developer laptop",
            "commit_message": "chore: bump dependencies",
            "diff_summary": "no app or test logic change",
            "run_context": "CI viewport defaults to 1920x1080; test targets a mobile menu",
            "history": "consistently fails only in CI",
        },
        expected_label="environment_issue",
        why="Element exists but hidden at desktop width; CI uses 1920px, test targets mobile "
            "menu. Deterministic, tied to VIEWPORT config, not app/test logic -> environment_issue.",
        trap=True,
        tags=["viewport", "environment", "config"],
    ),
    Fixture(
        id="migration_broke_data",
        case_file={
            "test_name": "test_user_profile_loads",
            "error_message": "AssertionError: expected profile name, got null",
            "error_type": "AssertionError",
            "logs": "SELECT full_name FROM users -> column does not exist",
            "retry_result": "fails identically on all retries",
            "commit_message": "db: migration - split full_name into first/last",
            "diff_summary": "migration dropped full_name column; app query NOT updated",
            "history": "passed until this migration",
        },
        expected_label="application_bug",
        why="LOOKS like a data problem, but a migration dropped a column the APP still "
            "queries and the app was NOT updated -> the app is now broken in production. "
            "Real regression -> application_bug (asymmetric risk: never dismiss as test data).",
        trap=True,
        tags=["data", "migration", "looks-like-data-issue"],
    ),

    # ---- AMBIGUOUS CASE (should trigger low confidence / human_review) ------
    Fixture(
        id="ambiguous_timeout",
        case_file={
            "test_name": "test_dashboard_loads",
            "error_message": "TimeoutError: waiting for selector '.chart' exceeded 5000ms",
            "error_type": "TimeoutError",
            "logs": "chart sometimes renders in 4.8s, sometimes 5.2s",
            "retry_result": "passes ~50% of retries",
            "commit_message": "feat: add heavy analytics query to dashboard",
            "diff_summary": "new query added that MAY have slowed the dashboard",
            "history": "no prior failures; started this sprint",
        },
        expected_label="application_bug",
        why="Genuinely ambiguous: could be a too-tight timeout (broken_test) OR a real "
            "performance regression from the new query (application_bug). Signals conflict. "
            "The IDEAL outcome is human_review (honest escalation). We also accept "
            "application_bug (leaning to the safe side per asymmetric risk). We do NOT "
            "accept a 'make it disappear' action (quarantine/heal) on a possible bug.",
        trap=True,
        tags=["ambiguous", "timeout", "performance", "conflict"],
    ),
]


if __name__ == "__main__":
    print(f"Total fixtures: {len(FIXTURES)}")
    traps = [f for f in FIXTURES if f.trap]
    print(f"Trap cases (designed to fool naive matchers): {len(traps)}")
    print("\nBreakdown by expected label:")
    from collections import Counter
    counts = Counter(f.expected_label for f in FIXTURES)
    for label, n in counts.items():
        print(f"  {label:20s} {n}")
    print("\nTrap cases:")
    for f in traps:
        print(f"  [{f.expected_label:17s}] {f.id} — {f.tags}")
