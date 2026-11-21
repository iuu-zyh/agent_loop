# -*- coding: utf-8 -*-
"""NPC 自主入宗 / 退宗契约（10-29）

用户原话：「我需要 npc 可以实现自由的加宗门和退出宗门，当然是需要门槛的，但是我不想要门槛设置
的是死的，所以决定权在 npc 上面……需要一个参数，作为加入或者退出宗门的理由」
以及后来的：「不是简单的加入宗门变为外门弟子就行了，还需要可以成为某某的真传弟子……当玩家要求
npc 加入某个宗门的时候，应该反问玩家是成为外门弟子还是内门还是谁的真传弟子」。

设计依据与 RVA 见 `docs/plan-sect.md` §6。本文件钉住：

  A. 分发位置：入宗/退宗必须在 `ResolveCounterparty` / `AiActionTargetCore` **之前**分流
     （它不是"对玩家的行动"，吃 target 会被当成攻击目标而拒掉）
  B. 引擎三件事照抄：`UnitActionSchoolJoin(schoolID, post, dept)` / `schoolNameID` 当 ID /
     `UnitActionSchoolExit()` 无参
  C. **绝不自己改 postData**（扣道具/加好感/离旧职/写日志/挂 SetPostType 全在引擎里）
  D. 三档身份 + 真传要落到"哪一堂的长老"（`InheritData` 只吃 department）
  E. 没定身份时**不动手**，只回"该问玩家什么"+该宗在任人数与可选师父
  F. 门槛不写死：拒因只写反编能证实的码，其余标注为"常见原因"
  G. 退宗拦两类：本就无宗、宗主/大长老（一退就空位）
  H. 渲染层三种形态：ask / 已落地 / 已受理未落地（不许把 pending 说成失败）
"""
from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
EXEC = _cs_tools.TOOL_SRC
SCHEMAS = ROOT / "tools" / "schemas.py"
RENDER = ROOT / "tools" / "text_render.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _block() -> str:
    """「宗门人事（入宗 / 退宗）」那一段。

    11-20 工具层按 Python 的域拆开后，这一段在 `tools/ActionTool.cs`（动作域·互动）
    里，夹在 `WorldAiAction` 与传功用的功法解析之间 —— 所以按**文件内**的两条横幅切，
    不再用"到 `QueryWorldEvents` 之前"（那个成员现在在 `QueryTool.cs`）。
    """
    src = _cs_tools.tool_file("ActionTool.cs")
    start = src.index("// ===== 宗门人事：入宗 / 退宗（10-29） =====")
    end = src.index("DataUnit.ActionMartialData ResolveMartial(")
    return src[start:end]


# ---------------------------------------------------------------- A 分发位置

def test_dispatch_precedes_target_resolution():
    """A：join/leave 若放在 `ResolveCounterparty` 之后，`target` 留空会被
    `AiActionTargetCore` 当成"打玩家"那条链，`mode` 一带就直接拒 —— 必须先分流。"""
    src = _read(EXEC)
    i_dispatch = src.index('if (op == "join_sect") return SectJoin(wub, args);')
    i_resolve = src.index("var player = ResolveCounterparty(args, out string cpName", i_dispatch - 4000)
    assert i_dispatch < i_resolve, "入宗/退宗必须在 ResolveCounterparty 之前分流"
    assert 'if (op == "leave_sect") return SectLeave(wub, args);' in src


# ---------------------------------------------------------------- B/C 引擎用法

def test_uses_native_join_action_and_build_id():
    """B：`schoolID` 必须是 **build 的 id**（`buildData.id`），不是 `schoolNameID`。

    11-10 证据：引擎 33 处构造点里 32 处是 `mov rXX,[buildData+0x18]`，而
    `BuildDataBase` 字段表写明 `0x18 = public string id`；`schoolNameID` 是 getter
    用 `ConfSchoolNameBase.GetItem + Int32.ToString()` 拼出来的宗名族 id。
    真机：传 schoolNameID → 两次裸 NRE；改成 buildData.id → 观莲教一次入宗成功。
    """
    b = _block()
    assert "new UnitActionSchoolJoin(schoolId, (SchoolPostType)post, (SchoolDepartmentType)dept)" in b
    assert "ResolveJoinSchoolId(ms)" in b, "schoolID 必须走 ResolveJoinSchoolId（先 buildData.id）"
    assert "npc.CreateAction(act)" in b
    helper = b[b.index(" string ResolveJoinSchoolId("):]
    assert "ms.buildData.id" in helper, "首选候选必须是 buildData.id"
    assert "GetBuild(ids[i])" in helper and "Pointer == ms.Pointer" in helper, \
        "候选用引擎同一把 GetBuild 自证（指针相同才算命中）"


def test_leave_is_parameterless_native_action():
    b = _block()
    assert "new UnitActionSchoolExit()" in b
    assert "npc.CreateAction(new UnitActionSchoolExit())" in b


def test_never_mutates_postdata_directly():
    """C：成功后的副作用全在引擎 `OnCreate` 里（扣道具/加好感/离旧职/日志）。
    自己改 postData 会漏掉整套缓存与日志 ⇒ 出"幽灵成员"。"""
    b = _block()
    for bad in ("SetPostInherit(", "SetPostType(", "ExitSchool(", "postMain.manageElders",
                "manageInherit =", "npcInherit.Add", ".postElders[", "SetPostInherit"):
        assert bad not in b, bad


