"""
LangGraph StateGraph definition for PR review pipeline.

THE GRAPH WIRING PATTERN:
==========================
LangGraph models workflows as directed graphs where:
- Nodes = functions that read/write state
- Edges = transitions between nodes
- State = shared data structure flowing through

Our graph structure:
    START
      ↓
    fetch_diff (tool node)
      ↓
    detect_language (tool node)
      ↓
    run_static_analysis (tool node)
      ↓
    search_context (tool node)
      ↓
    reviewer_agent (LLM node) ← This is where the magic happens
      ↓
    END

Each tool node is deterministic (no LLM calls).
The Reviewer agent is where we call the LLM with all the normalized data.

DESIGN BENEFITS:
================
1. Linear flow is easy to understand and debug
2. Each node has ONE job (single responsibility)
3. Nodes can be tested independently
4. Easy to add parallel branches later (e.g., run static analysis + context search concurrently)
5. State schema makes data flow explicit
"""

from typing import Dict, Any, Optional
import logging

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from src.state import PRReviewState, create_initial_state
from src.tools.diff_fetch import fetch_pr_diff
from src.language.detector import detect_languages_from_files
from src.static_analysis.registry import get_registry
from src.context_search.ripgrep_strategy import get_context_search_registry
from src.context_search.base import ReferenceType
from src.tools.github_post import post_findings_to_github

logger = logging.getLogger(__name__)


# ===== TOOL NODES (No LLM calls) =====

def fetch_diff_node(state: PRReviewState) -> Dict[str, Any]:
    """
    Node 1: Fetch PR diff from GitHub.

    Reads: repository, pr_number, github_token
    Writes: pr_diff
    """
    logger.info(f"Fetching PR #{state['pr_number']} from {state['repository']}")

    try:
        pr_diff = fetch_pr_diff(
            repository=state["repository"],
            pr_number=state["pr_number"],
            github_token=state.get("github_token")
        )

        logger.info(f"Fetched diff with {len(pr_diff.files)} changed files")

        return {
            "pr_diff": pr_diff,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "fetch_diff": {
                    "files_changed": len(pr_diff.files),
                    "base_sha": pr_diff.base_sha,
                    "head_sha": pr_diff.head_sha,
                }
            }
        }

    except Exception as e:
        logger.error(f"Failed to fetch diff: {e}")
        return {
            "errors": [f"fetch_diff failed: {str(e)}"]
        }


def detect_language_node(state: PRReviewState) -> Dict[str, Any]:
    """
    Node 2: Detect languages from file extensions.

    Reads: pr_diff
    Writes: detected_languages, primary_language
    """
    pr_diff = state.get("pr_diff")

    if not pr_diff:
        return {"errors": ["No PR diff available for language detection"]}

    # Extract file paths from diff
    file_paths = [f.filename for f in pr_diff.files]

    logger.info(f"Detecting languages from {len(file_paths)} files")

    try:
        detected_languages = detect_languages_from_files(file_paths)

        # Primary language is the one with most files
        primary_language = detected_languages[0].language if detected_languages else None

        logger.info(
            f"Detected languages: {[lang.language for lang in detected_languages]}"
        )

        return {
            "detected_languages": detected_languages,
            "primary_language": primary_language,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "detect_language": {
                    "languages": [
                        {
                            "language": lang.language,
                            "file_count": lang.file_count,
                        }
                        for lang in detected_languages
                    ],
                    "primary": primary_language,
                }
            }
        }

    except Exception as e:
        logger.error(f"Language detection failed: {e}")
        return {"errors": [f"detect_language failed: {str(e)}"]}


