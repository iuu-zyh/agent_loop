"""LLM Adapter — Canonical ↔ OpenAI Wire（对标 DSH dsh-llm/* 适配层）

分层职责（各司其职）：
- Session：只存 Canonical Messages（role:user/assistant + content:[{type:text|tool-call|tool-result}]），
           derive_messages() 原封返回冻结共享对象，不知 provider。
- DialogueAgent：只管 turn/preStep/step/header差分 与 Inbox/Session，不做 wire 翻译。
- LlmAdapter（本文件）：唯一拥有 Canonical ↔ provider wire 互译（OpenAI 兼容 与 Anthropic
          Messages 两路），DialogueAgent 只调它。

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


# ---------- Canonical → Anthropic（Messages API） ----------
#
# 为什么需要这一路（09-18 实机事故）：OpenCode Zen 的端点表把一部分模型**只挂在
# `POST {base}/messages`（Anthropic 协议）上**，例如 union-alpha、minimax-m3、qwen3.8-max。
# 对这类模型打 OpenAI 兼容的 `/chat/completions`，网关回的是一个**光秃秃的 500
# `{"type":"error","error":{"type":"error","message":"Internal server error"}}`**
# （同族模型 grok-4.6 才会明说 `not supported for format oa-compat`）。
# 于是"换个模型名"就变成"AI 全哑"，而玩家看到的只是一句"对方网关 5xx"。
#
# 与 OpenAI 路的**结构性差异**（照抄文档会踩的四处）：
#   ① `system` 是**顶层字段**，不是首条 role:system 消息；
#   ② `max_tokens` **必填**（缺了回 400 `max_tokens is required ...`，实测）；
#   ③ 工具结果用 `role:"user"` + `tool_result` 块（`tool_use_id` 回指），**没有** role:tool；
#   ④ 工具调用参数是**已解析的 JSON 对象**（`input`），不是字符串化的 arguments。

_ANTHROPIC_DEFAULT_MEDIA = "image/png"


def _anthropic_image_block(url: Any) -> Optional[Dict[str, Any]]:
    """Canonical image 块 → Anthropic image 块；认不出就返回 None（宁可不发，不发错）。

    Anthropic 的 image source 只接受两种：`base64`（media_type + data）与 `url`。
    本仓的图片来源经 `image_to_data_uri` 归一后**只可能是** data URI 或 http(s) 链接
    （截图/剪贴板走 base64，远程图走 url），故这里只认这两种形态。
    """
    s = str(url or "").strip()
    if s.startswith("data:"):
        head, _, data = s.partition(",")
        if not data or ";base64" not in head:
            return None                      # 非 base64 的 data URI（罕见）：丢掉，不猜
        mime = head[len("data:"):].split(";")[0].strip() or _ANTHROPIC_DEFAULT_MEDIA
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    if s.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": s}}
    return None


def _anthropic_tool_result_block(c: Dict[str, Any], fallback_id: str) -> Optional[Dict[str, Any]]:
    """Canonical tool-result 块 → Anthropic tool_result 块（带 _isError 标记另行处理）。"""
    call_id = c.get("toolCallId") or c.get("tool_call_id") or fallback_id or ""
    if not call_id:
        return None                          # 没有回指 id 的 tool_result 是坏数据：不发，别让整趟请求 400
    text = _extract_text_from_tool_result_content(c.get("content", ""))
    blk: Dict[str, Any] = {"type": "tool_result", "tool_use_id": str(call_id),
                           "content": [{"type": "text", "text": text}]}
    # 工具报错：Anthropic 有**专门**的 is_error 位（OpenAI 只能靠正文表达），别浪费
    if c.get("isError"):
        blk["is_error"] = True
    return blk


def canonical_to_anthropic(
    system: str,
    canonical_messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Canonical → Anthropic wire，返回 `(system, messages, tools)`。

    与 `canonical_to_openai` 的三处刻意不同：
    1. **同角色相邻消息会合并**（Anthropic 要求 user/assistant 交替；tool_result 与紧随其后的
       玩家发言在 Canonical 里是同角色的两条，不合并会被 API 拒）；合并时 tool_result 块**排在前**
       —— 这是 Anthropic 的硬要求（工具结果必须先于文字）。
    2. 工具结果不拆成 role:tool，而是留在 user 消息里作 `tool_result` 块（`tool_use_id` 回指）。
    3. assistant 的 tool-call 块转 `tool_use`，`arguments` 从"JSON 字符串/dict"解回 **dict**
       （Anthropic 的 input 是对象，塞字符串会被判非法）。
    """
    out: List[Dict[str, Any]] = []

    def _push(role: str, blocks: List[Dict[str, Any]]) -> None:
        """追加一条消息；同角色则合并（tool_result 块前置）。"""
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            prev = out[-1]["content"]
            prev_list = prev if isinstance(prev, list) else [{"type": "text", "text": prev}]
            if role == "user":
                merged = [b for b in blocks if b.get("type") == "tool_result"] + \
                         [b for b in prev_list if b.get("type") == "tool_result"] + \
                         [b for b in prev_list if b.get("type") != "tool_result"] + \
                         [b for b in blocks if b.get("type") != "tool_result"]
            else:
                merged = prev_list + blocks
            out[-1] = {"role": role, "content": merged}
            return
        out.append({"role": role, "content": blocks})

    for m in canonical_messages:
        role = m.get("role") or "user"
        content = m.get("content", [])

        # 1) 工具结果（Canonical 惯例：role:user + type:tool-result 块）
        if role == "user" and isinstance(content, list) and any(
            isinstance(c, dict) and c.get("type") == "tool-result" for c in content
        ):
            fallback_id = (m.get("source") or {}).get("callId", "") if isinstance(m.get("source"), dict) else ""
            results: List[Dict[str, Any]] = []
            others: List[Dict[str, Any]] = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool-result":
                    blk = _anthropic_tool_result_block(c, fallback_id)
                    if blk is not None:
                        results.append(blk)
                elif c.get("type") in ("text", "reasoning"):
                    txt = c.get("text", "")
                    if txt:                       # Anthropic 不收空文本块
                        others.append({"type": "text", "text": txt})
                elif c.get("type") == "image":
                    img = _anthropic_image_block(c.get("url"))
                    if img is not None:
                        others.append(img)
            # ★ tool_result 必须先于文字 ★：Anthropic 要求工具结果块排在同一条 user 消息的**最前**，
            #   而 Canonical 的 content 顺序不保证这一点（[text, tool-result] 是合法 Canonical）。
            #   按原序直发会被判非法 → 整趟 400（变异脚本 ⑤ 就是照这条设计的）。
            _push("user", results + others)
            continue

        # 2) assistant：text/reasoning → text 块；tool-call → tool_use 块
        if role == "assistant" and isinstance(content, list):
            blocks = []
            for blk in content:
                if not isinstance(blk, dict):
                    if str(blk).strip():
                        blocks.append({"type": "text", "text": str(blk)})
                    continue
                t = blk.get("type")
                if t in ("text", "reasoning"):
                    txt = blk.get("text", "")
                    if txt:
                        blocks.append({"type": "text", "text": txt})
                elif t == "tool-call":
                    args = blk.get("arguments", {})
                    if isinstance(args, str):
                        s = args.strip()
                        try:
                            args = json.loads(s) if s else {}
                        except Exception:
                            # 解不动就原样塞：宁可让 API 明确报错，也不静默把参数丢掉
                            args = {"_raw": s}
                    elif not isinstance(args, dict):
                        args = {"_raw": args}
                    tu: Dict[str, Any] = {"type": "tool_use", "id": blk.get("id") or _new_id(),
                                          "name": blk.get("name", ""), "input": args}
                    blocks.append(tu)
                elif blk.get("text"):
                    blocks.append({"type": "text", "text": str(blk["text"])})
            _push("assistant", blocks)
            continue

        # 3) 普通 user / 其它：文字 + 图片；有图才用块数组，纯文字用字符串（省一层结构）
        if isinstance(content, list):
            blocks = []
            for c in content:
                if not isinstance(c, dict):
                    if str(c).strip():
                        blocks.append({"type": "text", "text": str(c)})
                    continue
                t = c.get("type")
                if t in ("text", "reasoning"):
                    txt = c.get("text", "")
                    if txt:
                        blocks.append({"type": "text", "text": txt})
                elif t == "image":
                    img = _anthropic_image_block(c.get("url"))
                    if img is not None:
                        blocks.append(img)
            if blocks and all(b.get("type") == "text" for b in blocks):
                _push(role, [{"type": "text", "text": "".join(b["text"] for b in blocks)}])
            else:
                _push(role, blocks)
        elif isinstance(content, str):
            if content:
                _push(role, [{"type": "text", "text": content}])
        elif content is not None and str(content):
            _push(role, [{"type": "text", "text": str(content)}])

    anth_tools: Optional[List[Dict[str, Any]]] = None
    if tools:
        anth_tools = []
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if not isinstance(fn, dict):
                continue                          # 非 OpenAI 形状的工具定义：跳过而不是发坏结构
            item: Dict[str, Any] = {"name": fn.get("name", ""),
                                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}}
            if fn.get("description"):
                item["description"] = fn["description"]
            anth_tools.append(item)
        if not anth_tools:
            anth_tools = None
    return (system or ""), out, anth_tools


