"""llm/probe「测试连接」测试：错误归类 / 脱敏 / 不假成功 / 表单值优先

覆盖：
  1. 配置缺项 → no_config，且**根本不构造客户端**（不发无谓请求）
  2. Echo 兜底被识破 → 报 no_config，绝不当成功（这个按钮最不能犯的错）
  3. 成功路径 → ok + reply + 耗时
  4. 各类 HTTP 状态 → kind 归类正确
  5. 400 + "MissingSessionID" → hint 必须指向 headers（本功能存在的理由）
  6. 超时 / 连接失败 / 返回 HTML → 各自归类
  7. **脱敏**：api_key 与 header 值绝不出现在回包任何字段里
  8. 回包只给 header 名字，不给值
"""
from __future__ import annotations

import asyncio

import pytest

from agent_loop.llm.base import LlmResult
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.llm.probe import classify, probe_llm

KEY = "sk-verysecret-0123456789"
SECRET_HDR = "session-token-abcdef"


class _ApiError(Exception):
    """模拟 openai SDK 的 APIStatusError：靠 `status_code` + `body` 两个属性说话。"""

    def __init__(self, message, status_code=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"error": {"message": message}}
        if code is not None:
            self.body["error"]["code"] = code


class APITimeoutError(Exception):
    """类名与 SDK 一致 —— classify 靠类名兜底，故测试也必须叫这个名字。"""


class _FakeClient:
    def __init__(self, raises=None, text="ok"):
        self.raises, self.text, self.calls = raises, text, 0

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return LlmResult(text=self.text, model="m")


@pytest.fixture
def factory(monkeypatch):
    """替掉 factory.create_llm_client，把「构造出的客户端」握在测试手里。"""
    box = {"kwargs": None, "client": None}

    def _fake(**kw):
        box["kwargs"] = kw
        return box["client"]

    monkeypatch.setattr("agent_loop.llm.factory.create_llm_client", _fake)
    return box


def _run(**kw):
    return asyncio.run(probe_llm(**kw))


# ---------------------------------------------------------------- 配置与兜底

def test_missing_config_short_circuits(factory):
    """缺项必须**在构造之前**就返回 —— 否则会拿着空 base_url 去发请求，报一堆无意义的网络错。"""
    factory["client"] = _FakeClient()
    r = _run(base_url="", api_key=KEY, model="m")
    assert r["ok"] is False and r["error"]["kind"] == "no_config"
    assert "base_url" in r["error"]["message"]
    assert factory["kwargs"] is None, "缺项时不该构造客户端"
    print("✓ 缺 base_url → no_config，且未构造客户端")


def test_all_three_missing_listed(factory):
    r = _run(base_url="", api_key="", model="")
    assert r["error"]["kind"] == "no_config"
    for f in ("base_url", "api_key", "model"):
        assert f in r["error"]["message"], f"应点名缺 {f}"
    print("✓ 三项全缺 → 逐项点名")


def test_echo_client_reported_as_failure(factory):
    """最危险的一种假成功：构造出 Echo 兜底却报 ok —— 用户会以为配好了。"""
    factory["client"] = EchoLlmClient()
    r = _run(base_url="http://x/v1", api_key=KEY, model="m")
    assert r["ok"] is False and r["error"]["kind"] == "no_config"
    assert "Echo" in r["error"]["message"]
    print("✓ Echo 兜底被识破，不假报成功")


def test_success_path(factory):
    factory["client"] = _FakeClient(text="ok")
    r = _run(base_url="http://x/v1", api_key=KEY, model="deepseek-chat")
    assert r["ok"] is True and r["reply"] == "ok"
    assert r["latency_ms"] >= 0 and r["model"] == "deepseek-chat"
    assert r["error"] is None
    print("✓ 成功路径 ok/reply/耗时 齐备")


def test_probe_uses_form_values_not_saved_config(factory):
    """use_config 必须为 False：否则改了 base_url 却测的是 config.json 里那份旧配置。"""
    factory["client"] = _FakeClient()
    _run(base_url="http://form/v1", api_key=KEY, model="m", headers={"X-A": "1"})
    kw = factory["kwargs"]
    assert kw["use_config"] is False, "必须只测表单这一份"
    assert kw["base_url"] == "http://form/v1"
    assert kw["retries"] == 0, "测试要立刻看到真错误，不重试"
    assert kw["headers"] == {"X-A": "1"}
    print("✓ 只测表单值（use_config=False, retries=0）")


# ---------------------------------------------------------------- 错误归类

@pytest.mark.parametrize("status,kind", [
    (401, "auth"),
    (403, "forbidden"),
    (404, "not_found"),
    (429, "rate_limit"),
    (400, "bad_request"),
    (422, "bad_request"),
    (500, "server"),
    (502, "server"),
])
def test_status_classification(factory, status, kind):
    factory["client"] = _FakeClient(raises=_ApiError(f"boom {status}", status_code=status))
    r = _run(base_url="http://x/v1", api_key=KEY, model="m")
    assert r["ok"] is False
    assert r["error"]["kind"] == kind, f"{status} 应归 {kind}，实为 {r['error']['kind']}"
    assert r["error"]["status"] == status
    assert r["error"]["hint"], "每类都必须有处置建议"
    print(f"✓ HTTP {status} → {kind}")


