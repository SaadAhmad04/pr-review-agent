"""
Language detector based on file extensions.

Simple, reliable, and fast. Uses file extensions to determine which
programming languages are present in a diff. Returns a set of detected
languages that can be passed to the static analysis registry.
"""

from typing import Set, List
from pathlib import Path
from dataclasses import dataclass


@dataclass
class LanguageInfo:
    """Metadata about a detected language."""
    language: str  # Canonical name: "python", "java", "go", "c", "cpp"
    file_count: int
    files: List[str]


# Extension-to-language mapping
# This is the ONLY place language mappings are defined
EXTENSION_MAP = {
    # Python
    ".py": "python",
    ".pyw": "python",

    # Java
    ".java": "java",

    # Go
    ".go": "go",

    # C/C++
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",

    # JavaScript/TypeScript (for future expansion)
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",

    # Rust (for future expansion)
    ".rs": "rust",

    # Ruby (for future expansion)
    ".rb": "ruby",
}


def detect_languages_from_files(file_paths: List[str]) -> List[LanguageInfo]:
    """
    Detect all languages present in a list of file paths.

    Args:
        file_paths: List of file paths from a PR diff

    Returns:
        List of LanguageInfo objects, sorted by file count (most common first)

    Example:
        >>> detect_languages_from_files(["src/main.py", "lib/helper.py", "Main.java"])
        [LanguageInfo(language='python', file_count=2, files=['src/main.py', 'lib/helper.py']),
         LanguageInfo(language='java', file_count=1, files=['Main.java'])]
    """
    # Group files by detected language
    language_files = {}

    for file_path in file_paths:
        extension = Path(file_path).suffix.lower()

        if extension in EXTENSION_MAP:
            language = EXTENSION_MAP[extension]

            if language not in language_files:
                language_files[language] = []

            language_files[language].append(file_path)

    # Convert to LanguageInfo objects and sort by file count
    language_infos = [
        LanguageInfo(
            language=lang,
            file_count=len(files),
            files=sorted(files)
        )
        for lang, files in language_files.items()
    ]

    # Sort by file count descending (most common language first)
    language_infos.sort(key=lambda x: x.file_count, reverse=True)

    return language_infos


def get_supported_languages() -> Set[str]:
    """
    Return the set of all languages we can detect.

    Useful for checking if a language is supported before analysis.
    """
    return set(EXTENSION_MAP.values())


def is_language_supported(language: str) -> bool:
    """Check if a language is supported."""
    return language in get_supported_languages()
