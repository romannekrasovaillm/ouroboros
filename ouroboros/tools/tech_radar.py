"""Tech Radar tool — track LLM ecosystem changes, pricing, capabilities."""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import requests
from datetime import datetime, timezone

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


def _fetch_openrouter_models() -> Optional[List[Dict[str, Any]]]:
    """Fetch model list from OpenRouter API."""
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={
                "User-Agent": "Ouroboros/6.4.0 (tech-radar)",
                "Accept": "application/json",
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        log.warning("Failed to fetch OpenRouter models: %s", e, exc_info=True)
        return None


def _extract_model_info(model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant information from OpenRouter model data."""
    pricing = model_data.get("pricing", {})
    context_length = model_data.get("context_length", 0)
    
    return {
        "id": model_data.get("id", ""),
        "name": model_data.get("name", ""),
        "description": model_data.get("description", ""),
        "context_length": context_length,
        "architecture": model_data.get("architecture", ""),
        "provider": model_data.get("provider", {}).get("name", ""),
        "pricing": {
            "prompt": pricing.get("prompt", 0),
            "completion": pricing.get("completion", 0),
            "cached": pricing.get("cached", 0),
        },
        "capabilities": model_data.get("capabilities", {}),
        "top_provider": model_data.get("top_provider", {}),
        "created_at": model_data.get("created_at", ""),
        "updated_at": model_data.get("updated_at", ""),
    }


def _update_tech_radar(ctx: ToolContext) -> str:
    """Fetch latest model information and update tech radar knowledge base."""
    try:
        models = _fetch_openrouter_models()
        if not models:
            return "⚠️ Failed to fetch model data from OpenRouter API."
        
        # Process models
        tech_radar_data = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_models": len(models),
            "models": {},
            "summary": {
                "by_provider": {},
                "context_windows": {},
                "price_ranges": {},
            }
        }
        
        # Count models by context window ranges
        context_ranges = {
            "0-4k": 0,
            "4k-32k": 0,
            "32k-128k": 0,
            "128k-1M": 0,
            "1M+": 0,
        }
        
        # Count models by price tier (prompt price per 1M tokens)
        price_tiers = {
            "free": 0,
            "ultra_low": 0,    # < $0.10
            "low": 0,          # $0.10 - $1.00
            "medium": 0,       # $1.00 - $10.00
            "high": 0,         # $10.00 - $50.00
            "premium": 0,      # $50.00+
        }
        
        for model in models:
            model_info = _extract_model_info(model)
            model_id = model_info["id"]
            tech_radar_data["models"][model_id] = model_info
            
            # Update provider counts
            provider = model_info["provider"]
            tech_radar_data["summary"]["by_provider"][provider] = \
                tech_radar_data["summary"]["by_provider"].get(provider, 0) + 1
            
            # Update context window counts
            ctx_len = model_info["context_length"]
            if ctx_len <= 4096:
                context_ranges["0-4k"] += 1
            elif ctx_len <= 32768:
                context_ranges["4k-32k"] += 1
            elif ctx_len <= 131072:
                context_ranges["32k-128k"] += 1
            elif ctx_len <= 1048576:
                context_ranges["128k-1M"] += 1
            else:
                context_ranges["1M+"] += 1
            
            # Update price tier counts
            prompt_price = model_info["pricing"]["prompt"]
            if prompt_price == 0:
                price_tiers["free"] += 1
            elif prompt_price < 0.1:
                price_tiers["ultra_low"] += 1
            elif prompt_price < 1.0:
                price_tiers["low"] += 1
            elif prompt_price < 10.0:
                price_tiers["medium"] += 1
            elif prompt_price < 50.0:
                price_tiers["high"] += 1
            else:
                price_tiers["premium"] += 1
        
        tech_radar_data["summary"]["context_windows"] = context_ranges
        tech_radar_data["summary"]["price_ranges"] = price_tiers
        
        # Store in knowledge base
        from ouroboros.tools.knowledge import knowledge_write
        knowledge_write(
            ctx,
            topic="tech-radar",
            content=json.dumps(tech_radar_data, indent=2),
            mode="overwrite"
        )
        
        # Format response
        lines = []
        lines.append("✅ Tech radar updated successfully!")
        lines.append(f"**Total models:** {len(models)}")
        lines.append(f"**Last updated:** {tech_radar_data['last_updated']}")
        lines.append("\n**By provider:**")
        for provider, count in sorted(tech_radar_data["summary"]["by_provider"].items(), 
                                     key=lambda x: x[1], reverse=True)[:10]:
            lines.append(f"  - {provider}: {count}")
        
        lines.append("\n**Context windows:**")
        for range_name, count in context_ranges.items():
            if count > 0:
                lines.append(f"  - {range_name}: {count}")
        
        lines.append("\n**Price tiers (prompt $/1M tokens):**")
        for tier, count in price_tiers.items():
            if count > 0:
                lines.append(f"  - {tier}: {count}")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error("Failed to update tech radar: %s", e, exc_info=True)
        return f"⚠️ Failed to update tech radar: {e}"


def _show_tech_radar(ctx: ToolContext) -> str:
    """Display current tech radar status from knowledge base."""
    try:
        from ouroboros.tools.knowledge import knowledge_read
        content = knowledge_read(ctx, topic="tech-radar")
        
        if not content or content.startswith("Topic"):
            return "ℹ️ No tech radar data found. Run `update_tech_radar` first."
        
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return f"⚠️ Corrupted tech radar data. Run `update_tech_radar` to refresh.\nRaw content: {content[:500]}..."
        
        lines = []
        lines.append("## Tech Radar Status")
        lines.append(f"**Last updated:** {data.get('last_updated', 'unknown')}")
        lines.append(f"**Total models tracked:** {data.get('total_models', 0)}")
        
        summary = data.get("summary", {})
        
        if "by_provider" in summary:
            lines.append("\n**Top providers:**")
            providers = sorted(summary["by_provider"].items(), 
                             key=lambda x: x[1], reverse=True)[:10]
            for provider, count in providers:
                lines.append(f"  - {provider}: {count}")
        
        if "context_windows" in summary:
            lines.append("\n**Context window distribution:**")
            for range_name, count in summary["context_windows"].items():
                if count > 0:
                    lines.append(f"  - {range_name}: {count}")
        
        if "price_ranges" in summary:
            lines.append("\n**Price tier distribution:**")
            for tier, count in summary["price_ranges"].items():
                if count > 0:
                    lines.append(f"  - {tier}: {count}")
        
        # Show some notable models
        lines.append("\n**Notable models (recent/interesting):**")
        models = data.get("models", {})
        notable_models = []
        
        for model_id, model_info in models.items():
            # Filter for interesting models
            if any(keyword in model_id.lower() for keyword in 
                   ["claude", "gpt", "gemini", "grok", "llama", "mistral"]):
                notable_models.append((model_id, model_info))
        
        # Sort by provider then name
        notable_models.sort(key=lambda x: (x[1].get("provider", ""), x[1].get("name", "")))
        
        for model_id, model_info in notable_models[:15]:  # Limit to 15
            name = model_info.get("name", model_id)
            provider = model_info.get("provider", "")
            ctx_len = model_info.get("context_length", 0)
            prompt_price = model_info.get("pricing", {}).get("prompt", 0)
            
            lines.append(f"  - **{name}** ({provider})")
            lines.append(f"    Context: {ctx_len:,} | Prompt: ${prompt_price}/1M")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error("Failed to show tech radar: %s", e, exc_info=True)
        return f"⚠️ Failed to show tech radar: {e}"


def _compare_models(ctx: ToolContext, models: str) -> str:
    """Compare specific models by ID or name pattern."""
    try:
        from ouroboros.tools.knowledge import knowledge_read
        content = knowledge_read(ctx, topic="tech-radar")
        
        if not content or content.startswith("Topic"):
            return "ℹ️ No tech radar data found. Run `update_tech_radar` first."
        
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return "⚠️ Corrupted tech radar data. Run `update_tech_radar` to refresh."
        
        # Parse models parameter (comma-separated list)
        model_patterns = [m.strip() for m in models.split(",") if m.strip()]
        if not model_patterns:
            return "⚠️ Please provide at least one model ID or name pattern (comma-separated)."
        
        # Find matching models
        all_models = data.get("models", {})
        matched_models = []
        
        for model_id, model_info in all_models.items():
            model_name = model_info.get("name", "").lower()
            for pattern in model_patterns:
                pattern_lower = pattern.lower()
                if (pattern_lower in model_id.lower() or 
                    pattern_lower in model_name):
                    matched_models.append((model_id, model_info))
                    break  # Don't add same model multiple times
        
        if not matched_models:
            return f"⚠️ No models found matching patterns: {', '.join(model_patterns)}"
        
        # Sort by provider then name
        matched_models.sort(key=lambda x: (x[1].get("provider", ""), x[1].get("name", "")))
        
        lines = []
        lines.append(f"## Model Comparison ({len(matched_models)} models)")
        
        for model_id, model_info in matched_models:
            name = model_info.get("name", model_id)
            provider = model_info.get("provider", "")
            description = model_info.get("description", "")
            ctx_len = model_info.get("context_length", 0)
            pricing = model_info.get("pricing", {})
            capabilities = model_info.get("capabilities", {})
            created = model_info.get("created_at", "")
            updated = model_info.get("updated_at", "")
            
            lines.append(f"\n### {name} (`{model_id}`)")
            lines.append(f"**Provider:** {provider}")
            if description:
                lines.append(f"**Description:** {description[:200]}...")
            lines.append(f"**Context length:** {ctx_len:,} tokens")
            lines.append(f"**Pricing (per 1M tokens):**")
            lines.append(f"  - Prompt: ${pricing.get('prompt', 0):.4f}")
            lines.append(f"  - Completion: ${pricing.get('completion', 0):.4f}")
            lines.append(f"  - Cached: ${pricing.get('cached', 0):.4f}")
            
            if capabilities:
                lines.append("**Capabilities:**")
                for cap, value in capabilities.items():
                    if value:
                        lines.append(f"  - {cap}: {value}")
            
            if created:
                lines.append(f"**Created:** {created}")
            if updated and updated != created:
                lines.append(f"**Updated:** {updated}")
        
        return "\n".join(lines)
        
    except Exception as e:
        log.error("Failed to compare models: %s", e, exc_info=True)
        return f"⚠️ Failed to compare models: {e}"


def get_tools():
    return [
        ToolEntry("update_tech_radar", {
            "name": "update_tech_radar",
            "description": "Fetch latest model information from OpenRouter API and update tech radar knowledge base. Tracks pricing, context windows, capabilities, and provider distribution.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _update_tech_radar),
        ToolEntry("show_tech_radar", {
            "name": "show_tech_radar",
            "description": "Display current tech radar status from knowledge base. Shows model counts by provider, context window distribution, price tiers, and notable models.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _show_tech_radar),
        ToolEntry("compare_models", {
            "name": "compare_models",
            "description": "Compare specific models by ID or name pattern. Shows detailed information including pricing, context windows, capabilities, and timestamps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "models": {
                        "type": "string",
                        "description": "Comma-separated list of model IDs or name patterns to compare (e.g., 'claude,gpt-4,gemini')"
                    }
                },
                "required": ["models"]
            },
        }, _compare_models),
    ]