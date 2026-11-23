"""端到端：用仓库自己的客户端打真网关，覆盖探针 / 流式聊天 / 非流式压缩三条路。

用法： python scripts/dev/e2e_gateway.py <base_url> <api_key> [model]

base_url 填到 `/v1` 那一层（例：`https://<gateway>/provider/v1`），再深会拼成
`.../chat/completions/chat/completions` → 404。model 必须是网关认的 API id。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))))

from agent_loop.llm.probe import probe_llm  # noqa: E402
from agent_loop.llm.factory import create_llm_client  # noqa: E402

KEY = sys.argv[2]
BASE = sys.argv[1].rstrip("/")
MODEL = sys.argv[3]

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "取当前时间",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]


async def main() -> None:
    print("=" * 72)
    print("① 面板「测试连接」（probe_llm，tools=[] → 会走 _tool_kwargs 的空分支）")
    r = await probe_llm(base_url=BASE, api_key=KEY, model=MODEL, timeout=60.0)
    print("   ok =", r.get("ok"), "| protocol =", r.get("protocol"),
          "| latency =", r.get("latency_ms"), "ms")
    print("   reply =", (r.get("reply") or "")[:80].replace("\n", " "))
    if r.get("error"):
        print("   error =", r["error"])

    print("=" * 72)
    print("② 流式聊天、无工具（纯对话回合）")
    c = create_llm_client(base_url=BASE, api_key=KEY, model=MODEL,
                          use_config=False, retries=0, timeout=60, timeout_nonstream=120,
                          protocol="openai", headers={"User-Agent": "OpenAI/Python 1.109.1"})
    got, think = [], []
    res = await c.generate(
        "你是一个只回一个词的助手。",
        [{"role": "user", "content": [{"type": "text", "text": "回一个词：好"}]}],
        tools=[],
        on_token=lambda t: got.append(t) or asyncio.sleep(0),
        on_reasoning=lambda t: think.append(t) or asyncio.sleep(0),
    )
    print("   正文 =", res.text[:80].replace("\n", " "), f"（{len(''.join(got))} 字流式到达）")
    print("   思考 =", (res.reasoning or "")[:60].replace("\n", " "), f"（{len(''.join(think))} 字）")
    print("   用量 =", res.usage)

    print("=" * 72)
    print("③ 流式聊天、带真工具（工具回合）")
    got2 = []
    res2 = await c.generate(
        "你在测试工具，别真的调，只回一句「收到」。",
        [{"role": "user", "content": [{"type": "text", "text": "收到吗"}]}],
        tools=TOOLS,
        on_token=lambda t: got2.append(t) or asyncio.sleep(0),
    )
    print("   正文 =", res2.text[:80].replace("\n", " "))
    print("   tool_calls =", res2.tool_calls)

    print("=" * 72)
    print("④ 非流式（上下文压缩那条路，tools=[]）")
    res3 = await c.generate(
        "把用户的话浓缩成一句摘要。",
        [{"role": "user", "content": [{"type": "text", "text": "我们约好明天午时在青云山脚碰面。"}]}],
        tools=[],
    )
    print("   摘要 =", res3.text[:100].replace("\n", " "))
    print("   用量 =", res3.usage)


if __name__ == "__main__":
    asyncio.run(main())
