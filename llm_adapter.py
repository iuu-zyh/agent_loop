"""LLM Adapter — Canonical ↔ OpenAI Wire（对标 DSH dsh-llm/* 适配层）

分层职责（各司其职）：
- Session：只存 Canonical Messages（role:user/assistant + content:[{type:text|tool-call|tool-result}]），
           derive_messages() 原封返回冻结共享对象，不知 provider。
- DialogueAgent：只管 turn/preStep/step/header差分 与 Inbox/Session，不做 wire 翻译。
- LlmAdapter（本文件）：唯一拥有 Canonical ↔ OpenAI 互译，DialogueAgent 只调它。

对标 DSH：
- dsh-llm/lib/types/message.js: createToolResultMessage → Canonical 的 tool-result 在 role:user
- dsh-agent-loop/lib/index.js: step() → buildRequest(..., session.deriveMessages()) 原封透传
- dsh-llm-deepseek/pi-ai → 适配层负责 role:tool / tool_calls 的 wire 映射
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _new_id() -> str:
    return str(uuid.uuid4())


def _extract_text_from_tool_result_content(inner: Any) -> str:
    """tool-result 里的 content 可能是 list[text-block] / str / dict → 抽纯文本"""
    if inner is None:
        return ""
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        parts: List[str] = []
        for blk in inner:
            if isinstance(blk, dict):
                if blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
                else:
                    # 兜底：非 text 块转 json
                    parts.append(blk.get("text", "") or json.dumps(blk, ensure_ascii=False))
            else:
                parts.append(str(blk))
        return "\n".join(p for p in parts if p)
    if isinstance(inner, dict):
        # 可能是 {"type":"text","text":...} 单块
        if inner.get("type") == "text":
            return inner.get("text", "")
        return json.dumps(inner, ensure_ascii=False)
    return str(inner)


def _join_text_blocks(blocks: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for c in blocks:
        if not isinstance(c, dict):
            parts.append(str(c))
            continue
        t = c.get("type")
        if t == "image":
            # 图片不是文本：跳过，不误当成文本拼进 string content
            continue
        if t in ("text", "reasoning"):
            parts.append(c.get("text", ""))
        elif t == "tool-result":
            # tool-result 不在普通 user 文本里拼，已另作 role:tool
            continue
        elif t == "tool-call":
            continue
        else:
            parts.append(c.get("text", "") or "")
    return "\n".join(p for p in parts if p)


# ---------- Canonical → OpenAI ----------

def canonical_to_openai(
    system: str,
    canonical_messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """
    将 Session.derive_messages() 的 Canonical 转 OpenAI wire。

    - system 单独作首条 role:system
    - Canonical 的 tool/result（role:user + type:tool-result）→ 拆为多条 role:tool
    - Canonical 的 assistant 含 tool-call 块 → role:assistant + tool_calls
    - 普通 user/assistant 的 text/reasoning 块 → join 为 string
    - tools 原样透传（SystemPrompt 已按 OpenAI function 形状产出）
    """
    oai_messages: List[Dict[str, Any]] = []
    if system:
        oai_messages.append({"role": "system", "content": system})

    for m in canonical_messages:
        role = m.get("role")
        content = m.get("content", [])

        # 1) tool-result 伪装的 user：逐块拆为 role:tool
        if role == "user" and isinstance(content, list) and any(
            isinstance(c, dict) and c.get("type") == "tool-result" for c in content
        ):
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool-result":
                    continue
                tool_call_id = c.get("toolCallId") or c.get("tool_call_id") or m.get("source", {}).get("callId", "")
                inner = c.get("content", "")
                # isError 保留在文本前缀（OpenAI 无 isError 字段，靠内容表达）
                is_error = c.get("isError")
                text = _extract_text_from_tool_result_content(inner)
                if is_error:
                    # 轻量标记，不破坏 json 结构
                    text = text  # 调用方可按需前缀 "[ERROR] "，此处保持原样以免破坏 bridge 的 json
                oai_messages.append(
                    {"role": "tool", "tool_call_id": tool_call_id, "content": text}
                )
            # 若同一 user 消息里既有 tool-result 又有普通 text（罕见），剩余 text 另起一条 user
            remaining_text = _join_text_blocks([c for c in content if isinstance(c, dict) and c.get("type") != "tool-result"])
            if remaining_text:
                oai_messages.append({"role": "user", "content": remaining_text})
            continue

        # 2) assistant：可能含 tool-call 块
        if role == "assistant" and isinstance(content, list):
            text_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            for blk in content:
                if not isinstance(blk, dict):
                    text_parts.append(str(blk))
                    continue
                t = blk.get("type")
                if t in ("text", "reasoning"):
                    text_parts.append(blk.get("text", ""))
                elif t == "tool-call":
                    args = blk.get("arguments", "")
                    # Canonical 里 arguments 可能是 str(JSON) 或 dict
                    if isinstance(args, dict):
                        args_str = json.dumps(args, ensure_ascii=False)
                    else:
                        args_str = str(args) if args is not None else ""
                    tool_calls.append(
                        {
                            "id": blk.get("id") or _new_id(),
                            "type": "function",
                            "function": {
                                "name": blk.get("name", ""),
                                "arguments": args_str,
                            },
                        }
                    )
                else:
                    # 未知块按文本兜底
                    if "text" in blk:
                        text_parts.append(str(blk["text"]))
            text = "\n".join(p for p in text_parts if p)
            msg: Dict[str, Any] = {"role": "assistant", "content": text if text else ""}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            oai_messages.append(msg)
            continue

        # 3) 普通 user / 其它：content list → join string；若含 image 块则转多-part 数组
        if isinstance(content, list):
            # 检测是否含 image 块（需以多-part content 数组呈现，而非 string）
            has_image = any(isinstance(c, dict) and c.get("type") == "image" for c in content)
            if has_image:
                parts: List[Dict[str, Any]] = []
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    t = c.get("type")
                    if t in ("text", "reasoning"):
                        txt = c.get("text", "")
                        if txt:
                            parts.append({"type": "text", "text": txt})
                    elif t == "image":
                        parts.append({"type": "image_url", "image_url": {"url": c.get("url", "")}})
                    # tool-call / tool-result 在 image 场景不参与此 user 消息多-part（罕见，跳过）
                oai_messages.append({"role": role or "user", "content": parts})
                continue
            text = _join_text_blocks(content) if content else ""
            # content 可能是空 list（极端），保底 ""
            oai_messages.append({"role": role or "user", "content": text})
        elif isinstance(content, str):
            oai_messages.append({"role": role or "user", "content": content})
        else:
            oai_messages.append({"role": role or "user", "content": str(content) if content is not None else ""})

    oai_tools = tools if tools else None
    return oai_messages, oai_tools


# ---------- OpenAI → Canonical ----------

def normalize_openai_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
    """
    归一 OpenAI 的 tool_calls（兼容 dict / object 两种 SDK 形状）→
    Canonical 入参形态 [{"id","name","arguments": dict}]
    """
    if not raw_tool_calls:
        return []
    normalized: List[Dict[str, Any]] = []
    for tc in raw_tool_calls:
        if isinstance(tc, dict):
            fn = tc.get("function", {}) or {}
            args_raw = fn.get("arguments", "{}")
            tc_id = tc.get("id") or _new_id()
            name = fn.get("name")
        else:
            # object 形状：tc.function.arguments
            fn = getattr(tc, "function", None)
            args_raw = getattr(fn, "arguments", "{}") if fn else "{}"
            tc_id = getattr(tc, "id", None) or _new_id()
            name = getattr(fn, "name", None) if fn else None

        # arguments 可能是 JSON 字符串或已解析 dict
        if isinstance(args_raw, str):
            args_raw = args_raw.strip()
            if not args_raw:
                args: Dict[str, Any] = {}
            else:
                try:
                    args = json.loads(args_raw)
                    if not isinstance(args, dict):
                        args = {"_raw": args}
                except Exception:
                    args = {"_raw": args_raw}
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {"_raw": str(args_raw)}

        normalized.append({"id": tc_id, "name": name, "arguments": args})
    return normalized


def parse_openai_response(resp: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """
    从 OpenAI 兼容响应中抽 (assistant_text, tool_calls_normalized)
    兼容 dict 与 object（openai 1.x）两种形状。
    """
    if resp is None:
        return "", []
    # dict 形状
    if isinstance(resp, dict):
        choices = resp.get("choices") or []
        if not choices:
            return "", []
        msg = choices[0].get("message", {}) or {}
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or msg.get("toolCalls")
        return content or "", normalize_openai_tool_calls(tool_calls)

    # object 形状
    try:
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return "", []
        first = choices[0]
        msg = getattr(first, "message", None) or {}
        # openai 对象 message 可能是对象
        if isinstance(msg, dict):
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls")
        else:
            content = getattr(msg, "content", "") or ""
            tool_calls = getattr(msg, "tool_calls", None)
        return content or "", normalize_openai_tool_calls(tool_calls)
    except Exception:
        return str(resp), []


def create_canonical_assistant_message(
    text: str,
    tool_calls: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    产 Canonical assistant 消息（对标 dsh-llm createAssistantMessage）：
    - text → {type:"text", text}
    - tool_calls → 多个 {type:"tool-call", id, name, arguments: JSON字符串}
    - 若两者皆空则返回 None（DSH 的空 assistant 不落 surface）
    """
    blocks: List[Dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for tc in tool_calls or []:
        args = tc.get("arguments", {})
        # 存为 JSON 字符串，与 BlockAssembler 约定一致
        if isinstance(args, dict):
            args_str = json.dumps(args, ensure_ascii=False)
        else:
            args_str = str(args) if args is not None else ""
        blocks.append(
            {
                "type": "tool-call",
                "id": tc.get("id") or _new_id(),
                "name": tc.get("name", ""),
                "arguments": args_str,
            }
        )
    if not blocks:
        return None
    return {
        "role": "assistant",
        "content": blocks,
        "id": _new_id(),
        "source": {"kind": "model", "provider": "openai", "model": "unknown"},
    }


def create_canonical_tool_result_message(
    call_id: str,
    result_text: str,
    is_error: bool = False,
) -> Dict[str, Any]:
    """
    产 Canonical tool-result（对标 dsh-llm createToolResultMessage）：
    role:user + source:tool + content:[{type:tool-result, toolCallId, content:[{type:text,text}], isError}]
    """
    return {
        "role": "user",
        "content": [
            {
                "type": "tool-result",
                "toolCallId": call_id,
                "content": [{"type": "text", "text": result_text}],
                "isError": bool(is_error),
            }
        ],
        "id": _new_id(),
        "source": {"kind": "tool", "callId": call_id},
    }


def create_canonical_image_message(src: Any) -> Dict[str, Any]:
    """产 Canonical user 图片消息（对标 dsh ImageBlock）：
    role:user + content:[{type:image, url}]。

    src 接受任意图片来源（http(s) 链接 / data URI / 文件路径 / bytes / 字节流 / PIL 对象），
    内部经 image_to_data_uri 归一为 http 链接或 data URI 后作为 url。"""
    return {
        "role": "user",
        "content": [{"type": "image", "url": image_to_data_uri(src)}],
        "id": _new_id(),
        "source": {"kind": "player"},
    }


def content_has_image(content: Any) -> bool:
    """递归检测一组消息 content 里是否含 image 块（供 provider 能力判定）。"""
    if isinstance(content, dict):
        if content.get("type") == "image":
            return True
        return content_has_image(content.get("content"))
    if isinstance(content, list):
        return any(content_has_image(c) for c in content)
    return False


# ---- 图片输入便捷转换：把「截图 / 文件 / bytes」转成可发给 provider 的 data URI ----
#
# 背景：OpenAI 兼容 image_url 的 url 只接受两种字符串——http(s) 链接（provider 自 fetch），
# 或 data:...;base64 data URI（provider 直接解 base64）。屏幕截图/剪贴板拿到的是内存位图，
# 不是现成 url，需先编码成标准格式二进制 → base64 → 拼 data URI，再交 create_canonical_image_message。
# PIL（Pillow）为可选依赖，仅在需要「对象/像素 → 标准图」时导入。

_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}


