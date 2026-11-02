"""
LLM客户端封装
统一使用OpenAI格式调用

OpenReef: supports an optional provider chain per stage. When a provider
registry is configured (see llm_providers.py), calls fail over across the
chain on provider errors (rate limits, 5xx, timeouts, auth). With no
registry, behavior is identical to upstream: a single provider from
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL_NAME.
"""

import json
import logging
import re
from typing import Optional, Dict, Any, List
from openai import OpenAI

from ..config import Config
from .llm_providers import ChainEntry, resolve_chain
from .openai_chat_compat import create_chat_completion, extract_chat_completion_text


logger = logging.getLogger(__name__)


class LLMResponseError(ValueError):
    """A safe, structured error for unusable model responses."""

    def __init__(self, message: str, *, finish_reason: Optional[str] = None):
        super().__init__(message)
        self.finish_reason = finish_reason


# Provider-side failures worth failing over to the next chain entry.
# Content problems (LLMResponseError) are not here — they are retried
# within the current provider by chat_json's bounded attempts.
_FAILOVER_STATUSES = {401, 403, 408, 409, 425, 429, 500, 502, 503, 504}


def _is_failoverable(error: Exception) -> bool:
    if isinstance(error, LLMResponseError):
        return False
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and status in _FAILOVER_STATUSES:
        return True
    try:
        import openai

        if isinstance(
            error,
            (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.RateLimitError,
                openai.InternalServerError,
                openai.AuthenticationError,
                openai.PermissionDeniedError,
            ),
        ):
            return True
    except Exception:  # pragma: no cover - defensive
        pass
    return isinstance(error, (ConnectionError, TimeoutError, OSError))


def _is_response_format_unsupported(error: Exception) -> bool:
    """Detect an explicit provider rejection of JSON response_format."""

    if getattr(error, "status_code", None) not in {400, 422}:
        return False

    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return False

    details = body.get("error", body)
    if not isinstance(details, dict):
        return False

    param = str(details.get("param") or "").strip().lower()
    if param == "response_format" or param.startswith("response_format."):
        return True

    message = str(details.get("message") or "").lower()
    if "response_format" not in message:
        return False

    code = str(details.get("code") or "").lower()
    unsupported_codes = {
        "unsupported_parameter",
        "unsupported_value",
        "unknown_parameter",
        "invalid_parameter",
    }
    unsupported_phrases = (
        "not support",
        "unsupported",
        "unknown parameter",
        "unrecognized parameter",
    )
    return code in unsupported_codes or any(
        phrase in message for phrase in unsupported_phrases
    )


def _clean_chat_text(content: str) -> str:
    """Remove common reasoning wrappers and an outer Markdown JSON fence."""

    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
    cleaned = cleaned.lstrip("\ufeff")
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    return cleaned.strip()


