"""主动传音 / 剧情窗的**说话人精确身份**接线契约（09-28）。

## 这条链在解决什么

真机 09-28：NPC「管星贤」主动传音那一回合，模型调 `social_relation(reduce_intim)`
拿到 `{"success": false, "error": "未找到发起方 管星贤"}` —— 该回合 5 个动作工具
（social_relation / movement / world_ai_action / economy_item / item_acquire）**全部不可用**，
NPC 只能说话。

根因链（逐环有日志，见 Player.log + agent_loop.log）：

  ① 主动传音是**第三条会话入口**：不经过单聊 `open_chat`（只有那里 C# 才随请求带
     `unit_id` → Python `set_unit_id`），也不经过群聊入场 ⇒ 本会话 `agent.unit_id` 恒空；
  ② 于是工具调用**不注入** `initiator_id`（那段注入原先**只读** `self.unit_id`）——
     即便世界级名字→id 表里明明有 `管星贤 → 8DQUkF` 也没人问它；
  ③ C# 只能按名猜：`UnitIdRegistry` 那条零歧义登记恰被自愈摘除（`GetUnit(id)` 那一刻
     查不到），全图按名扫描也没覆盖到他 ⇒ `Resolve('管星贤') → 未命中`。

## 三层修复（用户 09-28 拍板：P + A；不做关系网兜底）

  · **A**：`npc_initiative` 帧带 `npc_unit_id`（C# 从**真身**读：日节拍/当面同意 =
    `FireOne` 手里的 `wub`；剧情窗 = 本页归属 NPC；排队回执 = 按存档登记表）→
    Python 会话建立时 `set_unit_id`；
  · **P**：Python 发起方注入补一条**查表回退**（与 `target/seller/buyer` **同一个**
    查表口 `_lookup_unit_id`）—— 会话没记住身份时用世界级名字→id 表；
  · 两条都保持"取不到就不注入 ⇒ C# 回落按名解析"的旧行为：**缺项/空串 = 逐字同从前**
    （老 C# 配新 Python、新 C# 配老 Python 都不炸）。

## 为什么必须有这个文件

行为测试在 `test_unit_refs.py`（P）与 `test_initiative.py`（A）—— 但它们**直接调
handler / 私有方法**，把接线删掉照样全绿（本仓已踩过这个坑：`test_world_identity_contract.py`
头注释里那条"改回去之后行为测试可能仍然全绿，而实机又坏了"）。本文件只钉**接线的位置与形状**。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WS_CS = ROOT / "csharp" / "bridge" / "WsClient.cs"
MON_CS = ROOT / "csharp" / "npc" / "NpcInitiativeMonitor.cs"
DRAMA_CS = ROOT / "csharp" / "UI" / "DramaAiOption.cs"
MODMAIN_CS = ROOT / "csharp" / "app" / "ModMain.cs"
WS_PY = ROOT / "transport" / "ws_channel.py"
AGENT_PY = ROOT / "agent" / "dialogue_agent.py"


def _strip_comments(src: str) -> str:
    """剥注释。**判接线之前必须剥** —— 注释里会写"以前是这么写的"/"为什么要这样"，
    拿它当代码会把"已删除的旧写法"或"说明了但没接线"误判成还在用。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return src


