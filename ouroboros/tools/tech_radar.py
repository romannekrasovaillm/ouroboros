"""Tech Radar tool — track LLM models, pricing, capabilities, and ecosystem changes.

Aligned with Principle 0 (Agency): staying aware of the ecosystem without being told.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

# Cache file location
CACHE_FILE = "memory/tech_radar_cache.json"
# How often to refresh cache (seconds)
CACHE_TTL = 86400  # 24 hours

# Model capability categories
CAPABILITY_CATEGORIES = {
    "reasoning": ["claude-3-opus", "claude-3-sonnet", "gpt-4", "o3", "gemini-2.5-pro"],
    "code_editing": ["claude-3-sonnet", "claude-3-haiku", "gpt-4", "deepseek-coder"],
    "lightweight": ["claude-3-haiku", "gpt-4o-mini", "gemini-2.0-flash", "qwen/qwen3.5"],
    "vision": ["claude-3-opus", "gpt-4o", "gemini-2.0-pro-vision"],
    "multimodal": ["claude-3-opus", "gpt-4o", "gemini-2.0-pro"],
}

# Task type recommendations
TASK_RECOMMENDATIONS = {
    "code_editing": {
        "primary": "anthropic/claude-3-sonnet",
        "fallback": "anthropic/claude-3-haiku",
        "reason": "Strong code understanding and generation",
    },
    "reasoning": {
        "primary": "anthropic/claude-3-opus",
        "fallback": "openai/o3",
        "reason": "Deep reasoning capabilities",
    },
    "lightweight": {
        "primary": "google/gemini-2.0-flash",
        "fallback": "anthropic/claude-3-haiku",
        "reason": "Fast and cost-effective",
    },
    "vision": {
        "primary": "openai/gpt-4o",
        "fallback": "anthropic/claude-3-opus",
        "reason": "Strong vision capabilities",
    },
    "general": {
        "primary": "anthropic/claude-3-sonnet",
        "fallback": "openai/gpt-4o",
        "reason": "Good balance of capability and cost",
    },
}


def _get_cache_path(ctx: ToolContext) -> Path:
    """Get path to cache file."""
    drive_root = Path(os.environ.get("DRIVE_ROOT", "/content/drive/MyDrive/Ouroboros"))
    return drive_root / CACHE_FILE


def _load_cache(ctx: ToolContext) -> Dict[str, Any]:
    """Load cached data from Drive."""
    cache_path = _get_cache_path(ctx)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Check if cache is still valid
                if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                    return data
        except Exception as e:
            log.warning("Failed to load tech radar cache: %s", e)
    return {"timestamp": 0, "models": {}, "last_scan": None, "changes": []}


def _save_cache(ctx: ToolContext, data: Dict[str, Any]) -> None:
    """Save data to cache file."""
    cache_path = _get_cache_path(ctx)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _fetch_openrouter_models() -> List[Dict[str, Any]]:
    """Fetch model list from OpenRouter API."""
    try:
        client = httpx.Client(timeout=30.0)
        response = client.get("https://openrouter.ai/api/v1/models")
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except Exception as e:
        log.warning("Failed to fetch OpenRouter models: %s", e)
        return []


def _extract_model_info(model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant info from OpenRouter model data."""
    model_id = model_data.get("id", "")
    
    # Extract pricing
    pricing = model_data.get("pricing", {})
    prompt_price = pricing.get("prompt", 0) * 1000000  # Price per 1M tokens
    completion_price = pricing.get("completion", 0) * 1000000
    
    # Extract context window
    context_length = model_data.get("context_length", 0)
    
    # Extract capabilities from description
    description = model_data.get("description", "").lower()
    capabilities = []
    if "vision" in description or "multimodal" in description:
        capabilities.append("vision")
    if "reason" in description or "thinking" in description:
        capabilities.append("reasoning")
    if "code" in description or "programming" in description:
        capabilities.append("code")
    
    # Determine provider
    provider = "unknown"
    if model_id.startswith("anthropic/"):
        provider = "anthropic"
    elif model_id.startswith("openai/"):
        provider = "openai"
    elif model_id.startswith("google/"):
        provider = "google"
    elif model_id.startswith("deepseek-"):
        provider = "deepseek"
    elif model_id.startswith("qwen/"):
        provider = "qwen"
    
    return {
        "id": model_id,
        "provider": provider,
        "prompt_price_per_million": prompt_price,
        "completion_price_per_million": completion_price,
        "context_length": context_length,
        "capabilities": capabilities,
        "description": description[:200] if description else "",
        "created_at": model_data.get("created", 0),
    }