def run_static_analysis_node(state: PRReviewState) -> Dict[str, Any]:
    """
    Node 3: Run static analysis tools based on detected languages.

    Reads: pr_diff, detected_languages
    Writes: static_analysis_results, static_analysis_findings

    THE KEY NORMALIZATION STEP:
    ===========================
    This node runs language-specific linters (pylint, checkstyle, etc.)
    but outputs a NORMALIZED list of Finding objects that all look the same.

    The Reviewer agent never sees pylint JSON or Checkstyle XML.
    It only sees Finding(file, line, severity, rule_id, message).
    """
    pr_diff = state.get("pr_diff")
    detected_languages = state.get("detected_languages", [])

    if not pr_diff or not detected_languages:
        logger.warning("Skipping static analysis: no diff or languages detected")
        return {
            "static_analysis_results": {},
            "static_analysis_findings": [],
        }

    logger.info(f"Running static analysis for {len(detected_languages)} languages")

    try:
        registry = get_registry()

        # Build map of language -> files
        language_to_files = {}
        for lang_info in detected_languages:
            language_to_files[lang_info.language] = lang_info.files

        # Run analysis for all languages
        results = registry.analyze_multi_language(
            language_to_files,
            skip_unavailable=True
        )

        # Flatten all findings into a single list (normalized format)
        all_findings = []
        for result in results.values():
            all_findings.extend(result.findings)

        logger.info(f"Static analysis found {len(all_findings)} total issues")

        # Log summary by language
        for lang, result in results.items():
            summary = result.get_summary()
            logger.info(
                f"{lang}: {summary['total_findings']} findings "
                f"({summary['analyzers_run']})"
            )

        # Convert AnalysisResult objects to serializable dicts
        results_dict = {
            lang: result.get_summary()
            for lang, result in results.items()
        }

        return {
            "static_analysis_results": results_dict,
            "static_analysis_findings": all_findings,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "static_analysis": results_dict
            }
        }

    except Exception as e:
        logger.error(f"Static analysis failed: {e}")
        return {"errors": [f"static_analysis failed: {str(e)}"]}


