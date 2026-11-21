"""room_narrate —— 动作类工具落定后的**公共叙述行**（群聊）。

## 为什么要有这一层
`docs/archive/2026-09-17-群聊编排/设计定稿.md:421-437` 早就设计过它（"动作类 → 派生一条第三人称事件行广播进 `Room.log`"），
但指定文件从未落地：实际只有 4 种提案 + 关系解除会写公开行。`world_ai_action` 在
`judge_proposal` 里直接判 `pass`（理由写在那儿：C# 硬拒一切非玩家对象，群里没有"对手方"可言），
于是**有人被邀去切磋、还打了一整场，全群的账本里一个字都没有**。

切磋跨战斗之后这件事从"少一行字"变成了"少几分钟"：`_run_round` 持 `room_lock` **串行**跑成员，
一个人等战斗 = 这一轮后面的人全都不说话。不广播一句，别的成员只会以为这一轮卡死了。

## 为什么只能走"公共行"，不能走"共享工具结果"
成员的 `tool/result` 必须与他看到的 `tool/call` 成对（同一个 `tool_call_id`）。把别人的工具结果
插进他的消息流 = 一条**孤儿 tool 消息**，OpenAI/Anthropic 网关会直接拒。
所以"结果全局可见"只有一种合法形态：**一条发言行** —— 落 `Room.log`，由 `RoomFlow.deliver`
按各自游标增量投给每个成员，进各自的账本，重开窗也回放得出来。

## 但它是**旁白**：画「行动记录」行、不画气泡（09-22 用户拍板）
这行是写给**别的成员**的（别以为这一轮卡死了），不是发起人的台词 —— 而且里面引用了
**玩家的原话**，画成他的气泡就是双重错位。所以它走 `narration=True`：
直播帧带 `narration:true` → `ChatWindow.AppendActionRow`（工具返回结果同款折叠行）；
重开窗走 `project_room_history` 的 `tool_result` 条目（`Room.Utterance.narration`），**重开窗也还在**。
**注意与提案/应下/解除那几条叙述行区分**：那几条仍走气泡（"当众做的事"，挂发起人名下是刻意的）。

## 本文件保持纯函数
只吃 `res`（bridge 返回的那份 dict）吐字符串，不碰 Room、不发 WS —— 与
`BubbleTextCore` / `PropsSigilCore` 同一套路：判据单独可测，接线留在 `room_flow`。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _said_lines(data: Dict[str, Any]) -> list:
    """战后台词 → `名字：「原话」` 片段（**原文一字不改**）。

    刻意不判"谁赢了"：那几句台词出自 `RoleLogLocal` 的语句池（带 condition/group 与
    `{call|B|A}` 占位，措辞随人设与关系变），拿关键词去匹配迟早撞上
    「侥幸罢了！…」这种既不认输也不直说的那一句。原话 + 归属摆出来，成员与模型都读得懂。
    """
    out = []
    lines = data.get("lines")
    npc = str(data.get("target") or "")
    if not isinstance(lines, list):
        return out
    for it in lines:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        # ★09-23：`lines` 是段的单流，`kind=option` 的是**玩家选择**★ —— 不写成「有人：「…」」
        #   （那是把选择当成台词，还挂到判不出的头上），照实写成「X选择了：「…」」。
        if str(it.get("kind") or "") == "option":
            picker = str(it.get("name") or data.get("player") or "玩家")
            out.append(f"{picker}选择了：「{text}」")
            continue
        who = str(it.get("who") or "")
        if who == "player":
            name = str(it.get("name") or data.get("player") or "玩家")
        elif who == "npc":
            name = str(it.get("name") or npc or "对方")
        else:
            name = "有人"          # 判不出就明写，绝不猜（归属猜反，胜负就安错了人头）
        out.append(f"{name}：「{text}」")
    return out


def battle_line(speaker: str, res: Optional[Dict[str, Any]]) -> Optional[str]:
    """战斗类动作（spar / attack）的公开叙述行。返回 None = 这一行**不该写**。

    不该写的两种：
      · 工具失败（含 C# 判"异地"直接回绝）—— 没发生的事不进公共流水，与提案的
        accept/break 两条收尾同一规约（`room_flow._make_accept_settle` 同理）；
      · 玩家没选就关了窗（`accepted` 缺失）—— 那是他与那个 NPC 之间的事，替他宣告不合适。

    `speaker` 是发起战斗的那个 NPC（这行挂在他名下，所以句式用"与某某…"而不是自报名字）。
    """
    if not isinstance(res, dict) or not res.get("success", True):
        return None
    data = res.get("data") or {}
    if not isinstance(data, dict):
        return None
    player = str(data.get("player") or "玩家")
    if str(data.get("op") or "") == "attack":
        return _attack_line(player, data)
    acc = data.get("accepted")
    if data.get("pending"):
        return f"（向{player}讨教切磋，等他回应）"
    if acc is False:
        return f"（向{player}讨教切磋，{player}没接招）"
    if acc is not True:
        return None
    said = _said_lines(data)
    if said:
        return f"（与{player}切磋了一场 —— {'　'.join(said)}）"
    if data.get("battle_finished"):
        return f"（与{player}切磋了一场，胜负未明）"
    if data.get("battle_seen"):
        return f"（与{player}动了手，结局没看清）"
    return f"（与{player}讨教切磋，没打成）"


def _attack_line(player: str, data: Dict[str, Any]) -> Optional[str]:
    """攻击那一支的叙述行。

    ★超时/没拿到结局的行**不许声称谁死谁活**★（与切磋同一规约，而攻击更要紧：这里挂着的是
    "某个成员会不会死"）。`verdict_page` = C# 收在**处置页**（那个 NPC 战败求饶、生死握在
    玩家手里还没落定）—— 这行必须把"未定"写出来，否则旁人会以为已经打完了。
    """
    if data.get("pending"):
        return f"（向{player}出手，等他应战）"
    said = _said_lines(data)
    if said:
        base = f"（与{player}动起手来 —— {'　'.join(said)}）"
        return base.replace("）", "，生死未定）") if data.get("verdict_page") else base
    if data.get("verdict_page"):
        return f"（与{player}动起手来，落在下风由他处置，生死未定）"
    if data.get("battle_finished"):
        return f"（与{player}动起手来，胜负未明）"
    if data.get("battle_seen"):
        return f"（与{player}动了手，结局没看清）"
    return f"（向{player}出手，没打成）"
