"""图片输入与能力判定测试（无网络依赖）

覆盖：
  1. image 块序列化：canonical_to_openai 转 OpenAI 兼容 image_url 多-part
  2. 图片与文本混合：image 不泄入 string content，文本正常拼接
  3. content_has_image：嵌套 list/dict 递归检测
  4. 能力判定：supports_image=False + 带图请求 → 报错；纯文本请求 → 放行
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from agent_loop import llm_adapter
from agent_loop.llm.openai_client import OpenAILlmClient


def _canonical_image(url: str) -> Dict[str, Any]:
    return llm_adapter.create_canonical_image_message(url)


def test_image_serialization_to_image_url():
    """canonical image 块 → wire 应为 image_url / multipart，不落成纯文本 string。"""
    msg = _canonical_image("https://example.com/a.png")
    oai_messages, _ = llm_adapter.canonical_to_openai("sys", [msg], None)
    user = [m for m in oai_messages if m.get("role") == "user"][-1]
    content = user["content"]
    assert isinstance(content, list), "含图消息应为多-part content 数组"
    img = [c for c in content if c.get("type") == "image_url"]
    assert img, f"应含 image_url 块, content={content}"
    assert img[0]["image_url"]["url"] == "https://example.com/a.png"
    print("✓ image 序列化：image 块 → image_url multipart")


def test_mixed_text_and_image():
    """同消息混合文本+图片：image 不泄入 string，文本保留。"""
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "描述这张图"},
            {"type": "image", "url": "https://example.com/b.png"},
        ],
        "id": "x1",
    }
    oai_messages, _ = llm_adapter.canonical_to_openai("sys", [msg], None)
    user = oai_messages[-1]
    content = user["content"]
    assert isinstance(content, list)
    texts = [c["text"] for c in content if c.get("type") == "text"]
    images = [c for c in content if c.get("type") == "image_url"]
    assert texts == ["描述这张图"], "文本块应保留"
    assert len(images) == 1, "应恰好一张图"
    joined = "\n".join(texts)
    assert "https://example.com/b.png" not in joined, "image url 不应泄入纯文本 string content"
    print("✓ 混合消息：image 独立成块，文本不串图")


def test_join_text_blocks_skips_image():
    """非多-part 路径下 image 块不应被 _join_text_blocks 当作文本拼接。"""
    blocks = [
        {"type": "text", "text": "你好"},
        {"type": "image", "url": "data:image/png;base64,AAAA"},
        {"type": "text", "text": "再见"},
    ]
    joined = llm_adapter._join_text_blocks(blocks)  # noqa: SLF001
    assert "data:image/png" not in joined and "AAAA" not in joined, "image 不应被拼进文本"
    assert joined == "你好\n再见"
    print("✓ _join_text_blocks：跳过 image 块")


def test_content_has_image_nested():
    """递归检测嵌套结构中的 image 类型块。"""
    assert llm_adapter.content_has_image({"type": "image", "url": "u"}) is True
    assert llm_adapter.content_has_image({"type": "text", "text": "x"}) is False
    assert llm_adapter.content_has_image(
        [{"type": "text", "text": "a"}, {"type": "image", "url": "u"}]
    ) is True
    assert llm_adapter.content_has_image(
        {"type": "wrap", "content": [{"type": "image", "url": "u"}]}
    ) is True
    assert llm_adapter.content_has_image("纯字符串") is False
    print("✓ content_has_image：递归检测正常")


# ---- 便捷转换：bytes / 文件 / PIL → data URI ----


def _tiny_png_bytes() -> bytes:
    """1x1 透明 PNG 的最小字节（IHDR+IDAT+IEND），不依赖 Pillow。"""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def test_bytes_to_data_uri():
    """bytes → data URI：前缀 + base64 可还原。"""
    raw = b"\x89PNG\r\n\x1a\n\x00"
    d = llm_adapter.bytes_to_data_uri(raw)
    assert d.startswith("data:image/png;base64,")
    assert llm_adapter.base64.b64decode(d.split(",", 1)[1]) == raw
    print("✓ bytes_to_data_uri：前缀正确 + base64 可还原")


def test_image_file_to_data_uri(tmp_path=None):
    """图片文件 → data URI，扩展名推导 mime；显式 mime 覆盖。"""
    import os
    import tempfile

    root = tmp_path or tempfile.mkdtemp()
    p = os.path.join(root, "shot.png")
    with open(p, "wb") as f:
        f.write(_tiny_png_bytes())

    d1 = llm_adapter.image_file_to_data_uri(p)
    assert d1.startswith("data:image/png;base64,"), "由 .png 推导 image/png"

    d2 = llm_adapter.image_file_to_data_uri(p, mime="image/webp")
    assert d2.startswith("data:image/webp;base64,"), "显式 mime 应覆盖扩展名"
    print("✓ image_file_to_data_uri：扩展名推导 + 显式 mime 覆盖")


def test_pil_image_to_data_uri():
    """PIL 截图对象 → data URI（依赖 Pillow；缺失则跳过）。"""
    try:
        from PIL import Image

        import io

        img = Image.new("RGB", (2, 2), (255, 0, 0))
    except Exception as e:  # noqa: BLE001
        print(f"⚠ 跳过 PIL 用例（Pillow 未装）：{e}")
        return
    d = llm_adapter.pil_image_to_data_uri(img)
    assert d.startswith("data:image/png;base64,")
    # 还原校验确实是 PNG
    back = Image.open(io.BytesIO(llm_adapter.base64.b64decode(d.split(",", 1)[1])))
    assert back.size == (2, 2)
    print("✓ pil_image_to_data_uri：截图对象 → PNG data URI 可还原")


def test_data_uri_feeds_canonical_image():
    """data URI 直接进 create_canonical_image_message → 序列化为 image_url（端到端）。"""
    import os
    import tempfile

    p = os.path.join(tempfile.mkdtemp(), "shot.png")
    with open(p, "wb") as f:
        f.write(_tiny_png_bytes())
    uri = llm_adapter.image_file_to_data_uri(p)

    msg = llm_adapter.create_canonical_image_message(uri)
    oai_messages, _ = llm_adapter.canonical_to_openai("sys", [msg], None)
    assert oai_messages[-1]["content"][0]["type"] == "image_url"
    assert oai_messages[-1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    print("✓ data URI 端到端：转换 → canonical → image_url")


# ---- 统一入口 image_to_data_uri ----


def test_normalize_passthrough_url_and_data():
    """归一化：http(s) 链接与 data URI 原样透传。"""
    assert llm_adapter.image_to_data_uri("https://a.com/x.png") == "https://a.com/x.png"
    assert llm_adapter.image_to_data_uri("http://a.com/x.png") == "http://a.com/x.png"
    uri = "data:image/png;base64,AAAA"
    assert llm_adapter.image_to_data_uri(uri) == uri
    print("✓ 归一化：http/https 链接与 data URI 原样透传")


def test_normalize_any_source_via_canonical():
    """任意来源（文件路径 / bytes / 已归一字符串）都能直接进 create_canonical_image_message。"""
    import io
    import os
    import tempfile

    p = os.path.join(tempfile.mkdtemp(), "shot.png")
    with open(p, "wb") as f:
        f.write(_tiny_png_bytes())

    # 文件路径
    m1 = llm_adapter.create_canonical_image_message(p)
    assert m1["content"][0]["url"].startswith("data:image/png;base64,")

    # bytes（magic 探测成 png）
    m2 = llm_adapter.create_canonical_image_message(_tiny_png_bytes())
    assert m2["content"][0]["url"].startswith("data:image/png;base64,")

    # 字节流（BytesIO）
    m3 = llm_adapter.create_canonical_image_message(io.BytesIO(_tiny_png_bytes()))
    assert m3["content"][0]["url"].startswith("data:image/png;base64,")

    # 远程链接透传
    m4 = llm_adapter.create_canonical_image_message("https://a.com/x.png")
    assert m4["content"][0]["url"] == "https://a.com/x.png"
    print("✓ 任意来源进 canonical：文件路径 / bytes / 流 / 链接 均统一")


def test_sniff_mime():
    """magic bytes 探测：JPEG/PNG GIF 得到正确 mime。"""
    assert llm_adapter._sniff_mime(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"  # noqa: SLF001
    assert llm_adapter._sniff_mime(b"GIF89a...") == "image/gif"
    assert llm_adapter._sniff_mime(_tiny_png_bytes()) == "image/png"
    assert llm_adapter._sniff_mime(b"\x00\x01unknown") == "image/png"
    print("✓ _sniff_mime：magic bytes 探测正确，未知兜底 png")


# ---- 能力判定（离线，无需真实 OpenAI） ----


class _StubClient:
    """模拟可供 OpenAILlmClient 调用的现成客户端，拦截 wire 请求。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def chat_completions_create(self, messages: List[Dict[str, Any]], tools: Any = None) -> Dict[str, Any]:
        self.calls.append({"messages": messages, "tools": tools})
        return {"choices": [{"message": {"content": "ok", "tool_calls": None}}]}


