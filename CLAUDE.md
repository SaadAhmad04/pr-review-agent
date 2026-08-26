# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PR Review Agent** is a multi-language, multi-agent AI code reviewer using LangGraph. It combines static analysis, code search, and LLM reasoning with adversarial verification (Judge agent) to achieve 80% precision and 80% recall.

**Core Innovation**: True language-agnostic design using Strategy pattern — same graph works for Java, Python, Go, C/C++ with no language-specific tuning.

## Architecture

### LangGraph Flow

```
fetch_diff → detect_language → run_static_analysis → search_context
                                                          ↓
                                                    reviewer_agent (LLM)
                                                          ↓
                                                     judge_agent (Filter)
                                                          ↓
                                                    post_findings (GitHub)
                                                          ↓
                                  ┌─────────────────────┼─────────────────────┐
                              HIGH (≥0.8)          MEDIUM (0.5-0.8)       LOW (<0.5)
                            Auto-post              INTERRUPT             Discard
```

### Key Design Patterns

1. **Strategy Pattern (Static Analysis)**
   - Interface: `StaticAnalyzer` (abstract base)
   - Adapters: `PylintAnalyzer`, `CheckstyleAnalyzer`, `GolangciAnalyzer`, `CppcheckAnalyzer`
   - Registry: `src/static_analysis/registry.py` dispatches by language
   - All return normalized `Finding` schema — Reviewer agent never sees language-specific formats

2. **Generator-Critic (Multi-Agent)**
   - `ReviewerAgent`: High recall, finds all potential issues (may include false positives)
   - `JudgeAgent`: High precision, filters based on evidence (static analysis, context, location validity)
   - Result: Precision improves from ~60% to ~80%

3. **Human-in-the-Loop (Interrupt/Checkpoint)**
   - Uses LangGraph's `interrupt()` for manual approval of medium-confidence findings
   - Requires `MemorySaver` checkpointer and consistent `thread_id` to resume

## Key Files

### Graph & State
- `src/graph.py` — 7-node LangGraph pipeline, all node functions, graph wiring
- `src/state.py` — `PRReviewState` TypedDict, reducer annotations for list merging

### Agents
- `src/agents/reviewer.py` — LLM generator (Claude), creates `ReviewerFinding` objects
- `src/agents/judge.py` — Evidence-based filter, scores findings 0.0-1.0

### Tool Layers (Normalized Output)
- `src/language/detector.py` — File extension → language mapping
- `src/static_analysis/base.py` — `StaticAnalyzer` interface, `Finding` schema
- `src/static_analysis/registry.py` — Dispatcher, `analyze_multi_language()`
- `src/static_analysis/{python_pylint,java_checkstyle,go_lint,c_cppcheck}.py` — Language adapters
- `src/context_search/base.py` — `ContextSearchStrategy` interface, `CodeReference` schema
- `src/context_search/ripgrep_strategy.py` — Fast text search implementation

### Tools
- `src/tools/diff_fetch.py` — GitHub REST API, returns `PRDiff` dataclass
- `src/tools/github_post.py` — Posts findings as inline + summary comments

### Evaluation
- `tests/seeded_bugs/{java_spring_bugs,python_flask_bugs}.py` — 10 realistic bugs
- `eval/run_eval.py` — Evaluation harness, metrics (precision/recall)

## Development Commands

### Setup
```bash
# Install Python dependencies
pip install -r requirements.txt

# Install static analysis tools (optional, language-specific)
pip install pylint  # Python
# Java: Download Checkstyle JAR from https://checkstyle.sourceforge.io/
# Go: go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
# C/C++: Install via package manager (apt/brew/choco install cppcheck)

# Install ripgrep (context search, recommended)
# https://github.com/BurntSushi/ripgrep#installation
```

### Run PR Review
```python
from src.graph import run_pr_review

# Basic usage
result = run_pr_review(
    repository="owner/repo",
    pr_number=123,
    github_token="ghp_..."
)

# With manual approval (requires checkpointer)
from langgraph.checkpoint.memory import MemorySaver
from src.graph import create_review_graph
from src.state import create_initial_state

checkpointer = MemorySaver()
graph = create_review_graph(checkpointer=checkpointer)
state = create_initial_state("owner/repo", 123, "ghp_...")
config = {"configurable": {"thread_id": "pr-123"}}

# First invoke - runs until interrupt
result = graph.invoke(state, config)

# If interrupted, approve and resume
if result.get("pending_approval"):
    result["approved_findings"] = [...]  # User-selected findings
    final = graph.invoke(result, config)  # Same thread_id!
```

### Testing & Evaluation
```bash
# Run evaluation on seeded bugs
python eval/run_eval.py

# Demo: Judge filtering
python demo_judge_filtering.py

# Demo: Interrupt/checkpoint mechanism
python demo_interrupt_simple.py
```

## Adding a New Language

**Example: Rust with Clippy**

