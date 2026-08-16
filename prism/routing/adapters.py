"""Provider adapters — translate OpenAI format to/from each provider's native API.

The gateway internally uses OpenAI format. Adapters handle:
  1. Request translation (OpenAI → provider-native)
  2. Response translation (provider-native → OpenAI)
  3. Auth header format differences
  4. Endpoint path differences

Supported providers:
  - openai: Native (no translation needed)
  - anthropic: Messages API (/v1/messages)
  - google: Gemini API (/v1/models/{model}:generateContent)
  - azure: Azure OpenAI (/openai/deployments/{model}/chat/completions)
  - bedrock: AWS Bedrock (SigV4 signing, invoke model)
  - groq/together/fireworks/mistral/deepseek: OpenAI-compatible (no translation)
  - ollama/vllm: OpenAI-compatible local (no translation)
"""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from typing import Any


class ProviderAdapter(ABC):
    """Base class for provider adapters."""

    @abstractmethod
    def translate_request(self, payload: dict) -> tuple[str, dict, dict]:
        """Translate OpenAI request to provider-native format.

        Args:
            payload: OpenAI-format request body

        Returns:
            (endpoint_path, translated_body, extra_headers)
        """

    @abstractmethod
    def translate_response(self, response: dict, model: str) -> dict:
        """Translate provider-native response to OpenAI format.

        Args:
            response: Provider-native response body
            model: The model that was requested

        Returns:
            OpenAI-format response body
        """

    def translate_auth_header(self, api_key: str) -> dict[str, str]:
        """Return auth headers for this provider."""
        return {"Authorization": f"Bearer {api_key}"}


class OpenAIAdapter(ProviderAdapter):
    """OpenAI and compatible providers (Groq, Together, Fireworks, Mistral, DeepSeek, vLLM, Ollama)."""

    def translate_request(self, payload: dict) -> tuple[str, dict, dict]:
        return "/v1/chat/completions", payload, {}

    def translate_response(self, response: dict, model: str) -> dict:
        return response  # Already in OpenAI format


class AnthropicAdapter(ProviderAdapter):
    """Anthropic Messages API adapter.

    Translates OpenAI chat format → Anthropic Messages format:
      - system message extracted to top-level `system` field
      - role mapping (assistant stays, user stays)
      - response format: content[] blocks → choices[].message
    """

    def translate_request(self, payload: dict) -> tuple[str, dict, dict]:
        messages = payload.get("messages", [])

        # Extract system message
        system_content = ""
        user_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_content += msg.get("content", "") + "\n"
            else:
                user_messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })

        body: dict[str, Any] = {
            "model": payload["model"],
            "messages": user_messages,
            "max_tokens": payload.get("max_tokens", 4096),
        }

        if system_content.strip():
            body["system"] = system_content.strip()
        if payload.get("temperature") is not None:
            body["temperature"] = payload["temperature"]
        if payload.get("top_p") is not None:
            body["top_p"] = payload["top_p"]
        if payload.get("stop"):
            body["stop_sequences"] = payload["stop"] if isinstance(payload["stop"], list) else [payload["stop"]]
        if payload.get("stream"):
            body["stream"] = True

        return "/v1/messages", body, {}

    def translate_response(self, response: dict, model: str) -> dict:
        # Anthropic response → OpenAI format
        content = ""
        for block in response.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        usage = response.get("usage", {})
        return {
            "id": f"chatcmpl-{response.get('id', uuid.uuid4().hex[:24])}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": _map_anthropic_stop(response.get("stop_reason", "end_turn")),
            }],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
        }

    def translate_auth_header(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }


class GeminiAdapter(ProviderAdapter):
    """Google Gemini API adapter.

    Translates OpenAI format → Gemini generateContent format:
      - messages → contents[] with parts[]
      - system message → systemInstruction
      - response: candidates[].content → choices[].message
    """

    def translate_request(self, payload: dict) -> tuple[str, dict, dict]:
        messages = payload.get("messages", [])
        model = payload["model"]

        # Extract system instruction
        system_parts = []
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append({"text": content})
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": content}],
                })

        body: dict[str, Any] = {
            "contents": contents,
        }

        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}

        # Generation config
        gen_config: dict[str, Any] = {}
        if payload.get("temperature") is not None:
            gen_config["temperature"] = payload["temperature"]
        if payload.get("top_p") is not None:
            gen_config["topP"] = payload["top_p"]
        if payload.get("max_tokens") is not None:
            gen_config["maxOutputTokens"] = payload["max_tokens"]
        if payload.get("stop"):
            stops = payload["stop"] if isinstance(payload["stop"], list) else [payload["stop"]]
            gen_config["stopSequences"] = stops
        if gen_config:
            body["generationConfig"] = gen_config

        # Gemini endpoint: /v1/models/{model}:generateContent
        endpoint = f"/v1/models/{model}:generateContent"
        return endpoint, body, {}

    def translate_response(self, response: dict, model: str) -> dict:
        candidates = response.get("candidates", [])
        content = ""
        finish_reason = "stop"

        if candidates:
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            content = "".join(p.get("text", "") for p in parts)
            finish_reason = _map_gemini_stop(candidate.get("finishReason", "STOP"))

        usage_meta = response.get("usageMetadata", {})
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": finish_reason,
            }],
            "usage": {
                "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                "total_tokens": usage_meta.get("totalTokenCount", 0),
            },
        }

    def translate_auth_header(self, api_key: str) -> dict[str, str]:
        # Gemini uses ?key= query param, but we handle it via header for uniformity
        return {"x-goog-api-key": api_key}


