"""
Go static analysis adapter using golangci-lint.

golangci-lint is a meta-linter that runs multiple Go linters in parallel
(golint, govet, staticcheck, etc.). It has excellent JSON output.
"""

import subprocess
import json
import logging
from typing import List

from .base import StaticAnalyzer, Finding, Severity

logger = logging.getLogger(__name__)


class GolangciLintAnalyzer(StaticAnalyzer):
    """Adapter for golangci-lint — runs multiple Go linters and normalizes output."""

    def __init__(self):
        self.name = "golangci-lint"

    def get_language(self) -> str:
        return "go"

    def is_available(self) -> bool:
        """Check if golangci-lint is installed."""
        try:
            result = subprocess.run(
                ["golangci-lint", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def analyze(self, file_paths: List[str]) -> List[Finding]:
        """
        Run golangci-lint with JSON output.

        JSON structure:
        {
            "Issues": [
                {
                    "FromLinter": "govet",
                    "Text": "error message",
                    "Severity": "error",
                    "Pos": {
                        "Filename": "main.go",
                        "Line": 10,
                        "Column": 5
                    }
                },
                ...
            ]
        }
        """
        if not file_paths:
            return []

        # Filter to only .go files
        go_files = [f for f in file_paths if f.endswith('.go')]
        if not go_files:
            return []

        try:
            # Run golangci-lint
            # Note: golangci-lint typically runs on directories or whole modules,
            # but can also target specific files
            cmd = [
                "golangci-lint",
                "run",
                "--out-format=json",
                "--print-issued-lines=false",
                "--print-linter-name=true",
            ] + go_files

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120  # Go linters can be slower
            )

            # golangci-lint returns non-zero if issues found
            if not result.stdout.strip():
                return []

            data = json.loads(result.stdout)
            issues = data.get("Issues", [])

            findings = []
            for issue in issues:
                pos = issue.get("Pos", {})
                finding = Finding(
                    file_path=pos.get("Filename", ""),
                    line=pos.get("Line", 0),
                    column=pos.get("Column", 0),
                    severity=self._normalize_severity(issue.get("Severity", "warning")),
                    rule_id=issue.get("FromLinter", "unknown"),
                    message=issue.get("Text", ""),
                    source="golangci-lint"
                )
                findings.append(finding)

            logger.info(f"golangci-lint found {len(findings)} issues in {len(go_files)} files")
            return findings

        except subprocess.TimeoutExpired:
            logger.error(f"golangci-lint timed out analyzing {len(go_files)} files")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse golangci-lint JSON output: {e}")
            return []
        except Exception as e:
            logger.error(f"golangci-lint analysis failed: {e}")
            return []

    def _normalize_severity(self, golangci_severity: str) -> Severity:
        """Map golangci-lint severity to normalized severity."""
        severity_lower = golangci_severity.lower()
        if severity_lower == "error":
            return Severity.ERROR
        elif severity_lower == "warning":
            return Severity.WARNING
        else:
            return Severity.INFO
