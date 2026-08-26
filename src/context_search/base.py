"""
Base interface for context search strategies.

STRATEGY PATTERN:
=================
Different strategies for finding where a symbol is referenced/defined:
- ripgrep (fast text search, language-agnostic)
- ctags (structured tag indexing, understands syntax)
- tree-sitter (AST-based, most accurate but slower)
- LSP (Language Server Protocol, if available)

Each strategy implements the same interface, so the graph can swap them
without changing any other code.

For v1, we ONLY implement ripgrep_strategy.py.
Adding tree-sitter later? Just create treesitter_strategy.py + register it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Set, Optional
from enum import Enum


class ReferenceType(Enum):
    """Type of reference found."""
    DEFINITION = "definition"       # Where the symbol is defined
    USAGE = "usage"                 # Where it's used/called
    IMPORT = "import"               # Import/include statements
    UNKNOWN = "unknown"             # Can't determine type


@dataclass
class CodeReference:
    """
    A single reference to a symbol in the codebase.

    Normalized format across all search strategies.
    """
    file_path: str                  # Path to file containing the reference
    line_number: int                # Line where symbol appears
    column: int                     # Column position (0 if unknown)
    context: str                    # The actual line of code
    reference_type: ReferenceType   # What kind of reference is this?
    symbol: str                     # The symbol that was searched for

    def to_dict(self):
        """Convert to dictionary for serialization."""
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column": self.column,
            "context": self.context,
            "reference_type": self.reference_type.value,
            "symbol": self.symbol,
        }


class ContextSearchStrategy(ABC):
    """
    Abstract base for all context search strategies.

    Each strategy finds references to a symbol in different ways,
    but all return the same normalized CodeReference format.
    """

    @abstractmethod
    def find_references(
        self,
        symbol: str,
        repo_path: str,
        file_filter: Optional[List[str]] = None,
        max_results: int = 100
    ) -> List[CodeReference]:
        """
        Find all references to a symbol in the repository.

        Args:
            symbol: The symbol to search for (function name, class, variable, etc.)
            repo_path: Path to the repository root
            file_filter: Optional list of file extensions to search (e.g., [".py", ".java"])
            max_results: Maximum number of results to return

        Returns:
            List of CodeReference objects
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this strategy is available (dependencies installed).

        Returns:
            True if the strategy can be used, False otherwise
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """
        Return the name of this strategy.

        Returns:
            Strategy name (e.g., "ripgrep", "ctags", "tree-sitter")
        """
        pass

    def find_definitions(
        self,
        symbol: str,
        repo_path: str,
        language: Optional[str] = None
    ) -> List[CodeReference]:
        """
        Find where a symbol is DEFINED (not just used).

        Default implementation filters find_references() results to DEFINITION type.
        Strategies that can distinguish definitions (like ctags, tree-sitter) can
        override this for better performance.

        Args:
            symbol: Symbol name
            repo_path: Repository path
            language: Language hint (helps some strategies narrow search)

        Returns:
            List of definition references
        """
        all_refs = self.find_references(symbol, repo_path)
        return [ref for ref in all_refs if ref.reference_type == ReferenceType.DEFINITION]


class StrategyRegistry:
    """
    Registry for context search strategies.

    Similar to the static analysis registry — allows pluggable strategies
    without changing the graph code.
    """

    def __init__(self):
        self._strategies: List[ContextSearchStrategy] = []
        self._default_strategy: Optional[ContextSearchStrategy] = None

    def register_strategy(self, strategy: ContextSearchStrategy, is_default: bool = False):
        """Register a search strategy."""
        self._strategies.append(strategy)
        if is_default or self._default_strategy is None:
            self._default_strategy = strategy

    def get_default_strategy(self) -> Optional[ContextSearchStrategy]:
        """Get the default strategy (fastest/most reliable)."""
        return self._default_strategy

    def get_strategy(self, name: str) -> Optional[ContextSearchStrategy]:
        """Get a specific strategy by name."""
        for strategy in self._strategies:
            if strategy.get_name() == name:
                return strategy
        return None

    def get_available_strategies(self) -> List[ContextSearchStrategy]:
        """Get all strategies that are currently available."""
        return [s for s in self._strategies if s.is_available()]

    def find_references_with_fallback(
        self,
        symbol: str,
        repo_path: str,
        preferred_strategy: Optional[str] = None,
        **kwargs
    ) -> List[CodeReference]:
        """
        Try to find references using the preferred strategy, with fallback.

        If preferred strategy fails or is unavailable, tries available alternatives.
        """
        # Try preferred strategy first
        if preferred_strategy:
            strategy = self.get_strategy(preferred_strategy)
            if strategy and strategy.is_available():
                return strategy.find_references(symbol, repo_path, **kwargs)

        # Fall back to default
        if self._default_strategy and self._default_strategy.is_available():
            return self._default_strategy.find_references(symbol, repo_path, **kwargs)

        # Try any available strategy
        available = self.get_available_strategies()
        if available:
            return available[0].find_references(symbol, repo_path, **kwargs)

        # No strategies available
        return []