def search_context_node(state: PRReviewState) -> Dict[str, Any]:
    """
    Node 4: Search for symbol references to understand context.

    Reads: pr_diff, detected_languages
    Writes: context_references

    CONTEXT SEARCH STRATEGY (relevance-ranked, level-based):
    =========================================================
    Level 0: Changed function code + DEFINITION refs
             WHY: Catch signature changes, refactorings that break callers
                  (e.g., method signature changed but caller not updated)

    Level 1: Callers (USAGE refs, ranked & deduped)
             WHY: Catch broken callers, mismatched usage patterns, type errors
                  Show actual usage to detect N+1 patterns, missing error handling

    Level 2: Summary only (awareness, no full code)
             WHY: Catch shared-state bugs (e.g., cache updated in 5 places,
                  one forgot to lock), identify if symbol is widely used
                  (affects risk assessment for changes)

    This targets LOGICAL bugs (broken callers, N+1, shared-state),
    not syntax errors (static analysis catches those).

    DOWNSTREAM NOTE: Reviewer agent should consume all three levels:
    - Level 0: Understand what changed
    - Level 1: Check if callers match new signature/behavior
    - Level 2: Assess blast radius and cross-cutting concerns
    """
    pr_diff = state.get("pr_diff")

    if not pr_diff:
        logger.warning("Skipping context search: no diff available")
        return {"context_references": {}}

    try:
        # Get the context search strategy (ripgrep for v1)
        strategy_registry = get_context_search_registry()
        strategy = strategy_registry.get_default_strategy()

        if not strategy or not strategy.is_available():
            logger.warning("Context search strategy not available, skipping")
            return {"context_references": {}}

        # Extract symbols with relevance scores (hunk headers = high priority)
        ranked_symbols = _extract_changed_symbols(pr_diff)

        # Cap to top 5 by RELEVANCE SCORE (not alphabetically)
        # WHY: We cap by score, so high-signal symbols (functions actually
        # changed) are never dropped, only low-signal noise is filtered
        top_symbols = ranked_symbols[:5]
        symbol_names = [sym for sym, score in top_symbols]

        logger.info(f"Searching for {len(symbol_names)} high-relevance symbols in codebase")

        # Initialize level structure
        level_0 = {}  # Changed code + definitions
        level_1 = {}  # Callers (usage refs, ranked & deduped)
        level_2_summary = {}  # Summary strings

        for symbol, score in top_symbols:
            try:
                # Search whole repo (repo_path=".") - we WANT cross-file refs
                # This is the point: find related code BEYOND the diff
                # max_results=50 per symbol so we have enough to rank before filtering
                refs = strategy.find_references(
                    symbol=symbol,
                    repo_path=".",
                    max_results=50
                )

                if not refs:
                    continue

                # Separate by reference type (reuse existing _infer_reference_type)
                definitions = [r for r in refs if r.reference_type == ReferenceType.DEFINITION]
                usages = [r for r in refs if r.reference_type == ReferenceType.USAGE]
                imports = [r for r in refs if r.reference_type == ReferenceType.IMPORT]

                # Level 0: Changed code + definitions
                changed_code = _extract_changed_code_for_symbol(pr_diff, symbol)
                level_0[symbol] = {
                    "definition_refs": definitions,
                    "changed_code": changed_code
                }

                # Level 1: Top ~10 callers (prod-first, deduped per file)
                # WHY: 10 gives enough examples to spot patterns without overwhelming context
                # Prod-first: real usage more important than test mocks
                ranked_callers = _rank_and_dedupe_callers(usages, max_per_file=2)
                level_1[symbol] = ranked_callers[:10]

                # Level 2: Summary of ALL usages + imports (full blast radius)
                # WHY: Level 2 is awareness summary - counts EVERYTHING to assess impact
                # Use ALL refs (not just remainder after Level 1) for complete blast radius
                all_refs_for_summary = usages + imports
                level_2_summary[symbol] = _create_level2_summary(all_refs_for_summary, symbol)

                logger.debug(
                    f"Symbol '{symbol}': L0={len(definitions)} def, "
                    f"L1={len(level_1[symbol])} callers, "
                    f"L2={len(all_refs_for_summary)} total refs summarized"
                )

            except Exception as e:
                logger.warning(f"Failed to search for '{symbol}': {e}")
                continue

        total_l1_refs = sum(len(refs) for refs in level_1.values())

        logger.info(
            f"Context search: {len(level_0)} symbols with changed code, "
            f"{total_l1_refs} caller refs (L1), {len(level_2_summary)} summaries (L2)"
        )

        # New shape: level-based structure
        # DOWNSTREAM: Reviewer agent consumes all three levels
        context_references = {
            "changed_symbols": symbol_names,
            "level_0": level_0,
            "level_1": level_1,
            "level_2_summary": level_2_summary,
        }

        return {
            "context_references": context_references,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "context_search": {
                    "symbols_searched": len(symbol_names),
                    "level_0_count": len(level_0),
                    "level_1_refs": total_l1_refs,
                    "level_2_summaries": len(level_2_summary),
                }
            }
        }

    except Exception as e:
        logger.error(f"Context search failed: {e}")
        return {"errors": [f"context_search failed: {str(e)}"]}


