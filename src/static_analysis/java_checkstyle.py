"""
Java static analysis adapter using Checkstyle.

Checkstyle outputs XML — we parse it and normalize to Finding objects.

DESIGN NOTE:
============
PMD could be added as java_pmd.py with the same interface.
Both could run in parallel, or the registry could choose based on
configuration. The interface makes this choice modular.
"""

import subprocess
import xml.etree.ElementTree as ET
import logging
from typing import List
from pathlib import Path

from .base import StaticAnalyzer, Finding, Severity

logger = logging.getLogger(__name__)


class CheckstyleAnalyzer(StaticAnalyzer):
    """Adapter for Checkstyle — translates XML output to normalized Findings."""

    def __init__(self, config_path: str = None):
        self.name = "checkstyle"
        # Use default config if none provided (google_checks.xml or sun_checks.xml)
        self.config_path = config_path or "/google_checks.xml"

    def get_language(self) -> str:
        return "java"

    def is_available(self) -> bool:
        """Check if Checkstyle JAR is available."""
        try:
            # Assuming checkstyle is available via command or CHECKSTYLE_JAR env var
            result = subprocess.run(
                ["java", "-jar", "checkstyle.jar", "-version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # Also check if checkstyle command exists (some systems have it aliased)
            try:
                result = subprocess.run(
                    ["checkstyle", "-version"],
                    capture_output=True,
                    timeout=5
                )
                return result.returncode == 0
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return False

    def analyze(self, file_paths: List[str]) -> List[Finding]:
        """
        Run Checkstyle and parse XML output.

        Checkstyle XML format:
        <checkstyle version="8.x">
            <file name="path/to/File.java">
                <error line="10" column="5" severity="error"
                       message="Line too long" source="com.puppycrawl.tools.checkstyle.checks.sizes.LineLengthCheck"/>
                ...
            </file>
        </checkstyle>
        """
        if not file_paths:
            return []

        # Filter to only .java files
        java_files = [f for f in file_paths if f.endswith('.java')]
        if not java_files:
            return []

        try:
            # Run Checkstyle with XML output
            cmd = [
                "checkstyle",  # Assumes checkstyle command is in PATH
                "-c", self.config_path,
                "-f", "xml",  # XML format output
            ] + java_files

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            # Checkstyle returns non-zero if issues found
            if not result.stdout.strip():
                return []

            # Parse XML
            root = ET.fromstring(result.stdout)

            findings = []
            for file_elem in root.findall("file"):
                file_path = file_elem.get("name")

                for error_elem in file_elem.findall("error"):
                    finding = Finding(
                        file_path=file_path,
                        line=int(error_elem.get("line", 0)),
                        column=int(error_elem.get("column", 0)) if error_elem.get("column") else None,
                        severity=self._normalize_severity(error_elem.get("severity", "warning")),
                        rule_id=self._extract_rule_name(error_elem.get("source", "")),
                        message=error_elem.get("message", ""),
                        source="checkstyle"
                    )
                    findings.append(finding)

            logger.info(f"checkstyle found {len(findings)} issues in {len(java_files)} files")
            return findings

        except subprocess.TimeoutExpired:
            logger.error(f"checkstyle timed out analyzing {len(java_files)} files")
            return []
        except ET.ParseError as e:
            logger.error(f"Failed to parse checkstyle XML output: {e}")
            return []
        except Exception as e:
            logger.error(f"checkstyle analysis failed: {e}")
            return []

    def _normalize_severity(self, checkstyle_severity: str) -> Severity:
        """Map Checkstyle severity to normalized severity."""
        severity_lower = checkstyle_severity.lower()
        if severity_lower == "error":
            return Severity.ERROR
        elif severity_lower == "warning":
            return Severity.WARNING
        else:  # info, ignore
            return Severity.INFO

    def _extract_rule_name(self, source: str) -> str:
        """
        Extract short rule name from full Java class path.

        Example: "com.puppycrawl.tools.checkstyle.checks.sizes.LineLengthCheck"
                 -> "LineLengthCheck"
        """
        if not source:
            return "unknown"
        return source.split(".")[-1].replace("Check", "")