# ---------- Anthropic → Canonical ----------

def normalize_anthropic_tool_calls(blocks: Any) -> List[Dict[str, Any]]:
    """Anthropic `tool_use` 块 → Canonical `[{id, name, arguments: dict}]`。

    不自己解析 JSON：`input` 本来就是对象（非流式）或已由流式侧拼好（partial_json 累积后），
    这里只做形状归一 —— 与 `normalize_openai_tool_calls` 出口保持同一形态，
    循环机与落盘侧因此**一行都不用改**。
    """
    if not blocks:
        return []
    normalized: List[Dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        args = b.get("input")
        if isinstance(args, str):
            s = args.strip()
            try:
                args = json.loads(s) if s else {}
            except Exception:
                args = {"_raw": s}
        elif not isinstance(args, dict):
            args = {} if args is None else {"_raw": args}
        normalized.append({"id": b.get("id") or _new_id(), "name": b.get("name"), "arguments": args})
    return normalized


def parse_anthropic_response(resp: Any) -> Tuple[str, List[Dict[str, Any]], str, Optional[str]]:
    """Anthropic 响应 → `(text, tool_calls, reasoning, stop_reason)`（dict / object 皆容）。

    text 块**直接拼接**（不加分隔符）：与流式路径的 `"".join(deltas)` 逐字节一致 ——
    两条路的产出必须同形，否则"非流式（压缩）"与"流式（对话）"会出现只有一处有的换行。
    """
    if resp is None:
        return "", [], "", None
    if not isinstance(resp, dict):
        d: Dict[str, Any] = {}
        for k in ("content", "stop_reason", "usage"):
            try:
                d[k] = getattr(resp, k, None)
            except Exception:
                d[k] = None
        resp = d
    content = resp.get("content")
    stop_reason = resp.get("stop_reason")
    if isinstance(content, str):
        return content, [], "", stop_reason
    if not isinstance(content, list):
        return "", [], "", stop_reason
    texts: List[str] = []
    think: List[str] = []
    tool_blocks: List[Dict[str, Any]] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            texts.append(b.get("text") or "")
        elif t == "tool_use":
            tool_blocks.append(b)
        elif t in ("thinking", "redacted_thinking"):
            # 思考块：只用于 UI 展示（Canonical 不回传），redacted 的没有正文
            think.append(b.get("thinking") or "")
    return "".join(texts), normalize_anthropic_tool_calls(tool_blocks), "".join(think), stop_reason


# ---------- Canonical → OpenAI Responses（/responses） ----------
#
# 第三条协议路（09-18 补）：OpenCode Zen/Go 的端点数把一部分模型只挂在 `POST {base}/responses`
# 上（grok-4.6、gpt-5.6-luna、muse-spark-*）。对它们打 /chat/completions 会得到
# `Model grok-4.6 is not supported for format oa-compat`，打 /messages 会得到
# `... not supported for format anthropic` —— 网关会**报出格式名**，这正好是自动识别的判据。
#
# 与另两条路的 wire 差异（全部实测自 opencode `/zen/go/v1/responses`）：
#   ① `input` 是**扁平条目列表**：role 消息 + 独立的 `function_call` / `function_call_output`
#      **并列**（不像 OpenAI 把工具调用挂在 message.tool_calls 里）；
#   ② system 走顶层 `instructions`；
#   ③ 工具定义是**扁平**的 {type:function, name, description, parameters}（不套 "function" 键）；
#   ④ 助手历史用 `output_text` 内容块回放（实测 `input_text` 也收，但前者是规范形态）；
#   ⑤ 输出在 `output[]` 里按类型分条目：reasoning / message / function_call。

_RESP_TEXT = "input_text"
_RESP_OUT_TEXT = "output_text"


def _responses_image_block(url: Any) -> Optional[Dict[str, Any]]:
    """Canonical image → Responses 的 `input_image`（image_url 直接吃 data URI）。

    ⚠ 实测限制：opencode 的 /responses 对 grok-4.6 带图会回 400
    `Error from provider (Console Go): Upstream request failed`（不带任何细节）。
    本函数照规范形态发（真 OpenAI 端点支持），**不在本地偷偷丢图** ——
    静默丢附件比一个明确的失败更难查。要用图就选支持视觉的模型/协议。
    """
    s = str(url or "").strip()
    if s.startswith("data:image/") or s.startswith(("http://", "https://")):
        return {"type": "input_image", "image_url": s}
    return None


def canonical_to_responses(
    system: str,
    canonical_messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Canonical → Responses wire，返回 `(instructions, input_items, tools)`。

    同角色相邻的**消息**会合并（与 Anthropic 路同理），但 `function_call` /
    `function_call_output` 是**独立条目**，不参与合并，且必须保持先后顺序
    （输出的 function_call_output 必须能找到前面那个 function_call）。
    """
    items: List[Dict[str, Any]] = []

    def _push_message(role: str, blocks: List[Dict[str, Any]]) -> None:
        if not blocks:
            return
        last = items[-1] if items else None
        if isinstance(last, dict) and last.get("type") not in ("function_call", "function_call_output") \
                and last.get("role") == role:
            last["content"] = list(last.get("content") or []) + blocks
            return
        items.append({"role": role, "content": blocks})

    for m in canonical_messages:
        role = m.get("role") or "user"
        content = m.get("content", [])

        # 1) 工具结果 → 独立的 function_call_output 条目
        if role == "user" and isinstance(content, list) and any(
            isinstance(c, dict) and c.get("type") == "tool-result" for c in content
        ):
            fallback_id = (m.get("source") or {}).get("callId", "") if isinstance(m.get("source"), dict) else ""
            texts: List[Dict[str, Any]] = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool-result":
                    call_id = c.get("toolCallId") or c.get("tool_call_id") or fallback_id or ""
                    if not call_id:
                        continue          # 没有回指 id 的结果是坏数据：不发（发了整趟 400）
                    items.append({"type": "function_call_output", "call_id": str(call_id),
                                  "output": _extract_text_from_tool_result_content(c.get("content", ""))})
                elif c.get("type") in ("text", "reasoning"):
                    txt = c.get("text", "")
                    if txt:
                        texts.append({"type": _RESP_TEXT, "text": txt})
                elif c.get("type") == "image":
                    img = _responses_image_block(c.get("url"))
                    if img is not None:
                        texts.append(img)
            _push_message("user", texts)
            continue

        # 2) assistant：文字进 message，工具调用拆成独立 function_call 条目
        if role == "assistant" and isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                t = blk.get("type")
                if t in ("text", "reasoning"):
                    txt = blk.get("text", "")
                    if txt:
                        blocks.append({"type": _RESP_OUT_TEXT, "text": txt})
                elif t == "tool-call":
                    args = blk.get("arguments", "")
                    if isinstance(args, dict):
                        args = json.dumps(args, ensure_ascii=False)
                    elif args is None:
                        args = ""
                    else:
                        args = str(args)
                    _push_message("assistant", blocks)   # 文字先落，顺序不乱
                    blocks = []
                    items.append({"type": "function_call", "call_id": blk.get("id") or _new_id(),
                                  "name": blk.get("name", ""), "arguments": args})
            _push_message("assistant", blocks)
            continue

        # 3) 普通 user / 其它
        if isinstance(content, list):
            blocks = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                t = c.get("type")
                if t in ("text", "reasoning"):
                    txt = c.get("text", "")
                    if txt:
                        blocks.append({"type": _RESP_TEXT, "text": txt})
                elif t == "image":
                    img = _responses_image_block(c.get("url"))
                    if img is not None:
                        blocks.append(img)
            _push_message(role, blocks)
        elif isinstance(content, str):
            if content:
                _push_message(role, [{"type": _RESP_TEXT, "text": content}])

    resp_tools: Optional[List[Dict[str, Any]]] = None
    if tools:
        resp_tools = []
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if not isinstance(fn, dict):
                continue
            item: Dict[str, Any] = {"type": "function", "name": fn.get("name", ""),
                                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}
            if fn.get("description"):
                item["description"] = fn["description"]
            resp_tools.append(item)
        if not resp_tools:
            resp_tools = None
    return (system or ""), items, resp_tools


# ---------- OpenAI Responses → Canonical ----------

def normalize_responses_tool_calls(items: Any) -> List[Dict[str, Any]]:
    """`output[]` 里的 function_call 条目 → Canonical `[{id, name, arguments: dict}]`。

    用 `call_id` 作 id（**不是** 条目自己的 `id`）：回灌时要放进 `function_call_output.call_id`，
    实测只有 call_id 能对上。
    """
    if not items:
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") != "function_call":
            continue
        args = it.get("arguments")
        if isinstance(args, str):
            s = args.strip()
            try:
                args = json.loads(s) if s else {}
            except Exception:
                args = {"_raw": s}
        elif not isinstance(args, dict):
            args = {} if args is None else {"_raw": args}
        out.append({"id": it.get("call_id") or it.get("id") or _new_id(),
                    "name": it.get("name"), "arguments": args})
    return out


def parse_responses_response(resp: Any) -> Tuple[str, List[Dict[str, Any]], str, Optional[str]]:
    """Responses 响应 → `(text, tool_calls, reasoning, incomplete_reason)`。

    text 取自 `output[].type=="message"` 里的 `output_text` 块，**直接拼接**
    （与流式 `"".join(deltas)` 同口径）；reasoning 取自 `type=="reasoning"` 条目的
    `summary[].text`（该协议的思考是**摘要**，没有原始思维链）。
    `incomplete_reason` 非空 = 回复被截断（如 `max_output_tokens`），由客户端负责出声。
    """
    if resp is None:
        return "", [], "", None
    if not isinstance(resp, dict):
        d: Dict[str, Any] = {}
        for k in ("output", "status", "incomplete_details", "usage"):
            try:
                d[k] = getattr(resp, k, None)
            except Exception:
                d[k] = None
        resp = d
    out = resp.get("output")
    texts: List[str] = []
    think: List[str] = []
    tool_items: List[Dict[str, Any]] = []
    if isinstance(out, list):
        for it in out:
            if not isinstance(it, dict):
                continue
            t = it.get("type")
            if t == "message":
                for c in it.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "output_text":
                        texts.append(c.get("text") or "")
            elif t == "reasoning":
                for part in it.get("summary") or []:
                    if isinstance(part, dict):
                        think.append(part.get("text") or "")
            elif t == "function_call":
                tool_items.append(it)
    reason = None
    details = resp.get("incomplete_details")
    if isinstance(details, dict):
        reason = details.get("reason")
    elif resp.get("status") == "incomplete":
        reason = "incomplete"
    return "".join(texts), normalize_responses_tool_calls(tool_items), "".join(think), reason


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