def _method_body(src: str, signature: str) -> str:
    """取 C# 方法的函数体（按大括号配平）。找不到返回空串。"""
    i = src.find(signature)
    if i < 0:
        return ""
    j = src.find("{", i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
    return ""


CS_WS = _strip_comments(WS_CS.read_text(encoding="utf-8"))
CS_MON = _strip_comments(MON_CS.read_text(encoding="utf-8"))
CS_DRAMA = _strip_comments(DRAMA_CS.read_text(encoding="utf-8"))
CS_MODMAIN = _strip_comments(MODMAIN_CS.read_text(encoding="utf-8"))
PY_WS = _strip_comments(WS_PY.read_text(encoding="utf-8"))
PY_AGENT = _strip_comments(AGENT_PY.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════
# ① C# 出口：两个帧构造器都要**能带**精确身份
# ══════════════════════════════════════════════════════════════════

def test_cs_initiative_frame_can_carry_the_unit_id():
    """`SendInitiative` / `SendGameDrama` 都要有 `unitId` 形参并把非空的写进 `npc_unit_id`。

    "非空才写"这条不是洁癖：空串与缺项对 Python 是**两件事**（缺项 = 老调用方 ⇒ 不注入
    身份 ⇒ 回落按名），写成无条件赋值会把"未知"升级成"空身份"。
    """
    for sig in ("public virtual void SendInitiative(", "public virtual void SendGameDrama("):
        body = _method_body(CS_WS, sig)
        assert body, f"找不到方法：{sig}"
        head = CS_WS[CS_WS.index(sig) : CS_WS.index("{", CS_WS.index(sig))]
        assert "string unitId = null" in head, f"{sig} 缺 unitId 形参：{head!r}"
        assert 'frame["npc_unit_id"] = unitId' in body, f"{sig} 没把身份写进帧"
        assert "if (!string.IsNullOrEmpty(unitId))" in body, f"{sig} 缺「非空才写」的守卫"
    print("✓ C# 出口：SendInitiative / SendGameDrama 都能带 npc_unit_id（非空才写）")


# ══════════════════════════════════════════════════════════════════
# ② C# 入口：四条发送路径都要**真给**身份（真身优先）
# ══════════════════════════════════════════════════════════════════

def test_cs_all_initiative_senders_pass_the_real_body_id():
    """日节拍/当面同意/异地传音 = `FireOne` 的真身；剧情窗 = 本页归属 NPC；
    排队回执 = 按存档登记表（那条路径手里没有真身，故取表；旧 id 由 C# 回验兜住）。"""
    assert CS_MON.count("SendInitiative(") == 2, \
        "`FireOne` 的两条发送路径（当面同意 / 异地传音）都要发，实际 %d 处" % CS_MON.count("SendInitiative(")
    assert CS_MON.count("unitId: DramaNativeClaim.UidOf(wub)") == 2, \
        "两处都必须传真身 unitID（`wub` 就是被触发的那个 NPC）"
    cb = _method_body(CS_DRAMA, "private static void Trigger(")
    if not cb:
        # 方法名改了也不许静默通过：直接按调用点取值（调用点 + 后续实参）
        i = CS_DRAMA.index("SendGameDrama(")
        cb = CS_DRAMA[i : i + 400]
    assert "SendGameDrama(" in cb, "剧情窗路径没发事件？"
    assert "DramaNativeClaim.UidOf(unit)" in cb, \
        "剧情窗必须带上**本页归属 NPC**的精确编号（它同样不经过 open_chat）"
    assert "UnitIdRegistry.IdOf(npc)" in CS_MODMAIN, \
        "排队回执通知器要取按存档登记表里的 id（`Notifier` 手里没有真身）"
    assert "unitId: uid" in CS_MODMAIN, "排队回执没把 id 传进帧"
    print("✓ C# 入口：日节拍/同意/传音/剧情窗/排队回执五处都带精确身份")


# ══════════════════════════════════════════════════════════════════
# ③ Python 入口：解析 → 透传 → 会话建立时写入
# ══════════════════════════════════════════════════════════════════

def test_py_dispatch_parses_and_forwards_npc_unit_id():
    """事件分发处要**解析**它，转推函数要**透传**它 —— 少一环就等于没带。"""
    assert 'str(msg.get("npc_unit_id", "") or "")' in PY_WS, "分发处没解析 npc_unit_id"
    sig = "async def _run_initiative("
    assert "npc_unit_id: str = \"\"" in PY_WS[PY_WS.index(sig) : PY_WS.index(sig) + 600], \
        "转推函数缺 npc_unit_id 形参"
    # ★ 10-16 扩参：尾部又追加了 `same_grid`（方位，可选）。原先这里写死 `"verdict, npc_unit_id)"`，
    #   加一个尾参就红 —— 那不是在防 bug，是在防扩展。真正要防的是"链上少了某一环"
    #   （解析 / 形参 / 转推），所以改成在同一段调用串里逐项点名。
    i = PY_WS.index('_run_initiative(str(msg.get("npc_id"')
    call = PY_WS[i : i + 900]
    for needle, why in (
        ("npc_unit_id", "npc_unit_id"),
        ("verdict", "verdict"),
        ("action", "action"),
        # 局部变量 `_sg` 只在末尾做 isinstance 清洗后才作为 `same_grid` 形参传进去；
        # 这里两种情况都算"带上了"（清洗表达式本身就是接线的一部分）。
        ("same_grid", "same_grid（方位文案会永远不生效）"),
    ):
        assert needle in call or (needle == "same_grid" and "_sg" in call), \
            f"转推函数没把 {why} 交给 handler"


def test_py_handle_initiative_writes_identity_but_never_clears_it():
    """`handle_initiative` 收到非空身份就写进 agent；**空值一律不写**（不许把
    "老 C# 没带" 变成"把已有身份清成空"）。"""
    i = PY_WS.index("async def handle_initiative(")
    body = PY_WS[i : PY_WS.index("\n    async def ", i + 10)]
    assert 'npc_unit_id: str = ""' in PY_WS[i : i + 600], "签名缺 npc_unit_id"
    assert "if npc_unit_id:" in body, "缺「非空才写」的守卫"
    assert "agent.set_unit_id(npc_unit_id)" in body, "没把身份写进会话"
    assert "set_unit_id(\"\")" not in body, "空值不许清掉已有身份"
    # 写在所有分支之前：本回合每一次工具调用都要用到它
    guard = body.index("if npc_unit_id:")
    assert guard < body.index("_inject_acquaintance_if_new"), \
        "身份必须在会话刚拿到时就写（后面的分支都要用）"
    print("✓ Python 入口：分发解析 → 转推 → 会话建立时写入（空值不动）")


# ══════════════════════════════════════════════════════════════════
# ④ Python 出口：发起方注入的**查表回退**（P）
# ══════════════════════════════════════════════════════════════════

def test_py_initiator_injection_falls_back_to_the_name_map():
    """发起方的精确身份在"会话没记住"时必须补查名字→id 表 —— 且**复用**
    `_lookup_unit_id`（与 target/seller/buyer 同一口径：表里没有就不注入）。

    真机那次表里**有** `管星贤 → 8DQUkF`，只是这条线从来不问它。
    """
    body = PY_AGENT[PY_AGENT.index("async def _execute_tool_calls("):]
    nxt = body.find("\n    async def ")
    if nxt > 0:
        body = body[:nxt]
    i = body.index('args["initiator"] = self.id')
    blk = body[i : i + 2000]
    assert "uid = self._unit_id()" in blk, "发起方身份的原有来源不见了"
    assert "uid = self._lookup_unit_id(self.id)" in blk, \
        "缺查表回退（P）—— 会话没记住身份时又只能按名猜了"
    assert blk.index("uid = self._lookup_unit_id(self.id)") < blk.index('args["initiator_id"] = uid'), \
        "回退必须发生在写入之前"
    print("✓ Python 出口：发起方身份有查表回退（与指人参数同口径）")
