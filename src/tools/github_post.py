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
import re
from typing import List, Optional, Dict, Set, Tuple
from dataclasses import dataclass

from src.state import ReviewerFinding
from src.tools.diff_fetch import PRDiff

logger = logging.getLogger(__name__)


def parse_diff_valid_lines(pr_diff: PRDiff) -> Set[Tuple[str, int]]:
    """
    Parse PR diff to determine which (file_path, line_number) pairs are valid
    for inline comments.

    GitHub's /pulls/{pr}/reviews API only accepts comments on lines that are
    part of the diff (changed or context lines). Comments on unchanged lines
    outside the diff hunks cause 422 errors.

    Args:
        pr_diff: The PR diff from fetch_pr_diff()

    Returns:
        Set of (file_path, line_number) tuples that are valid for inline comments

    Algorithm:
        For each file's patch:
        - Parse hunk headers: @@ -old_start,old_count +new_start,new_count @@
        - For each hunk, lines [new_start, new_start+new_count) are valid
        - These include both changed lines (+/-) and unchanged context lines
    """
    valid_lines = set()

    for file_diff in pr_diff.files:
        if not file_diff.patch:
            # No patch = no valid lines for inline comments
            continue

        # Parse hunk headers to find valid line ranges
        # Format: @@ -40,7 +40,9 @@ optional context
        # Captures new_start and new_count
        hunk_pattern = r'@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@'

        for match in re.finditer(hunk_pattern, file_diff.patch):
            new_start = int(match.group(1))
            new_count = int(match.group(2)) if match.group(2) else 1

            # All lines in [new_start, new_start + new_count) are valid
            for line_num in range(new_start, new_start + new_count):
                valid_lines.add((file_diff.filename, line_num))

    return valid_lines


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
        commit_sha: str,
        valid_lines: Set[Tuple[str, int]]
    ) -> List[PostedComment]:
        """
        Post findings as inline PR review comments.

        Uses the Pull Request Review API to batch-post comments.
        Only posts findings whose (file_path, line) are in valid_lines
        to avoid 422 errors from GitHub.

        Args:
            findings: Findings to post as inline comments
            commit_sha: The head SHA to comment on
            valid_lines: Set of (file_path, line) tuples that are valid for inline comments

        Returns:
            List of PostedComment objects
        """
        # Filter to only findings on valid lines
        inline_findings = [
            f for f in findings
            if (f.file_path, f.line) in valid_lines
        ]

        if not inline_findings:
            logger.info("No findings on valid diff lines for inline comments")
            return []

        # Build review comments (GitHub API format)
        comments = []
        for finding in inline_findings:
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
            "body": f"🤖 PR Review Agent found {len(inline_findings)} issue(s) on changed lines",
            "event": "COMMENT",  # COMMENT (no approval), APPROVE, or REQUEST_CHANGES
            "comments": comments,
        }

        try:
            response = requests.post(url, json=payload, headers=self._headers())
            response.raise_for_status()

            review_data = response.json()
            review_id = review_data["id"]

            logger.info(f"Posted {len(inline_findings)} inline comments (review #{review_id})")

            # Return posted comment records
            posted = [
                PostedComment(
                    finding=finding,
                    comment_id=review_id,  # All share same review ID
                    comment_url=review_data["html_url"]
                )
                for finding in inline_findings
            ]

            return posted

        except requests.HTTPError as e:
            logger.error(f"Failed to post inline comments: {e}")
            logger.error(f"Response: {e.response.text if e.response else 'N/A'}")
            raise

    def post_summary_comment(
        self,
        findings: List[ReviewerFinding],
        stats: dict,
        summary_only_findings: List[ReviewerFinding] = None
    ) -> PostedComment:
        """
        Post a summary comment on the PR.

        Uses the Issue Comments API (PRs are issues in GitHub's API).

        Args:
            findings: All findings (for summary)
            stats: Statistics about the review
            summary_only_findings: Findings that couldn't be posted inline
                                  (on unchanged lines) - will be included
                                  with full details in the summary

        Returns:
            PostedComment for the summary
        """
        url = f"{self.base_url}/repos/{self.repository}/issues/{self.pr_number}/comments"

        body = self._format_summary_comment(findings, stats, summary_only_findings or [])

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
        stats: dict,
        summary_only_findings: List[ReviewerFinding]
    ) -> str:
        """
        Format a summary comment for the PR.

        Includes:
        - Total counts by severity
        - List of findings with links
        - Full details for summary-only findings (on unchanged lines)
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

        # Summary-only findings (full details for findings on unchanged lines)
        if summary_only_findings:
            lines.append("\n---\n")
            lines.append("### 📝 Additional Findings (on unchanged lines)\n")
            lines.append(
                "*These findings are on lines outside the PR diff, "
                "so they couldn't be posted as inline comments. "
                "Full details below:*\n"
            )

            for finding in summary_only_findings:
                emoji = {
                    "critical": "🚨",
                    "major": "⚠️",
                    "minor": "ℹ️",
                }.get(finding.severity, "💡")

                lines.append(f"\n#### {emoji} {finding.title}\n")
                lines.append(f"**Location:** `{finding.file_path}:{finding.line}`\n")
                lines.append(f"**Severity:** {finding.severity.upper()}\n")
                lines.append(f"**Category:** {finding.category}\n")
                lines.append(f"\n{finding.description}\n")
                lines.append(f"\n**Suggestion:**")
                lines.append(f"```")
                lines.append(finding.suggestion)
                lines.append(f"```")
                lines.append(f"\n*Confidence: {finding.confidence:.0%}*\n")

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
    pr_diff: PRDiff,
    stats: Optional[dict] = None
) -> Dict[str, List[PostedComment]]:
    """
    Post findings to GitHub with smart handling of line locations.

    Findings on lines in the PR diff → posted as inline review comments
    Findings on unchanged lines (not in diff) → included in summary comment with full details

    This avoids 422 errors from GitHub's API which rejects inline comments on
    lines outside the diff hunks.

    Args:
        findings: Findings to post
        repository: Format "owner/repo"
        pr_number: PR number
        commit_sha: Commit SHA to comment on
        github_token: GitHub token
        pr_diff: The PR diff (to parse valid lines for inline comments)
        stats: Optional statistics to include in summary

    Returns:
        Dictionary with "inline" and "summary" posted comments
    """
    poster = GitHubPoster(github_token, repository, pr_number)

    # Parse valid lines from diff
    valid_lines = parse_diff_valid_lines(pr_diff)
    logger.info(f"Parsed {len(valid_lines)} valid lines from diff for inline comments")

    # Split findings: inline-eligible vs summary-only
    inline_findings = [
        f for f in findings
        if (f.file_path, f.line) in valid_lines
    ]
    summary_only_findings = [
        f for f in findings
        if (f.file_path, f.line) not in valid_lines
    ]

    logger.info(
        f"Split findings: {len(inline_findings)} inline-eligible, "
        f"{len(summary_only_findings)} summary-only (unchanged lines)"
    )

    # Post inline comments (only for findings on valid lines)
    inline_comments = []
    if inline_findings:
        try:
            inline_comments = poster.post_inline_comments(
                inline_findings,
                commit_sha,
                valid_lines
            )
        except requests.HTTPError as e:
            logger.error(f"Inline comment posting failed: {e}")
            # Don't raise - we can still post the summary
            # Move failed inline findings to summary-only
            summary_only_findings.extend(inline_findings)

    # Post summary comment (always, includes summary-only findings with full details)
    summary_comment = poster.post_summary_comment(
        findings,
        stats or {},
        summary_only_findings
    )

    return {
        "inline": inline_comments,
        "summary": [summary_comment],
    }
