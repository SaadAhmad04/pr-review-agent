"""
Base interface for static analysis adapters.

THE KEY DESIGN PATTERN:
=======================
Every linter (Checkstyle, pylint, golangci-lint, cppcheck, etc.) has its own
output format. This interface defines a SINGLE normalized schema that all
adapters must return.

Adding a new language requires:
1. Create one new adapter file (e.g., ruby_rubocop.py)
2. Implement the StaticAnalyzer interface
3. Add one line to registry.py to register it

That's it. The rest of the system (graph, agents, output formatting) never
needs to change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum


class Severity(Enum):
    """Normalized severity levels across all linters."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Finding:
    """
    Normalized finding format — SAME SCHEMA regardless of which linter produced it.

    This is the contract: every adapter MUST return findings in this exact format.
    The agent, graph, and output formatters only see this schema, never the
    raw linter output.
    """
    file_path: str              # Relative path to the file
    line: int                   # Line number where issue occurs
    column: Optional[int]       # Column number (if available)
    severity: Severity          # ERROR, WARNING, or INFO
    rule_id: str                # e.g., "E501" (pylint), "checkstyle:LineLength"
    message: str                # Human-readable description
    source: str                 # Which tool found it: "pylint", "checkstyle", "golangci-lint"

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "severity": self.severity.value,
            "rule_id": self.rule_id,
            "message": self.message,
            "source": self.source,
        }


class StaticAnalyzer(ABC):
    """
    Abstract base class for all static analysis adapters.

    Each adapter wraps a specific linter (pylint, Checkstyle, golangci-lint, etc.)
    and translates its output into our normalized Finding schema.

    INTERFACE CONTRACT:
    - analyze() takes a list of file paths
    - Returns a list of Finding objects
    - All exceptions should be caught internally and logged (don't crash the pipeline)
    - If the linter isn't installed, return empty list (graceful degradation)
    """

    @abstractmethod
    def analyze(self, file_paths: List[str]) -> List[Finding]:
        """
        Run static analysis on the given files.

        Args:
            file_paths: List of file paths to analyze

        Returns:
            List of normalized Finding objects
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if the underlying tool is installed and available.

        Returns:
            True if the tool is available, False otherwise
        """
        pass

    @abstractmethod
    def get_language(self) -> str:
        """
        Return the language this analyzer handles.

        Returns:
            Canonical language name (e.g., "python", "java", "go", "c")
        """
        pass


class AnalysisResult:
    """
    Container for analysis results with metadata.

    Used by the registry to return findings along with information about
    which analyzers ran, which failed, etc.
    """

    def __init__(self):
        self.findings: List[Finding] = []
        self.analyzers_run: List[str] = []
        self.analyzers_failed: List[str] = []
        self.analyzers_unavailable: List[str] = []

    def add_findings(self, findings: List[Finding], analyzer_name: str):
        """Add findings from an analyzer."""
        self.findings.extend(findings)
        self.analyzers_run.append(analyzer_name)

    def mark_failed(self, analyzer_name: str):
        """Mark an analyzer as failed."""
        self.analyzers_failed.append(analyzer_name)

    def mark_unavailable(self, analyzer_name: str):
        """Mark an analyzer as unavailable (not installed)."""
        self.analyzers_unavailable.append(analyzer_name)

    def get_summary(self) -> dict:
        """Get a summary of the analysis."""
        return {
            "total_findings": len(self.findings),
            "analyzers_run": self.analyzers_run,
            "analyzers_failed": self.analyzers_failed,
            "analyzers_unavailable": self.analyzers_unavailable,
            "findings_by_severity": {
                "error": len([f for f in self.findings if f.severity == Severity.ERROR]),
                "warning": len([f for f in self.findings if f.severity == Severity.WARNING]),
                "info": len([f for f in self.findings if f.severity == Severity.INFO]),
            }
        }
