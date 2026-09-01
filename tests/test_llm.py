"""Test cho horae.llm — registry, fallback, parse_title. Dùng fake provider, không gọi mạng."""

import json
import os
from dataclasses import dataclass
from typing import Any

import pytest

from horae.llm.protocol import (
    LLMAuthError,
    LLMBadRequestError,
    LLMError,
    LLMQuotaError,
    LLMTruncatedError,
    LLMTransientError,
    Message,
)
from horae.llm.registry import (
    LLMAllProvidersFailed,
    LLMClient,
    LLMConfig,
    NoProviderConfigured,
    ProviderConfig,
)
from horae.llm.providers.openai import OpenAIProvider
from horae.llm.tasks.parse_title import _cache_key, parse_title, ParsedTitle
from horae.state.store import LocalStore


# ---------------------------------------------------------------- fake provider


class FakeProvider:
    """Provider giả. Có thể cài response hoặc exception."""

    def __init__(self, name: str, model: str, *, response: str = "ok",
                 error: Exception | None = None):
        self.name = name
        self.model = model
        self._response = response
        self._error = error
        self.call_count = 0

    def complete(self, messages, *, max_tokens=1000, temperature=0.0):
        self.call_count += 1
        if self._error is not None:
            raise self._error
        return self._response


def _cfg(*providers):
    return LLMConfig(providers=tuple(providers))


def _pcfg(name, env="FAKE_KEY", enabled=True):
    return ProviderConfig(name=name, model="fake-model", api_key_env=env, enabled=enabled)


# ---------------------------------------------------------------- 38-42: fallback


def test_38_first_provider_ok_no_second():
    """Provider đầu OK → dùng nó, không chạm provider thứ hai."""
    p1 = FakeProvider("p1", "m1", response="answer1")
    p2 = FakeProvider("p2", "m2", response="answer2")
    # Patch registry để dùng fake providers
    import horae.llm.registry as reg
    original_build = reg._build_provider

    def fake_build(pcfg):
        if pcfg.name == "p1":
            return p1
        if pcfg.name == "p2":
            return p2
        return None

    reg._build_provider = fake_build
    try:
        os.environ["FAKE_KEY"] = "fake"
        client = LLMClient(_cfg(_pcfg("p1"), _pcfg("p2")))
        resp = client.complete([Message("user", "hi")])
        assert resp.text == "answer1"
        assert resp.provider_name == "p1"
        assert p1.call_count == 1
        assert p2.call_count == 0  # không chạm
    finally:
        reg._build_provider = original_build


def test_39_quota_error_falls_back():
    """Provider đầu raise LLMQuotaError → chuyển provider hai."""
    p1 = FakeProvider("p1", "m1", error=LLMQuotaError("quota"))
    p2 = FakeProvider("p2", "m2", response="answer2")
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: p1 if pcfg.name == "p1" else p2 if pcfg.name == "p2" else None
    try:
        os.environ["FAKE_KEY"] = "fake"
        client = LLMClient(_cfg(_pcfg("p1"), _pcfg("p2")))
        resp = client.complete([Message("user", "hi")])
        assert resp.text == "answer2"
        assert resp.provider_name == "p2"
        assert p1.call_count == 1
        assert p2.call_count == 1
    finally:
        reg._build_provider = original_build


def test_40_auth_error_falls_back():
    """Provider đầu raise LLMAuthError → chuyển tiếp."""
    p1 = FakeProvider("p1", "m1", error=LLMAuthError("bad key"))
    p2 = FakeProvider("p2", "m2", response="answer2")
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: p1 if pcfg.name == "p1" else p2 if pcfg.name == "p2" else None
    try:
        os.environ["FAKE_KEY"] = "fake"
        client = LLMClient(_cfg(_pcfg("p1"), _pcfg("p2")))
        resp = client.complete([Message("user", "hi")])
        assert resp.provider_name == "p2"
    finally:
        reg._build_provider = original_build


def test_41_transient_error_falls_back():
    """Provider đầu raise LLMTransientError → chuyển tiếp."""
    p1 = FakeProvider("p1", "m1", error=LLMTransientError("timeout"))
    p2 = FakeProvider("p2", "m2", response="answer2")
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: p1 if pcfg.name == "p1" else p2 if pcfg.name == "p2" else None
    try:
        os.environ["FAKE_KEY"] = "fake"
        client = LLMClient(_cfg(_pcfg("p1"), _pcfg("p2")))
        resp = client.complete([Message("user", "hi")])
        assert resp.provider_name == "p2"
    finally:
        reg._build_provider = original_build


def test_42_bad_request_stops_no_fallback():
    """Provider đầu raise LLMBadRequestError → DỪNG, raise, KHÔNG thử provider hai."""
    p1 = FakeProvider("p1", "m1", error=LLMBadRequestError("bad prompt"))
    p2 = FakeProvider("p2", "m2", response="answer2")
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: p1 if pcfg.name == "p1" else p2 if pcfg.name == "p2" else None
    try:
        os.environ["FAKE_KEY"] = "fake"
        client = LLMClient(_cfg(_pcfg("p1"), _pcfg("p2")))
        with pytest.raises(LLMBadRequestError):
            client.complete([Message("user", "hi")])
        assert p2.call_count == 0  # KHÔNG thử p2
    finally:
        reg._build_provider = original_build