def _pil_fmt_to_mime(fmt: str) -> str:
    """PIL 保存格式（PNG/JPEG/WEBP...）→ 标准 mime。"""
    up = fmt.upper()
    if up == "PNG":
        return "image/png"
    if up == "JPEG":
        return "image/jpeg"
    if up == "WEBP":
        return "image/webp"
    if up == "GIF":
        return "image/gif"
    return f"image/{fmt.lower()}"


def bytes_to_data_uri(data: bytes, mime: str = "image/png") -> str:
    """bytes → data URI：`data:{mime};base64,{b64}`。这是编码共用底层。"""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def image_file_to_data_uri(path, mime: Optional[str] = None) -> str:
    """读取图片文件 → data URI。mime 缺省按扩展名推断，未知扩展兜底 application/octet-stream。

    例：image_file_to_data_uri("shot.png")
        → "data:image/png;base64,iVBOR..."  直接可用作 create_canonical_image_message 的 url
    """
    p = Path(path)
    if mime is None:
        mime = _EXT_MIME.get(p.suffix.lower(), "application/octet-stream")
    return bytes_to_data_uri(p.read_bytes(), mime)


def pil_image_to_data_uri(pil_img: Any, fmt: str = "PNG") -> str:
    """把 PIL/Image 对象（含截图位图、剪贴板图像、RGBA 像素）编码为 data URI。

    依赖 Pillow（可从截图像素得到的对象）。fmt 决定编码格式与 mime，默认 PNG。
    例：pil_image_to_data_uri(ImageGrab.grab())  # 屏幕截图 → data URI
    """
    from io import BytesIO

    buf = BytesIO()
    pil_img.save(buf, format=fmt)
    return bytes_to_data_uri(buf.getvalue(), _pil_fmt_to_mime(fmt))


