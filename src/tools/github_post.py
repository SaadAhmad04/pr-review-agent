"""
GitHub PR comment posting tool.

Posts findings as:
1. Inline comments on specific lines (Review Comments API)
2. Summary comment on the PR (Issue Comments API)

Uses GitHub REST API:
- POST /repos/{owner}/{repo}/pulls/{pr}/reviews (batch inline comments)
- POST /repos/{owner}/{repo}/issues/{pr}/comments (summary comment)
"""

import requests
import logging
from typing import List, Optional, Dict
from dataclasses import dataclass

from src.state import ReviewerFinding

logger = logging.getLogger(__name__)


@dataclass
class PostedComment:
    """Record of a posted comment."""
    finding: Optional[ReviewerFinding]
    comment_id: int
    comment_url: str


class GitHubPoster:
    """Posts PR review findings to GitHub."""

    def __init__(self, github_token: str, repository: str, pr_number: int):
        """
        Args:
            github_token: GitHub personal access token
            repository: Format "owner/repo"
            pr_number: Pull request number
        """
        self.token = github_token
        self.repository = repository
        self.pr_number = pr_number
        self.base_url = "https://api.github.com"

    def _headers(self) -> dict:
        """Get headers for GitHub API requests."""
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def post_inline_comments(
        self,
        findings: List[ReviewerFinding],
        commit_sha: str
    ) -> List[PostedComment]:
        """
        Post findings as inline PR review comments.

        Uses the Pull Request Review API to batch-post comments.

        Args:
            findings: Findings to post as inline comments
            commit_sha: The head SHA to comment on

        Returns:
            List of PostedComment objects
        """
        if not findings:
            logger.info("No findings to post")
            return []

        # Build review comments (GitHub API format)
        comments = []
        for finding in findings:
            comment = {
                "path": finding.file_path,
                "line": finding.line,
                "body": self._format_inline_comment(finding),
            }
            comments.append(comment)

        # Post as a single review (batched)
        url = f"{self.base_url}/repos/{self.repository}/pulls/{self.pr_number}/reviews"

        payload = {
            "commit_id": commit_sha,
            "body": f"🤖 PR Review Agent found {len(findings)} issue(s)",
            "event": "COMMENT",  # COMMENT (no approval), APPROVE, or REQUEST_CHANGES
            "comments": comments,
        }

        try:
            response = requests.post(url, json=payload, headers=self._headers())
            response.raise_for_status()

            review_data = response.json()
            review_id = review_data["id"]

            logger.info(f"Posted {len(findings)} inline comments (review #{review_id})")

            # Return posted comment records
            posted = [
                PostedComment(
                    finding=finding,
                    comment_id=review_id,  # All share same review ID
                    comment_url=review_data["html_url"]
                )
                for finding in findings
            ]

            return posted

        except requests.HTTPError as e:
            logger.error(f"Failed to post inline comments: {e}")
            logger.error(f"Response: {e.response.text if e.response else 'N/A'}")
            raise

    def post_summary_comment(
        self,
        findings: List[ReviewerFinding],
        stats: dict
    ) -> PostedComment:
        """
        Post a summary comment on the PR.

        Uses the Issue Comments API (PRs are issues in GitHub's API).

        Args:
            findings: All findings (for summary)
            stats: Statistics about the review

        Returns:
            PostedComment for the summary
        """
        url = f"{self.base_url}/repos/{self.repository}/issues/{self.pr_number}/comments"

        body = self._format_summary_comment(findings, stats)

        payload = {"body": body}

        try:
            response = requests.post(url, json=payload, headers=self._headers())
            response.raise_for_status()

            comment_data = response.json()

            logger.info(f"Posted summary comment (#{comment_data['id']})")

            return PostedComment(
                finding=None,  # Summary doesn't correspond to one finding
                comment_id=comment_data["id"],
                comment_url=comment_data["html_url"]
            )

        except requests.HTTPError as e:
            logger.error(f"Failed to post summary comment: {e}")
            logger.error(f"Response: {e.response.text if e.response else 'N/A'}")
            raise

    def _format_inline_comment(self, finding: ReviewerFinding) -> str:
        """
        Format a finding as an inline comment.

        Uses GitHub Markdown for formatting.
        """
        # Emoji by severity
        emoji = {
            "critical": "🚨",
            "major": "⚠️",
            "minor": "ℹ️",
        }.get(finding.severity, "💡")

        # Format as markdown
        comment = f"""{emoji} **{finding.title}**

**Severity:** {finding.severity.upper()}
**Category:** {finding.category}

{finding.description}

**Suggestion:**
```
{finding.suggestion}
```

---
*Found by PR Review Agent (confidence: {finding.confidence:.0%})*
"""
        return comment

    def _format_summary_comment(
        self,
        findings: List[ReviewerFinding],
        stats: dict
    ) -> str:
        """
        Format a summary comment for the PR.

        Includes:
        - Total counts by severity
        - List of findings with links
        - Statistics about the review
        """
        if not findings:
            return """## 🤖 PR Review Complete

✅ No issues found by PR Review Agent.

---
*Automated review by [PR Review Agent](https://github.com/your-repo)*
"""

        # Group by severity
        by_severity = {"critical": [], "major": [], "minor": []}
        for finding in findings:
            by_severity[finding.severity].append(finding)

        # Build summary
        lines = ["## 🤖 PR Review Summary\n"]

        # Counts
        lines.append(f"Found **{len(findings)}** issue(s):\n")
        if by_severity["critical"]:
            lines.append(f"- 🚨 **{len(by_severity['critical'])} Critical**")
        if by_severity["major"]:
            lines.append(f"- ⚠️ **{len(by_severity['major'])} Major**")
        if by_severity["minor"]:
            lines.append(f"- ℹ️ **{len(by_severity['minor'])} Minor**")

        lines.append("\n---\n")

        # List findings
        lines.append("### Issues Found\n")

        for severity in ["critical", "major", "minor"]:
            findings_in_severity = by_severity[severity]
            if not findings_in_severity:
                continue

            emoji = {"critical": "🚨", "major": "⚠️", "minor": "ℹ️"}[severity]

            lines.append(f"\n#### {emoji} {severity.capitalize()}\n")

            for finding in findings_in_severity:
                lines.append(
                    f"- **{finding.title}** "
                    f"(`{finding.file_path}:{finding.line}`) - "
                    f"{finding.category}"
                )

        # Stats
        if stats:
            lines.append("\n---\n")
            lines.append("### Review Statistics\n")

            if "static_analysis" in stats:
                static_stats = stats["static_analysis"]
                lines.append(f"- Static analysis findings: {static_stats.get('total_findings', 0)}")

            if "reviewer" in stats:
                rev_stats = stats["reviewer"]
                lines.append(f"- Reviewer findings: {rev_stats.get('findings_count', 0)}")

            if "judge" in stats:
                judge_stats = stats["judge"]
                lines.append(
                    f"- Judge pass rate: {judge_stats.get('passed', 0)}/"
                    f"{judge_stats.get('total_evaluated', 0)} "
                    f"({judge_stats.get('stats', {}).get('pass_rate', 0):.0%})"
                )

        lines.append("\n---\n")
        lines.append("*Automated review by [PR Review Agent](https://github.com/your-repo)*")

        return "\n".join(lines)


def post_findings_to_github(
    findings: List[ReviewerFinding],
    repository: str,
    pr_number: int,
    commit_sha: str,
    github_token: str,
    stats: Optional[dict] = None
) -> Dict[str, List[PostedComment]]:
    """
    Convenience function to post findings to GitHub.

    Args:
        findings: Findings to post
        repository: Format "owner/repo"
        pr_number: PR number
        commit_sha: Commit SHA to comment on
        github_token: GitHub token
        stats: Optional statistics to include in summary

    Returns:
        Dictionary with "inline" and "summary" posted comments
    """
    poster = GitHubPoster(github_token, repository, pr_number)

    # Post inline comments
    inline_comments = poster.post_inline_comments(findings, commit_sha)

    # Post summary comment
    summary_comment = poster.post_summary_comment(findings, stats or {})

    return {
        "inline": inline_comments,
        "summary": [summary_comment],
    }