def _extract_changed_symbols(pr_diff):
    """
    Extract symbols from diff with relevance scores.

    PRIMARY source: Hunk headers (@@ ... @@ context) - these are HIGH priority
    because they identify functions actually being modified (catches signature
    changes, refactorings that break callers).

    SECONDARY source: Added lines - LOW priority, aggressively filtered
    (avoids noise from imports, test helpers, common names).

    Returns:
        List of (symbol, score) tuples sorted by score descending.
    """
    import re

    # Stoplist: common names that rarely help catch logical bugs (language-agnostic)
    STOPLIST = {
        'print', 'len', 'range', 'str', 'int', 'list', 'dict', 'set', 'get',
        'self', 'this', 'new', 'return', 'if', 'else', 'for', 'while',
        'true', 'false', 'null', 'none', 'error', 'log', 'debug', 'info',
        'test', 'mock', 'assert', 'expect', 'main', 'init', 'setup',
        'var', 'let', 'const', 'import', 'from', 'require', 'export',
    }

    symbols_with_scores = {}  # symbol -> highest score seen

    for file_diff in pr_diff.files:
        if not file_diff.patch:
            continue

        # PRIMARY: Extract from hunk headers
        # Format: @@ -40,7 +40,9 @@ def process_payment(user_id, amount):
        #                              ^^^ this is the enclosing symbol
        hunk_pattern = r'@@[^@]+@@\s*(.+)'
        for match in re.finditer(hunk_pattern, file_diff.patch):
            context_line = match.group(1).strip()
            if not context_line:
                continue

            # Language-agnostic: look for identifier before '(' or after keywords
            # Examples:
            #   "def process_payment(..." -> process_payment
            #   "function calculateTotal() {" -> calculateTotal
            #   "public void handleRequest(..." -> handleRequest
            #   "func (s *Service) GetUser(..." -> GetUser

            # Try: keyword + identifier pattern
            keyword_pattern = r'\b(?:def|func|function|class|interface|struct|type)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
            keyword_match = re.search(keyword_pattern, context_line)
            if keyword_match:
                symbol = keyword_match.group(1)
                symbols_with_scores[symbol] = max(symbols_with_scores.get(symbol, 0), 10.0)
                continue

            # Try: identifier before '('
            before_paren_pattern = r'([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
            before_paren_match = re.search(before_paren_pattern, context_line)
            if before_paren_match:
                symbol = before_paren_match.group(1)
                symbols_with_scores[symbol] = max(symbols_with_scores.get(symbol, 0), 10.0)

        # SECONDARY: Extract from added lines (filtered)
        added_lines = [
            line[1:].strip()
            for line in file_diff.patch.split('\n')
            if line.startswith('+') and not line.startswith('+++')
        ]

        for line in added_lines:
            # Function-like patterns: word(
            func_pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
            for match in re.finditer(func_pattern, line):
                symbol = match.group(1)
                if len(symbol) >= 3 and symbol.lower() not in STOPLIST:
                    # Low priority: 1.0
                    symbols_with_scores[symbol] = max(symbols_with_scores.get(symbol, 0), 1.0)

            # Capitalized words (likely classes/constants)
            capital_pattern = r'\b([A-Z][a-zA-Z0-9_]{2,})\b'
            for match in re.finditer(capital_pattern, line):
                symbol = match.group(1)
                # Keep ALL_CAPS constants (MAX_RETRIES, PAYMENT_TIMEOUT) - relevant to logical bugs
                if len(symbol) >= 3 and symbol.lower() not in STOPLIST:
                    symbols_with_scores[symbol] = max(symbols_with_scores.get(symbol, 0), 1.0)

    # Sort by score descending - HIGH priority symbols first
    ranked = sorted(symbols_with_scores.items(), key=lambda x: x[1], reverse=True)
    return ranked


def _extract_changed_code_for_symbol(pr_diff, symbol: str) -> str:
    """
    Extract the changed code for a symbol from diff hunks.

    Finds hunks whose header context mentions the symbol and returns
    the full hunk content (changed lines). Used for Level 0.
    """
    import re

    changed_code_parts = []

    for file_diff in pr_diff.files:
        if not file_diff.patch:
            continue

        # Split into hunks (headers at odd indices after split)
        hunks = re.split(r'(@@[^@]+@@[^\n]*\n)', file_diff.patch)

        for i in range(1, len(hunks), 2):  # Headers are at odd indices
            if i + 1 >= len(hunks):
                break

            header = hunks[i]
            content = hunks[i + 1]

            # Word boundary match to avoid partial names (e.g., 'pay' matching 'process_payment')
            if re.search(r'\b' + re.escape(symbol) + r'\b', header):
                changed_code_parts.append(f"# {file_diff.filename}\n{header}{content}")

    return "\n".join(changed_code_parts) if changed_code_parts else ""


def _rank_and_dedupe_callers(refs, max_per_file: int = 2):
    """
    Rank caller references: production code > test code.
    Dedupe: keep max N refs per file.

    WHY: For Level 1 (callers), we want real usage examples to catch
    broken callers, not exhaustive test coverage that bloats context.
    """
    # Separate prod vs test
    prod_refs = []
    test_refs = []

    for ref in refs:
        path_lower = ref.file_path.lower()
        if 'test' in path_lower or 'spec' in path_lower:
            test_refs.append(ref)
        else:
            prod_refs.append(ref)

    # Dedupe per file
    def dedupe_by_file(refs_list, max_per_file):
        by_file = {}
        for ref in refs_list:
            if ref.file_path not in by_file:
                by_file[ref.file_path] = []
            if len(by_file[ref.file_path]) < max_per_file:
                by_file[ref.file_path].append(ref)

        result = []
        for refs in by_file.values():
            result.extend(refs)
        return result

    prod_deduped = dedupe_by_file(prod_refs, max_per_file)
    test_deduped = dedupe_by_file(test_refs, max_per_file)

    # Prod first, then test
    return prod_deduped + test_deduped


