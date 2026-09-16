# Multi-Provider LLM Support - Implementation Summary

## Changes Overview

Added support for **Anthropic, OpenAI, and Ollama** LLM providers with minimal, surgical changes to the codebase.

## Files Changed

### 1. **NEW FILE: `src/llm_factory.py`** (77 lines)

Factory function that constructs provider-specific LLM clients:

```python
def get_llm(provider: str, model: str, temperature=0.0, max_tokens=4096, timeout=60.0) -> Any
```

**Features:**
- Lazy imports (only loads the package for the requested provider)
- Provider-specific parameter mapping:
  - **Anthropic**: `model_name`, `max_tokens_to_sample`, `timeout`, `stop`
  - **OpenAI**: `model`, `max_tokens`, `timeout`
  - **Ollama**: `model`, `temperature` (local, no timeout/API key)
- Clear error messages for invalid providers

**Supported providers:**
- `anthropic` → `ChatAnthropic` from `langchain-anthropic`
- `openai` → `ChatOpenAI` from `langchain-openai`
- `ollama` → `ChatOllama` from `langchain-ollama`

---

### 2. **MODIFIED: `src/agents/reviewer.py`**

#### Changes to `__init__`:
```python
def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
    self.provider = provider or os.getenv("LLM_PROVIDER", "anthropic").lower()
    
    if model:
        self.model = model
    elif os.getenv("LLM_MODEL"):
        self.model = os.getenv("LLM_MODEL")
    else:
        # Provider-specific defaults
        default_models = {
            "anthropic": "claude-sonnet-4-5-20250929",
            "openai": "gpt-4o",
            "ollama": "llama3",
        }
        self.model = default_models.get(self.provider, "claude-sonnet-4-5-20250929")
```

#### Changes to `review()`:

**API key check** (lines 119-136):
```python
# Provider-aware API key check
if self.provider == "anthropic":
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping LLM review...")
        return []
elif self.provider == "openai":
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — skipping LLM review...")
        return []
# Ollama is local, no API key required
```

**LLM construction** (lines 148-160):
```python
llm = get_llm(
    provider=self.provider,
    model=self.model,
    temperature=0.0,
    max_tokens=4096,
    timeout=60.0,
)
```

#### Type hint updates:
- `_count_tokens(text, llm: Any)` — was `llm: ChatAnthropic`
- `_truncate_to_budget(prompt, llm: Any)` — was `llm: ChatAnthropic`
- `_call_llm_with_retry(llm: Any, ...)` — was `llm: ChatAnthropic`

**Rationale:** The LLM client is now provider-agnostic; all use `.invoke()` from LangChain.

#### Docstring updates:
- Updated token counting docstring to explain provider-agnostic character approximation
- Removed Anthropic-specific references

---

### 3. **MODIFIED: `src/agents/judge.py`**

**Single change** (line 76):
```python
def __init__(self, threshold: float = 0.6, use_llm: bool = False, model: Optional[str] = None):
```

**Why:** Judge does NOT use an LLM (use_llm=False by default). The `model` parameter was a misleading default (`"claude-..."`). Changed to `Optional[str] = None` with updated docstring: "currently unused, reserved for future."

---

### 4. **MODIFIED: `requirements.txt`**

```diff
 # Core dependencies
 requests==2.34.2
 langgraph==1.2.11
 langchain==1.4.0
-langchain-anthropic==1.7.1
-anthropic==1.4.0
 python-dotenv==1.2.3
+
+# LLM provider integrations (install only what you need)
+langchain-anthropic==1.7.1
+anthropic==1.4.0
+langchain-openai==1.3.1
+langchain-ollama==1.3.0
```

**Note:** Users can install only the provider(s) they need. The factory's lazy imports prevent crashes when a provider's package is missing.

---

## Configuration

### Environment Variables

| Variable | Values | Default |
|----------|--------|---------|
| `LLM_PROVIDER` | `anthropic`, `openai`, `ollama` | `anthropic` |
| `LLM_MODEL` | Provider-specific model name | Provider-specific default |
| `ANTHROPIC_API_KEY` | Anthropic API key | (required for Anthropic) |
| `OPENAI_API_KEY` | OpenAI API key | (required for OpenAI) |

**Example `.env`:**
```bash
# Anthropic (default)
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-5-20250929
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...

# Ollama (local)
LLM_PROVIDER=ollama
LLM_MODEL=llama3
```

### Default Models (if `LLM_MODEL` not set)

| Provider | Default Model |
|----------|---------------|
| Anthropic | `claude-sonnet-4-5-20250929` |
| OpenAI | `gpt-4o` |
| Ollama | `llama3` |

---

## Verification Tests

All tests passed:

```bash
# Import check
✓ python -c "from src.graph import run_pr_review"

# Anthropic LLM construction
✓ python -c "from src.llm_factory import get_llm; llm = get_llm('anthropic', 'claude-sonnet-4-5-20250929')"
  → ChatAnthropic

# Invalid provider error handling
✓ python -c "from src.llm_factory import get_llm; get_llm('invalid', 'model')"
  → ValueError: "Unknown LLM provider: 'invalid'. Valid providers: anthropic, openai, ollama"

# ReviewerAgent defaults
✓ ReviewerAgent() → provider='anthropic', model='claude-sonnet-4-5-20250929'

# Multi-provider configuration
✓ LLM_PROVIDER=anthropic → anthropic/claude-sonnet-4-5-20250929
✓ LLM_PROVIDER=openai LLM_MODEL=gpt-4o → openai/gpt-4o
✓ ReviewerAgent(provider='ollama', model='llama3') → ollama/llama3
```

---

## What Was NOT Changed

- **Token counting** (`_count_tokens`, `_truncate_to_budget`) — already provider-agnostic (character approximation)
- **Judge logic** — does NOT use an LLM (only fixed misleading default parameter)
- **LLM invocation** (`.invoke()`, retry, parsing) — LangChain interface is uniform across providers
- **Graph orchestration** — no changes
- **Static analysis** — no changes
- **Context search** — no changes
- **GitHub posting** — no changes

---

## Design Principles

1. **Surgical change**: Only 4 files modified (1 new, 3 updated)
2. **Lazy imports**: Provider packages loaded only when needed
3. **Backward compatible**: Existing Anthropic-only setups work unchanged
4. **Provider-agnostic**: LangChain's `.invoke()` interface is uniform
5. **Graceful degradation**: Missing API keys log warnings, return empty findings (no crash)
6. **Clear errors**: Invalid provider → clear error message with valid options

---

## Usage Examples

### Anthropic (existing behavior, unchanged)
```python
from src.agents.reviewer import ReviewerAgent

# Uses ANTHROPIC_API_KEY from .env
agent = ReviewerAgent()
findings = agent.review(pr_diff, "python", langs, static_findings, context_refs)
```

### OpenAI
```python
import os
os.environ["LLM_PROVIDER"] = "openai"
os.environ["OPENAI_API_KEY"] = "sk-..."

agent = ReviewerAgent()  # Uses gpt-4o by default
findings = agent.review(...)
```

### Ollama (local)
```python
agent = ReviewerAgent(provider="ollama", model="llama3")
findings = agent.review(...)  # No API key needed
```

---

## Next Steps

To test with OpenAI/Ollama:

1. Install provider package:
   ```bash
   pip install langchain-openai  # for OpenAI
   pip install langchain-ollama  # for Ollama
   ```

2. Set environment variables in `.env`:
   ```bash
   LLM_PROVIDER=openai
   OPENAI_API_KEY=sk-...
   ```

3. Run the agent against a real PR (I will verify in production)
