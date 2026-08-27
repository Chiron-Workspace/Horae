"""Registry: dựng chuỗi provider theo config, fallback theo thứ tự."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from typing import Sequence

from horae.llm.protocol import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMProvider,
    LLMQuotaError,
    LLMTransientError,
    Message,
)


# ---------------------------------------------------------------- config


@dataclass(frozen=True)
class ProviderConfig:
    """Cấu hình một provider. api_key_env là TÊN biến môi trường, không phải giá trị."""

    name: str  # "anthropic" | "openai" | "deepseek" | "opencode_zen"
    model: str
    api_key_env: str
    enabled: bool = True


@dataclass(frozen=True)
class LLMConfig:
    """Cấu hình LLM. providers theo thứ tự ưu tiên (fallback)."""

    providers: tuple[ProviderConfig, ...]


# ---------------------------------------------------------------- response


@dataclass(frozen=True)
class LLMResponse:
    """Kết quả gọi LLM. attempts ghi thứ tự đã thử."""

    text: str
    provider_name: str
    model: str
    attempts: tuple[tuple[str, str], ...]  # (provider_name, error_type_or_"ok")


# ---------------------------------------------------------------- errors (registry-level)


class LLMAllProvidersFailed(LLMError):
    """Mọi provider đều lỗi. Kèm danh sách attempts."""

    def __init__(self, attempts: tuple[tuple[str, str], ...]):
        self.attempts = attempts
        details = ", ".join(f"{name}={err}" for name, err in attempts)
        super().__init__(f"Tất cả provider lỗi: {details}")


class NoProviderConfigured(LLMError):
    """Không có provider nào enabled."""


# ---------------------------------------------------------------- client


# Registry provider factories
_PROVIDER_FACTORIES: dict[str, callable] = {}


def _register_provider(name: str, factory):
    _PROVIDER_FACTORIES[name] = factory


def _build_provider(pcfg: ProviderConfig) -> LLMProvider | None:
    """Dựng provider từ config. Key không tồn tại → None (bỏ qua, không raise)."""
    api_key = os.environ.get(pcfg.api_key_env, "")
    if not api_key:
        warnings.warn(f"Provider {pcfg.name}: biến môi trường {pcfg.api_key_env} không tồn tại, bỏ qua")
        return None
    factory = _PROVIDER_FACTORIES.get(pcfg.name)
    if factory is None:
        warnings.warn(f"Provider {pcfg.name}: không có factory, bỏ qua")
        return None
    return factory(pcfg.model, api_key)


_FALLBACK_ERRORS = (LLMAuthError, LLMQuotaError, LLMTransientError)


class LLMClient:
    """Client LLM với fallback. Duyệt provider theo thứ tự config."""

    def __init__(self, config: LLMConfig):
        self._config = config

    def complete(self, messages: list[Message], **opts) -> LLMResponse:
        """Gọi LLM. Fallback theo luật. LLMBadRequestError → dừng ngay."""
        providers: list[tuple[str, LLMProvider]] = []
        for pcfg in self._config.providers:
            if not pcfg.enabled:
                continue
            provider = _build_provider(pcfg)
            if provider is not None:
                providers.append((pcfg.name, provider))

        if not providers:
            raise NoProviderConfigured("Không có provider nào enabled và có key")

        attempts: list[tuple[str, str]] = []
        for name, provider in providers:
            try:
                text = provider.complete(messages, **opts)
                attempts.append((name, "ok"))
                return LLMResponse(
                    text=text,
                    provider_name=name,
                    model=provider.model,
                    attempts=tuple(attempts),
                )
            except LLMBadRequestError as exc:
                # Lỗi của ta → dừng ngay, không fallback
                attempts.append((name, type(exc).__name__))
                raise
            except _FALLBACK_ERRORS as exc:
                attempts.append((name, type(exc).__name__))
                warnings.warn(f"Provider {name} lỗi ({type(exc).__name__}): {exc}, thử provider kế tiếp")
                continue
            except Exception as exc:
                # Lỗi không xếp loại → coi như transient
                attempts.append((name, type(exc).__name__))
                warnings.warn(f"Provider {name} lỗi không xếp loại ({type(exc).__name__}): {exc}")
                continue

        raise LLMAllProvidersFailed(tuple(attempts))
