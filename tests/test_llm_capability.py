"""llm/capability 能力探测 + reasoning_effort 注入门控（09-21）

覆盖：
  1. _judge 判定矩阵逐分支（纯函数）：严格网关 / 宽容网关 × 出思考 / 不出思考 / 各类失败
  2. probe_reasoning_effort 端到端（mock 三发）：三发并行、C 发走流式、缺项短路
  3. OpenAILlmClient 的 reasoning_effort 注入：extra_body 进请求体；未配置 = 零改动
  4. factory 门控（_gated_reasoning_effort 经 create_llm_client）：探测结论说了才算
  5. config_store：reasoning_effort / probe 两键的写、删、枚举拒绝
  6. 三处值域字面量（capability / config_store / C#）不许漂移的契约
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from agent_loop.config import config_store
from agent_loop.llm.base import LlmResult
from agent_loop.llm import capability
from agent_loop.llm.capability import (
    PROBE_SENTINEL,
    REASONING_EFFORTS,
    _judge,
    probe_reasoning_effort,
)

URL = "http://127.0.0.1:8788/v1"
KEY = "sk-verysecret-0123456789"
MODEL = "codebuddy-deepseek-v4.1-flash"


# ══════════════════════════════════════════════════════════════════
# ① 判定矩阵（_judge 纯函数）
# ══════════════════════════════════════════════════════════════════

def _r(ok, kind=None, chars=0):
    return {"ok": ok, "kind": kind, "reasoning_chars": chars}


def test_judge_matrix():
    """判定矩阵逐行钉死 —— 这是探测器的全部智能所在，漂一行就误配一次。

    ★ 判定顺序：**思考流出现 > B 的归类**（09-21 实测 proxy-hub 后修正）——
    网关可能把上游 400 包装成 502（server），B 的归类不可尽信，思考差分才是最强证据。
    """
    j = _judge
    # 出思考流 = supported，**无论 B 是 400 还是 502 包装还是宽容吞掉**
    assert j(_r(True), _r(False, "bad_request"), _r(True, None, 316))[0] == "supported"
    assert j(_r(True), _r(False, "server"), _r(True, None, 482))[0] == "supported", \
        "502 包装的 400 不许把实测出思考流判成 unknown（proxy-hub 实测形态）"
    assert j(_r(True), _r(True), _r(True, None, 160))[0] == "supported"
    # 不出思考：此时才轮到 B 的归类说话
    assert j(_r(True), _r(False, "bad_request"), _r(True, None, 0))[0] == "no_think"
    assert j(_r(True), _r(True), _r(True, None, 0))[0] == "unsupported"
    # C 发自己也 400 → 连 low 都被拒 → unsupported
    assert j(_r(True), _r(False, "bad_request"), _r(False, "bad_request"))[0] == "unsupported"
    # C 发网络类失败 / 基准失败 / B 与键无关且无思考 → unknown（保守）
    assert j(_r(True), _r(False, "bad_request"), _r(False, "timeout"))[0] == "unknown"
    assert j(_r(False, "auth"), _r(False, "bad_request"), _r(True, None, 5))[0] == "unknown"
    assert j(_r(True), _r(False, "server"), _r(True, None, 0))[0] == "unknown"
    for kind in ("timeout", "connection", "rate_limit", "auth", "not_found"):
        assert j(_r(True), _r(False, kind), _r(True, None, 0))[0] == "unknown", kind
    print("✓ 判定矩阵：思考流优先 + supported / no_think / unsupported / unknown 全分支")


# ══════════════════════════════════════════════════════════════════
# ② probe_reasoning_effort 端到端（mock 三发）
# ══════════════════════════════════════════════════════════════════

class _FakeLlm:
    """按 effort 分行为的假内芯：sentinel → 400；low → 出思考；None → 基准成功。"""

    def __init__(self, effort=None):
        self.effort = effort
        self.timeout = 5.0
        self.seen_streaming = None

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
        self.seen_streaming = on_token is not None or on_reasoning is not None
        if self.effort == PROBE_SENTINEL:
            e = Exception("the reasoning effort value is not supported by the current model")
            e.status_code = 400
            raise e
        reasoning = "我们需要比较 9.11 与 9.9 的大小。" if self.effort == "low" else ""
        return LlmResult(text="ok", reasoning=reasoning, model=MODEL)


@pytest.fixture
def fake_factory(monkeypatch):
    made = []

    def _make(base_url, api_key, model, headers, timeout, effort=None):
        c = _FakeLlm(effort)
        made.append(c)
        return c

    monkeypatch.setattr(capability, "_make_client", _make)
    return made


def _run(**kw):
    return asyncio.run(probe_reasoning_effort(**kw))


def test_probe_end_to_end_strict_gateway(fake_factory):
    """严格网关 + 出思考：三发并行、判定 supported、回包带指纹。"""
    r = _run(base_url=URL, api_key=KEY, model=MODEL)
    assert r["verdict"] == "supported"
    assert r["reasoning_chars"] > 0
    assert r["base_url"] == URL and r["model"] == MODEL
    assert len(fake_factory) == 3, "恰好三发：基准 / 非法档位 / low+推理题"
    # 三发的 effort 构造正确：基准不带、非法档位带哨兵、C 发 low
    assert [c.effort for c in fake_factory] == [None, PROBE_SENTINEL, "low"]
    # ★ C 发必须走流式（与真实对话 wire 同构；有的网关只在流式里给思考流）★
    assert fake_factory[2].seen_streaming is True
    assert fake_factory[0].seen_streaming is False
    print("✓ 端到端：三发并行、C 发流式、结论 supported + 指纹")


def test_probe_missing_config_short_circuits(fake_factory):
    r = _run(base_url="", api_key=KEY, model=MODEL)
    assert r["verdict"] == "unknown"
    assert fake_factory == [], "缺项时不该构造客户端"
    print("✓ 缺配置 → unknown 且不发请求")


# ══════════════════════════════════════════════════════════════════
# ③ OpenAILlmClient 的 reasoning_effort 注入
# ══════════════════════════════════════════════════════════════════

class _Completions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kw):
        self.kwargs = kw
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": None}


def _fake_sdk():
    comp = _Completions()
    return comp, types.SimpleNamespace(chat=types.SimpleNamespace(completions=comp))


def test_client_injects_reasoning_effort_via_extra_body():
    """配置了档位 → 请求体带 extra_body.reasoning_effort；大小写/空白归一。"""
    comp, sdk = _fake_sdk()
    from agent_loop.llm.openai_client import OpenAILlmClient

    c = OpenAILlmClient(client=sdk, reasoning_effort=" HIGH ")
    asyncio.run(c.generate(system="s", messages=[], tools=[]))
    assert comp.kwargs["extra_body"] == {"reasoning_effort": "high"}
    print("✓ 流式外非流式：reasoning_effort 经 extra_body 并入请求体顶层")


def test_client_without_effort_keeps_wire_identical():
    """未配置 → 请求里根本没有 extra_body 键（与旧版逐字节相同）。"""
    comp, sdk = _fake_sdk()
    from agent_loop.llm.openai_client import OpenAILlmClient

    c = OpenAILlmClient(client=sdk)
    asyncio.run(c.generate(system="s", messages=[], tools=[]))
    assert "extra_body" not in comp.kwargs
    print("✓ 未配置 reasoning_effort → 请求体零改动")


def test_client_streaming_carries_extra_body():
    """流式路径（真实对话走的路）同样带 extra_body。"""
    from agent_loop.llm.openai_client import OpenAILlmClient

    captured = {}

    class _StreamComp:
        def create(self, **kw):
            captured.update(kw)
            return iter([])   # 空流：_call_stream 立刻 END

    sdk = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_StreamComp()))
    c = OpenAILlmClient(client=sdk, reasoning_effort="max", timeout=1, idle_timeout=1)
    result = asyncio.run(c.generate(system="s", messages=[], tools=[], on_token=lambda t: None))
    assert captured.get("stream") is True
    assert captured["extra_body"] == {"reasoning_effort": "max"}
    assert result.reasoning == ""
    print("✓ 流式路径带 extra_body")


# ══════════════════════════════════════════════════════════════════
# ④ factory 门控
# ══════════════════════════════════════════════════════════════════

def _mk_cfg(tmp_path, llm_extra):
    cfg = {"llm": dict({"base_url": URL, "api_key": KEY, "model": MODEL, "protocol": "openai"},
                      **llm_extra)}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def _factory_effort(tmp_path, llm_extra):
    from agent_loop.llm.factory import create_llm_client

    client = create_llm_client(use_config=True, config_path=_mk_cfg(tmp_path, llm_extra))
    return getattr(client, "reasoning_effort", "MISSING")


def test_factory_effort_without_probe_passes_through(tmp_path):
    """config 没有 probe 块 = 手编直通（高级用户自负其责）。"""
    assert _factory_effort(tmp_path, {"reasoning_effort": "high"}) == "high"
    print("✓ 无 probe 块 → 手编直通")


def test_factory_effort_gated_by_probe_fingerprint(tmp_path):
    """probe 指纹匹配 + supported → 注入；换模型后结论过期 → 不注入。"""
    good = {"reasoning_effort": "high",
            "probe": {"verdict": "supported", "base_url": URL, "model": MODEL,
                      "reasoning_chars": 316}}
    assert _factory_effort(tmp_path, good) == "high"

    stale = {"reasoning_effort": "high",
             "probe": {"verdict": "supported", "base_url": URL, "model": "另一个模型",
                       "reasoning_chars": 316}}
    assert _factory_effort(tmp_path, stale) is None, "结论属于别的模型 → 不附加"
    print("✓ 指纹匹配注入 / 过期结论拒绝")


def test_factory_effort_rejects_bad_verdict_and_value(tmp_path):
    """verdict unsupported/unknown → 不注入；值域外的档位 → 不注入。"""
    base = {"base_url": URL, "model": MODEL}
    for verdict in ("unsupported", "unknown"):
        cfg = {"reasoning_effort": "low", "probe": dict({"verdict": verdict}, **base)}
        assert _factory_effort(tmp_path, cfg) is None, verdict
    assert _factory_effort(tmp_path, {"reasoning_effort": "ultra"}) is None, "值域外"
    assert _factory_effort(tmp_path, {}) is None, "没配 → None"
    assert _factory_effort(tmp_path, {"reasoning_effort": ""}) is None, "空串 → None"
    print("✓ 非法 verdict / 值域外 / 未配置 → 全部不注入")


# ══════════════════════════════════════════════════════════════════
# ⑤ config_store：两键的写 / 删 / 枚举拒绝
# ══════════════════════════════════════════════════════════════════

def test_store_reasoning_effort_write_and_delete(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"base_url": "u"}}), encoding="utf-8")
    # 写入 + 大小写归一
    config_store.set_config({"llm": {"reasoning_effort": "HIGH"}}, config_path=path)
    assert json.loads(path.read_text(encoding="utf-8"))["llm"]["reasoning_effort"] == "high"
    # 枚举外拒绝（fail-closed）
    with pytest.raises(ValueError):
        config_store.set_config({"llm": {"reasoning_effort": "bogus"}}, config_path=path)
    # 空值 = 删键（"不发送"，同 headers 的先例）
    config_store.set_config({"llm": {"reasoning_effort": ""}}, config_path=path)
    assert "reasoning_effort" not in json.loads(path.read_text(encoding="utf-8"))["llm"]
    print("✓ reasoning_effort：写/归一/枚举拒绝/空删键")


def test_store_probe_write_normalized_and_delete(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {}}), encoding="utf-8")
    config_store.set_config({"llm": {"probe": {
        "verdict": "supported", "base_url": URL, "model": MODEL,
        "reasoning_chars": 316, "latency_ms": 999, "why": "多余字段会被剥掉"}}},
        config_path=path)
    probe = json.loads(path.read_text(encoding="utf-8"))["llm"]["probe"]
    assert probe == {"verdict": "supported", "base_url": URL, "model": MODEL,
                     "reasoning_chars": 316}, "归一成四个键，多余字段剥除"
    with pytest.raises(ValueError):
        config_store.set_config({"llm": {"probe": {"verdict": "bogus"}}}, config_path=path)
    config_store.set_config({"llm": {"probe": None}}, config_path=path)
    assert "probe" not in json.loads(path.read_text(encoding="utf-8"))["llm"]
    print("✓ probe：归一落盘 / verdict 枚举拒绝 / None 删键")


# ══════════════════════════════════════════════════════════════════
# ⑥ 值域字面量三处同源契约（capability / config_store / C#）
# ══════════════════════════════════════════════════════════════════

def test_reasoning_effort_value_domains_are_in_sync():
    """档位枚举在 Python 探测器 / config_store / C# 保存校验三处各有一份字面量 —— 不许漂。"""
    from pathlib import Path

    assert tuple(config_store._ENUM_KEYS[("llm", "reasoning_effort")]) == REASONING_EFFORTS
    cs = (Path(__file__).resolve().parent.parent / "csharp" / "UI" / "ConfigPresenter.cs").read_text(encoding="utf-8")
    for v in REASONING_EFFORTS:
        assert '"%s"' % v in cs, f"C# 保存校验缺档位 {v!r}"
    print("✓ 值域三处同源（capability / config_store / C#）")


def test_probe_verdicts_in_sync_with_store():
    assert tuple(config_store._PROBE_VERDICTS) == capability.VERDICTS
    print("✓ verdict 值域同源（capability / config_store）")
