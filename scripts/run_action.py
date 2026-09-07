#!/usr/bin/env python3
"""
GitHub Action entry script for PR review agent.

Reads the GitHub event payload to extract repository and PR number,
then runs the review pipeline and posts findings to the PR.

Environment variables required:
- GITHUB_EVENT_PATH: Path to event JSON (provided by Actions)
- GITHUB_REPOSITORY: Repository in "owner/repo" format (provided by Actions)
- GITHUB_TOKEN: GitHub token for API access (provided by Actions)
- ANTHROPIC_API_KEY: Anthropic API key for Claude (from repo secret)

Exit codes:
- 0: Success (review completed) or non-fatal skip (not a PR event)
- 1: Hard failure (missing env vars, pipeline crash)
"""

import os
import sys
import json
import logging

# Add src to path so we can import from the agent
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.graph import run_pr_review

# Configure logging for GitHub Actions
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def load_event_payload():
    """
    Load the GitHub event payload from GITHUB_EVENT_PATH.

    Returns:
        dict: Event payload

    Raises:
        SystemExit: If GITHUB_EVENT_PATH is missing or file doesn't exist
    """
    event_path = os.environ.get('GITHUB_EVENT_PATH')

    if not event_path:
        logger.error("GITHUB_EVENT_PATH environment variable not set")
        logger.error("This script must be run within a GitHub Action context")
        sys.exit(1)

    if not os.path.exists(event_path):
        logger.error(f"Event file not found at: {event_path}")
        sys.exit(1)

    try:
        with open(event_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse event JSON: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to read event file: {e}")
        sys.exit(1)


def main():
    """
    Main entry point for GitHub Action.

    1. Load event payload
    2. Validate it's a pull_request event
    3. Extract repository and PR number
    4. Read required secrets from environment
    5. Run PR review
    6. Print summary
    """
    logger.info("=== PR Review Agent - GitHub Action ===")

    # Load event payload
    event = load_event_payload()

    # Check if this is a pull_request event
    if 'pull_request' not in event:
        logger.warning("Event is not a pull_request event")
        logger.warning(f"Event type: {event.get('action', 'unknown')}")
        logger.info("Skipping review (no PR to review)")
        sys.exit(0)  # Non-fatal skip

    # Extract PR number
    pr_number = event['pull_request']['number']
    logger.info(f"PR Number: #{pr_number}")

    # Extract repository from environment
    # GITHUB_REPOSITORY format: "owner/repo"
    repository = os.environ.get('GITHUB_REPOSITORY')
    if not repository:
        logger.error("GITHUB_REPOSITORY environment variable not set")
        sys.exit(1)

    logger.info(f"Repository: {repository}")

    # Read required secrets
    github_token = os.environ.get('GITHUB_TOKEN')
    if not github_token:
        logger.error("GITHUB_TOKEN environment variable not set")
        logger.error("Make sure to pass secrets.GITHUB_TOKEN in the workflow")
        sys.exit(1)

    anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY environment variable not set")
        logger.error("Add ANTHROPIC_API_KEY to repository secrets")
        sys.exit(1)

    # Set Anthropic API key for the agent
    # (The agent uses langchain-anthropic which reads from env)
    os.environ['ANTHROPIC_API_KEY'] = anthropic_api_key

    logger.info("Environment validated")
    logger.info("Starting PR review...")
    logger.info("-" * 60)

    # Run the review
    try:
        result = run_pr_review(
            repository=repository,
            pr_number=pr_number,
            github_token=github_token
        )

        logger.info("-" * 60)
        logger.info("Review completed successfully!")
        logger.info("")

        # Print summary
        logger.info("=== REVIEW SUMMARY ===")

        # Extract counts from result
        reviewer_findings = result.get('reviewer_findings', [])
        filtered_findings = result.get('filtered_findings', [])
        posted_findings = result.get('posted_findings', [])
        errors = result.get('errors', [])

        logger.info(f"Reviewer findings: {len(reviewer_findings)}")
        logger.info(f"Findings after Judge filter: {len(filtered_findings)}")
        logger.info(f"Posted to PR: {len(posted_findings)}")

        if errors:
            logger.warning(f"Errors encountered: {len(errors)}")
            for error in errors:
                logger.warning(f"  - {error}")

        # Check node outputs for details
        node_outputs = result.get('node_outputs', {})

        if 'context_search' in node_outputs:
            ctx = node_outputs['context_search']
            logger.info(f"Context search: {ctx.get('symbols_searched', 0)} symbols, "
                       f"{ctx.get('level_1_refs', 0)} caller refs")

        if 'judge' in node_outputs:
            judge = node_outputs['judge']
            logger.info(f"Judge: {judge.get('passed', 0)}/{judge.get('total_evaluated', 0)} "
                       f"findings passed (threshold: {judge.get('threshold', 0.6)})")

        if 'cleanup' in node_outputs:
            cleanup = node_outputs['cleanup']
            if cleanup.get('status') == 'success':
                logger.info("Cleanup: temp clone directory removed")
            else:
                logger.warning(f"Cleanup: {cleanup.get('status')} - {cleanup.get('error', 'unknown')}")

        logger.info("")
        logger.info("✅ Review posted to PR successfully")

        sys.exit(0)

    except Exception as e:
        error_msg = str(e)

        # Check for 422 "already reviewed" case (non-fatal)
        # GitHub returns 422 when trying to post duplicate review comments
        if '422' in error_msg or 'already' in error_msg.lower():
            logger.warning("PR appears to already have review comments")
            logger.warning(f"Details: {error_msg}")
            logger.info("Treating as non-fatal warning (review already exists)")
            sys.exit(0)  # Non-fatal

        # Hard failure
        logger.error("❌ Review failed with error:")
        logger.error(f"  {error_msg}")
        logger.error("")
        logger.error("Check the logs above for details")

        # Print traceback for debugging
        import traceback
        logger.error("Traceback:")
        logger.error(traceback.format_exc())

        sys.exit(1)


if __name__ == '__main__':
    main()