def _create_level2_summary(refs, symbol: str) -> str:
    """
    Create a one-line summary for Level 2 references.

    WHY: Level 2 is for awareness (this symbol is used elsewhere)
    without bloating context. Helps catch shared-state bugs (e.g.,
    cache updated in 5 places, one forgot locking) and N+1 patterns
    (used in loops across multiple files).
    """
    if not refs:
        return f"{symbol}: no additional references found"

    # Count by file and type
    files = set(ref.file_path for ref in refs)
    prod_files = [f for f in files if 'test' not in f.lower() and 'spec' not in f.lower()]
    test_files = [f for f in files if 'test' in f.lower() or 'spec' in f.lower()]

    # Check if clustered in few directories
    dirs = set('/'.join(f.split('/')[:-1]) for f in files if '/' in f)
    same_dir_hint = f"; clustered in {len(dirs)} dir(s)" if len(dirs) <= 3 else ""

    return (f"{symbol}: {len(refs)} usages across {len(files)} files "
            f"({len(prod_files)} prod, {len(test_files)} test){same_dir_hint}")


# ===== REVIEWER AGENT NODE (LLM) =====

def reviewer_agent_node(state: PRReviewState) -> Dict[str, Any]:
    """
    Node 5: LLM Reviewer Agent - finds issues beyond static analysis.

    Reads: ALL previous data (pr_diff, detected_languages, static_analysis_findings, context_references)
    Writes: reviewer_findings

    THE AGENT'S JOB:
    ================
    Static analysis catches syntax, style, and simple bugs.
    The Reviewer agent catches:
    - Logic errors
    - API misuse
    - Race conditions
    - Security vulnerabilities the linters miss
    - Performance issues
    - Architecture problems

    The agent sees LANGUAGE as an explicit input in the prompt, but all
    findings are in a NORMALIZED format (Finding objects), so the agent's
    reasoning is language-agnostic at the schema level.
    """
    pr_diff = state.get("pr_diff")
    primary_language = state.get("primary_language")
    detected_languages = state.get("detected_languages", [])
    static_findings = state.get("static_analysis_findings", [])
    context_refs = state.get("context_references", {})

    if not pr_diff:
        return {"errors": ["No PR diff for reviewer agent"]}
    
    if not primary_language:
        return {"errors": ["No primary language detected"]}

    logger.info(
        f"Reviewer agent analyzing {len(pr_diff.files)} files "
        f"(primary language: {primary_language})"
    )

    logger.info(f"Static analysis provided {len(static_findings)} findings")
    logger.info(f"Context search found {len(context_refs)} symbol references")

    from src.agents.reviewer import ReviewerAgent

    try:
        agent = ReviewerAgent()
        findings = agent.review(
            pr_diff=pr_diff,
            primary_language=primary_language,
            detected_languages=detected_languages,
            static_findings=static_findings,
            context_references=context_refs,
        )

        logger.info(f"Reviewer agent generated {len(findings)} findings")

        return {
            "reviewer_findings": findings,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "reviewer": {
                    "findings_count": len(findings),
                    "by_severity": {
                        "critical": len([f for f in findings if f.severity == "critical"]),
                        "major": len([f for f in findings if f.severity == "major"]),
                        "minor": len([f for f in findings if f.severity == "minor"]),
                    }
                }
            }
        }

    except Exception as e:
        logger.error(f"Reviewer agent failed: {e}")
        return {"errors": [f"reviewer_agent failed: {str(e)}"]}


