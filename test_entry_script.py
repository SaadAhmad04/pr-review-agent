#!/usr/bin/env python3
"""
Local test to verify scripts/run_action.py parses GitHub events correctly.

Tests event parsing WITHOUT calling the actual review pipeline.
"""

import os
import sys
import json
import tempfile
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.dirname(__file__))


def test_parse_pull_request_event():
    """Test that entry script correctly extracts repo and PR number from event."""

    print("=== Testing Entry Script Event Parsing ===\n")

    # Create fake event payload
    fake_event = {
        "pull_request": {
            "number": 42,
            "title": "Test PR for automated review",
            "user": {"login": "testuser"}
        },
        "action": "opened"
    }

    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(fake_event, f)
        event_file = f.name

    try:
        # Set up environment
        os.environ['GITHUB_EVENT_PATH'] = event_file
        os.environ['GITHUB_REPOSITORY'] = 'test-owner/test-repo'
        os.environ['GITHUB_TOKEN'] = 'ghp_fake_token_for_testing'
        os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-fake-key-for-testing'

        print(f"[OK] Created fake event file: {event_file}")
        print(f"[OK] Set GITHUB_EVENT_PATH: {event_file}")
        print(f"[OK] Set GITHUB_REPOSITORY: test-owner/test-repo")
        print(f"[OK] Set dummy tokens\n")

        # Mock run_pr_review so we don't actually call it
        with patch('src.graph.run_pr_review') as mock_review:
            # Make it return a fake successful result
            mock_review.return_value = {
                'reviewer_findings': [{'title': 'test finding'}],
                'filtered_findings': [{'title': 'test finding'}],
                'posted_findings': [{'title': 'test finding'}],
                'errors': [],
                'node_outputs': {}
            }

            # Import and run the entry script's main function
            from scripts.run_action import main

            print("Running entry script...\n")
            try:
                main()
                print("\n[PASS] Entry script executed successfully")
            except SystemExit as e:
                if e.code == 0:
                    print("\n[PASS] Entry script exited with code 0 (success)")
                else:
                    print(f"\n[FAIL] Entry script exited with code {e.code}")
                    return False

            # Verify run_pr_review was called with correct args
            if mock_review.called:
                call_args = mock_review.call_args
                print("\n=== Verification ===")
                print(f"run_pr_review was called: YES")
                print(f"  repository: {call_args.kwargs.get('repository')}")
                print(f"  pr_number: {call_args.kwargs.get('pr_number')}")
                print(f"  github_token: {call_args.kwargs.get('github_token')[:10]}...")

                # Check values
                assert call_args.kwargs['repository'] == 'test-owner/test-repo', \
                    f"Expected repo 'test-owner/test-repo', got {call_args.kwargs['repository']}"
                assert call_args.kwargs['pr_number'] == 42, \
                    f"Expected PR number 42, got {call_args.kwargs['pr_number']}"
                assert call_args.kwargs['github_token'] == 'ghp_fake_token_for_testing', \
                    "GitHub token not passed correctly"

                print("\n[PASS] ALL CHECKS PASSED")
                print("  - Repository parsed correctly: test-owner/test-repo")
                print("  - PR number extracted correctly: 42")
                print("  - GitHub token passed correctly")
                return True
            else:
                print("\n[FAIL] run_pr_review was not called")
                return False

    finally:
        # Clean up
        os.unlink(event_file)
        # Clean up env vars
        for key in ['GITHUB_EVENT_PATH', 'GITHUB_REPOSITORY', 'GITHUB_TOKEN', 'ANTHROPIC_API_KEY']:
            os.environ.pop(key, None)


def test_non_pr_event():
    """Test that script gracefully skips non-PR events."""

    print("\n\n=== Testing Non-PR Event Handling ===\n")

    # Create non-PR event (e.g., push event)
    fake_event = {
        "ref": "refs/heads/main",
        "commits": [{"message": "test commit"}],
        "action": "push"
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(fake_event, f)
        event_file = f.name

    try:
        os.environ['GITHUB_EVENT_PATH'] = event_file
        os.environ['GITHUB_REPOSITORY'] = 'test-owner/test-repo'
        os.environ['GITHUB_TOKEN'] = 'ghp_fake'
        os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-fake'

        print(f"[OK] Created non-PR event file")
        print(f"[OK] Event type: push (not pull_request)\n")

        with patch('src.graph.run_pr_review') as mock_review:
            from scripts.run_action import main

            print("Running entry script...\n")
            try:
                main()
                print("\n[FAIL] Expected SystemExit(0) for non-PR event")
                return False
            except SystemExit as e:
                if e.code == 0:
                    print("[PASS] Script exited with code 0 (graceful skip)")

                    # Verify review was NOT called
                    if not mock_review.called:
                        print("[PASS] run_pr_review was NOT called (correct)")
                        return True
                    else:
                        print("[FAIL] run_pr_review should not be called for non-PR events")
                        return False
                else:
                    print(f"[FAIL] Expected exit 0, got exit {e.code}")
                    return False

    finally:
        os.unlink(event_file)
        for key in ['GITHUB_EVENT_PATH', 'GITHUB_REPOSITORY', 'GITHUB_TOKEN', 'ANTHROPIC_API_KEY']:
            os.environ.pop(key, None)


if __name__ == '__main__':
    print("Testing Entry Script (scripts/run_action.py)")
    print("=" * 60)
    print()

    # Run tests
    test1_pass = test_parse_pull_request_event()
    test2_pass = test_non_pr_event()

    print("\n" + "=" * 60)
    if test1_pass and test2_pass:
        print("[SUCCESS] ALL TESTS PASSED")
        print("\nEntry script correctly:")
        print("  - Parses PR events and extracts repo/PR number")
        print("  - Passes correct arguments to run_pr_review()")
        print("  - Gracefully skips non-PR events (exit 0)")
        sys.exit(0)
    else:
        print("[FAILURE] SOME TESTS FAILED")
        sys.exit(1)
