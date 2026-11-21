# -*- coding: utf-8 -*-
"""入宗 / 退宗写进「经历」的契约（11-11）

用户原话：「加入宗门和退出宗门就是简单的工具的结果返回，而我是让模型传入理由的，
这样的话完全可以作为 npc 的经历去写入」。

## 问题

`join_sect` / `leave_sect` 强制模型填 `reason`，但原先它只回显在**工具结果**里。
玩家下一轮一开口，那条返回就从上下文里滚掉了 —— 理由随之蒸发。
而"我为什么入了青木宗""我为什么离开丹阳谷"正是这个 NPC 该记一辈子的事，
也是 agent 日后唯一读得到的记忆通道（`UnitSnapshot.BuildLogs` → `WorldUnitLogMgr`）。

## 本文件钉住

  A. 两个入口都必须真的调到写经历（不是"加了函数没接线"）
  B. 模板 id 必须登记在 `ModIds`（唯一事实来源），且偏移不撞车
  C. 文案用**自由文本槽**装理由 —— 理由原样照写，不塞进语义槽
  D. pending 与已落地**用不同模板**（不许把"正在办"写成"已经成了"）
  E. 复用 `TeamBattleFollowup` 那套（写+回读+合并面板），不另写一份
  F. 失败只打日志，不许让入宗/退宗本身变成失败
"""
from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
EXEC = _cs_tools.TOOL_SRC
MODIDS = ROOT / "csharp" / "game" / "ModIds.cs"
FOLLOWUP = ROOT / "csharp" / "tools" / "TeamBattleFollowup.cs"
ROLE_TEMPLATE = ROOT / "csharp" / "game" / "RoleLogTemplate.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """去注释（判据必须落在代码上，不能命中注释里的同名字样）。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _brace_body(text: str, at: int) -> str:
    """从 `at` 处的第一个 `{` 起按大括号配平取方法体。"""
    i = text.index("{", at)
    depth, k = 0, i
    while k < len(text):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
        k += 1
    raise AssertionError("大括号不配平")


def _method(code: str, sig: str) -> str:
    at = code.find(sig)
    assert at >= 0, f"找不到方法 {sig}"
    return _brace_body(code, at)


# ---------------------------------------------------------------- A 接线

def test_join_writes_the_log():
    """A：`SectJoin` 必须调 `WriteSectLog`，且传 join=true。"""
    code = _code(_read(EXEC))
    body = _method(code, " JObject SectJoin(WorldUnitBase npc, JObject args)")
    assert "WriteSectLog(" in body, "入宗没有写经历 —— 模型填的 reason 又只活在工具结果里了"
    assert re.search(r"WriteSectLog\(\s*npc\s*,\s*true\s*,", body), \
        "入宗调 WriteSectLog 时 join 参数不对（true 才是入宗）"


def test_leave_writes_the_log():
    """A：`SectLeave` 必须调 `WriteSectLog`，且传 join=false。"""
    code = _code(_read(EXEC))
    body = _method(code, " JObject SectLeave(WorldUnitBase npc, JObject args)")
    assert "WriteSectLog(" in body, "退宗没有写经历"
    assert re.search(r"WriteSectLog\(\s*npc\s*,\s*false\s*,", body), \
        "退宗调 WriteSectLog 时 join 参数不对（false 才是退宗）"


def test_log_is_written_after_outcome_is_known():
    """A：写经历必须在**读回名册之后** —— 那时才知道是"已成"还是"已提请"。

    若写在 `CreateAction` 之后、回读之前，`pending` 与 `joined` 还没定，
    文案只能靠猜（把 pending 当成功，日后这个 NPC 会跟人吹一件没成的事）。
    """
    code = _code(_read(EXEC))
    body = _method(code, " JObject SectJoin(WorldUnitBase npc, JObject args)")
    i_act = body.index("CreateAction(")
    i_read = body.index("var s2 = npc.data.school")
    i_log = body.index("WriteSectLog(")
    assert i_act < i_read < i_log, (
        "顺序不对：CreateAction → 回读名册 → 写经历，实际 "
        f"{i_act} / {i_read} / {i_log}"
    )


# ---------------------------------------------------------------- B id 登记

