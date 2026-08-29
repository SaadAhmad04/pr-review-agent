"""
Evaluation harness for PR Review Agent.

Runs the agent against seeded bugs and measures:
- True Positives: Seeded bugs correctly identified
- False Negatives: Seeded bugs missed
- False Positives: Issues flagged that aren't seeded bugs
- Precision: TP / (TP + FP)
- Recall: TP / (TP + FN)

Compares performance before and after Judge filtering.
Breaks down results per language to prove language-agnostic design.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dataclasses import dataclass
from typing import List, Dict, Set
import json

from tests.seeded_bugs.java_spring_bugs import get_java_bugs, SeededBug as JavaBug
from tests.seeded_bugs.python_flask_bugs import get_python_bugs, SeededBug as PythonBug
from src.state import ReviewerFinding, create_initial_state
from src.graph import create_review_graph
from langgraph.checkpoint.memory import MemorySaver


@dataclass
class EvalResult:
    """Results from evaluating the agent on one seeded bug PR."""
    bug_id: str
    language: str
    category: str
    severity: str

    # Before Judge filtering
    reviewer_caught: bool
    reviewer_findings: List[ReviewerFinding]

    # After Judge filtering
    judge_caught: bool
    filtered_findings: List[ReviewerFinding]

    # Analysis
    true_positive: bool  # Bug was caught
    false_negatives: List[str]  # Missed seeded bugs
    false_positives_reviewer: List[ReviewerFinding]  # Wrong issues (before Judge)
    false_positives_judge: List[ReviewerFinding]  # Wrong issues (after Judge)


@dataclass
class LanguageStats:
    """Aggregate stats for one language."""
    language: str
    total_bugs: int

    # Before Judge
    reviewer_tp: int  # True positives
    reviewer_fn: int  # False negatives
    reviewer_fp: int  # False positives
    reviewer_precision: float
    reviewer_recall: float

    # After Judge
    judge_tp: int
    judge_fn: int
    judge_fp: int
    judge_precision: float
    judge_recall: float

    # Improvement
    precision_improvement: float
    recall_change: float


def create_mock_pr_for_bug(bug):
    """
    Create a mock PR state for a seeded bug.

    In production, this would fetch real PR data from GitHub.
    For eval, we create synthetic diffs with the buggy code.

    Returns:
        PRDiff: A synthetic PR diff object
    """
    from src.tools.diff_fetch import PRDiff, FileDiff

    # Build valid unified diff format
    # Split multi-line code into lines
    before_lines = bug.code_before.split('\n')
    after_lines = bug.code_after.split('\n')

    # Count lines for hunk header
    before_count = len(before_lines)
    after_count = len(after_lines)

    # Build diff patch with correct prefixes
    diff_lines = [f"@@ -{bug.line_start},{before_count} +{bug.line_start},{after_count} @@"]

    # Add removed lines (prefix with -)
    for line in before_lines:
        diff_lines.append(f"-{line}")

    # Add added lines (prefix with +)
    for line in after_lines:
        diff_lines.append(f"+{line}")

    patch = '\n'.join(diff_lines)

    pr_diff = PRDiff(
        pr_number=int(bug.id.split('-')[1]),
        repository="example/test-repo",
        files=[
            FileDiff(
                filename=bug.file_path,
                status="modified",
                additions=after_count,
                deletions=before_count,
                patch=patch
            )
        ],
        base_sha="abc123",
        head_sha="def456"
    )

    return pr_diff


def is_finding_match(finding: ReviewerFinding, bug) -> bool:
    """
    Check if a ReviewerFinding matches a seeded bug.

    Matching criteria:
    - Same file
    - Line within ±5 of seeded bug
    - Category matches or is closely related
    """
    if finding.file_path != bug.file_path:
        return False

    # Check line proximity (within 5 lines)
    if not (bug.line_start - 5 <= finding.line <= bug.line_end + 5):
        return False

    # Check category match (fuzzy)
    category_mapping = {
        "PERFORMANCE": ["performance"],
        "SECURITY": ["security"],
        "BUG": ["bug", "logic"],
        "BREAKING_CHANGE": ["bug", "logic", "breaking"],
        "DATA_INTEGRITY": ["bug", "logic", "data"],
    }

    expected_categories = category_mapping.get(bug.category, [])
    if finding.category.lower() in expected_categories:
        return True

    # Check if title/description mentions key terms from bug
    bug_keywords = bug.title.lower().split()
    finding_text = (finding.title + " " + finding.description).lower()

    matches = sum(1 for keyword in bug_keywords if keyword in finding_text)
    return matches >= 2  # At least 2 keyword matches


def evaluate_bug(bug, graph, language: str) -> EvalResult:
    """
    Run the agent on a single seeded bug and evaluate results.

    NOTE: This eval pass tests the Reviewer in isolation. Static analysis and
    context search are intentionally left empty (known limitation) — feeding
    real static/context data is a future enhancement.
    """
    print(f"\n  Evaluating: {bug.id} - {bug.title}")

    # Create mock PR
    pr_diff = create_mock_pr_for_bug(bug)

    # Create initial state
    state = create_initial_state(
        repository="example/test-repo",
        pr_number=pr_diff.pr_number,
        github_token=None,  # No actual GitHub access needed
    )

    # Inject the PR diff directly (skip fetch_diff_node)
    state["pr_diff"] = pr_diff

    # Detect language
    from src.language.detector import detect_languages_from_files
    files = [f.filename for f in pr_diff.files]
    state["detected_languages"] = detect_languages_from_files(files)
    state["primary_language"] = language

    # Intentionally empty for this eval pass (testing reviewer in isolation)
    # TODO: Future eval passes should feed real static analysis and context search results
    state["static_analysis_findings"] = []
    state["static_analysis_results"] = {}
    state["context_references"] = {}

    # REAL Reviewer agent call (replaces placeholder fabrication)
    from src.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent()
    try:
        reviewer_findings = agent.review(
            pr_diff=pr_diff,
            primary_language=language,
            detected_languages=state["detected_languages"],
            static_findings=state["static_analysis_findings"],  # Empty for now (known limitation)
            context_references=state["context_references"],      # Empty for now (known limitation)
        )
    except Exception as e:
        print(f"    Reviewer call failed for {bug.id}: {e}")
        reviewer_findings = []

    state["reviewer_findings"] = reviewer_findings

    # Run through Judge
    from src.agents.judge import JudgeAgent
    judge = JudgeAgent(threshold=0.6)
    filtered, verdicts = judge.judge(
        reviewer_findings=state["reviewer_findings"],
        static_findings=state["static_analysis_findings"],
        context_references=state["context_references"],
        pr_diff=pr_diff
    )

    # Check if bug was caught
    reviewer_caught = any(
        is_finding_match(f, bug) for f in state["reviewer_findings"]
    )

    judge_caught = any(
        is_finding_match(f, bug) for f in filtered
    )

    # Identify false positives (findings that don't match the seeded bug)
    # NOTE: A "FP" here means "doesn't match THIS seeded bug" — it could be
    # a real issue elsewhere in the code. This is a limitation of single-bug eval.
    reviewer_fp = [
        f for f in state["reviewer_findings"]
        if not is_finding_match(f, bug)
    ]

    judge_fp = [
        f for f in filtered
        if not is_finding_match(f, bug)
    ]

    print(f"    Reviewer caught: {reviewer_caught}")
    print(f"    Judge caught: {judge_caught}")
    print(f"    Reviewer FP: {len(reviewer_fp)}")
    print(f"    Judge FP: {len(judge_fp)}")

    return EvalResult(
        bug_id=bug.id,
        language=language,
        category=bug.category,
        severity=bug.severity,
        reviewer_caught=reviewer_caught,
        reviewer_findings=state["reviewer_findings"],
        judge_caught=judge_caught,
        filtered_findings=filtered,
        true_positive=judge_caught,
        false_negatives=[bug.id] if not judge_caught else [],
        false_positives_reviewer=reviewer_fp,
        false_positives_judge=judge_fp,
    )


def calculate_language_stats(results: List[EvalResult], language: str):
    """Calculate aggregate statistics for one language.
    
    Returns:
        LanguageStats or None: Statistics for the language, or None if no results
    """
    lang_results = [r for r in results if r.language == language]
    total = len(lang_results)

    if total == 0:
        return None

    # Before Judge
    reviewer_tp = sum(1 for r in lang_results if r.reviewer_caught)
    reviewer_fn = sum(1 for r in lang_results if not r.reviewer_caught)
    reviewer_fp = sum(len(r.false_positives_reviewer) for r in lang_results)

    reviewer_precision = (
        reviewer_tp / (reviewer_tp + reviewer_fp) if (reviewer_tp + reviewer_fp) > 0 else 0
    )
    reviewer_recall = reviewer_tp / total if total > 0 else 0

    # After Judge
    judge_tp = sum(1 for r in lang_results if r.judge_caught)
    judge_fn = sum(1 for r in lang_results if not r.judge_caught)
    judge_fp = sum(len(r.false_positives_judge) for r in lang_results)

    judge_precision = (
        judge_tp / (judge_tp + judge_fp) if (judge_tp + judge_fp) > 0 else 0
    )
    judge_recall = judge_tp / total if total > 0 else 0

    return LanguageStats(
        language=language,
        total_bugs=total,
        reviewer_tp=reviewer_tp,
        reviewer_fn=reviewer_fn,
        reviewer_fp=reviewer_fp,
        reviewer_precision=reviewer_precision,
        reviewer_recall=reviewer_recall,
        judge_tp=judge_tp,
        judge_fn=judge_fn,
        judge_fp=judge_fp,
        judge_precision=judge_precision,
        judge_recall=judge_recall,
        precision_improvement=judge_precision - reviewer_precision,
        recall_change=judge_recall - reviewer_recall,
    )


def print_separator(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_stats_table(stats: LanguageStats):
    """Print a comparison table for one language."""
    print(f"\n{stats.language.upper()} RESULTS ({stats.total_bugs} seeded bugs)\n")

    print("┌─────────────────┬──────────┬──────────┬──────────┬───────────┬─────────┐")
    print("│ Stage           │    TP    │    FN    │    FP    │ Precision │  Recall │")
    print("├─────────────────┼──────────┼──────────┼──────────┼───────────┼─────────┤")

    print(
        f"│ Before Judge    │ {stats.reviewer_tp:8} │ {stats.reviewer_fn:8} │ "
        f"{stats.reviewer_fp:8} │ {stats.reviewer_precision:8.1%} │ {stats.reviewer_recall:7.1%} │"
    )

    print(
        f"│ After Judge     │ {stats.judge_tp:8} │ {stats.judge_fn:8} │ "
        f"{stats.judge_fp:8} │ {stats.judge_precision:8.1%} │ {stats.judge_recall:7.1%} │"
    )

    print("├─────────────────┼──────────┼──────────┼──────────┼───────────┼─────────┤")

    improvement = (
        f"+{stats.precision_improvement:.1%}" if stats.precision_improvement > 0
        else f"{stats.precision_improvement:.1%}"
    )
    recall_change = (
        f"{stats.recall_change:+.1%}" if stats.recall_change != 0
        else "  0.0%"
    )

    print(f"│ Improvement     │          │          │          │ {improvement:>9} │ {recall_change:>7} │")
    print("└─────────────────┴──────────┴──────────┴──────────┴───────────┴─────────┘")


def main():
    print("\n" + "=" * 80)
    print("  PR REVIEW AGENT - EVALUATION HARNESS")
    print("=" * 80)

    print("\nThis evaluation:")
    print("  1. Runs the agent on seeded bugs in Java and Python")
    print("  2. Measures which bugs were caught vs missed")
    print("  3. Counts false positives (issues flagged incorrectly)")
    print("  4. Compares performance BEFORE and AFTER Judge filtering")
    print("  5. Breaks down results per language to prove language-agnostic design")

    # Load seeded bugs
    print_separator("Loading Seeded Bugs")

    java_bugs = get_java_bugs()
    python_bugs = get_python_bugs()

    print(f"Java bugs: {len(java_bugs)}")
    for bug in java_bugs:
        print(f"  - {bug.id}: {bug.title} ({bug.category})")

    print(f"\nPython bugs: {len(python_bugs)}")
    for bug in python_bugs:
        print(f"  - {bug.id}: {bug.title} ({bug.category})")

    # Create graph
    print_separator("Creating Review Graph")
    checkpointer = MemorySaver()
    graph = create_review_graph(checkpointer=checkpointer)
    print("Graph created")

    # Evaluate Java bugs
    print_separator("Evaluating Java Bugs")
    java_results = []
    for bug in java_bugs:
        result = evaluate_bug(bug, graph, "java")
        java_results.append(result)

    # Evaluate Python bugs
    print_separator("Evaluating Python Bugs")
    python_results = []
    for bug in python_bugs:
        result = evaluate_bug(bug, graph, "python")
        python_results.append(result)

    # Calculate stats
    all_results = java_results + python_results
    java_stats = calculate_language_stats(all_results, "java")
    python_stats = calculate_language_stats(all_results, "python")

    # Print results
    print_separator("EVALUATION RESULTS")

    if java_stats:
        print_stats_table(java_stats)
    if python_stats:
        print_stats_table(python_stats)

    # Overall stats
    print_separator("OVERALL RESULTS")

    total_bugs = len(all_results)
    overall_reviewer_tp = sum(1 for r in all_results if r.reviewer_caught)
    overall_judge_tp = sum(1 for r in all_results if r.judge_caught)

    overall_reviewer_fp = sum(len(r.false_positives_reviewer) for r in all_results)
    overall_judge_fp = sum(len(r.false_positives_judge) for r in all_results)

    overall_reviewer_precision = (
        overall_reviewer_tp / (overall_reviewer_tp + overall_reviewer_fp)
        if (overall_reviewer_tp + overall_reviewer_fp) > 0 else 0
    )
    overall_judge_precision = (
        overall_judge_tp / (overall_judge_tp + overall_judge_fp)
        if (overall_judge_tp + overall_judge_fp) > 0 else 0
    )

    overall_reviewer_recall = overall_reviewer_tp / total_bugs
    overall_judge_recall = overall_judge_tp / total_bugs

    print(f"\nTotal seeded bugs: {total_bugs}")
    print(f"\nBefore Judge:")
    print(f"  Caught: {overall_reviewer_tp}/{total_bugs} ({overall_reviewer_recall:.1%})")
    print(f"  False Positives: {overall_reviewer_fp}")
    print(f"  Precision: {overall_reviewer_precision:.1%}")
    print(f"  Recall: {overall_reviewer_recall:.1%}")

    print(f"\nAfter Judge:")
    print(f"  Caught: {overall_judge_tp}/{total_bugs} ({overall_judge_recall:.1%})")
    print(f"  False Positives: {overall_judge_fp}")
    print(f"  Precision: {overall_judge_precision:.1%}")
    print(f"  Recall: {overall_judge_recall:.1%}")

    print(f"\nImprovement:")
    print(f"  Precision: {overall_judge_precision - overall_reviewer_precision:+.1%}")
    print(f"  Recall: {overall_judge_recall - overall_reviewer_recall:+.1%}")
    print(f"  False Positives Reduced: {overall_reviewer_fp - overall_judge_fp}")

    # Language-agnostic proof
    print_separator("LANGUAGE-AGNOSTIC DESIGN VALIDATION")

    if java_stats and python_stats:
        print("\nComparing performance across languages:\n")
        print(f"Java:")
        print(f"  Precision: {java_stats.judge_precision:.1%}")
        print(f"  Recall: {java_stats.judge_recall:.1%}")
        print()
        print(f"Python:")
        print(f"  Precision: {python_stats.judge_precision:.1%}")
        print(f"  Recall: {python_stats.judge_recall:.1%}")
        print()

        precision_diff = abs(java_stats.judge_precision - python_stats.judge_precision)
        recall_diff = abs(java_stats.judge_recall - python_stats.judge_recall)

        print(f"Precision difference: {precision_diff:.1%}")
        print(f"Recall difference: {recall_diff:.1%}")
        print()

        # Honest verdict: only claim similarity when both languages have non-trivial results
        if java_stats.judge_recall > 0.3 and python_stats.judge_recall > 0.3:
            if precision_diff < 0.15 and recall_diff < 0.15:
                print("✓ Both languages show non-trivial recall (>30%) with <15% difference")
                print("✓ Results support language-agnostic design claim")
            else:
                print("⚠ Both languages have non-trivial recall, but performance difference >15%")
                print("⚠ May need language-specific tuning")
        else:
            print("⚠ Results too low to claim language-agnostic parity")
            print(f"  (At least one language has recall ≤30% — need better prompts or real static/context data)")
    else:
        print("\n⚠ Insufficient data for language comparison")

    # Detailed findings with detection hints
    print_separator("MISSED BUGS (False Negatives)")

    # Build bug lookup for accessing detection_hints
    bugs_by_id = {}
    for bug in java_bugs + python_bugs:
        bugs_by_id[bug.id] = bug

    missed_count = 0
    for result in all_results:
        if not result.judge_caught:
            missed_count += 1
            bug = bugs_by_id.get(result.bug_id)
            print(f"\n{result.bug_id} ({result.language}): {result.category}")
            if bug and bug.detection_hints:
                print(f"  Detection hints:")
                for hint in bug.detection_hints:
                    print(f"    - {hint}")

    if missed_count == 0:
        print("\n✓ No missed bugs — perfect recall!")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
