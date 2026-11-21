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
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 境界

def _realm_body() -> str:
    src = _read("game/UnitSnapshot.cs")
    return src[src.index("private static string RealmOf("):
               src.index("private static bool _realmLogged;")]


def test_realm_does_not_feed_rowid_into_grade_param():
    """不许再把 gradeID 直接喂给 `GetGradeName(单个参数)` —— 那是**越界钳到最高档**的成因。"""
    src = _read("game/UnitSnapshot.cs")
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
    src = _read("game/UnitSnapshot.cs")
    brief = src[src.index("// —— 自身基本面"): src.index("snap[\"hobby\"]")]
    for prop in ("beauty", "reputation", "talent", "mood"):
        assert f'SetAttr(snap, dyn, pd, "{prop}", "{prop}")' in brief, \
            f"brief.{prop} 必须走 SetAttr（DynInt 优先），不许再直接读 pd.{prop}"
        assert f"pd.{prop}" not in brief, f"brief 里还残留裸字段 pd.{prop}"
    assert 'SetAttr(snap, dyn, pd, "age", "age", true)' in brief, "寿命/年龄按面板口径换算成年"
    assert "object dyn = null;" in brief, "dyn（DynInt 容器）要在 brief 段就取好"


def test_no_leftover_grade_id_only_write():
    """`grade_id` 仍要回传（诊断用），但已不是 realm 的唯一输入。"""
    src = _read("game/UnitSnapshot.cs")
    assert 'snap["realm_diag"] = realmDiag;' in src, "原始数字要留档"
    tool = _cs_tools.tool_src()
    assert 'data["realm_diag"] = snap["realm_diag"]' in tool, "brief 要把 realm_diag 带回给日志"


# ---------------------------------------------------------------- 自身段身份（登记表）

def test_chat_entry_registers_real_unit_id():
    """打开对话 UI 时握着真身 → 必须把它登记下来（否则自身段只能按名猜）。

    ★ 09-17 改：登记的落点从 `UnitLookup.Pin`（进程内字典）搬到 `UnitIdRegistry`
    （**落盘 + 按存档隔离**）—— 写入时刻不变（就是这里），但重开游戏仍然精确。
    钉**四件本质的事**：
      ① unitID 确实是从真身读出来的（`unit.data.unitData.unitID` 出现在本方法里）；
      ② 登记用的是它；
      ③ **只有"不是猜来的"才登记**（`!guessed` 守卫）—— 把猜测洗成权威正是
         文件头那次事故的形态；
      ④ 登记发生在打开窗口**之前**（后面的 L1 取数就要用它）。
    """
    src = _read("npc/ChatLauncher.cs")
    body = src[src.index("public static bool OpenForUnit(WorldUnitBase unit"):]
    body = body[:body.index("\n        /// <summary>手动好友的")]   # 只取 OpenForUnit 本体
    assert "unit.data.unitData.unitID" in body, \
        "unitID 必须从真身读出来（不是从别处推的）"
    assert re.search(r"UnitIdRegistry\.Remember\(\s*id\s*,\s*unitId\s*,", body), \
        "OpenForUnit 是全工程唯一「拿着 WorldUnitBase 开对话窗」的入口，必须在此登记"
    assert re.search(r"if\s*\(!\s*guessed\s*\)", body), \
        "登记必须被 `!guessed` 守卫 —— 按名猜出来的对象不许进登记表"
    # 登记必须在 OpenForNpc 之前（后面的 L1 取数就要用它）
    assert src.index("UnitIdRegistry.Remember(") < src.index("OpenForNpc(id)"), \
        "登记要发生在打开窗口之前"


def test_ab_fallback_also_registers():
    """AB 兜底路径同样握着真身 —— 一并登记（`Remember` 幂等，重复登记不写盘）。"""
    src = _read("UI/AbChatPanel.cs")
    assert re.search(r"if\s*\(!\s*guessed\s*\)\s*\{\s*try\s*\{\s*UnitIdRegistry\.Remember\(", src), \
        "AB 兜底路径同样握着真身，一并登记（且同样只许登记非猜测的）"


def test_registry_wins_over_name_scan_but_not_over_player():
    """登记要压过全图按名扫描，但**不能压过玩家直达**（09-12 的结论）。"""
    src = _read("game/UnitLookup.cs")
    body = src[src.index("private static WorldUnitBase ResolveInner("):]
    i_player = body.index("// ① 玩家直达")
    i_reg = body.index("⓪已登记unitID")
    i_scan = body.index("全图按 GetName()")
    assert i_player < i_reg < i_scan, "优先级：玩家直达 > 已登记 unitID > 全图按名扫描"


