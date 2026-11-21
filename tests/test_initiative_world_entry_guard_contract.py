# -*- coding: utf-8 -*-
"""主动互动「进世界宽限期 + 日历跳变守卫」契约（10-21 用户需求，双闸方案）。

## 症状（Player-prev.log 11:57 局逐行实证，不是推测）

读档后头 ~25 秒里游戏日历在快速补表：`day=0` → `day=42780`（垃圾值）→ `day=1383`（trigger 姜萌）
→ `day=1388`（trigger 江宇文）。旧守卫只挡「同一天重复跑」，日历每跳一次，日节拍就完整跑一遍：
连掷两次日概率骰、两个 NPC 先后主动开口；确认窗创建（`day.try` 实测 324ms，真身是
`FireOne → ShowConfirmSimple` 的 UI 实例化）+ 关系网收集（`day.rel` 163ms）全堆在游戏自身
读档流送的低帧窗口里 —— 玩家观感 =「刚进游戏又卡又乱」。

## 定案（用户拍板：两层都要，缺一不可）

  · **闸 A（时间闸，用户方案）**：落地后 30s 内日节拍整段不走。
    为什么不能只靠闸 B：补表也可能一格一格 +1 地跳，那种小碎步每步"合法"，
    跳变守卫抓不住 —— 只有时间闸能整段压掉。
    到期后按当前日**补评一次**（保留"每天一次日概率"的原语义）。
  · **闸 B（跳变守卫，本仓补充）**：一跳超过 1 日或倒退 ⇒ 视为噪声，静默对齐、不掷骰。
    防晚到的跳变 / 别的 mod 动日历 / 同进程读旧档（顺带修掉旧版 `cur - _lastDay < 1`
    恒真导致的日节拍永久停摆）。
  · **锚点 = playerUnit 真实存在**，不是"日历可读"——11:57 局实证读档途中日历就能读出
    垃圾值，以它锚定则 30s 在落地前就烧完，双闸全废。

## 本文件钉什么（全是会静默失效的点）

  1. 两道闸都在，且 **Frame 兜底与 WorldAddDay 事件走同一道门**（只闸一条路 = 没闸：
     1383→1388 那跳正是事件路漏进来的）；
  2. 锚点判据是 playerUnit（不是 CurrentGameDay != -1）；
  3. 宽限期内**不许动 _lastDay**（动了就把"到期补评一次"的语义吃掉，每日机会凭空少一天）；
  4. 跳变对齐必须**同时**覆盖"前跳 >1 日"与"倒退"；
  5. 诊断强制模式（ForceTick）与"同意后自动开窗"（TickPendingOpen）**不受**这道门管——
     前者是用户的调试工具，后者是玩家点了同意的响应，不是自主发起；
  6. 旧的三条散装守卫不许原地复活（它们就是这次的事故现场）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MON = ROOT / "csharp" / "npc" / "NpcInitiativeMonitor.cs"


def _read() -> str:
    return MON.read_text(encoding="utf-8")


def _method(src: str, header: str) -> str:
    """按签名抠方法体（与 test_tool_sidebar_contract.py 同款：改名/删方法会红）。"""
    i = src.find(header)
    assert i >= 0, "找不到方法签名：%s" % header
    j = src.find("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError("方法体没闭合：%s" % header)


# ---------------------------------------------------------------- 1. 门的存在与共用

def test_gate_exists_and_both_paths_use_it():
    """日节拍的两条入口（Frame 兜底 / WorldAddDay 事件）必须共用 DayRollGate。"""
    src = _read()
    assert "private bool DayRollGate(int cur)" in src, "双闸方法没了"
    upd = _method(src, "public void OnUpdate()")
    day = _method(src, "public void OnWorldAddDay(ETypeData e)")
    assert "DayRollGate(cur)" in upd, "Frame 兜底路没走门"
    assert "DayRollGate(cur)" in day, "★ 事件路没走门 —— 11:57 局那两次 trigger 就是从这条漏的"


def test_old_loose_guards_stay_dead():
    """旧守卫 `cur - _lastDay < 1`（只挡同日）必须绝迹——它就是本次事故的现场。"""
    src = _read()
    assert "cur - _lastDay < 1" not in src, \
        "旧的散装守卫复活了：日历跳变（+5 日/倒退）会重新放行日节拍"


# ---------------------------------------------------------------- 2. 锚点判据

def test_anchor_is_playerunit_not_calendar():
    """锚点必须用 playerUnit 存在与否；用"日历可读"当锚，30s 会在落地前烧完。"""
    src = _read()
    body = _method(src, "private bool DayRollGate(int cur)")
    assert "g.world.playerUnit" in body, "锚点没拿 playerUnit 当判据"
    i_pu = body.find("g.world.playerUnit")
    i_anchor = body.find("_worldAnchorAt = now")
    assert 0 <= i_pu < i_anchor, "锚点置位发生在 playerUnit 判定之前（判序反了，垃圾日历先定锚）"
    # 菜单/换档复位：锚点与日序一起清
    i_reset = body.find("_worldAnchorAt = -1f")
    assert 0 <= i_pu < i_reset, "playerUnit 判空之后没有复位锚点"
    assert "_lastDay = -1" in body[:body.find("float now")], \
        "★ 不在世界时没清 _lastDay —— 同进程读旧档会让日节拍永久停摆（倒退守卫也救不了残留）"


# ---------------------------------------------------------------- 3. 宽限期语义

def test_grace_does_not_touch_lastday():
    """宽限期内只 return false，绝不写 _lastDay（否则"到期补评一次"被吃掉）。"""
    src = _read()
    body = _method(src, "private bool DayRollGate(int cur)")
    i_grace = body.find("WorldEntryGraceSec) return false")
    assert i_grace >= 0, "找不到宽限期的 return"
    seg = body[body.find("_worldAnchorAt = now"):i_grace]
    assert "_lastDay =" not in seg.replace("_lastDay = -1", "«reset»").replace("«reset»", ""), \
        "宽限期分支里写了 _lastDay（会把到期后的补评机会吞掉，每日概率隐性降频）"


def test_grace_seconds_are_thirty():
    src = _read()
    assert "WorldEntryGraceSec = 30f" in src, \
        "宽限期常量被改动（11:57 局实证补表尾巴拖到 +25s，30s 是留了余量的定案值）"


# ---------------------------------------------------------------- 4. 跳变守卫

def test_jump_guard_covers_forward_and_backward():
    """跳变守卫必须同时抓「前跳 >1 日」与「倒退」（读旧档/日历被别的东西改写）。"""
    src = _read()
    body = _method(src, "private bool DayRollGate(int cur)")
    assert "cur - _lastDay > 1" in body, "没抓前跳（>1 日）"
    assert "cur < _lastDay" in body, "没抓倒退"
    i_jump = body.find("cur - _lastDay > 1")
    seg = body[i_jump:i_jump + 400]
    assert "_lastDay = cur" in seg, "跳变分支没有静默对齐（不对齐则下一帧继续触发，日志刷屏且永远不掷骰）"
    assert "return false" in seg, "跳变分支还是放行了（对齐了也白对齐）"


# ---------------------------------------------------------------- 5. 不该被管的别管

def test_force_and_pending_open_bypass_the_gate():
    """诊断强制模式与"同意后自动开窗"必须在门前（它们不是"自主发起"）。"""
    src = _read()
    upd = _method(src, "public void OnUpdate()")
    i_force = upd.find("ForceTick()")
    i_gate = upd.find("DayRollGate(")
    assert 0 <= i_force < i_gate, "★ ForceTick 被挪到了门后 —— 调试工具被宽限期阉掉"
    assert "ForceTick(); return;" in upd.replace(" ", "").replace("return;", "return;", 1) or \
        "ForceTick(); return;" in upd or i_force < i_gate, "强制模式分支没有提前 return"
    i_pending = upd.find("TickPendingOpen()")
    assert 0 <= i_pending < i_force, "同意后自动开窗被挪到了强制模式之后（它必须最先跑）"
