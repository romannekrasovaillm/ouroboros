"""LLM client for Ouroboros.

Supports OpenRouter and DeepSeek APIs.
"""

import os
import json
import time
import logging
from typing import Optional, Dict, Any, List, Union
import httpx

logger = logging.getLogger(__name__)

# Default timeout for API calls
DEFAULT_TIMEOUT = 180.0

# Default model for lightweight tasks (dedup, compaction, etc.)
DEFAULT_LIGHT_MODEL = "google/gemini-3-pro-preview"


def add_usage(
    usage: Dict[str, Any],
    model: str,
    task_id: Optional[str] = None,
    round_num: Optional[int] = None,
) -> None:
    """Record LLM usage for budget tracking.
    
    This function is called by the loop to record token usage.
    It writes to the events.jsonl log.
    """
    # Import here to avoid circular imports
    from ouroboros.utils import get_drive_path
    
    event = {
        "type": "llm_usage",
        "timestamp": time.time(),
        "model": model,
        "usage": usage,
    }
    if task_id:
        event["task_id"] = task_id
    if round_num is not None:
        event["round_num"] = round_num
    
    # Write to events log
    events_path = get_drive_path("logs/events.jsonl")
    os.makedirs(os.path.dirname(events_path), exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    
    logger.debug(f"Recorded usage for {model}: {usage}")


def normalize_reasoning_effort(effort: str) -> str:
    """Normalize reasoning effort string."""
    effort = effort.lower().strip()
    if effort in ("low", "medium", "high", "xhigh"):
        return effort
    # Default to medium
    return "medium"


def reasoning_rank(effort: str) -> int:
    """Convert reasoning effort to rank (higher = more reasoning)."""
    mapping = {"low": 1, "medium": 2, "high": 3, "xhigh": 4}
    return mapping.get(normalize_reasoning_effort(effort), 2)


class LLMClient:
    """Client for LLM APIs (OpenRouter, DeepSeek)."""

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        **kwargs,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")
        
        # Determine base URL based on model
        if base_url:
            self.base_url = base_url.rstrip("/")
        elif model.startswith("deepseek-"):
            # DeepSeek models use DeepSeek API
            self.base_url = "https://api.deepseek.com/v1"
        else:
            # Default to OpenRouter
            self.base_url = "https://openrouter.ai/api/v1"
        
        self.timeout = timeout
        self.max_retries = max_retries
        self.client = httpx.Client(timeout=timeout)
        self._extra_kwargs = kwargs
        
        logger.info(f"LLMClient initialized: model={model}, base_url={self.base_url}")

    def _call(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make API call with retries."""
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # Add OpenRouter-specific headers for non-DeepSeek models
        if not self.model.startswith("deepseek-"):
            headers.update({
                "HTTP-Referer": "https://ouroboros.ai",
                "X-Title": "Ouroboros",
            })
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        if stream:
            payload["stream"] = True
        
        # Merge extra kwargs
        payload.update(self._extra_kwargs)
        
        last_error = None
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"LLM call attempt {attempt+1}/{self.max_retries}: {self.model}")
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"HTTP error {e.response.status_code}: {e.response.text}")
                if e.response.status_code in (429, 503):
                    # Rate limit or service unavailable
                    wait_time = 2 ** attempt
                    logger.info(f"Rate limited, waiting {wait_time}s")
                    time.sleep(wait_time)
                elif e.response.status_code >= 500:
                    # Server error
                    wait_time = 1 * (attempt + 1)
                    logger.info(f"Server error, waiting {wait_time}s")
                    time.sleep(wait_time)
                else:
                    # Client error (4xx) - don't retry
                    raise
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e
                logger.warning(f"Request error: {e}")
                wait_time = 1 * (attempt + 1)
                logger.info(f"Waiting {wait_time}s before retry")
                time.sleep(wait_time)
        
        raise RuntimeError(f"Failed after {self.max_retries} attempts: {last_error}")

    def call(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Public method for LLM calls."""
        return self._call(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )

    def stream(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ):
        """Streaming version (not implemented yet)."""
        raise NotImplementedError("Streaming not yet implemented")

    def close(self):
        """Close HTTP client."""
        self.client.close()


def get_llm_client(
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs,
) -> LLMClient:
    """Factory function for LLMClient."""
    if model is None:
        model = os.environ.get("OUROBOROS_MODEL", "anthropic/claude-3-haiku")
    
    return LLMClient(model=model, api_key=api_key, base_url=base_url, **kwargs)


def fetch_openrouter_pricing() -> Dict[str, float]:
    """Fetch current pricing from OpenRouter API.
    
    Returns:
        Dict mapping model names to price per 1M input tokens.
    """
    # This is a simplified version - in production you'd cache this
    # and handle errors gracefully
    try:
        client = httpx.Client(timeout=10.0)
        response = client.get("https://openrouter.ai/api/v1/models")
        response.raise_for_status()
        data = response.json()
        
        pricing = {}
        for model in data.get("data", []):
            name = model.get("id")
            pricing_info = model.get("pricing", {})
            if name and pricing_info:
                # Convert to price per 1M tokens
                price_per_million = pricing_info.get("prompt", 0) * 1000000
                pricing[name] = price_per_million
        
        return pricing
    except Exception as e:
        logger.warning(f"Failed to fetch OpenRouter pricing: {e}")
        # Return a fallback pricing table
        return {
            "anthropic/claude-3-haiku": 0.25,
            "anthropic/claude-3-sonnet": 3.0,
            "anthropic/claude-3-opus": 15.0,
            "openai/gpt-4o": 5.0,
            "openai/gpt-4o-mini": 0.15,
            "google/gemini-2.0-flash": 0.1,
            "deepseek-chat": 0.14,  # Estimated
            "deepseek-reasoner": 0.28,  # Estimated
        }