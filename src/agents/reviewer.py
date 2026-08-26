"""
Reviewer Agent - LLM-powered code reviewer.

THE REVIEWER'S ROLE:
====================
Static analysis tools catch syntax, style, and simple bugs.
The Reviewer agent is an LLM that catches issues requiring REASONING:

- Logic errors ("this loops forever if X")
- API misuse ("this method was deprecated in v2.0")
- Race conditions ("two threads can modify this without locking")
- Security issues linters miss ("SQL injection possible here")
- Performance problems ("this N+1 query will be slow")
- Architecture violations ("this breaks the separation of concerns")

LANGUAGE-AGNOSTIC DESIGN:
=========================
The agent's prompt EXPLICITLY receives:
1. primary_language: str (e.g., "python", "java", "go")
2. normalized findings: List[Finding] (same schema for all languages)
3. normalized context: Dict[str, List[CodeReference]] (same schema)

The agent reasons ABOUT the language, but the data structures are universal.

Example prompt structure:
    "You are reviewing a {primary_language} pull request.
     Static analysis found these issues: {normalized_findings}
     Here are relevant code references: {context_references}

     Find additional issues that require semantic understanding..."

The prompt adapts to the language, but the INPUT/OUTPUT schemas don't change.
"""

import logging
from typing import List, Dict, Optional, Any

from src.state import ReviewerFinding
from src.tools.diff_fetch import PRDiff
from src.language.detector import LanguageInfo
from src.static_analysis.base import Finding
from src.context_search.base import CodeReference

logger = logging.getLogger(__name__)


