"""history — UI 历史投影：session 账本 → 对话 UI 时间线（只读纯函数）

各司其职：只读 session.log（不全读，只取 UI 关心的 user/assistant/tool 事实），
不碰 WS、不碰 Agent、不改账本。与 Session.derive_messages 同思路，但走原始 log
而非 surface——surface 只投影 LLM 可见消息（丢 tool/call），而对话 UI 需要
完整的「用户 → 工具调用 → 工具结果 → 回复」时间线。

用户可见性筛选（对 user/message 按 source 分类）：
- 无 source                       → user         玩家消息
- source.kind == "initiative"     → initiative   NPC 主动开口（伪 user 舞台指令不显示为玩家气泡）
- source.kind == "plugin"         → 跳过          L1 runtime context / 压缩 checkpoint（内部物）
其余类型：assistant/message 取 text 块；tool/call、tool/result 按 toolCallId 天然配对；
turn/end(reason.kind=error) → turn_error（回放时显示系统提示）。

截断规则：只保留已闭合 turn（在最后一个 turn/end 处切），再取尾部 max_turns 个。
半截 turn 不入历史——UI 端「历史重放 + 直播追加」因此天然不重复、不交叠。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .llm import think as _think
from .initiative import intent_label
from .session import collect_deleted_seqs


def _texts(content: Any) -> str:
    """拼接 content 块里的全部 text（安全容忍缺块/异形）。"""
    out: List[str] = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            out.append(str(b["text"]))
    return "".join(out)


def project_ui_history(session: Any, max_turns: Any = 10) -> Dict[str, Any]:
    """投影最近 max_turns 个完整 turn。

    **max_turns 语义（09-13）**：`None` 或 `<= 0` = **全部**（用户要求"历史要看全部的"，
    C# 侧固定传 `limit=0`）；正数 = 最近 N 个完整 turn（老行为；缺省 10 保持不变，
    向后兼容 `chat_cli`/测试/未显式传参的调用方）。

    返回 {"items":[...], "complete_turns": N}：
    - items：按账本顺序的 UI 时间线，每条含 kind/turn（/step/seq）等字段（见模块 docstring）；
    - complete_turns：账本中**未被删除**的完整 turn 总数（供 UI 判断「还有更早历史」）。
      ⚠ 全部加载时它恒等于已投影的回合数（C# 侧目前未使用该字段）。

    软删除过滤（09-12）：账本里 history/delete 注记的 seq（区间成员 ∪ drop_seqs）一律跳过。
    删除区间由完整 turn 跨度构成 ⇒ turn/start..end 一并被过滤 ⇒ 被删 turn 在时间线上整段
    消失，不会留下空 turn 壳。
    """
    try:
        _n = int(max_turns) if max_turns is not None else 0
    except Exception:
        _n = 10
    unlimited = _n <= 0
    deleted = collect_deleted_seqs(session.log)
    turns: List[Dict[str, Any]] = []          # 每个：{"turn": n, "items": [...], "closed": bool}
    cur: Dict[str, Any] = {"turn": 0, "items": [], "closed": True}
    turn_no = 0
    step_no = 0

    for ev in session.log:
        seq = ev.get("seq")
        if seq is not None and seq in deleted:
            continue
        t = ev.get("type")
        data = ev.get("data") or {}

        if t == "turn/start":
            turn_no = int(data.get("turn", turn_no + 1))
            step_no = 0
            cur = {"turn": turn_no, "items": [], "closed": False}
            turns.append(cur)
        elif t == "step/start":
            step_no = int(data.get("step", step_no + 1))
        elif t == "user/message":
            source = data.get("source") or {}
            kind = source.get("kind")
            if kind == "plugin":
                continue  # L1 runtime context / 压缩 checkpoint：内部物，不进 UI
            if kind == "initiative":
                intent = str(source.get("intent", ""))
                cur["items"].append({
                    "kind": "initiative", "turn": turn_no,
                    "intent": intent,
                    "intent_text": intent_label(intent),   # UI 分隔条用短标签（09-12）
                    "reason": str(source.get("reason", "")),
                })
                continue
            text = _texts(data.get("content"))
            if text:
                cur["items"].append({"kind": "user", "text": text, "turn": turn_no, "step": step_no, "seq": seq})
        elif t == "assistant/message":
            # 思考内容拆分：reasoning 存在则直接用；否则兜底拆 content 内嵌 <think> 标签。
            # think 独立成项（kind=think，UI 折叠展示），正文项只给 body
            data_reasoning = str(data.get("reasoning") or "")
            for b in ((data.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    txt = str(b["text"])
                    if data_reasoning:
                        _, body = _think.split_think(txt)   # 兜底剥标签（通常已干净）
                    else:
                        data_reasoning, body = _think.split_think(txt)
                    if data_reasoning:
                        cur["items"].append({"kind": "think", "text": data_reasoning, "turn": turn_no, "step": step_no, "seq": seq})
                    if body:
                        cur["items"].append({"kind": "assistant", "text": body, "turn": turn_no, "step": step_no, "seq": seq})
        elif t == "tool/call":
            try:
                args = json.loads(data.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {"raw": args}
            except Exception:
                args = {"raw": str(data.get("arguments", ""))}
            cur["items"].append({
                "kind": "tool_call", "turn": turn_no, "step": step_no,
                "call_id": str(data.get("callId", "")),
                "name": str(data.get("name", "")),
                "args": args,
                "seq": seq,
            })
        elif t == "tool/result":
            for b in ((data.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool-result":
                    cur["items"].append({
                        "kind": "tool_result", "turn": turn_no, "step": step_no,
                        "call_id": str(b.get("toolCallId", "")),
                        "text": _texts(b.get("content")),
                        "is_error": bool(b.get("isError", False)),
                        "seq": seq,
                    })
        elif t == "turn/end":
            reason = data.get("reason") or {}
            if reason.get("kind") == "error":
                cur["items"].append({
                    "kind": "turn_error", "turn": turn_no,
                    "text": str(reason.get("error") or reason.get("reason") or "回合异常"),
                })
            cur["closed"] = True

    closed = [x for x in turns if x["closed"]]
    picked = closed if unlimited else closed[-_n:]
    items: List[Dict[str, Any]] = []
    for x in picked:
        items.extend(x["items"])
    return {"items": items, "complete_turns": len(closed)}
