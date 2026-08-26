"""
Judge Agent - Independently scores and filters Reviewer findings.

THE JUDGE'S ROLE:
=================
The Reviewer agent can hallucinate or be overconfident. The Judge provides
a SECOND, INDEPENDENT opinion on each finding.

The Judge scores findings based on GROUNDING:
- Is the issue supported by static analysis findings?
- Is it supported by context references (how the code is used)?
- Is the reasoning sound based on the diff?
- Or is it just LLM speculation?

ADVERSARIAL DESIGN:
===================
The Judge is SKEPTICAL by default. It tries to REFUTE each finding.
Only findings that survive adversarial scrutiny get through.

This is a common pattern in multi-agent systems:
- Generator (Reviewer): Proposes candidates
- Critic (Judge): Filters false positives
- Result: Higher precision, lower false positive rate

SCORING RUBRIC:
===============
Score 0.0-1.0 based on evidence:

1.0: Directly supported by static analysis finding at same file:line
0.9: Supported by nearby static analysis finding (within 5 lines)
0.8: Referenced symbol has suspicious usage patterns in context
0.7: Pattern is documented in the language's best practices
0.6: Reasoning is sound but no direct evidence
0.5: Plausible but uncertain
0.4: Weak reasoning, speculative
0.3: Not supported by evidence
0.2: Contradicted by context
0.1: Almost certainly wrong
0.0: Definitely wrong (e.g., references nonexistent code)
"""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass

from src.state import ReviewerFinding
from src.static_analysis.base import Finding
from src.context_search.base import CodeReference

logger = logging.getLogger(__name__)


@dataclass
class JudgeVerdict:
    """
    The Judge's assessment of a Reviewer finding.
    """
    original_finding: ReviewerFinding
    judge_score: float  # 0.0 to 1.0
    reasoning: str  # Why this score?
    evidence: List[str]  # What evidence supports/refutes it?
    passed: bool  # Did it pass the threshold?