def test_ids_registered_and_within_private_range():
    """B：四个模板 id 都要在 `ModIds` 登记，且在私有上界 999 内、彼此不撞。"""
    ids = _code(_read(MODIDS))
    want = {
        "LogSectJoin": 911,
        "LogSectLeave": 912,
        "LogSectJoinPending": 913,
        "LogSectLeavePending": 914,
    }
    for name, off in want.items():
        m = re.search(rf"{name}\s*=\s*Mod\s*\+\s*(\d+)", ids)
        assert m, f"{name} 没有用 `= Mod + N` 的形式登记（偏移必须是显式常数）"
        assert int(m.group(1)) == off, f"{name} 的偏移应为 {off}，实际 {m.group(1)}"
        assert int(m.group(1)) <= 999, f"{name} 超出私有上界 999"
        assert f"{name}Key" in ids, f"{name} 缺 key 常量"

    # 与既有经历 id 不冲突
    used = {int(m) for m in re.findall(r"Log\w+\s*=\s*Mod\s*\+\s*(\d+)", ids)}
    assert len(used) == len(re.findall(r"Log\w+\s*=\s*Mod\s*\+\s*\d+", ids)), \
        "有经历 id 撞车（同一个偏移登记了两条）"


def test_pending_uses_its_own_template():
    """D：pending 与已落地必须是**不同 id**，否则措辞无法区分。"""
    ids = _code(_read(MODIDS))
    def off(name):
        m = re.search(rf"{name}\s*=\s*Mod\s*\+\s*(\d+)", ids)
        assert m, name
        return m.group(1)
    assert off("LogSectJoin") != off("LogSectJoinPending"), "入宗的 pending 与成功共用了同一模板"
    assert off("LogSectLeave") != off("LogSectLeavePending"), "退宗的 pending 与成功共用了同一模板"


# ---------------------------------------------------------------- C 文案形态

def test_reason_goes_into_a_free_text_slot():
    """C：理由必须落在**自由文本槽**里（原样照写），不能被塞进语义槽。"""
    code = _code(_read(EXEC))
    body = _method(code, " void WriteSectLog(")

    # 四个模板都要出现，且理由作为**最后一个** values 传入（自由文本位置）
    for tpl in ("入{0}为{1}。{2}", "已提请入{0}为{1}。{2}", "退出{0}。{1}", "已提请退出{0}。{1}"):
        assert tpl in body, f"缺少模板 {tpl}"
    # 理由变量必须真的被放进 values
    assert body.count("reason") >= 4, "理由没有进 values（被丢了）"


def test_reason_is_not_truncated_or_rewritten():
    """C：理由原样照写 —— 不许做摘要/截断/替换。

    经历会长期留在存档里，改写造成的失真会一直跟着这个 NPC。

    ⚠ 判据必须认准**对 reason 这个变量**做的加工。不能写成"方法体里出现 .Replace( 就算违规"：
      同一段里还有 `shown.Replace("{0}", …)` —— 那是**日志展示句**填模板用的，与理由无关。
      （第一版就这么误判过一次，被这条测试拦住后发现是判据太宽，不是代码有问题。）
    """
    code = _code(_read(EXEC))
    body = _method(code, " void WriteSectLog(")
    for pat in (r"reason\s*\.\s*Substring\(", r"reason\s*\.\s*Replace\(",
                r"reason\s*\.\s*Trim\(", r"Short\(\s*reason", r"Truncate\(\s*reason"):
        assert not re.search(pat, body), f"理由被加工了（{pat}）—— 应当原样照写"


# ---------------------------------------------------------------- E 复用

def test_reuses_the_shared_write_path():
    """E：必须复用 `TeamBattleFollowup` 那套（写+回读+合并面板），不另写一份。

    另写一份的后果：回读缺失 ⇒ 模板没注册时引擎只 LogError 然后静默丢弃，
    而这边会"看起来成功"，玩家和 agent 都发现不了。
    """
    code = _code(_read(EXEC))
    body = _method(code, " void WriteSectLog(")
    assert "TeamBattleFollowup.TryAddLog(" in body, "没有走统一写经历入口（它内部才有回读）"
    # 11-19：模板注册从 TeamBattleFollowup 提到 `game/RoleLogTemplate.cs`（唯一实现）
    assert "RoleLogTemplate.EnsureLogTemplate(" in body, "没有走模板兜底注册"
    assert "TeamBattleFollowup.ForceMergeToPanel(" in body, "写完没有立即合并进面板（要等月结才显示）"

    # 那几个方法必须真的对 ToolExecutor 可见
    fu = _code(_read(FOLLOWUP))
    for sig in ("internal static bool TryAddLog(", "internal static void ForceMergeToPanel("):
        assert sig in fu, f"TeamBattleFollowup 里 {sig} 不是 internal（跨类调不到）"
    rt = _code(_read(ROLE_TEMPLATE))
    assert "public static bool EnsureLogTemplate(" in rt, \
        "RoleLogTemplate.EnsureLogTemplate 不是 public（跨命名空间调不到）"


