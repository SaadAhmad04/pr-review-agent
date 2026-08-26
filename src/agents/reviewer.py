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

from email.mime import text
import logging
import json
import os
import re
from typing import List, Dict, Optional, Any

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

from src.state import ReviewerFinding
from src.tools.diff_fetch import PRDiff
from src.language.detector import LanguageInfo
from src.static_analysis.base import Finding
from src.context_search.base import CodeReference

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Conservative token budget ceiling — well under Claude's context window, leaving room for response
MAX_PROMPT_TOKENS = 15000


class ReviewerAgent:
    """
    LLM-powered reviewer that finds issues beyond static analysis.

    For v1, this is a PLACEHOLDER that shows the prompt structure.
    In the next phase, we'll add actual LLM calls (Claude via Anthropic API).
    """

    def __init__(self, model: str = "claude-sonnet-4-5-20250929"):
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

        # Graceful degradation if API key not set
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning(
                "ANTHROPIC_API_KEY not set — skipping LLM review, returning no findings. "
                "Set the key in .env to enable AI-powered review."
            )
            return []

        # Build the prompt
        prompt = self._build_prompt(
            pr_diff=pr_diff,
            primary_language=primary_language,
            detected_languages=detected_languages,
            static_findings=static_findings,
            context_references=context_references,
        )

        logger.info(f"Reviewer prompt built ({len(prompt)} chars)")

        # Create the LLM client ONCE and reuse it for token counting + the call
        try:
            llm = ChatAnthropic(
                model_name=self.model,        # correct for this version (verified via signature)
                temperature=0.0,              # deterministic review
                max_tokens_to_sample=4096,    # correct for this version (verified via signature)
                timeout=60.0,                 # REQUIRED in this version — 60s per call
                stop=None,                    # REQUIRED in this version — no custom stop sequences
            )
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
            return []

        # Token budgeting (pass the existing client — don't create a new one)
        prompt = self._truncate_to_budget(prompt, llm)

        # Call LLM with retry logic
        try:
            response_text = self._call_llm_with_retry(llm, prompt, max_attempts=2)
            if response_text is None:
                logger.error("LLM call failed after retries — returning no findings")
                return []

            # Parse response into ReviewerFinding objects
            findings = self._parse_findings(response_text)
            logger.info(f"Reviewer agent generated {len(findings)} findings")
            return findings

        except Exception as e:
            logger.error(f"Unexpected error during LLM review: {e}")
            return []

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

    def _count_tokens(self, text: str, llm: ChatAnthropic) -> int:
        """
        Estimate token count for budgeting purposes.

        WHY CHARACTER APPROXIMATION (not a "real" tokenizer):
        - tiktoken is OpenAI's tokenizer — WRONG for Claude. Never use it.
        - langchain-anthropic's get_num_tokens() does NOT use Claude's tokenizer
        either; in this version it falls back to a GPT-2 tokenizer (also wrong,
        and requires the heavy `transformers` package). Verified broken/inaccurate.
        - Anthropic's real count_tokens endpoint requires a network round-trip per
        call — overkill for a simple "is the prompt too big?" budget check.

        So we use a conservative ~4-chars-per-token approximation. This is only
        used to decide whether to truncate before a deliberately conservative
        ceiling (MAX_PROMPT_TOKENS), so approximate is sufficient.
        """
        return len(text) // 4

    def _truncate_to_budget(self, prompt: str, llm: ChatAnthropic) -> str:
        """
        Truncate prompt to fit within MAX_PROMPT_TOKENS budget.

        Strategy: Hard-truncate from the end (which contains diff/context sections,
        not the instructions). Append a clear note that truncation occurred.

        This is a simple, conservative approach. A more sophisticated version could
        preserve instructions and truncate only context sections, but this is
        sufficient for v1.

        Args:
            prompt: The prompt text to potentially truncate
            llm: The ChatAnthropic client to use for token counting

        Returns:
            The original or truncated prompt
        """
        token_count = self._count_tokens(prompt, llm)

        if token_count <= MAX_PROMPT_TOKENS:
            return prompt

        logger.warning(
            f"Prompt exceeds token budget ({token_count} > {MAX_PROMPT_TOKENS}), truncating"
        )

        # Approximate character budget (conservative ratio: 4 chars per token)
        char_budget = MAX_PROMPT_TOKENS * 4
        truncation_note = "\n\n[NOTE: Context truncated to fit token budget. Review focuses on visible changes.]"

        # Leave room for the truncation note
        truncated = prompt[: char_budget - len(truncation_note)] + truncation_note

        return truncated

    def _call_llm_with_retry(
        self, llm: ChatAnthropic, prompt: str, max_attempts: int = 2
    ) -> Optional[str]:
        """
        Call the LLM with simple retry logic for transient errors.

        Args:
            llm: The ChatAnthropic client
            prompt: The prompt to send
            max_attempts: Maximum number of attempts (default 2)

        Returns:
            Response text, or None if all attempts fail
        """
        for attempt in range(1, max_attempts + 1):
            try:
                response = llm.invoke(prompt)
                content = response.content

                # content can be a string OR a list of content blocks depending on
                # the response type. Normalize to a plain string for downstream parsing.
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, dict):
                            parts.append(block.get("text", ""))
                        else:
                            parts.append(str(block))
                    content = "".join(parts)

                return content
            except Exception as e:
                logger.warning(f"LLM call attempt {attempt}/{max_attempts} failed: {e}")
                if attempt == max_attempts:
                    logger.error("All LLM call attempts failed")
                    return None

        return None

    def _extract_json_array(self, text: str) -> Optional[str]:
        """
        Extract JSON array from LLM response.

        Prefer content inside a ```json ... ``` fence if present;
        otherwise take from the first '[' to the last ']'. This is more robust than a
        greedy regex when the model adds prose containing stray brackets.

        Args:
            text: Raw LLM response text

        Returns:
            Extracted JSON string, or None if no array found
        """
        # 1. Try a fenced code block first (handles both ```json and plain ```)
        fence = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
        if fence:
            return fence.group(1)

        # 2. Fall back to first '[' ... last ']'
        start = text.find('[')
        end = text.rfind(']')
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]

        return None

    def _parse_findings(self, response_text: str) -> List[ReviewerFinding]:
        """
        Parse LLM response into ReviewerFinding objects.

        Robust parsing strategy:
        1. Extract JSON array (handle markdown fences or surrounding prose)
        2. Parse each item with defaults for missing fields
        3. Clamp/coerce values to valid ranges
        4. Log warnings for malformed items but don't crash the pipeline

        Args:
            response_text: Raw LLM response (may contain JSON in markdown fences)

        Returns:
            List of ReviewerFinding objects (empty if parsing fails)
        """
        # Extract JSON array from response (may be wrapped in markdown fences or prose)
        json_str = self._extract_json_array(response_text)
        if json_str is None:
            logger.warning(
                f"No JSON array found in LLM response. Response preview: {response_text[:200]}"
            )
            return []

        try:
            items = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to parse JSON from LLM response: {e}. JSON preview: {json_str[:200]}"
            )
            return []

        if not isinstance(items, list):
            logger.warning(f"LLM response JSON is not an array: {type(items)}")
            return []

        findings = []
        for idx, item in enumerate(items):
            try:
                # Extract fields with defaults
                file_path = item.get("file_path", "unknown")
                line = int(item.get("line", 1))  # Coerce to int
                severity = item.get("severity", "minor")
                category = item.get("category", "logic")
                title = item.get("title", "")
                description = item.get("description", "")
                suggestion = item.get("suggestion", "")
                confidence = float(item.get("confidence", 0.5))  # Coerce to float

                # Clamp confidence to [0.0, 1.0]
                confidence = max(0.0, min(1.0, confidence))

                # Ensure line is positive
                line = max(1, line)

                finding = ReviewerFinding(
                    file_path=file_path,
                    line=line,
                    severity=severity,
                    category=category,
                    title=title,
                    description=description,
                    suggestion=suggestion,
                    confidence=confidence,
                )
                findings.append(finding)

            except (ValueError, TypeError) as e:
                logger.warning(f"Skipping malformed finding at index {idx}: {e}. Item: {item}")
                continue

        logger.info(f"Parsed {len(findings)}/{len(items)} findings from LLM response")
        return findings
