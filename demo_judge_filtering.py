"""
Demo script showing Judge agent filtering in action.

This creates mock data to demonstrate how the Judge scores and filters
Reviewer findings based on grounding evidence.

Run this to see:
1. Raw Reviewer findings (before filtering)
2. Judge scores and reasoning for each
3. Filtered findings (after filtering)
4. Comparison statistics
"""

from src.state import ReviewerFinding
from src.static_analysis.base import Finding, Severity
from src.context_search.base import CodeReference, ReferenceType
from src.agents.judge import JudgeAgent


def create_mock_pr_diff():
    """Create a mock PR diff for testing."""
    from src.tools.diff_fetch import PRDiff, FileDiff

    files = [
        FileDiff(
            filename="src/api/users.py",
            status="modified",
            additions=15,
            deletions=3,
            patch="""@@ -10,7 +10,10 @@ def get_user(user_id):
+    if not user_id:
+        raise ValueError("user_id is required")
+
     query = f"SELECT * FROM users WHERE id = {user_id}"
-    return db.execute(query)
+    cursor.execute(query)
+    return cursor.fetchone()
"""
        ),
        FileDiff(
            filename="src/api/auth.py",
            status="modified",
            additions=5,
            deletions=2,
            patch="""@@ -25,5 +25,8 @@ def authenticate(token):
+    if token is None:
+        return None
     user = decode_token(token)
-    return user
+    return user if user else None
"""
        ),
    ]

    return PRDiff(
        pr_number=123,
        repository="example/repo",
        files=files,
        base_sha="abc123",
        head_sha="def456"
    )


def create_mock_static_findings():
    """Create mock static analysis findings."""
    return [
        # This will support Reviewer finding #1
        Finding(
            file_path="src/api/users.py",
            line=13,
            column=12,
            severity=Severity.ERROR,
            rule_id="bandit:B608",
            message="Possible SQL injection vector through string concatenation",
            source="bandit"
        ),
        # This is unrelated
        Finding(
            file_path="src/api/users.py",
            line=3,
            column=0,
            severity=Severity.WARNING,
            rule_id="pylint:C0301",
            message="Line too long (120/100)",
            source="pylint"
        ),
        # This will support Reviewer finding #4 (nearby)
        Finding(
            file_path="src/api/auth.py",
            line=28,
            column=5,
            severity=Severity.WARNING,
            rule_id="pylint:R1705",
            message="Unnecessary else after return",
            source="pylint"
        ),
    ]


def create_mock_context_references():
    """Create mock context search results."""
    return {
        "decode_token": [
            CodeReference(
                file_path="src/api/auth.py",
                line_number=10,
                column=0,
                context="def decode_token(token):",
                reference_type=ReferenceType.DEFINITION,
                symbol="decode_token"
            ),
            CodeReference(
                file_path="src/api/auth.py",
                line_number=27,
                column=12,
                context="    user = decode_token(token)",
                reference_type=ReferenceType.USAGE,
                symbol="decode_token"
            ),
        ],
        "execute": [
            CodeReference(
                file_path="src/database/connection.py",
                line_number=45,
                column=8,
                context="    cursor.execute(sql, params)",
                reference_type=ReferenceType.USAGE,
                symbol="execute"
            ),
        ],
    }