def test_registry_falls_back_when_stale():
    """登记的 id 在当前存档查不到 → **摘掉登记**并回落，绝不拿旧 id 硬套。

    ★ 09-17：摘除动作搬进 `UnitIdRegistry.Forget`（本文件不再自己维护字典，
    自愈逻辑因此只有一处，不会两处漂移）。
    """
    src = _read("game/UnitLookup.cs")
    body = src[src.index("// ⓪ 权威登记"):]
    body = body[: body.index("// ② unitID 精确")]
    assert "UnitIdRegistry.Forget(npcId, pinned)" in body, "失效登记必须摘除"
    assert "登记失效" in body, "失效要留痕（否则'换档后一直查不到人'无从归因)"
    # 仍要回验 unitID 相等 —— GetUnit 对查不到的串会按名兜底
    assert "got == pinned" in body, "登记路径同样要回验身份"


def test_registry_only_accepts_real_handles():
    """`Remember` 的语义约束必须写在注释里（调用方必须真的握着 WorldUnitBase），
    并且**写入点必须只有两个** —— 这条约束靠"入口少"保证，不靠调用方自觉。"""
    src = _read("game/UnitIdRegistry.cs")
    head = src[: src.index("internal static void Remember(")]
    body = src[src.index("internal static void Remember("):]
    body = body[: body.index("internal static bool Forget(")]
    assert "必须真的握着" in head or "必须真的握着" in body, \
        "要写明「按名查出来的结果永远不许写进来」"
    # 两个合法写入点必须在注释里点名（换实现时忘了同步注释 = 后人照抄一个错的范例）
    assert "ChatLauncher.OpenForUnit" in head, "要点名写入点①（AI 对话）"
    assert "NpcPanelAddContact" in head, "要点名写入点②（加好友）"
    # 而"写入点只有两个"这件事**不能只写在注释里** —— 用实际调用点数量钉住它。
    callers = [n for n in ("npc/ChatLauncher.cs", "UI/AbChatPanel.cs", "UI/NpcPanelAddContact.cs",
                           "UI/ChatPresenter.cs", "UI/ContactPresenter.cs",
                           "game/UnitLookup.cs", "game/GameContext.cs",
                           "contacts/ContactDuty.cs", "npc/NpcInitiativeMonitor.cs")
               + tuple(_cs_tools.tool_files())          # 工具层已按类拆成多文件（11-19）
               if "UnitIdRegistry.Remember(" in _read(n)]
    assert sorted(callers) == ["UI/AbChatPanel.cs", "UI/NpcPanelAddContact.cs", "npc/ChatLauncher.cs"], \
        "`Remember` 的调用点只许是「AI 对话」（ChatLauncher / AbChatPanel 兜底）与「加好友」，" \
        "实际是：%s。多出来的那个就是「把猜测洗成权威」的入口。" % callers


def test_registry_flushes_only_on_game_save():
    """落盘时机 = **游戏存档**，不是每次登记（09-17 用户拍板）。

    原实现是"登记即原子写盘"。改动的理由（详见 `UnitIdRegistry.cs` 文件头「落盘时机」）：
    本表与 Python 侧的对话增量是**同一次握手产出的两份数据**，寿命语义必须一致
    （存档=固化，不存档=丢弃）—— 只固化其中一份，就会留下"NPC 不记得这次对话、
    但登记表里还留着这一局的 unitID"这种半状态，事后无法归因。

    这条纪律有三个能被悄悄破坏的地方，逐个钉住：
      ① `Remember` / `Forget` 里冒出 `Save()`（= 回到"每次更新就写"）；
      ② `Flush` 长出第二个调用点（多一个入口就多一个"半固化"的时机）；
      ③ `ResetForWorld` 忘了清 `_pending`（上一个存档的待写内容会写进新档 = 静默错人）。
    """
    src = _read("game/UnitIdRegistry.cs")
    remember = src[src.index("internal static void Remember("):src.index("internal static bool Forget(")]
    assert "Save()" not in remember, \
        "登记时不许落盘 —— 只置 `_pending`，写盘时机是游戏存档"
    forget = src[src.index("internal static bool Forget("):src.index("internal static void ResetForWorld(")]
    assert "Save()" not in forget, "自愈（`Forget`）也不许落盘：同上"
    reset = src[src.index("internal static void ResetForWorld("):src.index("// ---------- 落盘 ----------")]
    assert "_pending = false;" in reset, \
        "换存档必须丢掉未落盘的登记（否则上一个存档的 unitID 会落进新档）"
    assert "_loaded = false;" in reset, "换存档必须让内存表作废（否则新档继续用旧档那份）"
    # ★ 用**实际调用点**钉住 `Flush` 的唯一入口，不靠注释自觉（同 `test_registry_only_accepts_real_handles`）
    callers = sorted(p.name for p in CS.rglob("*.cs")
                     if "UnitIdRegistry.Flush(" in p.read_text(encoding="utf-8"))
    assert callers == ["ModMain.cs"], \
        "`Flush` 只许在存档事件里调，实际调用点：%s" % callers
    mm = _read("app/ModMain.cs")
    save_ev = mm[mm.index("private void OnSaveDataEvent("):mm.index("private void OnIntoWorldEvent(")]
    assert "UnitIdRegistry.Flush();" in save_ev, "存档事件（SaveData）里必须真的调 `Flush`"