def test_refusal_codes_are_not_overclaimed():
    """F：码表只写反编能证实的部分（11-10 起逐条反编 1/2/3/4/7/8/11/12）。"""
    b = _block()
    assert "返回码" in b
    assert "五花生灵" in b            # 12：GetFiveFlowerDramaNpcID → eax=0xc
    assert "exitSchoolUnit" in b       # 7：离宗名单（真机 + 反编 0xD0）
    assert "IsHerdNPC" in b            # 3
    assert "unitData.schoolID" in b    # 2：引擎认为他还有宗门
    # 不许出现"我们说必须有 X 境界才能入"这类自造门槛
    for bad in ("必须达到", "境界不足", "未达到境界", "至少要金丹"):
        assert bad not in b, bad


# ---------------------------------------------------------------- D/E 身份与反问

def test_three_posts_and_master_maps_to_department():
    """D：三档身份；真传必须落到"某堂长老"（`InheritData` 只吃 departmentType）。"""
    b = _block()
    # 三档职位的**字符串→枚举**映射在 `SectQueryTool.SectPostOf`（查询与动作共用一份），
    # 入宗动作只是消费它 —— 所以这两条断言落在查询文件上，不落在宗门人事段里。
    sect_q = _cs_tools.tool_file("SectQueryTool.cs")
    assert 'case "out": case "外门": case "外门弟子": return 6;' in sect_q
    assert 'case "in": case "内门": case "内门弟子": return 5;' in sect_q
    assert "case \"inherit\":" in sect_q and "return 4;" in sect_q
    assert "pd.GetElders((SchoolDepartmentType)d)" in b
    assert 'res["master_dept"] = SectQueryTool.SectDeptName(dept);' in b


def test_asks_back_instead_of_guessing():
    """E：没给 post / 真传没给 master ⇒ 不动手，回 ask + 名册素材。"""
    b = _block()
    assert b.count('["ask"] = true') >= 2
    assert "SectRoster(ms)" in b
    assert 'ask["question"]' in b
    # ask 分支必须在建动作之前返回
    i_ask = b.index('["ask"] = true')
    i_act = b.index("var act = new UnitActionSchoolJoin")
    assert i_ask < i_act


def test_roster_reports_counts_only_not_caps():
    """名额上限有两处候选数字、语义未定 ⇒ 只报在任人数，不报上限、不预拦。"""
    src = _read(EXEC)
    start = src.index(" JObject SectRoster(")
    end = src.index(" int SectPostOf(")
    body = src[start:end]
    assert '["out"]' in body and '["in"]' in body and '["inherit"]' in body
    for bad in ('"max"', '"capacity"', "numMax"):
        assert bad not in body, bad


# ---------------------------------------------------------------- G 退宗拦截

def test_leave_blocks_only_sect_head():
    b = _block()
    assert "if (post == 1)" in b and "if (post == 2)" in b
    assert "宗主" in b and "大长老" in b
    assert "散修" in b


def test_reason_is_required_for_both_ops():
    b = _block()
    assert b.count('["reason"]') >= 2
    assert "入宗必须填 reason" in b
    assert "退宗必须填 reason" in b


def test_cannot_join_twice_or_switch_without_leaving():
    b = _block()
    assert "已经在「" in b
    assert "先退宗" in b


# ---------------------------------------------------------------- schema

def test_schema_declares_new_params():
    src = _read(SCHEMAS)
    m = re.search(r"WORLD_AI_ACTION: Dict\[str, Any\] = \{(.*?)\n\}\n", src, re.S)
    assert m
    block = m.group(1)
    assert '"join_sect", "leave_sect"' in block
    for k in ('"sect":', '"post":', '"master":', '"reason":'):
        assert k in block, k
    assert '"enum": ["out", "in", "inherit"]' in block
    # reason 的描述必须让 NPC 自己权衡（用户明确要求）
    assert "由你定" in block
    assert "玩家只是提议" in block
    # target 要写清 join/leave 不用填
    assert "join_sect/leave_sect 不用填" in block


# ---------------------------------------------------------------- 渲染层

def _render(data: dict) -> str:
    from tools.text_render import render
    out = render("world_ai_action", {"op": data.get("op")}, {"success": True, "data": data})
    assert out, "渲染器应当命中"
    return out


def test_render_ask_lists_roster_and_elders():
    out = _render({"op": "join_sect", "ask": True, "sect": "丹阳谷",
                   "roster": {"out": 12, "in": 12, "inherit": 2,
                              "elders": [{"dept": "招贤堂", "name": "林清", "inherit": 1},
                                         {"dept": "巡查堂", "vacant": True}]},
                   "question": "还没定以什么身份入门"})
    assert out.startswith("（系统：这一调没有动手")
    assert "外门12人、内门12人、真传2人" in out
    assert "招贤堂·林清（门下真传1人）" in out
    assert "巡查堂（空缺）" in out


def test_render_join_success_and_pending():
    ok = _render({"op": "join_sect", "sect": "丹阳谷", "post": "真传弟子",
                  "master_dept": "招贤堂", "joined": True,
                  "now_sect": "丹阳谷永宁州分舵", "reason": "敬林清为人"})
    assert "你已入「丹阳谷」" in ok and "身份是真传弟子" in ok
    assert "拜在招贤堂长老门下" in ok
    assert "敬林清为人" in ok

    pend = _render({"op": "join_sect", "sect": "丹阳谷", "post": "外门弟子",
                    "pending": True, "reason": "想找个靠山"})
    assert "已受理" in pend and "别当没成" in pend


def test_render_leave_success_and_pending():
    ok = _render({"op": "leave_sect", "from": "丹阳谷永宁州分舵", "left": True,
                  "now_sect": "散修", "reason": "看不惯宗门盘剥乡里"})
    assert "你已退出「丹阳谷永宁州分舵」，如今是散修" in ok
    assert "看不惯宗门盘剥乡里" in ok

    pend = _render({"op": "leave_sect", "from": "丹阳谷", "pending": True,
                    "now_sect": "丹阳谷", "reason": "想另投"})
    assert "已受理" in pend and "别当没成" in pend