def create_mock_reviewer_findings():
    """
    Create mock Reviewer findings with different quality levels.

    These represent what the Reviewer agent might generate:
    - Some are well-grounded (supported by static analysis)
    - Some are plausible but unsupported
    - Some are weak/speculative
    - Some reference invalid locations
    """
    return [
        # Finding #1: HIGH QUALITY - Directly supported by static analysis
        ReviewerFinding(
            file_path="src/api/users.py",
            line=13,
            severity="critical",
            category="security",
            title="SQL Injection Vulnerability",
            description="User input `user_id` is directly concatenated into SQL query "
                       "without sanitization. An attacker could inject arbitrary SQL.",
            suggestion="Use parameterized queries: cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))",
            confidence=0.95
        ),

        # Finding #2: MEDIUM QUALITY - Plausible but no direct evidence
        ReviewerFinding(
            file_path="src/api/users.py",
            line=10,
            severity="major",
            category="bug",
            title="Missing Error Handling",
            description="The function doesn't handle the case where user_id is None or invalid. "
                       "This could cause database errors.",
            suggestion="Add validation: if not user_id: raise ValueError('Invalid user_id')",
            confidence=0.7
        ),

        # Finding #3: LOW QUALITY - Speculative, no evidence
        ReviewerFinding(
            file_path="src/api/users.py",
            line=15,
            severity="minor",
            category="performance",
            title="Potential N+1 Query Problem",
            description="This query might be called in a loop, causing N+1 query issues.",
            suggestion="Consider using batch queries or joins.",
            confidence=0.4
        ),

        # Finding #4: MEDIUM-HIGH QUALITY - Supported by nearby static finding
        ReviewerFinding(
            file_path="src/api/auth.py",
            line=29,
            severity="minor",
            category="style",
            title="Redundant Return Statement",
            description="The else branch is unnecessary after an early return. "
                       "This makes the code harder to read.",
            suggestion="Remove the else and reduce indentation: return user if user else None",
            confidence=0.8
        ),

        # Finding #5: ZERO QUALITY - Invalid location (hallucination)
        ReviewerFinding(
            file_path="src/api/nonexistent.py",
            line=100,
            severity="major",
            category="bug",
            title="Race Condition in Cache Update",
            description="The cache update is not thread-safe.",
            suggestion="Use a lock when updating the cache.",
            confidence=0.6
        ),

        # Finding #6: LOW QUALITY - Over-confident about subjective issue
        ReviewerFinding(
            file_path="src/api/auth.py",
            line=25,
            severity="minor",
            category="style",
            title="Variable Name Too Short",
            description="The variable name `token` should be more descriptive.",
            suggestion="Rename to `authentication_token` for clarity.",
            confidence=0.9  # High confidence on subjective issue
        ),

        # Finding #7: HIGH QUALITY - Supported by context
        ReviewerFinding(
            file_path="src/api/users.py",
            line=14,
            severity="major",
            category="bug",
            title="Raw execute() Call Without Error Handling",
            description="The `cursor.execute()` call doesn't handle potential database errors. "
                       "If the database is unavailable, this will crash.",
            suggestion="Wrap in try/except and handle DatabaseError appropriately.",
            confidence=0.75
        ),
    ]


