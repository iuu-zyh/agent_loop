"""境界取值 + 自身段身份契约（09-13 真机事故："他爸显示成登仙境"）。

**事故现场**（Player.log 原文，注意走的是 **unit_id 精确路径**，与重名无关）：

    [UnitLookup] Resolve('', unit_id=ECYyXU) → ②unitID精确 unitID=ECYyXU
    [Inspect] args={"unit_id":"ECYyXU","classes":["brief"],"initiator":"巩易"}
    → "realm": "登仙境"        （面板上此人明明是**结晶后期**）

**根因**：`g.conf.roleGrade.GetGradeName(pd.gradeID)` 把两个不同的东西对上了 ——
  · `RoleGrade` 表**一行 = 大境界×期×品质**（44 行 / 10 个大境界 / 3 期，`id` 1..44）；
  · `GetGradeName(Int32 grade)` 的形参名是 **`grade`（大境界号 1..10）**
    —— 同族 `GetNextGradeItem(Int32 gradeId)` 的形参名才是 `gradeId`（行号）。
  结晶后期的行号 ≈11 > 10 → 查表**越界被钳到最大档 → 登仙境**。
  低阶 NPC（行号 1~3）恰好落回炼气/筑基，于是"有时看着是对的"。

同源的第二处：**面板读 DynInt 层、brief 读裸字段**。某 NPC 声望裸值 3129、面板 3379 ——
差额 250 正好是其气运「赶尸道童+100 / 单身贵族+150」。故 brief 的数值字段全部改为 DynInt 优先。

第三处（用户同时反馈）：**L1 自身段也可能挂在同名者身上** —— 打开对话 UI 时 C# 手里
明明握着 WorldUnitBase，却只把中文名传下去，后面再按名全图猜。现在在 `ChatLauncher.OpenForUnit`
（全工程唯一"拿着真身开对话窗"的入口）把 name→unitID 钉进 `UnitLookup.Pin`。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 境界

def _realm_body() -> str:
    src = _read("UnitSnapshot.cs")
    return src[src.index("private static string RealmOf("):
               src.index("private static bool _realmLogged;")]


def test_realm_does_not_feed_rowid_into_grade_param():
    """不许再把 gradeID 直接喂给 `GetGradeName(单个参数)` —— 那是**越界钳到最高档**的成因。"""
    src = _read("UnitSnapshot.cs")
    # 旧写法：GetGradeName(gid) / GetGradeName(rowId) 直接当主要路径
    body = _realm_body()
    hits = re.findall(r"GetGradeName\(([A-Za-z_][A-Za-z0-9_]*)\)", body)
    assert hits, "RealmOf 里应当只在**最后兜底**用到单参 GetGradeName"
    # 单参调用只允许出现在**行号定位之后**的兜底档（② curGrade / ③ 旧写法），
    # 绝不能作为主路径 —— 那正是"越界钳到最高档"的成因
    i_lookup = body.index("allConfList")
    for m in re.finditer(r"GetGradeName\(([A-Za-z_][A-Za-z0-9_]*)\)", body):
        assert m.start() > i_lookup, \
            f"单参 GetGradeName 出现在行号定位之前（= 主路径），实参 {m.group(1)}"
    assert "★旧写法" in body, "最后一档必须标明可疑（越界钳制）"


def test_realm_prefers_row_lookup_with_self_check():
    """主路径必须是「行号定位 + 用 curGrade 自洽校验」，不是任何形式的猜。"""
    body = _realm_body()
    assert "allConfList" in body, "要到 RoleGrade 的行列表里定位（行号 → 行）"
    assert "curGrade" in body, "要用 dynUnitData.curGrade 做自洽判据"
    assert re.search(r"g\s*!=\s*curGrade\s*&&\s*g\s*!=\s*curGrade\s*\+\s*1", body), \
        "自洽判据：行内 grade 必须与 curGrade 对上（容忍 curGrade 0 基）"
    assert "continue;" in body, "对不上必须跳过（不采信），不能将就"
    # 行号 1 基 / 0 基两种读法都要试
    assert "for (int off = 1; off >= 0; off--)" in body, "1 基与 0 基两种下标读法都要试"


def test_realm_returns_full_name_with_phase():
    """命中行后要拼 `gradeName + phaseName`（LS 后即"结晶后期"，与面板逐字一致）。"""
    body = _realm_body()
    assert "it.gradeName" in body and "it.phaseName" in body, "行内同时给了大境界名与期名"
    assert "return gn + pn;" in body, "面板口径是「境界+期」两段拼接"


def test_realm_falls_back_safely_and_leaves_evidence():
    """校验不过 → 退 curGrade（丢期但不错人）；再不行才用旧写法，且**必须标明可疑**。"""
    body = _realm_body()
    assert body.index("allConfList") < body.index("curGrade退") < body.index("★旧写法"), \
        "三档顺序：行号命中 → curGrade 退 → 旧写法兜底"
    assert 'diag["realm_source"]' in body, "走了哪条路必须写进 diag（随 brief 回传，可现场判读）"
    assert "★行号自洽校验未过★" in body, "自洽校验失败要留日志，否则下一次还得靠猜"


def test_brief_numbers_prefer_dynint_layer():
    """brief 的数值字段必须与 stats 同口径（DynInt 优先）—— 裸字段不含气运/装备加成。"""
    src = _read("UnitSnapshot.cs")
    brief = src[src.index("// —— 自身基本面"): src.index("snap[\"hobby\"]")]
    for prop in ("beauty", "reputation", "talent", "mood"):
        assert f'SetAttr(snap, dyn, pd, "{prop}", "{prop}")' in brief, \
            f"brief.{prop} 必须走 SetAttr（DynInt 优先），不许再直接读 pd.{prop}"
        assert f"pd.{prop}" not in brief, f"brief 里还残留裸字段 pd.{prop}"
    assert 'SetAttr(snap, dyn, pd, "age", "age", true)' in brief, "寿命/年龄按面板口径换算成年"
    assert "object dyn = null;" in brief, "dyn（DynInt 容器）要在 brief 段就取好"


def test_no_leftover_grade_id_only_write():
    """`grade_id` 仍要回传（诊断用），但已不是 realm 的唯一输入。"""
    src = _read("UnitSnapshot.cs")
    assert 'snap["realm_diag"] = realmDiag;' in src, "原始数字要留档"
    tool = _read("ToolExecutor.cs")
    assert 'data["realm_diag"] = snap["realm_diag"]' in tool, "brief 要把 realm_diag 带回给日志"


# ---------------------------------------------------------------- 自身段身份（Pin）

def test_chat_entry_pins_real_unit_id():
    """打开对话 UI 时握着真身 → 必须把它钉下来（否则自身段只能按名猜）。"""
    src = _read("ChatLauncher.cs")
    assert "UnitLookup.Pin(id, unit.data.unitData.unitID)" in src, \
        "ChatLauncher.OpenForUnit 是全工程唯一「拿着 WorldUnitBase 开对话窗」的入口，必须在此登记"
    # 登记必须在 OpenForNpc 之前（后面的 L1 取数就要用它）
    assert src.index("UnitLookup.Pin(") < src.index("OpenForNpc(id)"), \
        "登记要发生在打开窗口之前"


def test_ab_fallback_also_pins():
    src = _read("UI/AbChatPanel.cs")
    assert "UnitLookup.Pin(npcId, unit.data.unitData.unitID)" in src, "AB 兜底路径同样握着真身，一并钉住"


def test_pin_wins_over_name_scan_but_not_over_player():
    """登记要压过全图按名扫描，但**不能压过玩家直达**（09-12 的结论）。"""
    src = _read("UnitLookup.cs")
    body = src[src.index("private static WorldUnitBase ResolveInner("):]
    i_player = body.index("// ① 玩家直达")
    i_pin = body.index("⓪已登记unitID")
    i_scan = body.index("全图按 GetName()")
    assert i_player < i_pin < i_scan, "优先级：玩家直达 > 已登记 unitID > 全图按名扫描"


def test_pin_falls_back_when_stale():
    """登记的 id 在当前存档查不到 → 摘掉登记并回落，绝不拿旧 id 硬套。"""
    src = _read("UnitLookup.cs")
    body = src[src.index("// ⓪ 权威登记"):]
    body = body[: body.index("// ② unitID 精确")]
    assert "_pinned.Remove(npcId)" in body, "失效登记必须摘除"
    assert "登记失效" in body, "失效要留痕（否则'换档后一直查不到人'无从归因)"
    # 仍要回验 unitID 相等 —— GetUnit 对查不到的串会按名兜底
    assert "got == pinned" in body, "登记路径同样要回验身份"


def test_pin_only_accepts_real_handles():
    """`Pin` 的语义约束必须写在注释里（调用方必须真的握着 WorldUnitBase）。"""
    src = _read("UnitLookup.cs")
    body = src[src.index("internal static void Pin("):]
    body = body[: body.index("internal static int PinnedCount")]
    assert "必须真的握着" in src[: src.index("internal static void Pin(")] or "必须真的握着" in body, \
        "要写明「不许把按名查出来的结果钉进去」"