class ReviewerAgent:
    """
    LLM-powered reviewer that finds issues beyond static analysis.

    For v1, this is a PLACEHOLDER that shows the prompt structure.
    In the next phase, we'll add actual LLM calls (Claude via Anthropic API).
    """

    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        self.model = model

    def review(
        self,
        pr_diff: PRDiff,
        primary_language: str,
        detected_languages: List[LanguageInfo],
        static_findings: List[Finding],
        context_references: Dict[str, Any],  # New 3-level structure from context search
    ) -> List[ReviewerFinding]:
        """
        Review a PR and generate findings.

        Args:
            pr_diff: The complete PR diff
            primary_language: Main language (e.g., "python", "java")
            detected_languages: All detected languages with file counts
            static_findings: Normalized findings from static analysis tools
            context_references: Symbol references from context search

        Returns:
            List of ReviewerFinding objects
        """

        # Build the prompt
        prompt = self._build_prompt(
            pr_diff=pr_diff,
            primary_language=primary_language,
            detected_languages=detected_languages,
            static_findings=static_findings,
            context_references=context_references,
        )

        logger.info(f"Reviewer prompt built ({len(prompt)} chars)")
        logger.debug(f"Prompt preview:\n{prompt[:500]}...")

        # TODO: Call LLM here
        # For now, return empty findings to show the structure

        # Placeholder: simulate finding one issue
        placeholder_findings = [
            ReviewerFinding(
                file_path=pr_diff.files[0].filename if pr_diff.files else "unknown",
                line=1,
                severity="minor",
                category="logic",
                title="Placeholder finding",
                description="This is a placeholder. LLM integration coming next.",
                suggestion="Integrate Claude API to generate real findings.",
                confidence=1.0,
            )
        ]

        return placeholder_findings

    def _build_prompt(
        self,
        pr_diff: PRDiff,
        primary_language: str,
        detected_languages: List[LanguageInfo],
        static_findings: List[Finding],
        context_references: Dict[str, Any],  # New 3-level structure
    ) -> str:
        """
        Build the reviewer agent prompt.

        THE PROMPT STRUCTURE:
        =====================
        1. Role definition (you are an expert code reviewer)
        2. Context about the PR (repository, language, file count)
        3. Language-specific guidance (explicitly mentioning the language)
        4. Static analysis summary (what tools already found)
        5. Context references (3-level structure: changed code, callers, blast radius)
        6. The actual diff patches (prioritized by relevance to changed symbols)
        7. Instructions for what to look for (level-aware guidance)
        8. Output format specification

        The key insight: The language is an EXPLICIT INPUT to the prompt,
        so the agent adapts its reasoning. But the findings schema is FIXED,
        so the output is language-agnostic.
        """

        # Format language info
        lang_summary = ", ".join([
            f"{lang.language} ({lang.file_count} files)"
            for lang in detected_languages
        ])

        # Format static analysis summary (hints/supporting evidence)
        static_summary = self._format_static_findings(static_findings)

        # Format context references (3-level structure)
        context_summary = self._format_context_references(context_references)

        # Format diff patches (prioritize files containing changed symbols)
        diff_content = self._format_diff_patches(pr_diff, context_references)

        # Build the prompt
        prompt = f"""You are an expert code reviewer specializing in {primary_language}.

## Pull Request Context

- Repository: {pr_diff.repository}
- PR Number: #{pr_diff.pr_number}
- Primary Language: {primary_language}
- All Languages: {lang_summary}
- Files Changed: {len(pr_diff.files)}
- Base SHA: {pr_diff.base_sha[:7]}
- Head SHA: {pr_diff.head_sha[:7]}

## Your Task

Review this {primary_language} pull request and identify issues that require **semantic understanding**.
Static analysis tools have already caught syntax and style issues. Focus on:

1. **Logic Errors**: Infinite loops, off-by-one errors, incorrect algorithms
2. **API Misuse**: Deprecated methods, incorrect usage patterns, missing error handling
3. **Concurrency Issues**: Race conditions, deadlocks, missing synchronization
4. **Security Vulnerabilities**: Injection attacks, authentication bypasses, data leaks
5. **Performance Problems**: N+1 queries, unnecessary copies, blocking operations
6. **Architecture Violations**: Coupling, abstraction breaks, separation of concerns

## Static Analysis Summary

{static_summary}

{context_summary}

## Diff Content

{diff_content}

## Instructions

The code context above is organized by relevance:
- **Changed Code**: Focus your analysis here — this is what the PR modifies.
- **Callers**: Check whether each caller still works correctly with the changed code (signature changes, return-type changes, new error conditions, usage patterns).
- **Blast Radius**: Use this to assess the risk and reach of shared-state or concurrency issues.

For each issue you find:
1. Specify the exact file and line number
2. Categorize as: bug, security, performance, style, or logic
3. Rate severity as: critical, major, or minor
4. Explain WHY it's an issue (not just WHAT is wrong)
5. Suggest a specific fix
6. Indicate your confidence (0.0 to 1.0)

Focus on HIGH-CONFIDENCE findings. If you're unsure, don't report it.

## Output Format

Return a JSON array of findings:

```json
[
  {{
    "file_path": "path/to/file.{self._get_extension(primary_language)}",
    "line": 42,
    "severity": "major",
    "category": "security",
    "title": "SQL injection vulnerability",
    "description": "User input is directly concatenated into SQL query without sanitization. An attacker could execute arbitrary SQL.",
    "suggestion": "Use parameterized queries: cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))",
    "confidence": 0.95
  }}
]
```

Remember: You're looking for issues that require REASONING about {primary_language} semantics.
Static analysis already caught the simple stuff.
"""

        return prompt

    def _format_static_findings(self, findings: List[Finding]) -> str:
        """
        Format static analysis findings for the prompt.

        These are HINTS/SUPPORTING EVIDENCE, not the main signal.
        The reviewer's job is to find SEMANTIC bugs static analysis missed.
        """
        if not findings:
            return "No static analysis findings (all tools unavailable or no issues found)."

        # Group by severity
        by_severity = {}
        for finding in findings:
            sev = finding.severity.value
            if sev not in by_severity:
                by_severity[sev] = []
            by_severity[sev].append(finding)

        summary_lines = [f"Static analysis found {len(findings)} issues:\n"]

        for severity in ["error", "warning", "info"]:
            if severity in by_severity:
                count = len(by_severity[severity])
                summary_lines.append(f"- {severity.upper()}: {count}")

                # Show top 3 per severity as hints
                for finding in by_severity[severity][:3]:
                    summary_lines.append(
                        f"  • {finding.file_path}:{finding.line} - "
                        f"{finding.rule_id}: {finding.message[:80]}"
                    )

                if len(by_severity[severity]) > 3:
                    summary_lines.append(f"  ... and {count - 3} more")

        return "\n".join(summary_lines)

    def _format_context_references(
        self,
        context_refs: Dict[str, Any]
    ) -> str:
        """
        Format context references for the prompt (NEW 3-level structure).

        The context search now outputs:
        - level_0: Changed code (what this PR modifies) — HIGHEST priority
        - level_1: Callers (code that depends on changes) — catch broken contracts
        - level_2_summary: Blast radius (awareness of wider usage)

        WHY LEVELS: Provides relevance-ranked context so the LLM focuses on the
        most important code first (the actual changes and their immediate callers),
        with blast-radius awareness to catch shared-state/concurrency bugs.
        """
        if not context_refs:
            return "No context references available."

        level_0 = context_refs.get("level_0", {})
        level_1 = context_refs.get("level_1", {})
        level_2_summary = context_refs.get("level_2_summary", {})

        # If all levels empty, return early
        if not level_0 and not level_1 and not level_2_summary:
            return "No context references available."

        lines = ["## Code Context (organized by relevance to this change)\n"]

        # Level 0: Changed code — focus here
        if level_0:
            lines.append("### Changed Code (what this PR modifies — review this most carefully)\n")
            for symbol, data in level_0.items():
                lines.append(f"**`{symbol}`**\n")

                # Show definitions
                definition_refs = data.get("definition_refs", [])
                if definition_refs:
                    def_locations = [
                        f"{ref.file_path}:{ref.line_number}"
                        for ref in definition_refs
                    ]
                    lines.append(f"Defined at: {', '.join(def_locations)}\n")

                # Show changed code (full diff hunk)
                changed_code = data.get("changed_code", "")
                if changed_code:
                    lines.append(f"```diff\n{changed_code}\n```\n")

        # Level 1: Callers — check for broken contracts
        if level_1:
            lines.append("### Callers (code that DEPENDS on the changed symbols — check for broken contracts)\n")
            for symbol, refs in level_1.items():
                if not refs:
                    continue

                lines.append(f"**`{symbol}` callers:**\n")

                # These are already ranked/deduped/capped by context search
                # DO NOT re-truncate — the context search deliberately chose these
                for ref in refs:
                    lines.append(
                        f"- {ref.file_path}:{ref.line_number} ({ref.reference_type.value})"
                    )
                    lines.append(f"  ```\n  {ref.context}\n  ```")

                lines.append("")  # Blank line between symbols

        # Level 2: Blast radius — awareness only
        if level_2_summary:
            lines.append("### Blast Radius (wider usage — awareness only)\n")
            for symbol, summary_str in level_2_summary.items():
                lines.append(f"- {summary_str}")

        return "\n".join(lines)

    def _format_diff_patches(
        self,
        pr_diff: PRDiff,
        context_references: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Format diff patches for the prompt, PRIORITIZED by relevance.

        WHY PRIORITIZE BY RELEVANCE (not by position):
        - Blind position-based truncation ([:20], [:2000]) can drop the most
          important changes (e.g., file #21 contains the changed function).
        - Files containing changed symbols (from level_0/level_1) are shown FIRST
          and IN FULL, so the LLM always sees the actual changes.
        - Other files are summarized if we hit token limits.

        This ensures the LLM never misses the core changes even in large PRs.
        """
        # Extract relevant file paths from context (level_0 and level_1)
        relevant_files = set()
        if context_references:
            level_0 = context_references.get("level_0", {})
            level_1 = context_references.get("level_1", {})

            # Collect file paths from definition_refs in level_0
            for symbol_data in level_0.values():
                for ref in symbol_data.get("definition_refs", []):
                    relevant_files.add(ref.file_path)

            # Collect file paths from level_1 callers
            for refs in level_1.values():
                for ref in refs:
                    relevant_files.add(ref.file_path)

        # Partition files: relevant first, others after
        relevant_file_diffs = []
        other_file_diffs = []

        for file_diff in pr_diff.files:
            if file_diff.filename in relevant_files:
                relevant_file_diffs.append(file_diff)
            else:
                other_file_diffs.append(file_diff)

        lines = []

        # Show relevant files FIRST and IN FULL (no truncation)
        for file_diff in relevant_file_diffs:
            lines.append(f"\n### File: `{file_diff.filename}` [RELEVANT]")
            lines.append(f"Status: {file_diff.status}")
            lines.append(f"Changes: +{file_diff.additions} -{file_diff.deletions}")

            if file_diff.patch:
                lines.append(f"```diff\n{file_diff.patch}\n```")
            else:
                lines.append("(Binary file or no patch available)")

        # Show other files up to a reasonable limit
        MAX_OTHER_FILES = 10
        for file_diff in other_file_diffs[:MAX_OTHER_FILES]:
            lines.append(f"\n### File: `{file_diff.filename}`")
            lines.append(f"Status: {file_diff.status}")
            lines.append(f"Changes: +{file_diff.additions} -{file_diff.deletions}")

            if file_diff.patch:
                # Moderate truncation for non-relevant files
                patch = file_diff.patch
                if len(patch) > 1000:
                    patch = patch[:1000] + "\n... (truncated, not a changed-symbol file)"

                lines.append(f"```diff\n{patch}\n```")
            else:
                lines.append("(Binary file or no patch available)")

        # Summarize remaining files
        remaining = len(other_file_diffs) - MAX_OTHER_FILES
        if remaining > 0:
            lines.append(f"\n... and {remaining} more files not shown (no changed symbols)")

        return "\n".join(lines)

    def _get_extension(self, language: str) -> str:
        """Map language to typical file extension."""
        extensions = {
            "python": "py",
            "java": "java",
            "go": "go",
            "c": "c",
            "cpp": "cpp",
            "javascript": "js",
            "typescript": "ts",
            "ruby": "rb",
            "rust": "rs",
        }
        return extensions.get(language, "txt")