def judge_agent_node(state: PRReviewState) -> Dict[str, Any]:
    """
    Node 6: Judge Agent - filters Reviewer findings based on evidence.

    Reads: reviewer_findings, static_analysis_findings, context_references, pr_diff
    Writes: filtered_findings

    THE HEURISTIC JUDGE FILTER:
    ===========================
    The Reviewer can hallucinate or be overconfident. The Judge provides
    independent scoring via heuristic rules:

    - Does static analysis support this finding?
    - Do context references support it?
    - Is the location valid?
    - Is the reasoning sound?

    The Judge uses deterministic rules (NOT an LLM) to score each finding
    based on corroborating evidence.

    CONFIGURABLE THRESHOLD:
    =======================
    The judge_threshold parameter (0.0 to 1.0) controls strictness:
    - 0.8: Very strict, only findings with strong evidence
    - 0.6: Moderate (default), balance precision/recall
    - 0.4: Lenient, accept most findings with weak evidence

    This lets you tune false-positive rate vs coverage.
    """
    reviewer_findings = state.get("reviewer_findings", [])
    static_findings = state.get("static_analysis_findings", [])
    context_refs = state.get("context_references", {})
    pr_diff = state.get("pr_diff")

    if not reviewer_findings:
        logger.info("No reviewer findings to judge")
        return {
            "filtered_findings": [],
            "node_outputs": {
                **state.get("node_outputs", {}),
                "judge": {"verdict": "No findings to judge"}
            }
        }

    # Get threshold from state (default 0.6)
    threshold = state.get("judge_threshold", 0.6)

    logger.info(
        f"Judge evaluating {len(reviewer_findings)} findings "
        f"(threshold: {threshold})"
    )

    try:
        from src.agents.judge import JudgeAgent

        judge = JudgeAgent(threshold=threshold)
        filtered, verdicts = judge.judge(
            reviewer_findings=reviewer_findings,
            static_findings=static_findings,
            context_references=context_refs,
            pr_diff=pr_diff,
        )

        stats = judge.get_summary_stats(verdicts)

        logger.info(
            f"Judge verdict: {len(filtered)}/{len(reviewer_findings)} findings passed "
            f"(pass rate: {stats['pass_rate']:.1%})"
        )

        return {
            "filtered_findings": filtered,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "judge": {
                    "total_evaluated": len(reviewer_findings),
                    "passed": len(filtered),
                    "failed": len(reviewer_findings) - len(filtered),
                    "threshold": threshold,
                    "stats": stats,
                    # Include sample verdicts for debugging
                    "sample_verdicts": [
                        {
                            "title": v.original_finding.title,
                            "score": v.judge_score,
                            "passed": v.passed,
                            "reasoning": v.reasoning,
                        }
                        for v in verdicts[:5]  # First 5 verdicts
                    ]
                }
            }
        }

    except Exception as e:
        logger.error(f"Judge agent failed: {e}")
        return {"errors": [f"judge_agent failed: {str(e)}"]}