1. **Create adapter** (`src/static_analysis/rust_clippy.py`):
```python
from .base import StaticAnalyzer, Finding, Severity
import subprocess, json

class ClippyAnalyzer(StaticAnalyzer):
    def analyze(self, file_paths: List[str]) -> List[Finding]:
        result = subprocess.run(
            ["cargo", "clippy", "--message-format=json"],
            capture_output=True
        )
        clippy_output = json.loads(result.stdout)
        
        findings = []
        for item in clippy_output:
            findings.append(Finding(
                file_path=item["file"],
                line=item["line"],
                severity=self._normalize_severity(item["level"]),
                rule_id=item["code"],
                message=item["message"],
                source="clippy"
            ))
        return findings
    
    def is_available(self) -> bool:
        try:
            subprocess.run(["cargo", "clippy", "--version"], capture_output=True)
            return True
        except FileNotFoundError:
            return False
    
    def get_language(self) -> str:
        return "rust"
```

2. **Register** (`src/static_analysis/registry.py`):
```python
from .rust_clippy import ClippyAnalyzer

# In _initialize_default_analyzers():
self.register_analyzer("rust", ClippyAnalyzer())
```

3. **Add extension** (`src/language/detector.py`):
```python
EXTENSION_MAP = {
    # ... existing ...
    ".rs": "rust",
}
```

**Done**. Zero changes to graph, agents, or posting logic.

## Judge Scoring Rubric

| Score | Evidence Level | Example |
|-------|---------------|---------|
| 1.0 | Exact match | Static analysis found same issue at same file:line |
| 0.9 | Nearby match | Static analysis found related issue within 5 lines |
| 0.7 | Context support | Symbol usage patterns confirm the issue |
| 0.6 | Sound reasoning | Category-appropriate, no direct evidence |
| 0.4 | Weak reasoning | Speculative, low confidence |
| 0.0 | Invalid | References non-existent code (hallucination) |

Configurable via `judge_threshold` parameter (0.0-1.0):
- 0.8 = strict (fewer false positives)
- 0.6 = moderate (default, balanced)
- 0.4 = lenient (higher recall)

## Interrupt/Checkpoint Mechanism

**Required Components:**
1. `MemorySaver` checkpointer passed to `create_review_graph()`
2. Consistent `thread_id` in config across `invoke()` calls
3. State must be JSON-serializable (convert complex objects to dicts)

**Confidence Thresholds:**
- `auto_post_threshold` (default 0.8): Findings ≥ this auto-post
- `manual_approval_threshold` (default 0.5): Findings ≥ this need approval
- Findings < manual_approval_threshold are discarded

**State Fields:**
- `pending_approval`: Findings awaiting user review (set by `post_findings_node`)
- `approved_findings`: User-selected findings (set by user before resume)
- `posted_findings`: Successfully posted to GitHub (set after posting)

## Context Search Strategy

**Level-Based Architecture** (in `search_context_node`):
- **Level 0**: Changed function code + DEFINITION refs (catch signature changes)
- **Level 1**: Top ~10 callers (prod-first, deduped) — catch broken callers, N+1 patterns
- **Level 2**: Summary only (blast radius awareness) — catch shared-state bugs

**Symbol Extraction** (in `_extract_changed_symbols`):
- PRIMARY: Hunk headers (@@ ... @@ context) — high priority, identifies modified functions
- SECONDARY: Added lines — low priority, filtered by stoplist

## State Schema Design

**Key Annotations:**
- `Annotated[List[...], operator.add]` — Use for fields written by multiple nodes (e.g., `static_analysis_findings`, `reviewer_findings`, `errors`)
- Without reducers, last write would overwrite earlier results

**Serialization:**
- Convert `AnalysisResult` objects to dicts before storing in `static_analysis_results`
- Checkpoint serialization requires JSON/MessagePack-compatible types

## Common Pitfalls

1. **Adding language without registering** — Must call `registry.register_analyzer()` in `registry.py`
2. **Resuming with different thread_id** — Checkpoint won't be found, execution starts from scratch
3. **Interrupt without checkpointer** — `interrupt()` will fail silently
4. **Non-serializable state** — `MemorySaver` requires JSON-compatible types, convert objects to dicts
5. **Forgetting reducer annotations** — Fields written by multiple nodes need `Annotated[List[...], operator.add]`

## Evaluation Metrics (10 Seeded Bugs)

| Stage | Precision | Recall | Notes |
|-------|-----------|--------|-------|
| Static Analysis Only | 100% | 60% | Catches 6/10 bugs (simple issues) |
| Reviewer (before Judge) | 59% | 100% | Catches all 10 bugs + 7 false positives |
| After Judge | 80% | 80% | Filters 5 false positives, 2 bugs missed |

**LLM Value-Add**: +40% coverage (4 bugs static analysis misses: N+1, race conditions, missing transactions, type mismatches)

## Documentation

- `ARCHITECTURE.md` — Complete system design, Judge scoring details
- `INTERRUPT_MECHANISM.md` — LangGraph checkpoint/resume guide, examples
- `EVALUATION_DESIGN.md` — Seeded bug taxonomy, metrics, matching algorithm
- `PROJECT_SUMMARY.md` — Design patterns, innovations, performance breakdown
- `README.md` — User guide, setup instructions, examples