def test_query_results_are_registered_without_overwriting():
    """查询结果的**顺带登记**（09-17）：三条纪律，每条都对应一种静默错误。

    ① 只对**只读查询**做（动作工具可能开确认窗，不该在它返回时顺手动登记表）；
    ② **绝不覆盖**已有登记（`RememberIfAbsent`）—— 查询是"路过看见"，
       压掉一次"手握真身"的登记就可能把名字钉到另一个人身上；
    ③ **一次载荷里同名多值 ⇒ 整组不登记**（那是重名，登记谁都是错的）。
       这一条**顺带覆盖了 `inspect_unit` 的同名歧义**：那次载荷里被查者与候选同名。
    """
    src = _cs_tools.tool_src()
    assert '(name == "inspect_unit" || name == "search_units")' in src, \
        "只对两个只读查询做顺带登记"
    assert 'UnitIdRegistry.RememberObservedFromPayload(res["data"], name)' in src, \
        "查询成功时要把 data 交给登记表"
    reg = _read("game/UnitIdRegistry.cs")
    body = reg[reg.index("internal static int RememberObservedFromPayload("):
               reg.index("private static void CollectRefs(")]
    assert "_core.RememberIfAbsent(" in body, "只能走不覆盖的入口"
    assert "_core.Remember(" not in body, "顺带登记里不许出现会覆盖的 `Remember`"
    assert "dup.Contains(kv.Key)" in body and "dup.Add(kv.Key)" in body, \
        "同名多值必须整组跳过（先数后写，不能边扫边写）"
    # 写入点用**实际调用点**钉住（照 `Remember` 那条的做法）
    callers = [n for n in ("npc/ChatLauncher.cs", "UI/AbChatPanel.cs",
                           "UI/NpcPanelAddContact.cs", "game/UnitSnapshot.cs", "game/GameContext.cs")
               + tuple(_cs_tools.tool_files())
               if "UnitIdRegistry.RememberObservedFromPayload(" in _read(n)]
    assert set(callers) <= set(_cs_tools.tool_files()) and callers, \
        "顺带登记只许在工具层发生（别扩散到 L1/立绘那些地方），实际：%s" % callers


def test_social_relation_reads_intim_from_the_object_in_hand():
    """好感数字必须从**手里那个对象**读，不许拿名字回全图重解析一遍。

    原实现（`GetIntimWith(string)`）在"对手方是玩家"这条**默认分支**上：
    `UnitLookup.Resolve(名字)` 重新找人 —— 同名时命中遍历序第一个（可能是别人，
    名字等于玩家名时甚至直接命中玩家）⇒ `before`/`after` 读的是**另一个人**，
    两次都读到同一个错的人 ⇒ `actual_delta` **恒为 0**：动作落对了人，模型却读到
    "好感未变化"，于是重复落刀。已删掉该辅助函数，三处一律用 `GetIntimBetween(wub, player)`。
    """
    src = _cs_tools.tool_src()
    # 剥注释再查：墓碑注释里会提到那个被删的函数名（那是文档，不是代码）
    code = re.sub(r"//[^\n]*", "", src)
    assert "GetIntimWith" not in code, "按名重解析的辅助函数必须整体删除"
    # 11-20 起 `SocialRelation` 独占 `tools/RelationTool.cs`，`Movement` 在另一个文件 ——
    # 边界改成**同文件里的下一个成员**（`StartRelationAction`），不再跨文件找 `Movement`。
    rel = _cs_tools.tool_file("RelationTool.cs")
    body = rel[rel.index(" JObject SocialRelation(JObject args)"):]
    body = body[:body.index(" JObject StartRelationAction(")]
    assert body.count("GetIntimBetween(wub, player)") >= 3, \
        "before/after 两处（add/reduce 各一对）都要用手里那个对象"
    assert "otherIsPlayer ?" not in body, "两个分支本就是同一个表达式，不该再分叉"


def test_trade_requires_the_actor_to_be_a_party():
    """`trade` 必须校验**发起方是买卖的一方** —— 描述里一直写着"不许替他人做主"，
    但 09-17 之前代码里没有这道闸（群聊有提案闸门兜着，单聊没有）。"""
    src = _cs_tools.tool_src()
    body = src[src.index(" JObject Trade(JObject args)"):]
    body = body[:body.index("string sellerName = UnitIdentity.Name(")]
    assert "ResolveActor(args)" in body and "actorUid" in body, "要用发起方的 unitID 判"
    assert "actorUid != sellerUid && actorUid != buyerUid" in body, \
        "发起方既不是卖方也不是买方 ⇒ 拒绝"