def _contains_additional_json_container(content: str) -> bool:
    """Return True when trailing text embeds another JSON object or array."""

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", content):
        try:
            value, _ = decoder.raw_decode(content[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return True
    return False


class LLMClient:
    """LLM客户端"""

    # Sentinel for instances built without __init__ (e.g. upstream tests use
    # object.__new__(LLMClient) with a fake self.client): behave exactly like
    # upstream — a single provider from the legacy attributes, no failover.
    _LEGACY_ENTRY = object()

    def _entries(self) -> List[ChainEntry]:
        chain = getattr(self, "_chain", None)
        return chain if chain else [self._LEGACY_ENTRY]

    @staticmethod
    def _provider_label(entry: Any) -> str:
        return getattr(entry, "provider", "legacy")

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        stage: str = "default",
    ):
        # Explicit credentials keep today's behavior exactly: one provider,
        # no registry involvement.
        if api_key is not None or base_url is not None or model is not None:
            self.api_key = api_key or Config.LLM_API_KEY
            self.base_url = base_url or Config.LLM_BASE_URL
            self.model = model or Config.LLM_MODEL_NAME
            if not self.api_key:
                raise ValueError("LLM_API_KEY 未配置")
            self._chain: List[ChainEntry] = [
                ChainEntry(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    model=self.model,
                    provider="explicit",
                )
            ]
        else:
            self._chain = resolve_chain(stage)
            head = self._chain[0]
            self.api_key = head.api_key
            self.base_url = head.base_url
            self.model = head.model
            if not self.api_key:
                raise ValueError("LLM_API_KEY 未配置")

        self._clients: Dict[ChainEntry, OpenAI] = {}

    def _client_for(self, entry: ChainEntry) -> OpenAI:
        client = self._clients.get(entry)
        if client is None:
            client = OpenAI(api_key=entry.api_key, base_url=entry.base_url)
            self._clients[entry] = client
        return client

    def _create_completion(
        self,
        entry: Any = None,
        *,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        response_format: Optional[Dict[str, Any]],
    ) -> Any:
        """Send one raw Chat Completions request through the compatibility layer."""

        if entry is None or entry is self._LEGACY_ENTRY:
            return create_chat_completion(
                self.client,
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        return create_chat_completion(
            self._client_for(entry),
            model=entry.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    def _should_failover(self, error: Exception, entry_index: int, total: int) -> bool:
        return _is_failoverable(error) and entry_index < total - 1

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            response_format: 响应格式（如JSON模式）

        Returns:
            模型响应文本
        """
        last_error: Optional[Exception] = None
        entries = self._entries()
        for index, entry in enumerate(entries):
            try:
                response = self._create_completion(
                    entry,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )
                content = extract_chat_completion_text(response)
                return _clean_chat_text(content)
            except Exception as error:
                if not self._should_failover(error, index, len(entries)):
                    raise
                last_error = error
                logger.warning(
                    "LLM provider %s failed (%s: %s); failing over to next provider",
                    self._provider_label(entry),
                    type(error).__name__,
                    error,
                )
        assert last_error is not None  # pragma: no cover - loop guard
        raise last_error

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: Optional[int] = 4096,
        max_attempts: int = 1,
    ) -> Dict[str, Any]:
        """
        发送聊天请求并返回JSON

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            max_attempts: 内容生成尝试次数（不含一次明确的JSON模式能力降级）

        Returns:
            解析后的JSON对象
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        last_error: Optional[Exception] = None
        entries = self._entries()

        for index, entry in enumerate(entries):
            response_format: Optional[Dict[str, str]] = {"type": "json_object"}
            request_max_tokens = max_tokens

            for attempt in range(1, max_attempts + 1):
                entry_error: Optional[Exception] = None
                # JSON-mode capability negotiation is separate from content
                # regeneration. An explicit response_format rejection may add one
                # request, but it must not consume a content attempt.
                while True:
                    try:
                        response = self._create_completion(
                            entry,
                            messages=messages,
                            temperature=temperature,
                            max_tokens=request_max_tokens,
                            response_format=response_format,
                        )
                    except Exception as error:
                        if (
                            response_format is not None
                            and _is_response_format_unsupported(error)
                        ):
                            logger.warning(
                                "LLM provider %s explicitly rejected response_format; "
                                "retrying once with prompt-only JSON guidance",
                                self._provider_label(entry),
                            )
                            response_format = None
                            continue
                        if self._should_failover(error, index, len(entries)):
                            entry_error = error
                            break
                        raise
                    break

                if entry_error is not None:
                    break

                try:
                    return self._parse_json_response(response)
                except LLMResponseError as error:
                    entry_error = error
                    if attempt >= max_attempts:
                        break

                    # A caller-supplied cap is the common cause of a partial JSON
                    # object. Omit it for the one bounded retry so the provider can
                    # use its model-specific output limit.
                    had_token_cap = request_max_tokens is not None
                    request_max_tokens = None
                    logger.warning(
                        "LLM provider %s returned unusable JSON (finish_reason=%s); "
                        "retrying content generation%s",
                        self._provider_label(entry),
                        error.finish_reason or "unknown",
                        " without an output token cap" if had_token_cap else "",
                    )

            # This provider is exhausted (content retries spent or a hard
            # provider error). Try the next chain entry with fresh settings:
            # a different provider may support JSON mode or produce parseable
            # output where this one could not.
            last_error = entry_error
            if entry_error is None:  # pragma: no cover - defensive
                last_error = LLMResponseError("LLM did not produce a JSON response")
            if index < len(entries) - 1:
                logger.warning(
                    "LLM provider %s exhausted; failing over to next provider",
                    self._provider_label(entry),
                )

        if last_error is not None:
            raise last_error
        raise LLMResponseError("LLM did not produce a JSON response")

    @staticmethod
    def _parse_json_response(response: Any) -> Dict[str, Any]:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMResponseError("LLM returned no choices")

        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise LLMResponseError(
                "LLM JSON output was truncated at the token limit",
                finish_reason=finish_reason,
            )
        if finish_reason not in {None, "stop"}:
            raise LLMResponseError(
                f"LLM JSON generation stopped unexpectedly ({finish_reason})",
                finish_reason=finish_reason,
            )

        content = _clean_chat_text(extract_chat_completion_text(response))
        if not content:
            raise LLMResponseError(
                "LLM returned empty JSON content",
                finish_reason=finish_reason,
            )

        try:
            value = json.loads(content)
        except json.JSONDecodeError as strict_error:
            # Some compatible providers append a short explanation after an
            # otherwise complete JSON object. Accept only an object decoded
            # from the beginning; never repair or invent truncated JSON.
            try:
                value, end = json.JSONDecoder().raw_decode(content)
            except json.JSONDecodeError:
                raise LLMResponseError(
                    "LLM returned invalid JSON "
                    f"(line {strict_error.lineno}, column {strict_error.colno})",
                    finish_reason=finish_reason,
                ) from strict_error

            trailing = content[end:].strip()
            if trailing:
                if _contains_additional_json_container(trailing):
                    raise LLMResponseError(
                        "LLM returned multiple JSON values",
                        finish_reason=finish_reason,
                    )
                logger.warning("Ignoring text after a complete LLM JSON object")

        if not isinstance(value, dict):
            raise LLMResponseError(
                "LLM JSON response must be a top-level JSON object",
                finish_reason=finish_reason,
            )

        return value
