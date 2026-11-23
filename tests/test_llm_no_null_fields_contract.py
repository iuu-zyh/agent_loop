"""LLM 请求体「零 null」契约（11-23）：没有工具时 tools/tool_choice 必须整对缺席。

**用户报告**：把 mod 接到某家严格 schema 的 OpenAI 兼容网关（`https://<gateway>/provider/v1`）后，
每一轮都 `400 Bad Request`，日志里只有一行：

    {'error': {'message': 'Invalid input: expected array, received null',
               'type': 'invalid_request_error', 'param': 'tools'}}

排查结论：那不是 header 问题（`x-cli-environment` / `x-command-code-version` /
`x-project-slug` 全试过，仍 400），也不是模型名问题，而是**请求体里带了 `"tools": null`**。
OpenAI SDK 对 `tools=None` 的处理不是省略键，是**序列化成 null**；严格 schema 的网关
（实测的那家是其中之一）见到 null 直接 400，且只报第一个坏字段 —— 所以 `tool_choice: null`
跟在后面一起坏，只是被 `tools` 挡住了看不见。

真正会踩的路径全是"本轮没有工具"这种正常情形：探针（`tools=[]`）、纯聊天回合、
压缩摘要（`nonstream` + 空工具）。于是配置完全正确的人也会看到「传音法阵没接上」。

**契约（钉两件事）**：
  A. 源码里旧的裸形态绝迹：任何 `tools=oai_tools` / `tool_choice="auto" if ... else None`
     都不得回来（后者会把 None 直接送到线上）
  B. 行为：无工具 → 请求体 kwargs **不含** tools 与 tool_choice 两个键；
     有工具 → 两个键都在，且 tool_choice == "auto"
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

from agent_loop.llm.openai_client import OpenAILlmClient

ROOT = Path(__file__).resolve().parent.parent

REAL_TOOL = {
    "type": "function",
    "function": {
        "name": "give_item",
        "description": "给玩家一件物品",
        "parameters": {"type": "object", "properties": {"item_id": {"type": "string"}}},
    },
}


# ---------------------------------------------------------------- 假客户端

class _CaptureCreate:
    """记下每次 create 的 kwargs；按 stream 标志返回非流式 dict 或空流。"""

    def __init__(self):
        self.kwargs: list = []

    @property
    def chat(self):
        return SimpleNamespace(completions=self)

    def create(self, **kw):
        self.kwargs.append(kw)
        if not kw.get("stream"):
            return {"choices": [{"message": {"content": "好", "tool_calls": None}}]}
        return iter(())


def _client(capture, **kw) -> OpenAILlmClient:
    return OpenAILlmClient(base_url="http://x/v1", api_key="k", model="m",
                           client=capture, retries=0, **kw)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- A. 源码契约

def _code_only(src: str) -> str:
    """去掉 docstring 与 # 注释 —— 否则说明文字里的反例会让断言自我满足。"""
    src = re.sub(r'""".*?"""', "", src, flags=re.S)
    return re.sub(r"#.*", "", src)


def test_no_bare_tools_none_shape_in_source():
    src = _code_only((ROOT / "llm" / "openai_client.py").read_text(encoding="utf-8"))
    assert "tools=oai_tools" not in src, \
        "旧的裸形态回来了：tools=None 会被 SDK 序列化成 \"tools\": null，严格网关必然 400"
    assert 'tool_choice="auto" if' not in src, \
        "旧的裸形态回来了：无工具时它返回 None，线上就是 \"tool_choice\": null"
    assert '"tools": None' not in src and '"tool_choice": None' not in src, \
        "换皮形态：显式塞 None 与 SDK 自己序列化 null 等效，网关一样 400"
    assert "_tool_kwargs(" in src, "请求体片段的唯一出口不见了"


# ---------------------------------------------------------------- B. 行为契约

def test_stream_without_tools_omits_both_keys():
    """流式 + 空工具（探针 / 纯聊天回合）→ 两个键都不该出现在请求里。"""
    cap = _CaptureCreate()
    c = _client(cap)

    async def on_token(_t):
        pass

    _run(c.generate("sys", [{"role": "user", "content": "hi"}], [], on_token=on_token))
    kw = cap.kwargs[0]
    assert "tools" not in kw, f"空工具却发了 tools={kw.get('tools')!r}（null 会让严格网关 400）"
    assert "tool_choice" not in kw, f"空工具却发了 tool_choice={kw.get('tool_choice')!r}"
    assert kw.get("stream") is True, "本用例要走流式分支，否则测的不是同一条 wire"


def test_stream_without_tools_omits_keys_on_typeerror_fallback():
    """`stream_options` 回退分支同样不能把 null 塞回来（两条 create 调用都要干净）。"""

    class _NoStreamOptions(_CaptureCreate):
        def create(self, **kw):
            self.kwargs.append(kw)
            if "stream_options" in kw:
                raise TypeError("unexpected keyword argument 'stream_options'")
            return iter(())

    cap = _NoStreamOptions()
    c = _client(cap)

    async def on_token(_t):
        pass

    _run(c.generate("sys", [{"role": "user", "content": "hi"}], [], on_token=on_token))
    assert len(cap.kwargs) == 2, f"应当先失败一次再回退一次，实际 {len(cap.kwargs)} 次"
    for i, kw in enumerate(cap.kwargs):
        assert "tools" not in kw and "tool_choice" not in kw, \
            f"第 {i + 1} 次 create 仍带 null 字段：tools={kw.get('tools')!r} tool_choice={kw.get('tool_choice')!r}"


def test_nonstream_without_tools_omits_both_keys():
    """非流式 + 空工具（压缩摘要路径，openai_client.py:307 那条）→ 同样不许发 null。"""
    cap = _CaptureCreate()
    c = _client(cap)
    _run(c.generate("sys", [{"role": "user", "content": "hi"}], []))
    kw = cap.kwargs[0]
    assert "stream" not in kw, "本用例要走非流式分支"
    assert "tools" not in kw and "tool_choice" not in kw, \
        f"非流式空工具仍带 null：tools={kw.get('tools')!r} tool_choice={kw.get('tool_choice')!r}"


def test_tools_present_still_sent_with_auto_choice():
    """有工具时两个键都要在 —— 修 null 不能把工具也一起修没了。"""
    cap = _CaptureCreate()
    c = _client(cap)

    async def on_token(_t):
        pass

    _run(c.generate("sys", [{"role": "user", "content": "hi"}], [REAL_TOOL], on_token=on_token))
    kw = cap.kwargs[0]
    assert kw.get("tools") == [REAL_TOOL], f"工具没发出去：{kw.get('tools')!r}"
    assert kw.get("tool_choice") == "auto"
