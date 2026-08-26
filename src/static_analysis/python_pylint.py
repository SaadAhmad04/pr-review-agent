"""
Python static analysis adapter using pylint.

ADAPTER PATTERN IN ACTION:
===========================
This file is the ONLY place that knows about pylint's JSON output format.
It translates pylint's schema into our normalized Finding schema.

To add bandit (security linter) later:
1. Create python_bandit.py
2. Implement the same StaticAnalyzer interface
3. Register it in registry.py
4. Done — no other files change
"""

import subprocess
import json
import logging
from typing import List
from pathlib import Path

from .base import StaticAnalyzer, Finding, Severity

logger = logging.getLogger(__name__)


class PylintAnalyzer(StaticAnalyzer):
    """Adapter for pylint — translates pylint JSON to normalized Findings."""

    def __init__(self):
        self.name = "pylint"

    def get_language(self) -> str:
        return "python"

    def is_available(self) -> bool:
        """Check if pylint is installed."""
        try:
            result = subprocess.run(
                ["pylint", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def analyze(self, file_paths: List[str]) -> List[Finding]:
        """
        Run pylint on files and normalize output.

        Pylint outputs JSON with this structure:
        [
            {
                "type": "convention|warning|error|...",
                "module": "module_name",
                "obj": "",
                "line": 10,
                "column": 0,
                "path": "path/to/file.py",
                "symbol": "line-too-long",
                "message": "Line too long (120/100)",
                "message-id": "C0301"
            },
            ...
        ]

        We normalize this to our Finding schema.
        """
        if not file_paths:
            return []

        # Filter to only .py files
        python_files = [f for f in file_paths if f.endswith('.py')]
        if not python_files:
            return []

        try:
            # Run pylint with JSON output
            cmd = [
                "pylint",
                "--output-format=json",
                "--reports=no",  # Don't generate summary reports
                "--score=no",    # Don't show score
            ] + python_files

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            # Pylint returns non-zero if it finds issues, so don't check returncode
            # Just parse the JSON output
            if not result.stdout.strip():
                return []

            pylint_results = json.loads(result.stdout)

            # Translate to normalized findings
            findings = []
            for item in pylint_results:
                finding = Finding(
                    file_path=item["path"],
                    line=item["line"],
                    column=item.get("column", 0),
                    severity=self._normalize_severity(item["type"]),
                    rule_id=f"{item['message-id']}:{item['symbol']}",
                    message=item["message"],
                    source="pylint"
                )
                findings.append(finding)

            logger.info(f"pylint found {len(findings)} issues in {len(python_files)} files")
            return findings

        except subprocess.TimeoutExpired:
            logger.error(f"pylint timed out analyzing {len(python_files)} files")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse pylint JSON output: {e}")
            return []
        except Exception as e:
            logger.error(f"pylint analysis failed: {e}")
            return []

    def _normalize_severity(self, pylint_type: str) -> Severity:
        """
        Map pylint message types to our normalized severity.

        Pylint types: convention, refactor, warning, error, fatal
        """
        if pylint_type in ["error", "fatal"]:
            return Severity.ERROR
        elif pylint_type == "warning":
            return Severity.WARNING
        else:  # convention, refactor
            return Severity.INFO
