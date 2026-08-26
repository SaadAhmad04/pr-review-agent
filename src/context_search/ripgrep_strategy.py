"""
Ripgrep-based context search strategy.

THE V1 IMPLEMENTATION:
======================
This is the ONLY concrete strategy we implement for v1.

Why ripgrep?
- Fast: faster than grep, ag, ack
- Language-agnostic: pure text search, no parsing needed
- Reliable: works on any codebase without language-specific setup
- Ubiquitous: easy to install on any platform

What ripgrep CAN'T do (but future strategies can):
- Distinguish definitions from usages (everything is just a text match)
- Understand scope (finds ALL matches, including comments/strings)
- Handle refactorings (no AST awareness)

Future strategies that could be added:
- ctags_strategy.py — fast, understands definitions, language-aware
- treesitter_strategy.py — AST-based, most accurate, slower
- lsp_strategy.py — uses Language Server Protocol if available

Each would just implement ContextSearchStrategy and get registered.
The graph doesn't care which one it uses.
"""

import subprocess
import re
import logging
from typing import List, Optional
from pathlib import Path

from .base import (
    ContextSearchStrategy,
    CodeReference,
    ReferenceType
)

logger = logging.getLogger(__name__)


class RipgrepStrategy(ContextSearchStrategy):
    """
    Fast text-based search using ripgrep.

    Language-agnostic, widely available, good enough for v1.
    """

    def __init__(self):
        self.name = "ripgrep"

    def get_name(self) -> str:
        return self.name

    def is_available(self) -> bool:
        """Check if ripgrep (rg) is installed."""
        try:
            result = subprocess.run(
                ["rg", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def find_references(
        self,
        symbol: str,
        repo_path: str,
        file_filter: List[str] = None,
        max_results: int = 100
    ) -> List[CodeReference]:
        """
        Use ripgrep to find all occurrences of a symbol.

        We use ripgrep with:
        - Word boundary matching (--word-regexp) to avoid partial matches
        - Line numbers (--line-number) and column numbers (--column)
        - Context (--context) to get surrounding lines
        - JSON output (--json) for easy parsing

        Args:
            symbol: Symbol to search for
            repo_path: Repository root path
            file_filter: Optional list of extensions like [".py", ".java"]
            max_results: Max results to return

        Returns:
            List of CodeReference objects
        """
        if not self.is_available():
            logger.error("ripgrep is not available")
            return []

        try:
            # Build ripgrep command
            cmd = [
                "rg",
                "--json",                    # JSON output for easy parsing
                "--line-number",             # Include line numbers
                "--column",                  # Include column numbers
                "--word-regexp",             # Match whole words only
                "--max-count", str(max_results),  # NOTE: Per-FILE limit, not total
                "--",                        # Separator before pattern
                symbol,                      # The symbol to search for
                repo_path                    # Where to search
            ]

            # Add file type filters if provided
            if file_filter:
                for ext in file_filter:
                    cmd.insert(-2, "--glob")  # Insert before -- separator
                    cmd.insert(-2, f"*{ext}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            # ripgrep returns 0 if matches found, 1 if no matches, 2 if error
            if result.returncode == 1:
                logger.info(f"No matches found for symbol: {symbol}")
                return []
            elif result.returncode > 1:
                logger.error(f"ripgrep failed with code {result.returncode}: {result.stderr}")
                return []

            # Parse JSON output
            references = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                try:
                    import json
                    data = json.loads(line)

                    # ripgrep outputs multiple JSON message types
                    # We want 'match' messages
                    if data.get('type') != 'match':
                        continue

                    match_data = data.get('data', {})
                    path_data = match_data.get('path', {})
                    line_number = match_data.get('line_number', 0)

                    # Get the matched line content
                    lines = match_data.get('lines', {})
                    context = lines.get('text', '').strip() if lines else ''

                    # Get column from first submatch
                    submatches = match_data.get('submatches', [])
                    column = submatches[0].get('start', 0) if submatches else 0

                    # Try to infer reference type from context
                    ref_type = self._infer_reference_type(context, symbol)

                    reference = CodeReference(
                        file_path=path_data.get('text', ''),
                        line_number=line_number,
                        column=column,
                        context=context,
                        reference_type=ref_type,
                        symbol=symbol
                    )
                    references.append(reference)

                    # Total cap (across all files) - needed because --max-count is per-file
                    if len(references) >= max_results:
                        break

                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse ripgrep JSON line: {line}")
                    continue

            logger.info(f"Found {len(references)} references to '{symbol}' in {repo_path}")
            return references

        except subprocess.TimeoutExpired:
            logger.error(f"ripgrep search timed out for symbol: {symbol}")
            return []
        except Exception as e:
            logger.error(f"ripgrep search failed: {e}")
            return []

    def _infer_reference_type(self, context: str, symbol: str) -> ReferenceType:
        """
        Try to guess if this is a definition or usage based on context.

        This is HEURISTIC and language-agnostic, so it's not perfect.

        FUTURE: Tree-sitter or LSP could provide accurate type info.
        KNOWN LIMITATION: May match in comments/strings (ripgrep is text-based).

        Heuristics:
        - "def symbol", "func symbol", "class symbol" -> likely DEFINITION
        - "import symbol", "from ... import symbol" -> IMPORT
        - Otherwise -> USAGE
        """
        context_lower = context.lower()

        # Definition patterns (language-agnostic)
        definition_keywords = [
            f"def {symbol}",      # Python
            f"func {symbol}",     # Go
            f"function {symbol}", # JavaScript
            f"class {symbol}",    # Multiple languages
            f"interface {symbol}",# Java, TypeScript
            f"struct {symbol}",   # Go, C
            f"type {symbol}",     # Go, TypeScript
            f"void {symbol}",     # C, Java
            f"public {symbol}",   # Java
            f"private {symbol}",  # Java
        ]

        for pattern in definition_keywords:
            if pattern.lower() in context_lower:
                return ReferenceType.DEFINITION

        # Import patterns
        import_keywords = [
            "import",
            "from",
            "require",
            "#include",
        ]

        for keyword in import_keywords:
            if keyword in context_lower:
                return ReferenceType.IMPORT

        # Default to usage
        return ReferenceType.USAGE


# Singleton registry for context search strategies
_strategy_registry = None


def get_context_search_registry():
    """
    Get the global strategy registry.

    Initializes with ripgrep as the default (and only) strategy for v1.
    """
    global _strategy_registry

    if _strategy_registry is None:
        from .base import StrategyRegistry
        _strategy_registry = StrategyRegistry()

        # Register ripgrep as default strategy
        ripgrep = RipgrepStrategy()
        _strategy_registry.register_strategy(ripgrep, is_default=True)

        # FUTURE: Register additional strategies here
        # _strategy_registry.register_strategy(CtagsStrategy())
        # _strategy_registry.register_strategy(TreeSitterStrategy())

    return _strategy_registry
