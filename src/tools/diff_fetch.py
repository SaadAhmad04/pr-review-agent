"""
Fetches PR diffs from GitHub REST API.

Returns parsed diff with file paths, additions, deletions, and line numbers.
"""

import requests
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class FileDiff:
    """Represents changes to a single file in a PR."""
    filename: str
    status: str  # 'added', 'modified', 'removed', 'renamed'
    additions: int
    deletions: int
    patch: Optional[str]  # The actual diff content
    old_filename: Optional[str] = None  # For renamed files


@dataclass
class PRDiff:
    """Complete PR diff with all changed files."""
    pr_number: int
    repository: str  # format: "owner/repo"
    files: List[FileDiff]
    base_sha: str
    head_sha: str


def fetch_pr_diff(
    repository: str,
    pr_number: int,
    github_token: Optional[str] = None
) -> PRDiff:
    """
    Fetch PR diff from GitHub REST API.

    Args:
        repository: Format "owner/repo" (e.g., "facebook/react")
        pr_number: Pull request number
        github_token: GitHub personal access token (optional, increases rate limit)

    Returns:
        PRDiff object containing all changed files and metadata

    Raises:
        requests.HTTPError: If API call fails
    """
    url = f"https://api.github.com/repos/{repository}/pulls/{pr_number}/files"

    headers = {
        "Accept": "application/vnd.github.v3+json",
    }

    if github_token:
        headers["Authorization"] = f"token {github_token}"

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    files_data = response.json()

    # Also fetch PR metadata to get base/head SHAs
    pr_url = f"https://api.github.com/repos/{repository}/pulls/{pr_number}"
    pr_response = requests.get(pr_url, headers=headers)
    pr_response.raise_for_status()
    pr_data = pr_response.json()

    files = []
    for file_data in files_data:
        file_diff = FileDiff(
            filename=file_data["filename"],
            status=file_data["status"],
            additions=file_data.get("additions", 0),
            deletions=file_data.get("deletions", 0),
            patch=file_data.get("patch"),  # May be None for binary files
            old_filename=file_data.get("previous_filename")
        )
        files.append(file_diff)

    return PRDiff(
        pr_number=pr_number,
        repository=repository,
        files=files,
        base_sha=pr_data["base"]["sha"],
        head_sha=pr_data["head"]["sha"]
    )


def fetch_file_content(
    repository: str,
    file_path: str,
    ref: str,
    github_token: Optional[str] = None
) -> str:
    """
    Fetch full file content from a specific commit/branch.

    Useful for getting complete file context beyond the diff.

    Args:
        repository: Format "owner/repo"
        file_path: Path to file in repository
        ref: Git ref (commit SHA, branch name, tag)
        github_token: GitHub token

    Returns:
        File content as string
    """
    url = f"https://api.github.com/repos/{repository}/contents/{file_path}"

    headers = {
        "Accept": "application/vnd.github.v3.raw",  # Get raw content directly
    }

    if github_token:
        headers["Authorization"] = f"token {github_token}"

    params = {"ref": ref}

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    return response.text