def print_separator(title: str):
    """Print a visual separator."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def print_finding(finding: ReviewerFinding, index: int):
    """Pretty-print a finding."""
    print(f"{index}. [{finding.severity.upper()}] {finding.title}")
    print(f"   Location: {finding.file_path}:{finding.line}")
    print(f"   Category: {finding.category}")
    print(f"   Reviewer Confidence: {finding.confidence:.2f}")
    print(f"   Description: {finding.description[:100]}...")
    print()


def print_verdict(verdict, index: int):
    """Pretty-print a Judge verdict."""
    finding = verdict.original_finding
    status = "[PASSED]" if verdict.passed else "[FILTERED]"

    print(f"{index}. {status} - {finding.title}")
    print(f"   Judge Score: {verdict.judge_score:.2f} (threshold: varies)")
    print(f"   Reasoning: {verdict.reasoning}")
    if verdict.evidence:
        print(f"   Evidence:")
        for ev in verdict.evidence:
            print(f"     - {ev}")
    print()


def main():
    """Run the demo."""
    print("\n*** PR REVIEW AGENT - JUDGE FILTERING DEMO ***\n")

    # Create mock data
    print("Setting up mock PR review data...")
    pr_diff = create_mock_pr_diff()
    static_findings = create_mock_static_findings()
    context_refs = create_mock_context_references()
    reviewer_findings = create_mock_reviewer_findings()

    print(f"[OK] Mock PR with {len(pr_diff.files)} files")
    print(f"[OK] {len(static_findings)} static analysis findings")
    print(f"[OK] {len(context_refs)} symbols with context references")
    print(f"[OK] {len(reviewer_findings)} reviewer findings")

    # Show raw findings
    print_separator("BEFORE FILTERING: Raw Reviewer Findings")
    print(f"The Reviewer agent generated {len(reviewer_findings)} findings:\n")

    for i, finding in enumerate(reviewer_findings, 1):
        print_finding(finding, i)

    # Test different thresholds
    thresholds = [0.4, 0.6, 0.8]

    for threshold in thresholds:
        print_separator(f"JUDGE FILTERING: Threshold = {threshold}")
        print(f"Running Judge with threshold {threshold} (")
        if threshold == 0.4:
            print("lenient - accepts weak evidence)")
        elif threshold == 0.6:
            print("moderate - balanced)")
        elif threshold == 0.8:
            print("strict - requires strong evidence)")
        print()

        # Create judge and run
        judge = JudgeAgent(threshold=threshold)
        filtered, verdicts = judge.judge(
            reviewer_findings=reviewer_findings,
            static_findings=static_findings,
            context_references=context_refs,
            pr_diff=pr_diff,
        )

        # Show verdicts
        print("Judge Verdicts:\n")
        for i, verdict in enumerate(verdicts, 1):
            print_verdict(verdict, i)

        # Show summary
        stats = judge.get_summary_stats(verdicts)
        print(f"\nSummary:")
        print(f"  Total findings: {stats['total_findings']}")
        print(f"  Passed: {stats['passed']} ({stats['pass_rate']:.1%})")
        print(f"  Failed: {stats['failed']}")
        print(f"  Average score: {stats['avg_score']:.2f}")
        print(f"  Average passed score: {stats['avg_passed_score']:.2f}")
        print(f"  Average failed score: {stats['avg_failed_score']:.2f}")

        print("\nBy category:")
        for category, cat_stats in stats['by_category'].items():
            print(f"  {category}: {cat_stats['passed']}/{cat_stats['total']} passed")

        # Show filtered findings
        print_separator(f"AFTER FILTERING: Findings that passed (threshold={threshold})")

        if not filtered:
            print("No findings passed the Judge's scrutiny.\n")
        else:
            print(f"{len(filtered)} findings passed:\n")
            for i, finding in enumerate(filtered, 1):
                print_finding(finding, i)

    # Final comparison
    print_separator("THRESHOLD COMPARISON")
    print("How many findings pass at each threshold?\n")
    print("Threshold | Passed | Failed | Pass Rate")
    print("----------|--------|--------|----------")

    for threshold in thresholds:
        judge = JudgeAgent(threshold=threshold)
        filtered, verdicts = judge.judge(
            reviewer_findings=reviewer_findings,
            static_findings=static_findings,
            context_references=context_refs,
            pr_diff=pr_diff,
        )
        stats = judge.get_summary_stats(verdicts)
        print(f"  {threshold:.1f}     | {stats['passed']:6} | {stats['failed']:6} | {stats['pass_rate']:>7.1%}")

    print("\n" + "=" * 80)
    print("\nKey Insights:")
    print("  - Finding #1 (SQL Injection): HIGH score - directly confirmed by static analysis")
    print("  - Finding #2 (Missing Error Handling): MEDIUM score - plausible but no direct evidence")
    print("  - Finding #3 (N+1 Query): LOW score - speculative, weak confidence")
    print("  - Finding #5 (Race Condition): ZERO score - references non-existent file (hallucination)")
    print("  - Finding #6 (Variable Name): LOW score - subjective style issue despite high confidence")
    print("\n  The Judge uses evidence-based heuristic filtering — corroborating findings against")
    print("  static analysis and context references, checking location validity, and scoring")
    print("  by category/confidence. It uses deterministic rules, NOT an LLM.")
    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