# ---------------------------------------------------------------- F 失败不牵连

def test_failure_does_not_fail_the_action():
    """F：写经历失败只打日志 —— 不能因为"没记上"就让入宗/退宗报失败。"""
    code = _code(_read(EXEC))
    body = _method(code, " void WriteSectLog(")
    assert "catch (Exception" in body, "写经历没有兜异常（会让入宗/退宗一起炸）"
    # 整个方法体里不许出现 return Fail —— 那是工具失败信号，这里不该产生
    assert "Fail(" not in body, "写经历里出现了 Fail(...)，会把工具判成失败"

    # 两个入口里，WriteSectLog 的返回值不得参与结果判定（它是 void）
    for sig in (" JObject SectJoin(WorldUnitBase npc, JObject args)",
                " JObject SectLeave(WorldUnitBase npc, JObject args)"):
        b = _method(code, sig)
        for m in re.finditer(r"(joined|left)\s*=\s*WriteSectLog", b):
            raise AssertionError(f"用 WriteSectLog 的返回值决定了结果：{m.group(0)}")


def test_panel_readback_uses_the_unique_reason_not_the_sect_name():
    """`ForceMergeToPanel` 的第二个参数只用于**回读校验**，必须传独一无二的那串。

    踩点：组队那边 `values[0]` 是"同行者名单"，独一无二，所以传它没问题。
    但入宗/退宗的 `values[0]` 是**宗门名** —— 同一个 NPC 的经历里本来就可能有
    「退出「青木宗」」这种旧条目。拿宗门名当校验串，回读会**永远为真**，
    "这次到底写进去了没有"就再也测不出来了（回读整条机制失效，而且日志一片成功）。
    理由是一整句话，独一无二，才是正确的校验串。
    """
    code = _code(_read(EXEC))
    body = _method(code, " void WriteSectLog(")
    fm = body[body.index("ForceMergeToPanel("):]
    fm = fm[: fm.index(";")]
    assert "values[0]" not in fm, \
        "回读校验用了 values[0]（宗门名会与旧经历撞车 ⇒ 回读恒真，失效）"
    assert "reason" in fm, "回读校验没有用 reason（它才是唯一的那串）"


def test_readback_probe_is_passed_as_reason_not_sect_name():
    """★ 回读校验串必须传 reason ★

    `TryAddLog` 内部用 `PendingHasValue(unit, values[0])` 回读暂存层，
    默认取 `values[0]`。组队那几条的 `values[0]` 是"同行者名单"，唯一，没问题；
    但入宗/退宗的 `values[0]` 是**宗门名** —— 同一个 NPC 的经历里本就可能有
    「退出「青木宗」」这种旧条目，回读会恒真 ⇒ 整条回读机制失效，且日志一片 ✅。
    """
    code = _code(_read(EXEC))
    body = _method(code, " void WriteSectLog(")
    call = body[body.index("TryAddLog("):]
    call = call[: call.index(")")]
    assert "reason" in call, "没有把 reason 作为回读校验串传给 TryAddLog（会恒真）"


def test_display_text_is_passed_so_logs_are_not_misleading():
    """★ 日志展示句必须由调用方给 ★

    `TryAddLog` 末尾有一串 `else if (key == …) text = …` 的分支，最后一档是
    「与{0}同闯秘境，并肩作战。」。入宗/退宗的 key 不在那些分支里，不传 displayText
    就会掉进最后那个 else，打出「与「青木宗」同闯秘境，并肩作战」—— 文案完全错误，
    而排障时看到这句会以为**模板写错了**，实际只是日志展示没接上。
    """
    code = _code(_read(EXEC))
    body = _method(code, " void WriteSectLog(")
    call = body[body.index("TryAddLog("):]
    call = call[: call.index(")")]
    assert "shown" in call, "没有传 displayText，日志会打出错误的「同闯秘境」文案"

    # 那个 shown 必须是按模板填出来的，不能是写死的字符串
    assert 'Replace("{" + i + "}"' in body, "displayText 不是按 values 填模板得来的"