def _detect_changes(old_models: Dict[str, Any], new_models: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect changes between old and new model data."""
    changes = []
    
    # Check for new models
    for model_id, new_info in new_models.items():
        if model_id not in old_models:
            changes.append({
                "type": "new_model",
                "model_id": model_id,
                "provider": new_info.get("provider"),
                "timestamp": time.time(),
            })
    
    # Check for removed models
    for model_id in old_models:
        if model_id not in new_models:
            changes.append({
                "type": "removed_model",
                "model_id": model_id,
                "timestamp": time.time(),
            })
    
    # Check for price changes (significant: >10%)
    for model_id, new_info in new_models.items():
        if model_id in old_models:
            old_info = old_models[model_id]
            old_price = old_info.get("prompt_price_per_million", 0)
            new_price = new_info.get("prompt_price_per_million", 0)
            
            if old_price > 0 and new_price > 0:
                change_pct = abs(new_price - old_price) / old_price
                if change_pct > 0.1:  # >10% change
                    changes.append({
                        "type": "price_change",
                        "model_id": model_id,
                        "old_price": old_price,
                        "new_price": new_price,
                        "change_pct": change_pct,
                        "timestamp": time.time(),
                    })
    
    return changes


def _tech_radar_scan(ctx: ToolContext) -> str:
    """Perform a full tech radar scan and update knowledge base."""
    try:
        # Load current cache
        cache = _load_cache(ctx)
        old_models = cache.get("models", {})
        
        # Fetch latest models from OpenRouter
        log.info("Fetching OpenRouter models...")
        model_data = _fetch_openrouter_models()
        
        if not model_data:
            return "⚠️ Failed to fetch model data from OpenRouter API"
        
        # Process models
        new_models = {}
        for model in model_data:
            info = _extract_model_info(model)
            new_models[info["id"]] = info
        
        # Detect changes
        changes = _detect_changes(old_models, new_models)
        
        # Update cache
        cache.update({
            "timestamp": time.time(),
            "models": new_models,
            "last_scan": datetime.utcnow().isoformat(),
            "changes": changes + cache.get("changes", [])[:50],  # Keep last 50 changes
        })
        _save_cache(ctx, cache)
        
        # Update knowledge base
        from ouroboros.tools.core import knowledge_write
        
        # Create knowledge base entry
        kb_content = f"""# Tech Radar - Last updated: {datetime.utcnow().isoformat()}

## Summary
- **Total models tracked:** {len(new_models)}
- **Providers:** {len(set(m['provider'] for m in new_models.values()))}
- **Last scan:** {cache['last_scan']}
- **Recent changes:** {len(changes)} in this scan

## Recent Changes
"""
        
        if changes:
            for change in changes[:10]:  # Show last 10 changes
                kb_content += f"- **{change['type']}**: {change['model_id']}"
                if change['type'] == 'price_change':
                    kb_content += f" ({change['change_pct']:.1%} price change)"
                kb_content += "\n"
        else:
            kb_content += "No significant changes detected.\n"
        
        kb_content += """

## Top Models by Category

### Reasoning (deep thinking)
"""
        # Find reasoning models
        reasoning_models = []
        for model_id, info in new_models.items():
            if "reasoning" in info.get("capabilities", []):
                reasoning_models.append((model_id, info))
        
        reasoning_models.sort(key=lambda x: x[1].get("context_length", 0), reverse=True)
        for model_id, info in reasoning_models[:5]:
            price = info.get("prompt_price_per_million", 0)
            ctx = info.get("context_length", 0)
            kb_content += f"- {model_id}: ${price:.2f}/M tokens, {ctx:,} context\n"
        
        kb_content += """
### Code Editing
"""
        code_models = []
        for model_id, info in new_models.items():
            if "code" in info.get("capabilities", []):
                code_models.append((model_id, info))
        
        code_models.sort(key=lambda x: x[1].get("prompt_price_per_million", 0))
        for model_id, info in code_models[:5]:
            price = info.get("prompt_price_per_million", 0)
            ctx = info.get("context_length", 0)
            kb_content += f"- {model_id}: ${price:.2f}/M tokens, {ctx:,} context\n"
        
        kb_content += """
### Vision/Multimodal
"""
        vision_models = []
        for model_id, info in new_models.items():
            if "vision" in info.get("capabilities", []):
                vision_models.append((model_id, info))
        
        vision_models.sort(key=lambda x: x[1].get("context_length", 0), reverse=True)
        for model_id, info in vision_models[:5]:
            price = info.get("prompt_price_per_million", 0)
            ctx = info.get("context_length", 0)
            kb_content += f"- {model_id}: ${price:.2f}/M tokens, {ctx:,} context\n"
        
        # Write to knowledge base
        knowledge_write(ctx, topic="tech-radar", content=kb_content, mode="overwrite")
        
        # Prepare response
        response = [
            f"✅ Tech radar scan completed at {cache['last_scan']}",
            f"**Models tracked:** {len(new_models)}",
            f"**Recent changes:** {len(changes)}",
        ]
        
        if changes:
            response.append("\n**Significant changes:**")
            for change in changes[:5]:
                if change['type'] == 'new_model':
                    response.append(f"  🆕 {change['model_id']} ({change.get('provider', 'unknown')})")
                elif change['type'] == 'removed_model':
                    response.append(f"  ❌ {change['model_id']} removed")
                elif change['type'] == 'price_change':
                    response.append(f"  💰 {change['model_id']}: {change['change_pct']:.1%} price change")
        
        response.append(f"\nKnowledge base updated: topic 'tech-radar'")
        
        return "\n".join(response)
        
    except Exception as e:
        log.error("Tech radar scan failed: %s", e, exc_info=True)
        return f"⚠️ Tech radar scan failed: {e}"


def _tech_radar_status(ctx: ToolContext) -> str:
    """Show current tech radar status."""
    try:
        cache = _load_cache(ctx)
        
        if cache["timestamp"] == 0:
            return "⚠️ Tech radar has never been scanned. Run `tech_radar_scan` first."
        
        models = cache.get("models", {})
        last_scan = cache.get("last_scan", "never")
        changes = cache.get("changes", [])
        
        # Count by provider
        providers = {}
        for info in models.values():
            provider = info.get("provider", "unknown")
            providers[provider] = providers.get(provider, 0) + 1
        
        # Recent changes (last 7 days)
        week_ago = time.time() - 7 * 86400
        recent_changes = [c for c in changes if c.get("timestamp", 0) > week_ago]
        
        response = [
            f"## Tech Radar Status",
            f"**Last scan:** {last_scan}",
            f"**Models tracked:** {len(models)}",
            f"**Providers:** {', '.join(f'{k}: {v}' for k, v in sorted(providers.items()))}",
            f"**Recent changes (7 days):** {len(recent_changes)}",
        ]
        
        if recent_changes:
            response.append("\n**Recent changes:**")
            for change in recent_changes[:5]:
                timestamp = datetime.fromtimestamp(change.get("timestamp", 0)).strftime("%Y-%m-%d")
                if change['type'] == 'new_model':
                    response.append(f"  {timestamp} 🆕 {change['model_id']}")
                elif change['type'] == 'removed_model':
                    response.append(f"  {timestamp} ❌ {change['model_id']}")
                elif change['type'] == 'price_change':
                    response.append(f"  {timestamp} 💰 {change['model_id']} ({change['change_pct']:.1%})")
        
        # Show current recommendations
        response.append("\n**Current recommendations:**")
        for task_type, rec in TASK_RECOMMENDATIONS.items():
            response.append(f"  {task_type}: {rec['primary']} ({rec['reason']})")
        
        return "\n".join(response)
        
    except Exception as e:
        log.error("Tech radar status failed: %s", e, exc_info=True)
        return f"⚠️ Tech radar status failed: {e}"


def _tech_radar_recommend(ctx: ToolContext, task_type: str) -> str:
    """Recommend models for a specific task type."""
    try:
        cache = _load_cache(ctx)
        models = cache.get("models", {})
        
        # Check if we have cached data
        if not models:
            return "⚠️ No model data available. Run `tech_radar_scan` first."
        
        # Get recommendation for task type
        if task_type not in TASK_RECOMMENDATIONS:
            valid_types = ", ".join(TASK_RECOMMENDATIONS.keys())
            return f"⚠️ Invalid task type. Valid types: {valid_types}"
        
        rec = TASK_RECOMMENDATIONS[task_type]
        primary = rec["primary"]
        fallback = rec["fallback"]
        
        # Check if recommended models exist in our data
        primary_info = models.get(primary, {})
        fallback_info = models.get(fallback, {})
        
        response = [
            f"## Recommendation for '{task_type}' tasks",
            f"**Primary:** {primary}",
            f"**Reason:** {rec['reason']}",
        ]
        
        if primary_info:
            price = primary_info.get("prompt_price_per_million", 0)
            ctx = primary_info.get("context_length", 0)
            response.append(f"  - Price: ${price:.2f}/M tokens")
            response.append(f"  - Context: {ctx:,} tokens")
            if primary_info.get("capabilities"):
                response.append(f"  - Capabilities: {', '.join(primary_info['capabilities'])}")
        else:
            response.append(f"  ⚠️ Model not found in current scan")
        
        response.append(f"\n**Fallback:** {fallback}")
        
        if fallback_info:
            price = fallback_info.get("prompt_price_per_million", 0)
            ctx = fallback_info.get("context_length", 0)
            response.append(f"  - Price: ${price:.2f}/M tokens")
            response.append(f"  - Context: {ctx:,} tokens")
            if fallback_info.get("capabilities"):
                response.append(f"  - Capabilities: {', '.join(fallback_info['capabilities'])}")
        else:
            response.append(f"  ⚠️ Model not found in current scan")
        
        # Also suggest alternatives based on capabilities
        response.append("\n**Alternative options:**")
        
        # Find models with relevant capabilities
        relevant_capabilities = []
        if task_type == "code_editing":
            relevant_capabilities = ["code"]
        elif task_type == "reasoning":
            relevant_capabilities = ["reasoning"]
        elif task_type == "vision":
            relevant_capabilities = ["vision"]
        
        if relevant_capabilities:
            alternatives = []
            for model_id, info in models.items():
                if any(cap in info.get("capabilities", []) for cap in relevant_capabilities):
                    if model_id not in [primary, fallback]:
                        alternatives.append((model_id, info))
            
            # Sort by price (cheapest first)
            alternatives.sort(key=lambda x: x[1].get("prompt_price_per_million", float('inf')))
            
            for model_id, info in alternatives[:3]:
                price = info.get("prompt_price_per_million", 0)
                ctx = info.get("context_length", 0)
                response.append(f"  - {model_id}: ${price:.2f}/M tokens, {ctx:,} context")
        
        return "\n".join(response)
        
    except Exception as e:
        log.error("Tech radar recommend failed: %s", e, exc_info=True)
        return f"⚠️ Tech radar recommend failed: {e}"


def _tech_radar_changes(ctx: ToolContext, days: int = 7) -> str:
    """Show changes in the last N days."""
    try:
        cache = _load_cache(ctx)
        changes = cache.get("changes", [])
        
        if not changes:
            return "No changes recorded yet. Run `tech_radar_scan` first."
        
        cutoff = time.time() - days * 86400
        recent_changes = [c for c in changes if c.get("timestamp", 0) > cutoff]
        
        if not recent_changes:
            return f"No changes in the last {days} days."
        
        response = [f"## Tech Radar Changes (last {days} days)"]
        
        # Group by type
        by_type = {}
        for change in recent_changes:
            change_type = change.get("type", "unknown")
            by_type.setdefault(change_type, []).append(change)
        
        for change_type, type_changes in sorted(by_type.items()):
            response.append(f"\n**{change_type.replace('_', ' ').title()}** ({len(type_changes)}):")
            
            for change in type_changes[:10]:  # Limit per type
                timestamp = datetime.fromtimestamp(change.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M")
                model_id = change.get("model_id", "unknown")
                
                if change_type == "price_change":
                    old_price = change.get("old_price", 0)
                    new_price = change.get("new_price", 0)
                    change_pct = change.get("change_pct", 0)
                    response.append(f"  {timestamp} {model_id}: ${old_price:.2f} → ${new_price:.2f} ({change_pct:.1%})")
                else:
                    response.append(f"  {timestamp} {model_id}")
        
        return "\n".join(response)
        
    except Exception as e:
        log.error("Tech radar changes failed: %s", e, exc_info=True)
        return f"⚠️ Tech radar changes failed: {e}"


def get_tools():
    return [
        ToolEntry("tech_radar_scan", {
            "name": "tech_radar_scan",
            "description": "Perform a full tech radar scan: fetch current LLM models, pricing, capabilities from OpenRouter API. Updates knowledge base topic 'tech-radar'. Cache TTL: 24 hours.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _tech_radar_scan),
        ToolEntry("tech_radar_status", {
            "name": "tech_radar_status",
            "description": "Show current tech radar status: last scan time, models tracked, recent changes, recommendations.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _tech_radar_status),
        ToolEntry("tech_radar_recommend", {
            "name": "tech_radar_recommend",
            "description": "Recommend models for a specific task type. Valid types: code_editing, reasoning, lightweight, vision, general.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "enum": ["code_editing", "reasoning", "lightweight", "vision", "general"],
                        "description": "Type of task to recommend models for",
                    },
                },
                "required": ["task_type"],
            },
        }, _tech_radar_recommend),
        ToolEntry("tech_radar_changes", {
            "name": "tech_radar_changes",
            "description": "Show changes in the tech radar over the last N days (default: 7).",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "default": 7,
                        "description": "Number of days to look back",
                    },
                },
                "required": [],
            },
        }, _tech_radar_changes),
    ]