def post_findings_node(state: PRReviewState) -> Dict[str, Any]:
    """
    Node 7: Post findings to GitHub PR.

    CONFIDENCE-BASED ROUTING:
    ==========================
    Findings are categorized into three buckets:

    1. HIGH CONFIDENCE (>= auto_post_threshold, default 0.8):
       → Auto-post immediately to GitHub

    2. MEDIUM CONFIDENCE (>= manual_approval_threshold, default 0.5):
       → INTERRUPT the graph and wait for manual approval
       → User reviews, approves/rejects, then resumes graph

    3. LOW CONFIDENCE (< manual_approval_threshold):
       → Discard, don't post

    INTERRUPT MECHANISM:
    ====================
    When medium-confidence findings exist, this node:
    1. Sets pending_approval = medium_confidence_findings
    2. Returns without posting (no side effects yet)
    3. LangGraph interrupts and returns control to user
    4. User calls graph.invoke() again with approved_findings set
    5. Node resumes and posts approved findings

    Reads: filtered_findings, auto_post_threshold, manual_approval_threshold
    Writes: posted_findings, pending_approval, approved_findings
    """
    from langgraph.types import interrupt

    filtered_findings = state.get("filtered_findings", [])
    auto_post_threshold = state.get("auto_post_threshold", 0.8)
    manual_approval_threshold = state.get("manual_approval_threshold", 0.5)
    github_token = state.get("github_token")
    pr_diff = state.get("pr_diff")

    if not filtered_findings:
        logger.info("No findings to post")
        return {
            "posted_findings": [],
            "node_outputs": {
                **state.get("node_outputs", {}),
                "post_findings": {"status": "no_findings"}
            }
        }

    # Categorize findings by confidence
    high_confidence = [
        f for f in filtered_findings if f.confidence >= auto_post_threshold
    ]
    medium_confidence = [
        f for f in filtered_findings
        if manual_approval_threshold <= f.confidence < auto_post_threshold
    ]
    low_confidence = [
        f for f in filtered_findings if f.confidence < manual_approval_threshold
    ]

    logger.info(
        f"Categorized {len(filtered_findings)} findings: "
        f"high={len(high_confidence)}, medium={len(medium_confidence)}, low={len(low_confidence)}"
    )

    # Check if this is a resume after approval
    approved = state.get("approved_findings", [])
    is_resume = len(approved) > 0

    if is_resume:
        # Resuming after approval - post approved findings
        logger.info(f"Resuming: posting {len(approved)} approved findings")
        to_post = high_confidence + approved

    elif medium_confidence:
        # First run with medium-confidence findings - INTERRUPT
        logger.info(
            f"Found {len(medium_confidence)} medium-confidence findings - "
            f"requesting manual approval"
        )

        # Use LangGraph's interrupt() to pause execution
        # The value passed to interrupt() is returned to the user
        approval_request = {
            "status": "awaiting_approval",
            "message": f"Found {len(medium_confidence)} findings that need manual review",
            "high_confidence_count": len(high_confidence),
            "medium_confidence_count": len(medium_confidence),
            "low_confidence_count": len(low_confidence),
            "pending_findings": [
                {
                    "title": f.title,
                    "file": f.file_path,
                    "line": f.line,
                    "severity": f.severity,
                    "confidence": f.confidence,
                    "description": f.description[:200] + "...",
                }
                for f in medium_confidence
            ],
            "instructions": (
                "Review the pending_findings above. To approve and post them:\n"
                "1. Set approved_findings in state\n"
                "2. Call graph.invoke(state, config) with the same thread_id\n"
                "Or to reject them, call graph.invoke() with approved_findings=[]"
            )
        }

        # INTERRUPT: This pauses the graph and returns control to user
        interrupt(approval_request)

        # Set pending_approval so user knows what to review
        return {
            "pending_approval": medium_confidence,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "post_findings": approval_request
            }
        }

    else:
        # No medium-confidence findings, just post high-confidence
        to_post = high_confidence

    # Post to GitHub
    if not to_post:
        logger.info("No findings to post after filtering")
        return {
            "posted_findings": [],
            "node_outputs": {
                **state.get("node_outputs", {}),
                "post_findings": {
                    "status": "completed",
                    "posted_count": 0,
                    "high_confidence": len(high_confidence),
                    "medium_confidence": len(medium_confidence),
                    "low_confidence": len(low_confidence),
                }
            }
        }

    if not github_token:
        logger.warning("No GitHub token provided, skipping posting")
        return {
            "errors": ["Cannot post to GitHub: no github_token provided"],
            "posted_findings": [],
        }

    try:
        logger.info(f"Posting {len(to_post)} findings to GitHub PR #{state['pr_number']}")

        # Gather stats for summary comment
        stats = state.get("node_outputs", {})

        # Post to GitHub
        posted = post_findings_to_github(
            findings=to_post,
            repository=state["repository"],
            pr_number=state["pr_number"],
            commit_sha=pr_diff.head_sha if pr_diff else "HEAD",
            github_token=github_token,
            stats=stats,
        )

        logger.info(
            f"Successfully posted {len(posted['inline'])} inline comments "
            f"and {len(posted['summary'])} summary comment"
        )

        return {
            "posted_findings": to_post,
            "node_outputs": {
                **state.get("node_outputs", {}),
                "post_findings": {
                    "status": "completed",
                    "posted_count": len(to_post),
                    "inline_comments": len(posted["inline"]),
                    "summary_comments": len(posted["summary"]),
                    "high_confidence": len(high_confidence),
                    "medium_confidence": len(approved) if is_resume else 0,
                    "low_confidence": len(low_confidence),
                }
            }
        }

    except Exception as e:
        logger.error(f"Failed to post findings to GitHub: {e}")
        return {"errors": [f"post_findings failed: {str(e)}"]}