def _run(coro) -> Any:
    return asyncio.run(coro)


def test_capability_blocks_image_when_unsupported():
    """supports_image=False 且请求带图 → 应报错而非静默丢图。"""
    stub = _StubClient()
    client = OpenAILlmClient(client=stub, model="m", supports_image=False)
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "看下图"},
                                      {"type": "image", "url": "https://x/y.png"}]}
    ]
    try:
        _run(client.generate("sys", msgs, None))
        raise AssertionError("应因 supports_image=False 而抛错")
    except RuntimeError as e:
        assert "不支持图片输入" in str(e)
    assert stub.calls == [], "报错前不应发起任何请求"
    print("✓ 能力判定：不支持却带图 → 报错且不发起请求")


def test_capability_passes_text_without_image():
    """supports_image=False 但请求纯文本 → 应放行并发起请求。"""
    stub = _StubClient()
    client = OpenAILlmClient(client=stub, model="m", supports_image=False)
    msgs = [{"role": "user", "content": "今天天气如何？"}]
    res = _run(client.generate("sys", msgs, None))
    assert stub.calls, "纯文本应发起请求"
    assert res.text == "ok"
    print("✓ 能力判定：不支持但纯文本 → 放行")


def test_capability_none_passes_image():
    """supports_image=None（未知）→ 放行带图，交由 API 自身决定。"""
    stub = _StubClient()
    client = OpenAILlmClient(client=stub, model="m", supports_image=None)
    msgs = [_canonical_image("https://x/y.png")]
    res = _run(client.generate("sys", msgs, None))
    assert res.text == "ok"
    # 确认发出的确实是 image_url 多-part
    sent = stub.calls[0]["messages"]
    assert isinstance(sent[-1]["content"], list)
    assert sent[-1]["content"][0]["type"] == "image_url"
    print("✓ 能力判定：None（未知）→ 带图放行")


if __name__ == "__main__":
    test_image_serialization_to_image_url()
    test_mixed_text_and_image()
    test_join_text_blocks_skips_image()
    test_content_has_image_nested()
    test_bytes_to_data_uri()
    test_image_file_to_data_uri()
    test_pil_image_to_data_uri()
    test_data_uri_feeds_canonical_image()
    test_normalize_passthrough_url_and_data()
    test_normalize_any_source_via_canonical()
    test_sniff_mime()
    test_capability_blocks_image_when_unsupported()
    test_capability_passes_text_without_image()
    test_capability_none_passes_image()
    print("\n图片输入测试全部通过")