"""
Static analyzer registry — maps languages to analyzers.

THE SINGLE POINT OF REGISTRATION:
==================================
This is the ONLY file you touch when adding a new language.

To add Ruby support:
1. Create ruby_rubocop.py that implements StaticAnalyzer
2. Add ONE line here: register_analyzer("ruby", RubocopAnalyzer())
3. Done

The registry:
- Maps language names to analyzer instances
- Handles analyzer availability checking
- Coordinates running multiple analyzers in parallel (future)
- Aggregates results into a single AnalysisResult
"""

import logging
from typing import Dict, List, Optional

from .base import StaticAnalyzer, AnalysisResult
from .python_pylint import PylintAnalyzer
from .java_checkstyle import CheckstyleAnalyzer
from .go_lint import GolangciLintAnalyzer
from .c_cppcheck import CppcheckAnalyzer

logger = logging.getLogger(__name__)


class StaticAnalysisRegistry:
    """
    Central registry that maps languages to their analyzers.

    Singleton pattern — only one registry instance per process.
    """

    def __init__(self):
        # Maps language name -> list of analyzers
        # (multiple analyzers per language are supported, e.g., pylint + bandit)
        self._analyzers: Dict[str, List[StaticAnalyzer]] = {}
        self._initialize_default_analyzers()

    def _initialize_default_analyzers(self):
        """
        Register all built-in analyzers.

        THIS IS THE REGISTRATION POINT.
        Add your new analyzer here.
        """
        # Python
        self.register_analyzer("python", PylintAnalyzer())

        # Java
        self.register_analyzer("java", CheckstyleAnalyzer())

        # Go
        self.register_analyzer("go", GolangciLintAnalyzer())

        # C/C++ — use same analyzer for both
        cppcheck = CppcheckAnalyzer()
        self.register_analyzer("c", cppcheck)
        self.register_analyzer("cpp", cppcheck)

        # FUTURE EXPANSIONS (examples):
        # self.register_analyzer("python", BanditAnalyzer())  # Security linter
        # self.register_analyzer("javascript", ESLintAnalyzer())
        # self.register_analyzer("typescript", ESLintAnalyzer())
        # self.register_analyzer("rust", ClippyAnalyzer())
        # self.register_analyzer("ruby", RubocopAnalyzer())

    def register_analyzer(self, language: str, analyzer: StaticAnalyzer):
        """
        Register an analyzer for a language.

        Multiple analyzers for the same language are supported.
        They'll all run in sequence (or parallel in future versions).
        """
        if language not in self._analyzers:
            self._analyzers[language] = []

        self._analyzers[language].append(analyzer)
        logger.debug(f"Registered {analyzer.__class__.__name__} for {language}")

    def get_analyzers(self, language: str) -> List[StaticAnalyzer]:
        """Get all analyzers registered for a language."""
        return self._analyzers.get(language, [])

    def has_analyzer(self, language: str) -> bool:
        """Check if any analyzer is registered for a language."""
        return language in self._analyzers and len(self._analyzers[language]) > 0

    def analyze_files(
        self,
        language: str,
        file_paths: List[str],
        skip_unavailable: bool = True
    ) -> AnalysisResult:
        """
        Run all analyzers for a given language on the specified files.

        Args:
            language: The language to analyze (e.g., "python", "java")
            file_paths: List of file paths to analyze
            skip_unavailable: If True, skip analyzers that aren't installed

        Returns:
            AnalysisResult with findings and metadata
        """
        result = AnalysisResult()

        analyzers = self.get_analyzers(language)

        if not analyzers:
            logger.warning(f"No analyzers registered for language: {language}")
            return result

        for analyzer in analyzers:
            analyzer_name = analyzer.__class__.__name__

            # Check if analyzer is available
            if not analyzer.is_available():
                logger.warning(f"{analyzer_name} is not available (not installed?)")
                result.mark_unavailable(analyzer_name)
                if skip_unavailable:
                    continue
                else:
                    result.mark_failed(analyzer_name)
                    continue

            # Run the analyzer
            try:
                logger.info(f"Running {analyzer_name} on {len(file_paths)} files...")
                findings = analyzer.analyze(file_paths)
                result.add_findings(findings, analyzer_name)
                logger.info(f"{analyzer_name} found {len(findings)} issues")

            except Exception as e:
                logger.error(f"{analyzer_name} failed: {e}")
                result.mark_failed(analyzer_name)

        return result

    def analyze_multi_language(
        self,
        language_to_files: Dict[str, List[str]],
        skip_unavailable: bool = True
    ) -> Dict[str, AnalysisResult]:
        """
        Run analyzers for multiple languages at once.

        Args:
            language_to_files: Map of language -> list of files
            skip_unavailable: If True, skip unavailable analyzers

        Returns:
            Map of language -> AnalysisResult

        Example:
            >>> results = registry.analyze_multi_language({
            ...     "python": ["src/main.py", "src/utils.py"],
            ...     "java": ["Main.java"]
            ... })
        """
        results = {}

        for language, file_paths in language_to_files.items():
            logger.info(f"Analyzing {language}: {len(file_paths)} files")
            result = self.analyze_files(language, file_paths, skip_unavailable)
            results[language] = result

        return results

    def get_all_findings(
        self,
        language_to_files: Dict[str, List[str]],
        skip_unavailable: bool = True
    ) -> AnalysisResult:
        """
        Convenience method: analyze multiple languages and merge all findings.

        Returns a single AnalysisResult with combined findings from all languages.
        """
        language_results = self.analyze_multi_language(language_to_files, skip_unavailable)

        # Merge all results
        combined = AnalysisResult()
        for result in language_results.values():
            combined.findings.extend(result.findings)
            combined.analyzers_run.extend(result.analyzers_run)
            combined.analyzers_failed.extend(result.analyzers_failed)
            combined.analyzers_unavailable.extend(result.analyzers_unavailable)

        return combined


# Global singleton registry
_global_registry: Optional[StaticAnalysisRegistry] = None


def get_registry() -> StaticAnalysisRegistry:
    """
    Get the global analyzer registry (singleton).

    Usage:
        >>> from static_analysis.registry import get_registry
        >>> registry = get_registry()
        >>> result = registry.analyze_files("python", ["main.py"])
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = StaticAnalysisRegistry()
    return _global_registry