class JudgeAgent:
    """
    Adversarial judge that scores Reviewer findings based on evidence.
    """

    def __init__(
        self,
        threshold: float = 0.6,
        use_llm: bool = False,  # For v1, use heuristics; v2 can use LLM
        model: str = "claude-3-5-sonnet-20241022"
    ):
        """
        Args:
            threshold: Minimum score to pass (0.0 to 1.0)
            use_llm: Whether to use LLM for scoring (not implemented in v1)
            model: LLM model to use if use_llm=True
        """
        self.threshold = threshold
        self.use_llm = use_llm
        self.model = model

    def judge(
        self,
        reviewer_findings: List[ReviewerFinding],
        static_findings: List[Finding],
        context_references: Dict[str, List[CodeReference]],
        pr_diff,  # For checking if referenced code exists
    ) -> tuple[List[ReviewerFinding], List[JudgeVerdict]]:
        """
        Score each Reviewer finding and filter by threshold.

        Args:
            reviewer_findings: Findings from the Reviewer agent
            static_findings: Static analysis findings (ground truth)
            context_references: Context search results
            pr_diff: The PR diff (to verify referenced code exists)

        Returns:
            Tuple of:
            - Filtered findings (passed threshold)
            - All verdicts (for debugging/analysis)
        """
        logger.info(f"Judge evaluating {len(reviewer_findings)} findings")

        verdicts = []

        for finding in reviewer_findings:
            if self.use_llm:
                verdict = self._judge_with_llm(
                    finding, static_findings, context_references, pr_diff
                )
            else:
                verdict = self._judge_with_heuristics(
                    finding, static_findings, context_references, pr_diff
                )

            verdicts.append(verdict)

        # Filter to passing findings
        passed = [v.original_finding for v in verdicts if v.passed]
        failed = [v for v in verdicts if not v.passed]

        logger.info(
            f"Judge verdict: {len(passed)}/{len(reviewer_findings)} findings passed "
            f"(threshold: {self.threshold})"
        )

        # Log some examples of failed findings
        for verdict in failed[:3]:
            logger.debug(
                f"Filtered: {verdict.original_finding.title} "
                f"(score: {verdict.judge_score:.2f})"
            )

        return passed, verdicts

    def _judge_with_heuristics(
        self,
        finding: ReviewerFinding,
        static_findings: List[Finding],
        context_references: Dict[str, List[CodeReference]],
        pr_diff,
    ) -> JudgeVerdict:
        """
        Score a finding using deterministic heuristics (no LLM).

        HEURISTIC RULES:
        ================
        1. Check if static analysis found the same issue (highest confidence)
        2. Check if static analysis found nearby issues (high confidence)
        3. Check if context shows the pattern (medium confidence)
        4. Check if the file/line exists in the diff (sanity check)
        5. Default to medium confidence if reasoning seems sound
        """
        evidence = []
        score = 0.5  # Default: uncertain

        # Rule 1: Direct match with static analysis
        exact_match = self._find_static_match(finding, static_findings, max_line_diff=0)
        if exact_match:
            score = 1.0
            evidence.append(
                f"Static analysis ({exact_match.source}) found same issue at same location: "
                f"{exact_match.rule_id}"
            )
            reasoning = "Directly confirmed by static analysis tool"
        else:
            # Rule 2: Nearby match with static analysis
            nearby_match = self._find_static_match(finding, static_findings, max_line_diff=5)
            if nearby_match:
                score = 0.9
                evidence.append(
                    f"Static analysis ({nearby_match.source}) found related issue nearby: "
                    f"{nearby_match.rule_id} at line {nearby_match.line}"
                )
                reasoning = "Supported by nearby static analysis finding"
            else:
                # Rule 3: Check context references
                context_support = self._check_context_support(finding, context_references)
                if context_support:
                    score = 0.7
                    evidence.extend(context_support)
                    reasoning = "Pattern supported by code context references"
                else:
                    # Rule 4: Sanity check - does the file/line exist?
                    if not self._verify_location_exists(finding, pr_diff):
                        score = 0.0
                        evidence.append("Referenced file or line does not exist in diff")
                        reasoning = "Invalid location reference"
                    else:
                        # Rule 5: Category-based scoring
                        score = self._score_by_category(finding)
                        evidence.append(f"No direct evidence, scored by category and confidence")
                        reasoning = f"Based on category ({finding.category}) and agent confidence"

        # Adjust score based on Reviewer's own confidence
        # If Reviewer was uncertain, Judge should be too
        adjusted_score = score * finding.confidence

        passed = adjusted_score >= self.threshold

        return JudgeVerdict(
            original_finding=finding,
            judge_score=adjusted_score,
            reasoning=reasoning,
            evidence=evidence,
            passed=passed,
        )

    def _find_static_match(
        self,
        finding: ReviewerFinding,
        static_findings: List[Finding],
        max_line_diff: int = 0
    ) -> Optional[Finding]:
        """
        Find a static analysis finding that matches the Reviewer finding.

        Args:
            finding: Reviewer finding
            static_findings: All static analysis findings
            max_line_diff: Maximum line number difference (0 = exact, 5 = nearby)

        Returns:
            Matching Finding or None
        """
        for static in static_findings:
            # Same file?
            if static.file_path != finding.file_path:
                continue

            # Same or nearby line?
            line_diff = abs(static.line - finding.line)
            if line_diff > max_line_diff:
                continue

            # Similar category/severity?
            # Map Reviewer categories to static analysis patterns
            category_keywords = {
                "security": ["security", "injection", "xss", "auth"],
                "performance": ["performance", "complexity", "loop"],
                "bug": ["error", "exception", "null", "undefined"],
                "style": ["style", "format", "convention"],
            }

            if finding.category in category_keywords:
                keywords = category_keywords[finding.category]
                rule_lower = (static.rule_id + " " + static.message).lower()

                for keyword in keywords:
                    if keyword in rule_lower:
                        return static

        return None

    def _check_context_support(
        self,
        finding: ReviewerFinding,
        context_references: Dict[str, List[CodeReference]]
    ) -> List[str]:
        """
        Check if context references support the finding.

        Returns list of evidence strings if supported, empty list otherwise.
        """
        evidence = []

        # Extract potential symbols from the finding description
        # Simple heuristic: look for capitalized words or words in backticks
        import re
        symbols = re.findall(r'`([^`]+)`', finding.description)
        symbols.extend(re.findall(r'\b([A-Z][a-zA-Z0-9_]*)\b', finding.description))

        for symbol in symbols:
            if symbol in context_references:
                refs = context_references[symbol]
                # Check if the symbol is used in ways that support the finding
                evidence.append(
                    f"Symbol '{symbol}' referenced in {len(refs)} locations"
                )

        return evidence

    def _verify_location_exists(
        self,
        finding: ReviewerFinding,
        pr_diff
    ) -> bool:
        """
        Verify that the finding's file and line exist in the PR diff.

        Catches hallucinated locations.
        """
        if not pr_diff or not pr_diff.files:
            return True  # Can't verify, give benefit of doubt

        # Check if file exists in diff
        for file_diff in pr_diff.files:
            if file_diff.filename == finding.file_path:
                # File exists, now check line number is reasonable
                if file_diff.patch:
                    # Extract line numbers from patch
                    import re
                    # Diff format: @@ -old_start,old_count +new_start,new_count @@
                    ranges = re.findall(r'@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@', file_diff.patch)

                    for start, count in ranges:
                        start = int(start)
                        count = int(count) if count else 1
                        if start <= finding.line <= start + count:
                            return True

                    # Line not in diff range, but file exists - could be context
                    # Give benefit of doubt if line is reasonable
                    return finding.line > 0 and finding.line < 10000

                return True  # File exists, can't verify line

        # File not found in diff
        return False

    def _score_by_category(self, finding: ReviewerFinding) -> float:
        """
        Score based on category when no other evidence exists.

        Some categories are easier to hallucinate than others.
        """
        # Security and bugs are more objective - either it's vulnerable or not
        # Style and minor logic issues are more subjective
        category_base_scores = {
            "security": 0.7,   # High stakes, usually clear cut
            "bug": 0.6,        # Objective but can be subtle
            "performance": 0.5,  # Often depends on scale/context
            "logic": 0.5,      # Can be subjective
            "style": 0.4,      # Very subjective
        }

        base = category_base_scores.get(finding.category, 0.5)

        # Adjust by severity
        severity_multipliers = {
            "critical": 1.0,
            "major": 0.9,
            "minor": 0.8,
        }

        multiplier = severity_multipliers.get(finding.severity, 0.9)

        return base * multiplier

    def _judge_with_llm(
        self,
        finding: ReviewerFinding,
        static_findings: List[Finding],
        context_references: Dict[str, List[CodeReference]],
        pr_diff,
    ) -> JudgeVerdict:
        """
        Use an LLM to judge the finding (future implementation).

        The LLM prompt would be adversarial:
        "Try to REFUTE this finding. Look for evidence that contradicts it.
         Only if you cannot refute it should you accept it."
        """
        # TODO: Implement LLM-based judging
        # For now, fall back to heuristics
        logger.warning("LLM-based judging not yet implemented, using heuristics")
        return self._judge_with_heuristics(
            finding, static_findings, context_references, pr_diff
        )

    def get_summary_stats(self, verdicts: List[JudgeVerdict]) -> dict:
        """
        Generate summary statistics about the Judge's verdicts.
        """
        passed = [v for v in verdicts if v.passed]
        failed = [v for v in verdicts if not v.passed]

        return {
            "total_findings": len(verdicts),
            "passed": len(passed),
            "failed": len(failed),
            "pass_rate": len(passed) / len(verdicts) if verdicts else 0,
            "avg_score": sum(v.judge_score for v in verdicts) / len(verdicts) if verdicts else 0,
            "avg_passed_score": sum(v.judge_score for v in passed) / len(passed) if passed else 0,
            "avg_failed_score": sum(v.judge_score for v in failed) / len(failed) if failed else 0,
            "by_category": self._group_by_category(verdicts),
        }

    def _group_by_category(self, verdicts: List[JudgeVerdict]) -> dict:
        """Group verdicts by category."""
        by_category = {}

        for verdict in verdicts:
            category = verdict.original_finding.category
            if category not in by_category:
                by_category[category] = {"total": 0, "passed": 0, "failed": 0}

            by_category[category]["total"] += 1
            if verdict.passed:
                by_category[category]["passed"] += 1
            else:
                by_category[category]["failed"] += 1

        return by_category