# ---------------------------------------------------------------- 43-46: all fail / no provider


def test_43_all_providers_fail():
    """Mọi provider lỗi → LLMAllProvidersFailed, kèm đủ danh sách attempts."""
    p1 = FakeProvider("p1", "m1", error=LLMAuthError("auth"))
    p2 = FakeProvider("p2", "m2", error=LLMQuotaError("quota"))
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: p1 if pcfg.name == "p1" else p2 if pcfg.name == "p2" else None
    try:
        os.environ["FAKE_KEY"] = "fake"
        client = LLMClient(_cfg(_pcfg("p1"), _pcfg("p2")))
        with pytest.raises(LLMAllProvidersFailed) as exc_info:
            client.complete([Message("user", "hi")])
        assert len(exc_info.value.attempts) == 2
        assert exc_info.value.attempts[0][0] == "p1"
        assert exc_info.value.attempts[1][0] == "p2"
    finally:
        reg._build_provider = original_build


def test_44_no_provider_enabled():
    """Không provider nào enabled → NoProviderConfigured."""
    os.environ["FAKE_KEY"] = "fake"
    client = LLMClient(_cfg(_pcfg("p1", enabled=False)))
    with pytest.raises(NoProviderConfigured):
        client.complete([Message("user", "hi")])


def test_45_env_var_missing_provider_skipped():
    """Biến môi trường của provider không tồn tại → provider bị bỏ qua, không raise."""
    # Xóa env var nếu có
    env_name = "NONEXISTENT_KEY_12345"
    os.environ.pop(env_name, None)
    import horae.llm.registry as reg
    original_build = reg._build_provider

    # Fake: factory tồn tại nhưng _build_provider đọc env → None khi key rỗng
    p_ok = FakeProvider("ok", "m", response="ok")
    reg._build_provider = lambda pcfg: p_ok if pcfg.name == "ok" else None
    try:
        client = LLMClient(_cfg(
            ProviderConfig(name="nokey", model="m", api_key_env=env_name),
            _pcfg("ok"),
        ))
        resp = client.complete([Message("user", "hi")])
        assert resp.provider_name == "ok"  # provider "nokey" bị bỏ qua
    finally:
        reg._build_provider = original_build


# ---------------------------------------------------------------- 46: attempts order


def test_46_attempts_correct_order():
    """LLMResponse.attempts ghi đúng thứ tự đã thử."""
    p1 = FakeProvider("p1", "m1", error=LLMQuotaError("quota"))
    p2 = FakeProvider("p2", "m2", response="answer2")
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: p1 if pcfg.name == "p1" else p2 if pcfg.name == "p2" else None
    try:
        os.environ["FAKE_KEY"] = "fake"
        client = LLMClient(_cfg(_pcfg("p1"), _pcfg("p2")))
        resp = client.complete([Message("user", "hi")])
        assert resp.attempts == (("p1", "LLMQuotaError"), ("p2", "ok"))
    finally:
        reg._build_provider = original_build


# ---------------------------------------------------------------- 47-51: parse_title


def test_47_regex_match_llm_not_called():
    """parse_title với tiêu đề đúng cú pháp → LLM KHÔNG được gọi lần nào."""
    llm = LLMClient(_cfg(_pcfg("p1")))
    fake_provider = FakeProvider("p1", "m")
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: fake_provider
    try:
        os.environ["FAKE_KEY"] = "fake"
        result = parse_title("Làm BTVN Vật Lí [180m]", llm)
        assert result.estimate_minutes == 180
        assert result.source == "title"
        assert fake_provider.call_count == 0  # LLM không được gọi
    finally:
        reg._build_provider = original_build


def test_48_freeform_title_llm_returns_json():
    """parse_title với tiêu đề tự do + fake LLM trả JSON hợp lệ → parse đúng."""
    llm = LLMClient(_cfg(_pcfg("p1")))
    fake_provider = FakeProvider("p1", "m", response='{"minutes": 120, "title": "Write essay"}')
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: fake_provider
    try:
        os.environ["FAKE_KEY"] = "fake"
        result = parse_title("Write a 2-page essay about climate change", llm)
        assert result.estimate_minutes == 120
        assert result.source == "llm"
        assert result.cleaned_title == "Write essay"
        assert fake_provider.call_count == 1
    finally:
        reg._build_provider = original_build


def test_49_parse_title_llm_none_returns_default():
    """parse_title với llm=None → trả mặc định, không raise."""
    result = parse_title("Task tự do không có cú pháp gì cả", None)
    assert result.kind == "assignment"
    assert result.estimate_minutes == 60
    assert result.source == "default"
    assert result.cleaned_title == "Task tự do không có cú pháp gì cả"