def test_bad_request_hint_points_at_headers(factory):
    """本功能存在的理由：网关缺自定义头时，必须告诉用户「去填 headers」。"""
    factory["client"] = _FakeClient(raises=_ApiError(
        "Missing session ID", status_code=400, code="MissingSessionID"))
    r = _run(base_url="http://x/v1", api_key=KEY, model="m")
    assert r["error"]["kind"] == "bad_request"
    assert "headers" in r["error"]["hint"], "hint 必须指向 headers 那一行"
    # 原文与业务码分两个字段带回（C# 那边会把 code 一起显示），故这里分别断言
    assert r["error"]["message"] == "Missing session ID"
    assert r["error"]["code"] == "MissingSessionID"
    print("✓ 400 MissingSessionID → hint 指向 headers")


def test_bad_request_model_not_found_hint(factory):
    """同样是 400，但原文说的是模型不存在 —— hint 该换一句，而不是继续劝人填 headers。"""
    factory["client"] = _FakeClient(raises=_ApiError(
        "The model `gpt-9` does not exist", status_code=400, code="model_not_found"))
    r = _run(base_url="http://x/v1", api_key=KEY, model="gpt-9")
    assert "模型名" in r["error"]["hint"]
    assert "headers" not in r["error"]["hint"], "认得出是模型问题就不该再提 headers"
    print("✓ 400 model_not_found → hint 指向模型名而非 headers")


def test_forbidden_cloudflare_1010_hint(factory):
    factory["client"] = _FakeClient(raises=_ApiError(
        "error code: 1010", status_code=403, code="1010"))
    r = _run(base_url="http://x/v1", api_key=KEY, model="m")
    assert r["error"]["kind"] == "forbidden"
    assert "User-Agent" in r["error"]["hint"], "1010 要提示加自定义 UA"
    print("✓ 403 Cloudflare 1010 → hint 提示自定义 User-Agent")


def test_timeout_classified(factory):
    factory["client"] = _FakeClient(raises=APITimeoutError("timed out"))
    r = _run(base_url="http://x/v1", api_key=KEY, model="m")
    assert r["error"]["kind"] == "timeout"
    print("✓ 超时 → timeout")


def test_connection_classified(factory):
    factory["client"] = _FakeClient(raises=ConnectionError("拒绝连接"))
    r = _run(base_url="http://x/v1", api_key=KEY, model="m")
    assert r["error"]["kind"] == "connection"
    print("✓ 连接失败 → connection")


def test_html_response_classified_as_bad_json(factory):
    """base_url 填成网页地址的典型症状。"""
    factory["client"] = _FakeClient(raises=_ApiError(
        "<!DOCTYPE html><html><head><title>404</title>", status_code=None))
    r = _run(base_url="http://x/", api_key=KEY, model="m")
    assert r["error"]["kind"] == "bad_json"
    assert "网页" in r["error"]["hint"] or "JSON" in r["error"]["hint"]
    print("✓ 返回 HTML → bad_json（提示 base_url 指向了网页）")


def test_unknown_exception_still_returns_struct(factory):
    factory["client"] = _FakeClient(raises=RuntimeError("说不清"))
    r = _run(base_url="http://x/v1", api_key=KEY, model="m")
    assert r["ok"] is False and r["error"]["kind"] == "unknown"
    assert r["error"]["hint"], "未归类也要有兜底 hint"
    print("✓ 未知异常 → unknown（仍返回结构化结果，不抛）")


def test_classify_never_raises_on_weird_exceptions():
    for exc in (Exception(), ValueError(""), KeyError("x"), BaseException("b")):
        kind, status, code, msg = classify(exc)
        assert kind and isinstance(msg, str)
    print("✓ classify 对任意异常都不抛")


# ---------------------------------------------------------------- 脱敏

def test_secrets_never_leak_into_response(factory):
    """key 与 header 值出现在对方错误原文里时，回包必须已抹掉。"""
    leaky = f"bad key {KEY} and header {SECRET_HDR}"
    factory["client"] = _FakeClient(raises=_ApiError(leaky, status_code=401))
    r = _run(base_url="http://x/v1", api_key=KEY, model="m",
             headers={"x-opencode-session": SECRET_HDR})
    blob = repr(r)
    assert KEY not in blob, "api_key 泄漏进回包"
    assert SECRET_HDR not in blob, "header 值泄漏进回包"
    assert "***" in r["error"]["message"], "应留下脱敏痕迹而不是整段消失"
    print("✓ key / header 值已脱敏，回包里找不到")


def test_response_carries_header_names_not_values(factory):
    factory["client"] = _FakeClient()
    r = _run(base_url="http://x/v1", api_key=KEY, model="m",
             headers={"x-opencode-session": SECRET_HDR, "User-Agent": "ua-xyz"})
    assert r["header_names"] == ["User-Agent", "x-opencode-session"]
    assert SECRET_HDR not in repr(r)
    print("✓ 回包只有 header 名字，没有值")


def test_short_secret_not_redacted_to_avoid_mangling():
    """长度 <6 的串不参与替换 —— 否则 "1" 这种会把正常文本打成筛子。"""
    from agent_loop.llm.probe import _redact
    assert _redact("error 1 occured", ["1"]) == "error 1 occured"
    assert _redact("error abcdef", ["abcdef"]) == "error ***"
    print("✓ 短密钥不脱敏（防误伤原文）")
