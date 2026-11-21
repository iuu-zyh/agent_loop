"""协议自动识别契约：什么时候切、什么时候**不许**切、切完锁不锁

为什么值得单独一组测试：这段代码的失效方式全都是**静默且昂贵**的 ——
  · 该切不切 → 用户填个模型名就"AI 全哑"（09-18 的真实事故）；
  · 不该切乱切 → 同一段话被演两遍（玩家看到重影），或每次对话都白发两发请求；
  · 锁不住 → 每个回合都白跑一次识别。

上游事实（实测自 opencode zen/go，见 docs/reference/csharp-iron-laws.md docs/features/brain-liveness.md §4）：
  · union-alpha 打 /chat/completions → 500 `{"type":"error","error":{"type":"error",
    "message":"Internal server error"}}`；同模型打 /messages → 200。
  · grok-4.6 打 /chat/completions → 401 `Model grok-4.6 is not supported for format oa-compat`。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_loop.config import config_store
from agent_loop.llm import probe as probe_mod
from agent_loop.llm.anthropic_client import AnthropicHttpError, AnthropicLlmClient
from agent_loop.llm.auto_client import AutoProtocolLlmClient, protocol_mismatch
from agent_loop.llm.base import LlmResult
from agent_loop.llm.factory import PROTOCOLS, create_llm_client
from agent_loop.llm.openai_client import OpenAILlmClient
from agent_loop.llm.responses_client import OpenAIResponsesLlmClient

# ---------------------------------------------------------------- 假内芯


class FakeClient:
    """可编排的假内芯：按脚本决定"推几个 token 再抛什么错"或"成功返回什么"。"""

    def __init__(self, name, *, text="ok", fail=None, push_before_fail=0):
        self.protocol_name = name
        self.text = text
        self.fail = fail
        self.push_before_fail = push_before_fail
        self.calls = 0

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
        self.calls += 1
        for i in range(self.push_before_fail):
            if on_token is not None:
                await on_token(f"片{i}")
        if self.fail is not None:
            raise self.fail
        if on_token is not None:
            await on_token(self.text)
        return LlmResult(text=self.text, provider=self.protocol_name, model="m")


def _bare_500() -> AnthropicHttpError:
    return AnthropicHttpError(500, {"type": "error", "error": {"type": "error",
                                                              "message": "Internal server error"}},
                              "Internal server error")


def _oa_compat_401() -> AnthropicHttpError:
    return AnthropicHttpError(401, {"type": "error", "error": {
        "type": "ModelError", "message": "Model grok-4.6 is not supported for format oa-compat"}},
        "not supported for format oa-compat")


def _run(coro):
    return asyncio.run(coro)


async def _call(client, on_token=None):
    return await client.generate("s", [{"role": "user", "content": [{"type": "text", "text": "x"}]}], [],
                                 on_token=on_token)


# ---------------------------------------------------------------- 判据本身


def test_mismatch_truth_table():
    """判据要窄：只有"这条路不为这个模型服务"才算，别的错误一概不许触发切换。"""
    assert protocol_mismatch(_bare_500()) is True
    assert protocol_mismatch(_oa_compat_401()) is True
    # 429 限流 / 503 上游抖动：与协议无关，切了只会把等待与开销翻倍
    assert protocol_mismatch(AnthropicHttpError(429, {"error": {"message": "rate limited"}}, "rl")) is False
    assert protocol_mismatch(AnthropicHttpError(503, {"error": {"message": "Endpoint is unavailable."}},
                                                "unavailable")) is False
    # 5xx 但文案不是那一句：可能有别的含义，不猜
    assert protocol_mismatch(AnthropicHttpError(500, {"error": {"message": "database exploded"}},
                                                "db")) is False
    # 没有 status 的网络层异常：与协议无关
    assert protocol_mismatch(TimeoutError("首字超时：模型一直没开口")) is False
    assert protocol_mismatch(ConnectionError("connection reset")) is False
    # 400 但说的是别的事（例如 max_tokens 非法）
    assert protocol_mismatch(AnthropicHttpError(400, {"error": {"message": "max_tokens is required"}},
                                                "max_tokens")) is False


# ---------------------------------------------------------------- 切换与锁定


def test_switches_to_secondary_on_mismatch_and_locks():
    """识别成功 → 返回备用协议的结果，并**锁定**（后续请求不再试探）。"""
    a = FakeClient("openai", fail=_bare_500())
    b = FakeClient("anthropic", text="你好")
    c = AutoProtocolLlmClient([a, b], primary_name="openai")

    r = _run(_call(c))
    assert r.text == "你好" and r.provider == "anthropic"
    assert c.protocol_name == "anthropic", "识别成功后没锁定协议名"
    # 第二发只打备用协议
    _run(_call(c))
    assert (a.calls, b.calls) == (1, 2), f"锁定后还在试探：{a.calls}/{b.calls}"


def test_never_switches_after_tokens_pushed():
    """★ 已经推过内容就绝不换协议重发 ★ —— 重发会把同一段话给玩家演两遍。"""
    a = FakeClient("openai", fail=_bare_500(), push_before_fail=2)
    b = FakeClient("anthropic", text="你好")
    c = AutoProtocolLlmClient([a, b], primary_name="openai")
    seen = []

    async def on_tok(s):
        seen.append(s)

    with pytest.raises(AnthropicHttpError):
        _run(_call(c, on_token=on_tok))
    assert b.calls == 0, "已推送内容后仍然换了协议 —— 会重复展示"
    assert seen == ["片0", "片1"]


def test_non_mismatch_error_does_not_switch():
    """超时/限流不切：那不是协议问题（切了等于把玩家的等待翻倍）。"""
    a = FakeClient("openai", fail=TimeoutError("首字超时：模型一直没开口"))
    b = FakeClient("anthropic", text="你好")
    c = AutoProtocolLlmClient([a, b], primary_name="openai")
    with pytest.raises(TimeoutError):
        _run(_call(c))
    assert b.calls == 0


def test_both_fail_raises_the_original_error():
    """两条路都不通 → 报**第一条**错误。

    理由：第一条对应配置里写的那条路（auto 下即 OpenAI 兼容），文案稳定、面板的归类与建议
    也是照它给的；第二条挂作 `__cause__` 并单独记一条 WARNING —— 两条原文都能查到。
    """
    a = FakeClient("openai", fail=_bare_500())
    b = FakeClient("anthropic", fail=AnthropicHttpError(404, {"error": {"message": "nope"}}, "nope"))
    c = AutoProtocolLlmClient([a, b], primary_name="openai")
    with pytest.raises(AnthropicHttpError) as ei:
        _run(_call(c))
    assert ei.value.status_code == 500, "报的不是原始错误（会让用户看到更没用的那句话）"
    assert getattr(ei.value, "__cause__", None) is not None, "第二条错误丢失了（排障时看不到备用协议的原文）"
    assert c.protocol_name == "auto", "两条都不通却锁定了协议"


def test_failed_detection_is_not_retried_every_call():
    """识别失败后进入存疑态：不许每次对话都白发两发请求（主协议恢复成功才解除）。

    存疑只在"以定性失败收场"时置位（格式错/模型不存在/鉴权错）；若最后一条是 503 这类
    瞬时失败则不置位 —— 那条路可能只是上游在抖，留着下次再试才有机会自愈。
    """
    a = FakeClient("openai", fail=_bare_500())
    b = FakeClient("anthropic", fail=AnthropicHttpError(404, {"error": {"message": "nope"}}, "nope"))
    c = AutoProtocolLlmClient([a, b], primary_name="openai")
    for _ in range(3):
        with pytest.raises(Exception):
            _run(_call(c))
    assert (a.calls, b.calls) == (3, 1), f"存疑态没生效：{a.calls}/{b.calls}"

    # 对照：末条是**瞬时**失败（503）则不置存疑 —— 下次仍给识别一次机会
    a2 = FakeClient("openai", fail=_bare_500())
    b2 = FakeClient("anthropic", fail=AnthropicHttpError(503, {"error": {"message": "unavailable"}}, "x"))
    c2 = AutoProtocolLlmClient([a2, b2], primary_name="openai")
    for _ in range(2):
        with pytest.raises(Exception):
            _run(_call(c2))
    assert (a2.calls, b2.calls) == (2, 2), f"瞬时失败被误判成存疑：{a2.calls}/{b2.calls}"

    # 主协议恢复可用 → 解除存疑 → 下一次 mismatch 允许再识别
    a.fail = None
    _run(_call(c))
    a.fail = _bare_500()
    with pytest.raises(AnthropicHttpError):
        _run(_call(c))
    assert b.calls == 2, "主协议恢复后没有再给识别机会"


def test_explicit_protocol_wraps_without_fallback():
    """显式指定协议 = 只造那一个内芯、不做任何试探（用户要确定性）。"""
    for proto, cls in (("openai", OpenAILlmClient), ("anthropic", AnthropicLlmClient),
                       ("responses", OpenAIResponsesLlmClient)):
        c = create_llm_client(base_url="http://x/v1", api_key="k", model="m",
                              use_config=False, protocol=proto)
        assert type(c) is cls, f"{proto} → {type(c).__name__}"
        assert c.protocol_name == proto


def test_auto_wraps_three_protocols_in_order():
    """auto 的候选顺序 = 命中概率 × 保持历史行为：openai → anthropic → responses。"""
    c = create_llm_client(base_url="http://x/v1", api_key="k", model="m", use_config=False)
    assert isinstance(c, AutoProtocolLlmClient)
    assert c.protocol_name == "auto"
    kinds = [type(x) for x in c._clients]
    assert kinds == [OpenAILlmClient, AnthropicLlmClient, OpenAIResponsesLlmClient], kinds


def test_three_protocol_chain_reaches_the_third():
    """只在第三条路（/responses）上的模型要能被找到 —— 这正是加第三条的意义。

    实测：grok-4.6 打 /chat/completions 说 `not supported for format oa-compat`，
    打 /messages 说 `not supported for format anthropic`，只有 /responses 通。
    """
    a = FakeClient("openai", fail=_oa_compat_401())
    b = FakeClient("anthropic", fail=AnthropicHttpError(
        401, {"type": "error", "error": {"type": "ModelError",
                                         "message": "Model grok-4.6 is not supported for format anthropic"}},
        "not supported for format anthropic"))
    d = FakeClient("responses", text="ok")
    c = AutoProtocolLlmClient([a, b, d], primary_name="openai")
    r = _run(_call(c))
    assert r.text == "ok" and r.provider == "responses"
    assert c.protocol_name == "responses", "第三条命中后没锁定"
    _run(_call(c))
    assert (a.calls, b.calls, d.calls) == (1, 1, 2), "锁定后还在试探"


def test_unknown_protocol_falls_back_to_auto_not_crash():
    """拼错的 protocol 不该让 mod 起不来：降级成 auto 并把实情写进日志。"""
    c = create_llm_client(base_url="http://x/v1", api_key="k", model="m",
                          use_config=False, protocol="httpz")
    assert isinstance(c, AutoProtocolLlmClient)


def test_protocol_enum_matches_config_store_whitelist():
    """工厂认的三个值必须与配置层白名单**逐字一致**（两处漂了，用户就会存进一个跑不通的值）。"""
    allowed = config_store._ENUM_KEYS[("llm", "protocol")]
    assert tuple(allowed) == tuple(PROTOCOLS)


def test_loader_defaults_match_factory_enums():
    """config_loader 的默认值必须与工厂认的值一致。

    漂开的后果是静默的：默认值写成工厂不认的字符串 → 每次启动都走"降级成 auto + 一条 WARNING"，
    没人看日志就永远发现不了。
    """
    from agent_loop.config.config_loader import DEFAULT_CONFIG

    llm = DEFAULT_CONFIG["llm"]
    assert llm["protocol"] in PROTOCOLS, llm["protocol"]
    assert isinstance(llm["max_tokens"], int) and llm["max_tokens"] > 0


def test_config_roundtrip_keeps_protocol_and_max_tokens(tmp_path):
    """面板保存会带上其它键；protocol/max_tokens 必须能在白名单里活下来（不被删、不被改写）。"""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m",
                                       "protocol": "anthropic", "max_tokens": 4096}},
                              ensure_ascii=False), encoding="utf-8")
    got = config_store.get_config(cfg)
    assert got["llm"]["protocol"] == "anthropic" and got["llm"]["max_tokens"] == 4096

    # 只提交 model（面板的常态）：另外两个键必须**原样保留**，不能被整块覆盖掉
    config_store.set_config({"llm": {"model": "union-alpha"}}, cfg)
    again = config_store.get_config(cfg)
    assert again["llm"]["protocol"] == "anthropic" and again["llm"]["max_tokens"] == 4096

    # 合法值可以改；非法值 fail-closed（不许悄悄写进一个跑不通的值）
    res = config_store.set_config({"llm": {"protocol": "openai", "max_tokens": 2048}}, cfg)
    assert "llm.protocol" in res["updated"] and "llm.max_tokens" in res["updated"]
    with pytest.raises(ValueError):
        config_store.set_config({"llm": {"protocol": "http"}}, cfg)
    # 数值键的既有政策是"越界夹取 + WARNING"（不 fail-closed），max_tokens 沿用同一条：
    # 0 会被夹到 1（Anthropic 要求正整数，没有"0 = 不限"这种语义）。
    config_store.set_config({"llm": {"max_tokens": 0}}, cfg)
    assert config_store.get_config(cfg)["llm"]["max_tokens"] == 1


# ---------------------------------------------------------------- 探针上报


class _DetectingClient(FakeClient):
    """模拟自动识别门面：**调用前**报 auto，**调用成功后**才报出真实协议。

    这一点是这条用例的全部意义所在 —— 只断言"返回值里有 protocol"是抓不到
    "在构造后取一次就完事"这种错的（那时它还是 auto）。
    """

    def __init__(self):
        super().__init__("auto", text="ok")

    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):
        r = await super().generate(system, messages, tools, on_token=on_token, on_reasoning=on_reasoning)
        self.protocol_name = "anthropic"    # 识别在**这一趟调用里**才完成
        return r


def test_probe_reports_the_protocol_actually_used(monkeypatch):
    """「测试连接」要把**实际用上的**协议报出来 —— 面板里模型名一换就成功时，这就是"为什么"。"""
    fake = _DetectingClient()

    def _fake_factory(**kwargs):
        assert kwargs.get("protocol") is None or kwargs.get("protocol") == "auto"
        return fake

    monkeypatch.setattr("agent_loop.llm.factory.create_llm_client", _fake_factory)
    r = _run(probe_mod.probe_llm(base_url="http://x/v1", api_key="k", model="union-alpha"))
    assert r["ok"] is True and r["reply"] == "ok"
    assert r["protocol"] == "anthropic", f"探针没报协议：{r}"


def test_probe_passes_protocol_through_to_factory(monkeypatch):
    """手编过 protocol 的用户点「测试连接」时，那份选择必须被尊重（而不是一律 auto）。"""
    seen = {}

    def _fake_factory(**kwargs):
        seen.update(kwargs)
        return FakeClient("anthropic", text="ok")

    monkeypatch.setattr("agent_loop.llm.factory.create_llm_client", _fake_factory)
    r = _run(probe_mod.probe_llm(base_url="http://x/v1", api_key="k", model="m", protocol="anthropic"))
    assert seen.get("protocol") == "anthropic"
    assert r["protocol"] == "anthropic"
