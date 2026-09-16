"""
LLM Factory - Multi-provider LLM client construction.

Supports Anthropic, OpenAI, and Ollama through their respective LangChain adapters.
Each provider is lazily imported to avoid requiring all provider packages.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_llm(
    provider: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: float = 60.0
) -> Any:
    """
    Construct an LLM client for the specified provider.

    Args:
        provider: One of "anthropic", "openai", "ollama"
        model: Model name (provider-specific)
        temperature: Sampling temperature (0.0 = deterministic)
        max_tokens: Maximum tokens in response
        timeout: Request timeout in seconds (ignored for Ollama)

    Returns:
        A LangChain chat model instance (.invoke() compatible)

    Raises:
        ValueError: If provider is not recognized
        ImportError: If the provider's package is not installed
    """
    provider = provider.lower()

    if provider == "anthropic":
        # Lazy import to avoid requiring anthropic when using other providers
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name=model,
            temperature=temperature,
            max_tokens_to_sample=max_tokens,  # Anthropic-specific param name
            timeout=timeout,
            stop=None,
        )

    elif provider == "openai":
        # Lazy import to avoid requiring openai when using other providers
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    elif provider == "ollama":
        # Lazy import to avoid requiring ollama when using other providers
        from langchain_ollama import ChatOllama

        # Ollama is local, no API key or timeout needed
        return ChatOllama(
            model=model,
            temperature=temperature,
        )

    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Valid providers: anthropic, openai, ollama"
        )