def _sniff_mime(data: bytes) -> str:
    """用 magic bytes 探测常见图片格式；未知兜底 image/png。用于 bytes/流来源自动选 mime。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/png"


def image_to_data_uri(src: Any, *, mime: Optional[str] = None, fmt: str = "PNG") -> str:
    """统一图片输入门面：任意来源 → 可直接填 image_url.url 的字符串。

    归一化语义（按输入类型分流）：
      - http(s) 链接 / 已是 data URI 的字符串 → 原样透传（provider 自 fetch / 直接解 base64）
      - 其余字符串 → 视为文件路径读
      - bytes / bytearray → base64（mime 用 magic 字节探测，可显式 mime 覆盖）
      - 有 .read() 的字节流（BytesIO 等）→ 读后同上
      - 有 .save() 的 PIL/Image 对象 → 按 fmt 编码成标准图

    调用方无需判断「手里是 url 还是文件还是截图」，统一喂进来即可。
    """
    # 1) 已归一字符串：远程链接 / data URI 原样透传
    if isinstance(src, str):
        s = src.strip()
        if s.startswith(("http://", "https://")) or s.startswith("data:image/"):
            return s
        return image_file_to_data_uri(s, mime)
    # 2) bytes / bytearray
    if isinstance(src, (bytes, bytearray)):
        b = bytes(src)
        return bytes_to_data_uri(b, mime or _sniff_mime(b))
    # 3) 字节流（BytesIO / file-like）
    if hasattr(src, "read"):
        b = src.read()
        if isinstance(b, bytearray):
            b = bytes(b)
        return bytes_to_data_uri(b, mime or _sniff_mime(b))
    # 4) PIL/Image（有 .save 的对象，含屏幕截图 ImageGrab.grab()）
    return pil_image_to_data_uri(src, fmt)
