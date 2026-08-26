"""
Demo: LangGraph Interrupt/Checkpoint for Manual Approval

This demonstrates LangGraph's interrupt mechanism for human-in-the-loop workflows.

SCENARIO:
=========
The PR review finds 5 issues:
- 2 high-confidence (≥0.8) → Auto-post to GitHub
- 2 medium-confidence (0.5-0.8) → INTERRUPT for manual approval
- 1 low-confidence (<0.5) → Discard

FLOW:
=====
1. Run graph.invoke() - processes until post_findings_node
2. post_findings_node sees medium-confidence findings → calls interrupt()
3. Graph pauses and returns state snapshot
4. User reviews pending_approval findings
5. User approves some, rejects others
6. User calls graph.invoke() again with SAME thread_id
7. Graph resumes from checkpoint and posts approved findings

KEY CONCEPTS:
=============
- **Checkpointer**: Saves state snapshots at each node
- **thread_id**: Identifies a unique graph execution (like a session ID)
- **interrupt()**: Pauses execution and returns control to user
- **Resume**: Call invoke() with same thread_id to continue from checkpoint
"""

import logging
from src.state import create_initial_state, ReviewerFinding
from src.graph import create_review_graph
from langgraph.checkpoint.memory import MemorySaver
from src.static_analysis.base import Finding, Severity
from src.context_search.base import CodeReference, ReferenceType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_mock_state_with_findings():
    """Create a state with findings at different confidence levels."""

    # Create initial state
    state = create_initial_state(
        repository="example/repo",
        pr_number=123,
        github_token="ghp_fake_token_for_demo",
        auto_post_threshold=0.8,
        manual_approval_threshold=0.5,
    )

    # Mock PR diff
    from src.tools.diff_fetch import PRDiff, FileDiff
    state["pr_diff"] = PRDiff(
        pr_number=123,
        repository="example/repo",
        files=[
            FileDiff(
                filename="api/users.py",
                status="modified",
                additions=10,
                deletions=2,
                patch="@@ -10,7 +10,10 @@..."
            )
        ],
        base_sha="abc123",
        head_sha="def456"
    )

    # Mock detected language
    from src.language.detector import LanguageInfo
    state["detected_languages"] = [
        LanguageInfo(language="python", file_count=1, files=["api/users.py"])
    ]
    state["primary_language"] = "python"

    # Mock static analysis findings (for Judge to reference)
    state["static_analysis_findings"] = [
        Finding(
            file_path="api/users.py",
            line=15,
            column=12,
            severity=Severity.ERROR,
            rule_id="bandit:B608",
            message="SQL injection vulnerability",
            source="bandit"
        )
    ]

    # Mock context references
    state["context_references"] = {
        "execute_query": [
            CodeReference(
                file_path="api/users.py",
                line_number=15,
                column=0,
                context="cursor.execute(query)",
                reference_type=ReferenceType.USAGE,
                symbol="execute_query"
            )
        ]
    }

    # Create findings at different confidence levels
    state["reviewer_findings"] = [
        # HIGH CONFIDENCE #1 - will auto-post
        ReviewerFinding(
            file_path="api/users.py",
            line=15,
            severity="critical",
            category="security",
            title="SQL Injection Vulnerability",
            description="Direct SQL concatenation allows injection attacks.",
            suggestion="Use parameterized queries",
            confidence=0.95  # HIGH
        ),

        # HIGH CONFIDENCE #2 - will auto-post
        ReviewerFinding(
            file_path="api/users.py",
            line=20,
            severity="major",
            category="bug",
            title="Missing Null Check",
            description="Variable could be None, causing AttributeError.",
            suggestion="Add null check before accessing",
            confidence=0.85  # HIGH
        ),

        # MEDIUM CONFIDENCE #1 - needs approval
        ReviewerFinding(
            file_path="api/users.py",
            line=25,
            severity="major",
            category="performance",
            title="Potential N+1 Query",
            description="This query might be in a loop.",
            suggestion="Consider batch loading",
            confidence=0.70  # MEDIUM
        ),

        # MEDIUM CONFIDENCE #2 - needs approval
        ReviewerFinding(
            file_path="api/users.py",
            line=30,
            severity="minor",
            category="logic",
            title="Edge Case Not Handled",
            description="Empty list case might cause issues.",
            suggestion="Check for empty list before processing",
            confidence=0.65  # MEDIUM
        ),

        # LOW CONFIDENCE - will be discarded
        ReviewerFinding(
            file_path="api/users.py",
            line=35,
            severity="minor",
            category="style",
            title="Variable Name Too Short",
            description="Variable 'x' should have descriptive name.",
            suggestion="Rename to something descriptive",
            confidence=0.40  # LOW
        ),
    ]

    # Simulate Judge filtering (Judge passed all of them for demo)
    state["filtered_findings"] = state["reviewer_findings"]

    return state