class AzureOpenAIAdapter(ProviderAdapter):
    """Azure OpenAI adapter.

    Same request format as OpenAI, different endpoint path and auth header.
    base_url should be: https://{resource}.openai.azure.com
    """

    def translate_request(self, payload: dict) -> tuple[str, dict, dict]:
        model = payload.get("model", "")
        # Azure uses deployment name in the URL
        endpoint = f"/openai/deployments/{model}/chat/completions?api-version=2024-02-01"
        # Remove model from body (it's in the URL)
        body = {k: v for k, v in payload.items() if k != "model"}
        return endpoint, body, {}

    def translate_response(self, response: dict, model: str) -> dict:
        # Azure returns OpenAI format, just ensure model is set
        response["model"] = model
        return response

    def translate_auth_header(self, api_key: str) -> dict[str, str]:
        return {"api-key": api_key}


class BedrockAdapter(ProviderAdapter):
    """AWS Bedrock adapter.

    Translates OpenAI format → Bedrock Converse API format.
    base_url should be: https://bedrock-runtime.{region}.amazonaws.com

    Note: For real Bedrock, you'd use SigV4 signing via botocore.
    This adapter supports the simpler Bedrock proxy approach where
    a local proxy handles SigV4 and exposes HTTP.
    """

    def translate_request(self, payload: dict) -> tuple[str, dict, dict]:
        model = payload["model"]
        messages = payload.get("messages", [])

        # Bedrock Converse API format
        system_msgs = []
        converse_msgs = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_msgs.append({"text": content})
            else:
                converse_msgs.append({
                    "role": role,
                    "content": [{"text": content}],
                })

        body: dict[str, Any] = {
            "messages": converse_msgs,
        }
        if system_msgs:
            body["system"] = system_msgs

        # Inference config
        inference_config: dict[str, Any] = {}
        if payload.get("max_tokens") is not None:
            inference_config["maxTokens"] = payload["max_tokens"]
        if payload.get("temperature") is not None:
            inference_config["temperature"] = payload["temperature"]
        if payload.get("top_p") is not None:
            inference_config["topP"] = payload["top_p"]
        if payload.get("stop"):
            stops = payload["stop"] if isinstance(payload["stop"], list) else [payload["stop"]]
            inference_config["stopSequences"] = stops
        if inference_config:
            body["inferenceConfig"] = inference_config

        endpoint = f"/model/{model}/converse"
        return endpoint, body, {}

    def translate_response(self, response: dict, model: str) -> dict:
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        content = "".join(b.get("text", "") for b in content_blocks)

        usage = response.get("usage", {})
        stop_reason = response.get("stopReason", "end_turn")

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": _map_bedrock_stop(stop_reason),
            }],
            "usage": {
                "prompt_tokens": usage.get("inputTokens", 0),
                "completion_tokens": usage.get("outputTokens", 0),
                "total_tokens": usage.get("inputTokens", 0) + usage.get("outputTokens", 0),
            },
        }


# ─── Adapter Registry ─────────────────────────────────────────────────────────

# Provider name → adapter class
ADAPTER_REGISTRY: dict[str, type[ProviderAdapter]] = {
    # Native OpenAI
    "openai": OpenAIAdapter,
    # Anthropic
    "anthropic": AnthropicAdapter,
    # Google
    "google": GeminiAdapter,
    "gemini": GeminiAdapter,
    # Azure
    "azure": AzureOpenAIAdapter,
    "azure_openai": AzureOpenAIAdapter,
    # AWS
    "bedrock": BedrockAdapter,
    "aws_bedrock": BedrockAdapter,
    # OpenAI-compatible (use OpenAI adapter — no translation needed)
    "groq": OpenAIAdapter,
    "together": OpenAIAdapter,
    "fireworks": OpenAIAdapter,
    "mistral": OpenAIAdapter,
    "deepseek": OpenAIAdapter,
    "perplexity": OpenAIAdapter,
    "anyscale": OpenAIAdapter,
    "kiro": OpenAIAdapter,
    "ollama": OpenAIAdapter,
    "vllm": OpenAIAdapter,
    "lmstudio": OpenAIAdapter,
    "litellm": OpenAIAdapter,
}


def get_adapter(provider_name: str) -> ProviderAdapter:
    """Get the adapter for a provider. Falls back to OpenAI-compatible."""
    adapter_cls = ADAPTER_REGISTRY.get(provider_name.lower(), OpenAIAdapter)
    return adapter_cls()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _map_anthropic_stop(stop_reason: str) -> str:
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
    }.get(stop_reason, "stop")


def _map_gemini_stop(finish_reason: str) -> str:
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
    }.get(finish_reason, "stop")


def _map_bedrock_stop(stop_reason: str) -> str:
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "content_filtered": "content_filter",
    }.get(stop_reason, "stop")