# ===== GRAPH CONSTRUCTION =====

def create_review_graph(checkpointer=None):
    """
    Build the LangGraph StateGraph with checkpoint support.

    THE WIRING (UPDATED WITH POSTING + INTERRUPTS):
    ================================================
    1. Create StateGraph with our state schema
    2. Add nodes (functions that read/write state)
    3. Add edges (define the flow)
    4. Set entry point and compile with checkpointer

    FULL FLOW:
        START
          ↓
        fetch_diff
          ↓
        detect_language
          ↓
        run_static_analysis
          ↓
        search_context
          ↓
        reviewer_agent (generates findings)
          ↓
        judge_agent (filters findings)
          ↓
        post_findings (posts to GitHub) ← NEW NODE
          │
          ├─ High confidence (≥0.8): Auto-post immediately
          │
          ├─ Medium confidence (0.5-0.8): INTERRUPT for approval
          │  ↓
          │  User reviews → approves/rejects → resumes graph
          │
          └─ Low confidence (<0.5): Discard
          ↓
        END

    INTERRUPT/CHECKPOINT MECHANISM:
    ===============================
    When medium-confidence findings exist:
    1. post_findings_node calls interrupt()
    2. Graph pauses and returns state snapshot
    3. User reviews pending_approval findings
    4. User updates state.approved_findings
    5. User calls graph.invoke(state, config) with same thread_id
    6. Graph resumes from checkpoint and posts approved findings

    Args:
        checkpointer: Optional MemorySaver for checkpoint/resume support
                     Required for interrupt mechanism to work

    Result: A callable graph that takes initial state and returns final state.
    """

    # Create graph with our state schema
    graph = StateGraph(PRReviewState)

    # Add nodes
    graph.add_node("fetch_diff", fetch_diff_node)
    graph.add_node("detect_language", detect_language_node)
    graph.add_node("run_static_analysis", run_static_analysis_node)
    graph.add_node("search_context", search_context_node)
    graph.add_node("reviewer_agent", reviewer_agent_node)
    graph.add_node("judge_agent", judge_agent_node)
    graph.add_node("post_findings", post_findings_node)  # NEW: Posts to GitHub

    # Define edges (linear flow ending with posting)
    graph.set_entry_point("fetch_diff")
    graph.add_edge("fetch_diff", "detect_language")
    graph.add_edge("detect_language", "run_static_analysis")
    graph.add_edge("run_static_analysis", "search_context")
    graph.add_edge("search_context", "reviewer_agent")
    graph.add_edge("reviewer_agent", "judge_agent")
    graph.add_edge("judge_agent", "post_findings")  # NEW: Judge → Post
    graph.add_edge("post_findings", END)

    # Compile with checkpointer for interrupt support
    # Without checkpointer, interrupts will fail
    return graph.compile(checkpointer=checkpointer)


# ===== CONVENIENCE FUNCTION =====

def run_pr_review(
    repository: str,
    pr_number: int,
    github_token: Optional[str] = None
) -> dict:
    """
    Convenience function to run a complete PR review.

    Example:
        >>> result = run_pr_review("facebook/react", 12345, "ghp_token")
        >>> print(f"Found {len(result['reviewer_findings'])} issues")
    """
    initial_state = create_initial_state(repository, pr_number, github_token)
    graph = create_review_graph()
    final_state = graph.invoke(initial_state)
    return final_state


# ===== GRAPH VISUALIZATION (for debugging) =====

def visualize_graph():
    """
    Generate a visual representation of the graph.

    Useful for documentation and debugging.
    Requires: pip install pygraphviz
    """
    graph = create_review_graph()

    try:
        # LangGraph can export to Mermaid diagram format
        print(graph.get_graph().draw_mermaid())
    except Exception as e:
        logger.warning(f"Could not visualize graph: {e}")
        logger.info("Install pygraphviz for graph visualization")


if __name__ == "__main__":
    # Print the graph structure
    visualize_graph()
