"""
C/C++ static analysis adapter using cppcheck.

cppcheck is a lightweight, cross-platform static analyzer for C/C++.
It has excellent XML output that's easy to parse.
"""

import subprocess
import xml.etree.ElementTree as ET
import logging
from typing import List

from .base import StaticAnalyzer, Finding, Severity

logger = logging.getLogger(__name__)


class CppcheckAnalyzer(StaticAnalyzer):
    """Adapter for cppcheck — analyzes C/C++ code and normalizes findings."""

    def __init__(self, enable_all_checks: bool = True):
        self.name = "cppcheck"
        self.enable_all_checks = enable_all_checks

    def get_language(self) -> str:
        """
        Returns 'c' but actually handles both C and C++.

        The registry can map both 'c' and 'cpp' to this analyzer.
        """
        return "c"

    def is_available(self) -> bool:
        """Check if cppcheck is installed."""
        try:
            result = subprocess.run(
                ["cppcheck", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def analyze(self, file_paths: List[str]) -> List[Finding]:
        """
        Run cppcheck with XML output.

        XML structure (version 2):
        <results version="2">
            <cppcheck version="2.x"/>
            <errors>
                <error id="nullPointer" severity="error" msg="Null pointer dereference">
                    <location file="main.c" line="10" column="5"/>
                </error>
                ...
            </errors>
        </results>
        """
        if not file_paths:
            return []

        # Filter to C/C++ files
        c_cpp_files = [
            f for f in file_paths
            if any(f.endswith(ext) for ext in ['.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.hh', '.hxx'])
        ]

        if not c_cpp_files:
            return []

        try:
            # Build cppcheck command
            cmd = [
                "cppcheck",
                "--xml",              # XML output
                "--xml-version=2",    # Use version 2 format
                "-q",                 # Quiet (only errors)
            ]

            if self.enable_all_checks:
                cmd.append("--enable=all")

            cmd.extend(c_cpp_files)

            # cppcheck writes XML to stderr (not stdout)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            # Parse XML from stderr
            if not result.stderr.strip():
                return []

            root = ET.fromstring(result.stderr)
            errors_elem = root.find("errors")

            if errors_elem is None:
                return []

            findings = []
            for error_elem in errors_elem.findall("error"):
                # cppcheck can report multiple locations for one error
                # We'll take the first location
                location = error_elem.find("location")
                if location is None:
                    continue

                finding = Finding(
                    file_path=location.get("file", ""),
                    line=int(location.get("line", 0)),
                    column=int(location.get("column", 0)) if location.get("column") else None,
                    severity=self._normalize_severity(error_elem.get("severity", "warning")),
                    rule_id=error_elem.get("id", "unknown"),
                    message=error_elem.get("msg", ""),
                    source="cppcheck"
                )
                findings.append(finding)

            logger.info(f"cppcheck found {len(findings)} issues in {len(c_cpp_files)} files")
            return findings

        except subprocess.TimeoutExpired:
            logger.error(f"cppcheck timed out analyzing {len(c_cpp_files)} files")
            return []
        except ET.ParseError as e:
            logger.error(f"Failed to parse cppcheck XML output: {e}")
            return []
        except Exception as e:
            logger.error(f"cppcheck analysis failed: {e}")
            return []

    def _normalize_severity(self, cppcheck_severity: str) -> Severity:
        """
        Map cppcheck severity to normalized severity.

        cppcheck severities: error, warning, style, performance, portability, information
        """
        if cppcheck_severity == "error":
            return Severity.ERROR
        elif cppcheck_severity in ["warning", "performance", "portability"]:
            return Severity.WARNING
        else:  # style, information
            return Severity.INFO