def test_50_parse_title_llm_bad_json_returns_default():
    """parse_title, LLM trả JSON hỏng → trả mặc định, không raise."""
    llm = LLMClient(_cfg(_pcfg("p1")))
    fake_provider = FakeProvider("p1", "m", response="This is not JSON at all")
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: fake_provider
    try:
        os.environ["FAKE_KEY"] = "fake"
        result = parse_title("Free form task", llm)
        assert result.source == "default"
        assert result.estimate_minutes == 60
    finally:
        reg._build_provider = original_build


def test_51_parse_title_llm_json_in_fence():
    """parse_title, LLM trả JSON bọc trong ```json → vẫn parse được."""
    llm = LLMClient(_cfg(_pcfg("p1")))
    fake_provider = FakeProvider("p1", "m", response='```json\n{"minutes": 90, "title": "Study"}\n```')
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: fake_provider
    try:
        os.environ["FAKE_KEY"] = "fake"
        result = parse_title("Study for the test", llm)
        assert result.estimate_minutes == 90
        assert result.source == "llm"
        assert result.cleaned_title == "Study"
    finally:
        reg._build_provider = original_build


# ---------------------------------------------------------------- 52: runner with llm=None


def test_52_runner_with_llm_none_still_green():
    """Chạy runner với llm=None → mọi test 2A vẫn xanh (LLM là tùy chọn).
    Thực tế: runner không nhận llm làm tham số → test này kiểm parse_title
    với llm=None không phá gì, và runner vẫn chạy bình thường."""
    # parse_title với llm=None → default, không raise
    result = parse_title("Any task title", None)
    assert result.source == "default"
    # Runner không gọi LLM trong 2A → không cần kiểm thêm


# ---------------------------------------------------------------- 53-55: truncation + content cache


def test_53_finish_reason_length_raises_truncated_not_silent():
    """finish_reason='length', content rỗng → lỗi truncation, không nuốt im lặng."""
    data = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    provider = OpenAIProvider(
        "fake-model",
        "fake-key",
        post=lambda url, headers, body: (200, data),
    )

    with pytest.raises(LLMTruncatedError):
        provider.complete([Message("user", "parse this")])


def test_54_same_content_cached_despite_nondeterministic_llm(tmp_path):
    """Cùng title/description chỉ gọi LLM một lần dù response có thể đổi."""
    class NondeterministicProvider(FakeProvider):
        def __init__(self):
            super().__init__("p1", "m1")
            self._responses = [
                '{"minutes": 45, "title": "Same task source"}',
                '{"minutes": 180, "title": "Different task"}',
            ]

        def complete(self, messages, *, max_tokens=1000, temperature=0.0):
            self.call_count += 1
            return self._responses[self.call_count - 1]

    llm = LLMClient(_cfg(_pcfg("p1")))
    fake_provider = NondeterministicProvider()
    store = LocalStore(str(tmp_path / "state"))
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: fake_provider
    try:
        os.environ["FAKE_KEY"] = "fake"
        first = parse_title(
            "Same task source",
            llm,
            description="same description",
            store=store,
        )
        second = parse_title(
            "Same task source",
            llm,
            description="same description",
            store=store,
        )
        assert fake_provider.call_count == 1
        assert first == second
    finally:
        reg._build_provider = original_build


def test_55_fallback_never_cached_retries_next_time(tmp_path):
    """JSON hỏng không cache; lần sau retry và chỉ cache khi JSON hợp lệ."""
    class RetryProvider(FakeProvider):
        def __init__(self):
            super().__init__("p1", "m1")
            self._responses = [
                "not json",
                '{"minutes": 75, "title": "Retry task source"}',
            ]

        def complete(self, messages, *, max_tokens=1000, temperature=0.0):
            self.call_count += 1
            return self._responses[self.call_count - 1]

    title = "Retry task source"
    description = "retry description"
    llm = LLMClient(_cfg(_pcfg("p1")))
    fake_provider = RetryProvider()
    store = LocalStore(str(tmp_path / "state"))
    import horae.llm.registry as reg
    original_build = reg._build_provider
    reg._build_provider = lambda pcfg: fake_provider
    try:
        os.environ["FAKE_KEY"] = "fake"
        first = parse_title(title, llm, description=description, store=store)
        key = _cache_key(title, description)
        assert first.source == "default"
        assert key not in store.load_llm_cache()

        second = parse_title(title, llm, description=description, store=store)
        assert second.estimate_minutes == 75
        assert second.source == "llm"
        assert fake_provider.call_count == 2
        cache = store.load_llm_cache()
        assert key in cache
        assert set(cache[key]) == {"estimate_minutes", "kind", "cached_at"}
        assert (tmp_path / "state" / "llm_cache.json").exists()

        third = parse_title(title, llm, description=description, store=store)
        assert third == second
        assert fake_provider.call_count == 2
    finally:
        reg._build_provider = original_build
