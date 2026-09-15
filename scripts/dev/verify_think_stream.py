# -*- coding: utf-8 -*-
"""端到端验证：用修好的 OpenAILlmClient 直连 8123 网关跑一次流式生成，
确认 think（reasoning 字段）与 body 分流回调都到了。"""
import asyncio, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, "F:/")
from agent_loop.llm.openai_client import OpenAILlmClient


async def main():
    cli = OpenAILlmClient(base_url="http://127.0.0.1:8123/v1", api_key="free",
                          model="mimo-v2.5-free")
    think_buf, body_buf = [], []

    async def on_reasoning(tok):
        think_buf.append(tok)

    async def on_token(tok):
        body_buf.append(tok)

    result = await cli.generate("你是一个测试助手",
                                [{"role": "user", "content": "9.11 和 9.8 哪个大？先想一想再答，一句话。"}],
                                None, on_token=on_token, on_reasoning=on_reasoning)
    think = "".join(think_buf)
    body = "".join(body_buf)
    print("think 回调片段数:", len(think_buf), "| 总字数:", len(think))
    print("think 开头:", think[:60].replace(chr(10), " "))
    print("body  字数:", len(body), "| body:", body[:80])
    print("LlmResult.reasoning 字数:", len(result.reasoning or ""))
    assert think_buf, "think 流仍为空！"
    assert body_buf, "body 流为空！"
    print()
    print("== 验证通过：think 与 body 分流正常 ==")


asyncio.run(main())
