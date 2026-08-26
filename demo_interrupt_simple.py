"""
Simplified Demo: LangGraph Interrupt/Checkpoint Mechanism

This demonstrates the core interrupt/resume mechanism without
running the full PR review pipeline.

KEY CONCEPTS:
=============
1. Checkpointer: Saves state snapshots
2. thread_id: Identifies a unique execution
3. interrupt(): Pauses execution and returns to user
4. Resume: Call invoke() with same thread_id to continue
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt
from typing import TypedDict, List, Annotated
import operator


# Simple state for demo
class ApprovalState(TypedDict):
    findings: List[dict]
    high_confidence: List[dict]
    medium_confidence: List[dict]
    low_confidence: List[dict]
    approved: List[dict]
    posted: List[dict]


def categorize_node(state: ApprovalState) -> dict:
    """Node 1: Categorize findings by confidence."""
    print("\n[Node 1: Categorize]")
    print("Categorizing findings by confidence...")

    findings = state["findings"]

    high = [f for f in findings if f["confidence"] >= 0.8]
    medium = [f for f in findings if 0.5 <= f["confidence"] < 0.8]
    low = [f for f in findings if f["confidence"] < 0.5]

    print(f"  High confidence: {len(high)}")
    print(f"  Medium confidence: {len(medium)}")
    print(f"  Low confidence: {len(low)}")

    return {
        "high_confidence": high,
        "medium_confidence": medium,
        "low_confidence": low,
    }


def post_node(state: ApprovalState) -> dict:
    """Node 2: Post findings (with interrupt for medium confidence)."""
    print("\n[Node 2: Post]")

    high = state.get("high_confidence", [])
    medium = state.get("medium_confidence", [])
    approved = state.get("approved", [])

    # Check if this is first run or resume
    is_resume = len(approved) > 0

    if is_resume:
        print("RESUMING after approval...")
        to_post = high + approved
        print(f"Posting {len(to_post)} findings (high + approved)")

    elif medium:
        print(f"Found {len(medium)} medium-confidence findings")
        print("INTERRUPTING for manual approval...")

        # Create approval request
        request = {
            "status": "awaiting_approval",
            "high_count": len(high),
            "medium_count": len(medium),
            "pending": [
                f"{f['title']} (confidence: {f['confidence']})"
                for f in medium
            ]
        }

        # INTERRUPT - pause execution here
        interrupt(request)

        # This code won't run until resume
        print("This line won't print until after approval")
        return {}

    else:
        print("No medium-confidence findings, posting high only")
        to_post = high

    # Post findings
    print(f"POSTING {len(to_post)} findings...")
    for finding in to_post:
        print(f"  - {finding['title']}")

    return {"posted": to_post}


def create_demo_graph():
    """Create a simple graph with interrupt."""
    graph = StateGraph(ApprovalState)

    graph.add_node("categorize", categorize_node)
    graph.add_node("post", post_node)

    graph.set_entry_point("categorize")
    graph.add_edge("categorize", "post")
    graph.add_edge("post", END)

    # Compile with checkpointer (required for interrupt)
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


def print_separator(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main():
    print("\n*** SIMPLIFIED INTERRUPT/CHECKPOINT DEMO ***\n")

    # Create sample findings
    findings = [
        {"title": "SQL Injection", "confidence": 0.95},
        {"title": "Null Check Missing", "confidence": 0.85},
        {"title": "N+1 Query", "confidence": 0.70},
        {"title": "Edge Case", "confidence": 0.65},
        {"title": "Style Issue", "confidence": 0.40},
    ]

    print(f"Sample findings: {len(findings)}")
    for f in findings:
        conf_label = "HIGH" if f["confidence"] >= 0.8 else "MEDIUM" if f["confidence"] >= 0.5 else "LOW"
        print(f"  - [{conf_label:6s}] {f['title']} ({f['confidence']})")

    # Create graph
    print_separator("Creating Graph with Checkpointer")
    graph = create_demo_graph()
    print("Graph created with MemorySaver checkpointer")

    # Initial state
    state = ApprovalState(
        findings=findings,
        high_confidence=[],
        medium_confidence=[],
        low_confidence=[],
        approved=[],
        posted=[],
    )

    # Configuration with thread_id
    config = {"configurable": {"thread_id": "demo-123"}}

    print_separator("First Invoke - Will Interrupt")
    print("Calling graph.invoke()...")

    try:
        result = graph.invoke(state, config)
        print("\nGraph paused (interrupted)")

        # Check what was returned
        node_output = result.get("posted", None)
        if not node_output:
            print("\nInterruption detected!")
            print("\nMedium-confidence findings pending approval:")
            for f in result.get("medium_confidence", []):
                print(f"  - {f['title']} (confidence: {f['confidence']})")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return

    print_separator("User Reviews Findings")
    print("User reviews the medium-confidence findings...")
    print("\nDecision:")
    print("  - APPROVE: N+1 Query (seems plausible)")
    print("  - REJECT: Edge Case (too speculative)")

    # User approves one finding
    medium = result.get("medium_confidence", [])
    approved = [medium[0]]  # Approve first one only

    print(f"\nApproved: {len(approved)}/{len(medium)}")

    # Update state with approval
    result["approved"] = approved

    print_separator("Resume Execution")
    print("Resuming graph with same thread_id...")
    print("(Graph loads checkpoint and continues from where it left off)")

    try:
        # CRITICAL: Same thread_id to resume
        final_result = graph.invoke(result, config)

        print("\nGraph completed!")
        print(f"\nTotal posted: {len(final_result.get('posted', []))}")
        print("Posted findings:")
        for f in final_result.get("posted", []):
            print(f"  - {f['title']} (confidence: {f['confidence']})")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return

    print_separator("How Interrupt/Checkpoint Works")
    print("""
1. CHECKPOINTER (MemorySaver):
   - Saves state after each node execution
   - State is keyed by thread_id
   - Enables pause/resume

2. INTERRUPT:
   - Called inside a node: interrupt(data)
   - Pauses execution immediately
   - Returns control to caller
   - State saved at checkpoint

3. THREAD_ID:
   - Unique identifier: {"configurable": {"thread_id": "..."}}
   - MUST use same thread_id to resume
   - Different thread_id = new execution

4. RESUME:
   - Update state (e.g., add approved findings)
   - Call graph.invoke(updated_state, same_config)
   - Graph loads checkpoint and continues

5. USE CASES:
   - Human-in-the-loop workflows
   - Manual approval gates
   - Review before production actions
   - Gradual automation (auto high confidence, manual medium)

The interrupt mechanism enables AI agents to pause and wait for human
input before taking irreversible actions (like posting to PRs).
    """)


if __name__ == "__main__":
    main()