def test_tryaddlog_new_params_have_defaults():
    """新增的两个参数必须有默认值 —— 否则组队那 4 个既有调用点全得改。"""
    fu = _code(_read(FOLLOWUP))
    body = _method(fu, "internal static bool TryAddLog(")
    head = fu[fu.index("internal static bool TryAddLog("): fu.index("{", fu.index("internal static bool TryAddLog("))]
    assert "string probeValue = null" in head, "probeValue 没有默认值（会影响组队那 4 个调用点）"
    assert "string displayText = null" in head, "displayText 没有默认值（会影响组队那 4 个调用点）"
    # 默认行为不能变：probe 缺省时仍取 values[0]
    assert "values[0] : probeValue" in body or "string.IsNullOrEmpty(probeValue) ? values[0]" in body, \
        "probeValue 缺省时没有回落 values[0]（会改变组队行为）"


# ---------------------------------------------------------------- G 配置表

#: 入宗/退宗四条经历的 `MID&偏移` → (keyID, 中文模板)
_TABLE_ROWS = {
    911: ("eventLogSectJoin", "入{0}为{1}。{2}"),
    912: ("eventLogSectLeave", "退出{0}。{1}"),
    913: ("eventLogSectJoinPending", "已提请入{0}为{1}。{2}"),
    914: ("eventLogSectLeavePending", "已提请退出{0}。{1}"),
}

XLSX = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/RoleLogLocal.xlsx")


def test_config_table_has_the_four_rows():
    """G：`RoleLogLocal.xlsx` 里必须有这四行。

    ## 为什么这条测试重要

    运行时兜底注册（`EnsureLogTemplate`）只是**保险**，正式做法是写进表里。
    只改 C# 不改表，等于把每台机器都压在兜底上：
      · 兜底要克隆 `MID&1` 那一行，万一那行不在/结构变了，功能就没了；
      · 表是**唯一事实来源**，缺行时新人接手会以为"这功能没配表"。

    ## 为什么这里直接读 xlsx

    导出产物 `ModExcel/*.json` 是 `1b8bg-` 加密的，Python 读不了（也不该读）——
    那必须由游戏编辑器导出。所以测试盯的是**源表**（xlsx），这正好是人也该改的地方。
    """
    if not XLSX.is_file():
        import pytest
        pytest.skip(f"源表不在本机：{XLSX}")

    import openpyxl
    ws = openpyxl.load_workbook(XLSX).active
    # 第1行中文表头、第2行字段名、第3行类型，数据从第4行起
    found = {}
    for r in ws.iter_rows(min_row=4, values_only=True):
        if not r or r[0] is None:
            continue
        m = re.match(r"MID&(\d+)$", str(r[0]).strip())
        if not m:
            continue
        found[int(m.group(1))] = (str(r[1] or ""), str(r[14] or ""))

    for off, (key, ch) in _TABLE_ROWS.items():
        assert off in found, f"表里缺 MID&{off} 这一行（key={key}）"
        got_key, got_ch = found[off]
        assert got_key == key, f"MID&{off} 的 keyID 应为 {key}，实际 {got_key}"
        assert got_ch == ch, f"MID&{off} 的中文模板应为 {ch!r}，实际 {got_ch!r}"

    # 四种语言都要有，不许只填中文（导出时空列会变空模板）
    for r in ws.iter_rows(min_row=4, values_only=True):
        if not r or r[0] is None:
            continue
        if str(r[0]).strip() in {f"MID&{o}" for o in _TABLE_ROWS}:
            for col, lang in ((14, "ch"), (15, "tc"), (16, "en"), (17, "kr")):
                assert str(r[col] or "").strip(), f"{r[0]} 的 {lang} 列是空的"


def test_table_offsets_match_csharp_ids():
    """G：表里的 `MID&N` 必须与 `ModIds` 的 `Mod + N` 逐一对上。

    两处各写一遍偏移，是最容易悄悄漂移的地方（表改了 911→915、C# 没改，
    表现是"经历写不进去"，而日志只会说找不到模板）。
    """
    ids = _code(_read(MODIDS))
    pairs = {
        "LogSectJoin": 911,
        "LogSectLeave": 912,
        "LogSectJoinPending": 913,
        "LogSectLeavePending": 914,
    }
    for name, off in pairs.items():
        m = re.search(rf"{name}\s*=\s*Mod\s*\+\s*(\d+)", ids)
        assert m, f"ModIds 里找不到 {name}"
        assert int(m.group(1)) == off, f"{name} 的偏移 {m.group(1)} 与表里的 MID&{off} 不一致"