def print_separator(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def main():
    print("\n*** LANGGRAPH INTERRUPT/CHECKPOINT DEMO ***\n")

    print("This demo shows how LangGraph's interrupt mechanism enables")
    print("human-in-the-loop workflows for manual approval.\n")

    # ===== STEP 1: Create graph with checkpointer =====
    print_separator("STEP 1: Create Graph with Checkpointer")

    print("Creating graph with MemorySaver checkpointer...")
    checkpointer = MemorySaver()  # In-memory checkpoint storage
    graph = create_review_graph(checkpointer=checkpointer)

    print("[OK] Graph created with checkpoint support")
    print("\nKey concept: The checkpointer saves state snapshots at each node.")
    print("This allows the graph to pause and resume later.\n")

    # ===== STEP 2: Create state with findings =====
    print_separator("STEP 2: Create Mock State with Findings")

    state = create_mock_state_with_findings()

    print(f"Created mock review with {len(state['reviewer_findings'])} findings:")
    for i, finding in enumerate(state['reviewer_findings'], 1):
        confidence_label = (
            "HIGH" if finding.confidence >= 0.8
            else "MEDIUM" if finding.confidence >= 0.5
            else "LOW"
        )
        print(
            f"  {i}. [{confidence_label:6s}] {finding.title} "
            f"(confidence: {finding.confidence:.2f})"
        )

    print("\nThresholds:")
    print(f"  Auto-post: >={state['auto_post_threshold']} (HIGH)")
    print(f"  Manual approval: >={state['manual_approval_threshold']} (MEDIUM)")
    print(f"  Discard: <{state['manual_approval_threshold']} (LOW)")

    # ===== STEP 3: First invoke - will interrupt =====
    print_separator("STEP 3: First Invoke - Graph Runs Until Interrupt")

    print("Calling graph.invoke() with thread_id='demo-thread-123'...\n")

    # IMPORTANT: thread_id identifies this execution
    # We'll use the same thread_id to resume later
    config = {"configurable": {"thread_id": "demo-thread-123"}}

    # Mock the actual GitHub posting (we don't want to hit real API)
    # In real usage, this would post to GitHub
    import src.tools.github_post as github_post_module

    original_post = github_post_module.post_findings_to_github

    def mock_post(*args, **kwargs):
        logger.info("MOCK: Would post to GitHub here")
        return {"inline": [], "summary": []}

    github_post_module.post_findings_to_github = mock_post

    try:
        # This will run until post_findings_node calls interrupt()
        result = graph.invoke(state, config)

        print("\nGraph execution paused!")
        print("\nINTERRUPTION DETECTED\n")

        # Check what was returned
        post_output = result.get("node_outputs", {}).get("post_findings", {})

        if post_output.get("status") == "awaiting_approval":
            print("Status: Awaiting manual approval")
            print(f"Message: {post_output['message']}")
            print(f"\nBreakdown:")
            print(f"  High confidence (auto-post): {post_output['high_confidence_count']}")
            print(f"  Medium confidence (need approval): {post_output['medium_confidence_count']}")
            print(f"  Low confidence (discard): {post_output['low_confidence_count']}")

            print("\nPending approval:")
            for i, finding_dict in enumerate(post_output['pending_findings'], 1):
                print(
                    f"  {i}. {finding_dict['title']} "
                    f"(confidence: {finding_dict['confidence']:.2f})"
                )
                print(f"     {finding_dict['file']}:{finding_dict['line']}")
                print(f"     {finding_dict['description'][:80]}...")

    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()

    # ===== STEP 4: User reviews findings =====
    print_separator("STEP 4: Manual Review")

    print("The graph is paused. Now the user reviews the pending findings.\n")

    pending = result.get("pending_approval", [])

    print("Pending findings for review:\n")
    for i, finding in enumerate(pending, 1):
        print(f"{i}. {finding.title}")
        print(f"   Severity: {finding.severity}, Confidence: {finding.confidence:.0%}")
        print(f"   Description: {finding.description[:100]}...")
        print()

    print("User decision:")
    print("  - Approve finding #1 (N+1 Query) - seems plausible")
    print("  - REJECT finding #2 (Edge Case) - too speculative")

    # User approves some findings
    approved = [pending[0]]  # Approve only the first one

    print(f"\nApproved: {len(approved)}/{len(pending)} findings")

    # ===== STEP 5: Resume with approval =====
    print_separator("STEP 5: Resume Graph with Approval")

    print("Resuming graph with approved_findings set...\n")

    # Update state with approved findings
    result["approved_findings"] = approved

    print("Calling graph.invoke() AGAIN with SAME thread_id...\n")

    # CRITICAL: Use same thread_id to resume from checkpoint
    result2 = graph.invoke(result, config)

    print("\nGraph execution completed!\n")

    # Check final output
    post_output2 = result2.get("node_outputs", {}).get("post_findings", {})

    if post_output2.get("status") == "completed":
        print("Status: Completed successfully")
        print(f"\nPosted to GitHub:")
        print(f"  High confidence (auto): {post_output2.get('high_confidence', 0)}")
        print(f"  Medium confidence (approved): {post_output2.get('medium_confidence', 0)}")
        print(f"  Total posted: {post_output2.get('posted_count', 0)}")

        print("\nPosted findings:")
        for i, finding in enumerate(result2.get("posted_findings", []), 1):
            print(f"  {i}. {finding.title} (confidence: {finding.confidence:.2f})")

    # Restore original function
    github_post_module.post_findings_to_github = original_post

    # ===== SUMMARY =====
    print_separator("SUMMARY: How Interrupt/Checkpoint Works")

    print("""
1. CHECKPOINTER:
   - MemorySaver() stores state snapshots after each node
   - State is keyed by thread_id (like a session ID)
   - Enables pause/resume across invoke() calls

2. INTERRUPT:
   - Called inside post_findings_node: interrupt(approval_request)
   - Pauses graph execution and returns control to user
   - State is saved at checkpoint

3. THREAD_ID:
   - Passed via config: {"configurable": {"thread_id": "..."}}
   - MUST use same thread_id to resume
   - Different thread_id = new execution from scratch

4. RESUME:
   - Update state with user's decision (approved_findings)
   - Call graph.invoke(updated_state, same_config)
   - Graph loads checkpoint and continues from where it left off

5. WHY THIS MATTERS:
   - Human-in-the-loop approval workflows
   - Review findings before posting to production PRs
   - Gradual rollout: high confidence auto-posts, medium needs approval
   - Prevents false positive spam in PR comments
    """)

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
