from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

import httpx

from .call_logging import safe_write_call_log
from .config import get_settings
from .schemas import AIThemeRequest, ThemeConfig


SYSTEM_PROMPT = """You design calm, accessible interfaces for a private research RSS reader.
Return only one JSON object. Do not return markdown or code. The object must contain:
id, label, accent, secondary, nav, paper, surface, ink, density, typography, motif, source.
Colors must be six-digit hex values with sufficient contrast. density is compact, balanced,
or relaxed. typography is technical, editorial, or balanced. motif is orbit, network, market,
proof, silicon, circuit, or grid. source must be ai. Do not add other fields. The interface
must remain readable and professional; domain references should be subtle, not decorative."""


def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _extract_json(value: str) -> dict[str, Any]:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.DOTALL)
        if not match:
            raise ValueError("The model did not return a JSON theme") from None
        result = json.loads(match.group(0))
    if not isinstance(result, dict):
        raise ValueError("The model did not return a JSON object")
    return result


async def _generate_ai_theme(body: AIThemeRequest) -> ThemeConfig:
    prompt = (
        f"Primary domain: {body.primary_domain}\n"
        f"All domains: {', '.join(body.selected_domains)}\n"
        f"Additional preference: {body.style_prompt or 'none'}\n"
        "Blend cross-domain signals while letting the primary domain lead."
    )
    payload = {
        "model": body.model,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {body.api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.post(_chat_url(str(body.base_url)), headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = f"Model service returned HTTP {exc.response.status_code}"
        try:
            message = exc.response.json().get("error", {}).get("message")
            if isinstance(message, str) and message:
                detail = f"{detail}: {message[:300]}"
        except (ValueError, AttributeError):
            pass
        raise ValueError(detail) from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"Could not reach the model service: {exc}") from exc

    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("The model service returned an unsupported response") from exc
    theme = ThemeConfig.model_validate(_extract_json(content))
    return theme.model_copy(update={"source": "ai"})


async def generate_ai_theme(body: AIThemeRequest) -> ThemeConfig:
    started_at = perf_counter()
    input_chars = len(SYSTEM_PROMPT) + sum(
        len(value)
        for value in [
            body.primary_domain,
            *body.selected_domains,
            body.style_prompt or "",
        ]
    )
    try:
        theme = await _generate_ai_theme(body)
    except Exception as exc:
        safe_write_call_log(
            category="llm",
            operation="chat_completion",
            feature="ai_theme",
            status="error",
            duration_ms=round((perf_counter() - started_at) * 1000),
            input_chars=input_chars,
            model=body.model,
            error=str(exc),
            settings=get_settings(),
        )
        raise
    safe_write_call_log(
        category="llm",
        operation="chat_completion",
        feature="ai_theme",
        status="success",
        duration_ms=round((perf_counter() - started_at) * 1000),
        input_chars=input_chars,
        output_chars=len(theme.model_dump_json()),
        model=body.model,
        settings=get_settings(),
    )
    return theme
