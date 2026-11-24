"""剧情账本契约（10-25）：把「游戏里演过的每页剧情 + 玩家每次选择」全量留痕，
并支持「取某场战斗区间内的剧情」→ 写进 NPC 队友的经历。

## 为什么要有这个文件
账本本身是纯逻辑（`DramaLedgerCore`，由 `csharp/tests/DramaLedgerCoreTests` 逐条断言），
但**接线错了表现全是静默的**：
  · 判不出归属的页没进账本 ⇒ 看起来一切正常，只是"进副本那一幕"永远缺席；
  · `end` 钉子插在提前 return 之后 ⇒ 从第二场战斗起剧情再也没记过，且没有任何报错；
  · 单人战斗也插 `end` ⇒ 孤儿钉子把真正那场的配对堵死，症状是"履历里剧情永远是空的"；
  · 窗没关就结束了 ⇒ 下一场 `BeginWindow` 被幂等判据挡掉（同一症状）；
  · 模板没注册 ⇒ 引擎 `找不到日志` 后**静默丢弃**，而托管层不抛异常。
这些在真机上只表现为"NPC 的经历里就是没有剧情"，没有任何异常可查。所以接线必须钉死。

## 钉六件事
  A. 账本**流式**记录：页与选项都进，且**判不出归属也进**（这是它比段多出来的覆盖）
  B. 段与账本**并行不反哺**：段原有的判据一个字不改（判不出就不入段、不许凭空造段）
  C. 事件钉子：起点由 `★直塞完成★` 插（不是 `BattleStart`：真机会重复 5 次），
     终点由 `BattleExit` 插，且**必须在任何提前 return 之前**
  D. 取区间用「最后一对 begin…end」，**不用时间窗**（真机：进副本那幕比队友进场早 95 秒）
  E. 写经历：只写 `_mates`（玩家那条不动）；另起一条，不塞进击杀模板
  F. 模板双保险：表里 `MID&904` + 运行时克隆注册；写完必须回读，未落库要明说
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _brace_body(src: str, start: int) -> str:
    """从 `start`（某方法签名处）截到该方法的**配对花括号结束**。

    为什么不用"切到下一个注释/下一个方法"：那样一旦格式变了就切失败，
    而 `body = src[start:]` 会让断言在**整个文件剩余部分**上命中 —— 假绿的经典形状。
    """
    i = src.find("{", start)
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(src)):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i: j + 1]
    return src[i:]


def _code(src: str) -> str:
    """剥掉 `//` 行注释（注释里写 API 名/反例是合法的）。"""
    return "\n".join(ln[: ln.find("//")] if "//" in ln else ln for ln in src.splitlines())


LEDGER = CS / "drama" / "DramaLedger.cs"
LEDGER_CORE = CS / "drama" / "DramaLedgerCore.cs"
FORMAT_CORE = CS / "drama" / "DungeonDramaCore.cs"
RECORDER = CS / "drama" / "DramaSegmentRecorder.cs"
FOLLOWUP = CS / "tools" / "TeamBattleFollowup.cs"
INJECT = CS / "tools" / "TeamContainerInject.cs"
OPTION = CS / "UI" / "DramaAiOption.cs"
MODMAIN = CS / "app" / "ModMain.cs"
MODIDS = CS / "game" / "ModIds.cs"


# ---------------------------------------------------------------- A. 流式记录

def test_ledger_files_exist():
    assert LEDGER_CORE.is_file(), "没有 DramaLedgerCore.cs（账本纯逻辑核）"
    assert LEDGER.is_file(), "没有 DramaLedger.cs（账本接线层）"
    assert FORMAT_CORE.is_file(), "没有 DungeonDramaCore.cs（渲染纯逻辑核）"


def test_pages_and_options_go_into_ledger_even_without_owner():
    """★ 这一条是整个功能的命门 ★

    段在 `unitId` 为空时 `return`（判不出就闭嘴，不许猜归属）——**这个判据必须保留**。
    但账本不行：真机上「进入副本」那一页（`610911`，选项「深入探索」）正是"没有归属"，
    而它恰恰是"怎么进的这个副本"的唯一线索。所以：
      · 账本调用必须在 `if (string.IsNullOrEmpty(unitId)) return;` **之前**；
      · 选项同理（UI 层那个 "本页没有归属" 的提前 return 里也要记一笔）。
    """
    code = _code(_read(RECORDER))
    # NotePage：账本调用出现在归属判据之前
    i_ledger = code.find("DramaLedger.NotePage(")
    i_guard = code.find("if (string.IsNullOrEmpty(unitId))")
    assert i_ledger >= 0, "分段器入口没有记账本 —— 账本永远不会被写入"
    assert i_guard >= 0, "分段器丢了「判不出对方 ⇒ 不入段」的判据"
    assert i_ledger < i_guard, \
        "账本调用在归属判据之后 —— 判不出归属的页（如进副本那一幕）会被一起丢掉"
    # NoteOption 同理
    j_ledger = code.find("DramaLedger.NoteOption(")
    j_guard = code.find("if (string.IsNullOrEmpty(unitId)) return;")
    assert j_ledger >= 0, "选项没有记账本"
    assert j_ledger < j_guard, "选项的账本调用在归属判据之后（无归属的选择会丢）"

    # UI 层：无归属点击的提前 return 里必须记账本
    ocode = _code(_read(OPTION))
    seg = ocode[ocode.find("private static void OnOptionTapped"):]
    seg = seg[: seg.find("string pname = SafeName")] if "string pname = SafeName" in seg else seg
    assert "DramaLedger.NoteOption" in seg, \
        "「本页没有归属」的选项点击没有进账本 —— 进副本那一幕的「深入探索」就是这么丢的"


def test_ledger_does_not_feed_back_into_segments():
    """账本**只读不反哺**：一段内容绝不能因为"账本里有"就投给模型。

    段的「判不出就闭嘴」「换人 ⇒ 收口」是它正确性的骨架；一旦让它从账本捞别人的内容，
    就会把别人的话投给当前对话对象（09-23 出过同形事故：把闲聊聊成"我推辞了论道"）。
    实现约束：分段器只调账本的**写**接口（NotePage/NoteOption），从不调读接口。
    """
    code = _code(_read(RECORDER))
    for reader in ("TakeBattleWindow", "TakeWindow(", "DramaLedger.Range(", "DramaLedger.Report("):
        assert reader not in code, \
            f"分段器调用了账本的读接口 {reader} —— 账本不许反哺投递（会串人/串场）"


# ---------------------------------------------------------------- C. 事件钉子

def test_battle_begin_marker_is_on_injection_not_battlestart():
    """起点钉在 `★直塞完成★`，不是 `BattleStart`。

    真机证据（10-24，副本 4901）：两者在**同一毫秒**（18:31:24.103），但 `BattleStart`
    会被引擎重复调用 **5 次**（日志里连打 5 行），`★直塞完成★` 是我们的代码，一次就一次。
    而且语义更准：单人切磋不写履历，"有人被塞进容器"正是"这场要写履历"的前提。
    """
    code = _code(_read(INJECT))
    assert "DramaLedger.BeginWindow(" in code, \
        "★直塞完成★ 没有开战斗窗 —— 账本里不会有起点钉子，履历永远拿不到剧情"
    assert code.count("DramaLedger.BeginWindow(") == 1, \
        "BeginWindow 出现了多次（起点钉子必须只有一处、且幂等）"
    # 位置：必须在"直塞完成"那行日志之后（即真的塞完了才开窗）
    i_log = code.find("★直塞完成★")
    i_mark = code.find("DramaLedger.BeginWindow(")
    assert 0 <= i_log < i_mark, "BeginWindow 不在「直塞完成」之后 —— 可能队友还没进容器就开窗"

    assert "EBattleType.BattleStart" not in code, \
        "用了 BattleStart 当起点（真机会重复触发 5 次）"


def test_battle_end_marker_precedes_every_early_return():
    """`BattleExit` 里的关窗必须在**任何提前 return 之前**。

    这个方法有两处提前 return（找不到玩家 / 读不到坐标）。窗若留到它们之后才关：
      · 那两条路上 `end` 钉子永远插不上；
      · 窗仍处于"开着"⇒ 下一场 `BeginWindow` 被幂等判据挡掉；
      · 症状 = **从第二场战斗起剧情再也没记过**，而且一条报错都没有。
    """
    code = _code(_read(FOLLOWUP))
    body = code[code.find("private static void OnBattleExit"):]
    body = body[: body.find("private static void ", 10)] if "private static void " in body[10:] else body
    i_end = body.find("DramaLedger.EndWindow(")
    assert i_end >= 0, "BattleExit 没有关战斗窗 —— end 钉子永远插不上"
    i_armed_guard = body.find("if (!_armed) return;")
    i_player_guard = body.find("if (player == null)")
    assert i_armed_guard >= 0 and i_player_guard >= 0, "BattleExit 的提前 return 结构变了，需重新核对"
    assert i_end < i_armed_guard, "关窗在 `if (!_armed) return;` 之后（单人战斗路径会跳过它）"
    assert i_end < i_player_guard, "关窗在「找不到玩家」的提前 return 之后 —— 那条路上窗永远关不掉"


def test_window_open_close_are_idempotent():
    """开/关都必须幂等，且核里要有窗状态。

    为什么必须：一场副本里队友可能被**多次**塞进容器（多波次），`★直塞完成★` 会打多次；
    而单人战斗也会走 `BattleExit`。少任何一个幂等判据，症状都是"剧情永远为空"且无报错。
    """
    core = _code(_read(LEDGER_CORE))
    assert "internal bool BeginWindow" in core, "核里没有 BeginWindow"
    assert "internal bool EndWindow" in core, "核里没有 EndWindow"
    assert "_windowOpen" in core, "核里没有「窗是否开着」的状态 —— 无法幂等"
    assert "if (_windowOpen) return false;" in core, "BeginWindow 不幂等（多波次会插多根 begin）"
    assert "if (!_windowOpen) return false;" in core, "EndWindow 不判「开过没有」（会插孤儿 end）"
    assert "_windowOpen = false;" in core[core.find("internal void Reset()"):][:400], \
        "Reset（换世界）没清窗状态 —— 换档后第一场会因为「窗还开着」而记不到剧情"


def test_take_window_uses_marker_pair_not_time_window():
    """取区间用**最后一对 begin…end**，不用时间窗。

    真机反例（副本 4901）：进副本那一幕 `610911` 在 18:29:49，队友进场在 18:31:24
    —— 差 **95 秒**。任何"往前 60 秒"的时间窗都会把它切掉；放到 2 分钟又会混进副本外的闲聊。
    改由事件自己插钉子，边界就由**发生的事实**给出，不靠猜时长。
    """
    core = _code(_read(LEDGER_CORE))
    assert "TakeWindow(" in core, "核里没有 TakeWindow（按钉子取区间）"
    assert "StartFound" in core, "没有「起点有没有找到」的标记 —— 无法如实上报「只拿到后半段」"
    assert "HadAnyMarker" in core, "无法区分「这场没开过窗」与「开过但起点被裁掉了」"
    # 不许在写履历的路径上用时间窗
    code = _code(_read(FOLLOWUP))
    body = code[code.find("private static void WriteDungeonDrama"):]
    body = body[: body.find("private static bool EnsureDramaLogTemplate")]
    assert "TakeBattleWindow()" in body, "写履历没有从账本的战斗窗取内容"
    assert "DramaLedger.Range(" not in body, \
        "写履历用了时间区间读法 —— 会在「进副本那一幕」上判错（比队友进场早 95 秒）"


# ---------------------------------------------------------------- E. 写经历

def test_drama_log_written_to_mates_only():
    """只写 NPC 队友，玩家自己那条不动（用户口径 10-25）。"""
    code = _code(_read(FOLLOWUP))
    body = code[code.find("private static void WriteDungeonDrama"):]
    body = body[: body.find("private static bool EnsureDramaLogTemplate")]
    assert "_mates" in body, "没有遍历队友"
    assert "_mates[i]" in body or "foreach" in body, "没有按队友逐个写"
    # 玩家对象只能出现在 UnitConditionData 的构造里（那是"和谁一起"的上下文，不是"写给他"）
    cond_uses = body.count("new UnitConditionData(mate, player)")
    assert cond_uses == 1, \
        f"玩家对象的用法不对（UnitConditionData 构造应为 1 处，实际 {cond_uses}）"
    assert "_mates.Count == 0" in body, "没有「没队友就早退」的判据"
    # 玩家那条只应出现在 UnitConditionData 的构造里，不许出现 player 版本的 AddLog
    assert "TryAddLog(logMgr, player" not in body, \
        "把副本剧情写进了玩家自己的经历 —— 用户要求只写 NPC"


def test_drama_log_is_separate_row_not_stuffed_into_kill_template():
    """另起一条经历，不塞进击杀模板。

    击杀模板 `与{0}同闯秘境，斩杀{1}。` 两个槽都是语义槽；塞一段话进 `{1}` 会变成
    「斩杀竹妖 ×59、……、你选择了「誓死抵抗」。」—— 病句，且本场无击杀时连槽都没有。
    """
    code = _code(_read(FOLLOWUP))
    body = code[code.find("private static void WriteDungeonDrama"):]
    body = body[: body.find("private static bool EnsureDramaLogTemplate")]
    assert "TryAddLog(logMgr" in body and "DramaLogKey" in body, "没有用独立 key 写剧情那条"
    assert "KillLogKey" not in body, "把剧情塞进了击杀模板（会渲染成病句）"


def test_drama_log_verifies_landing():
    """模板缺失时引擎只 `LogError` + 静默丢弃，所以必须回读。"""
    code = _code(_read(FOLLOWUP))
    body = code[code.find("private static void WriteDungeonDrama"):]
    body = body[: body.find(" static string UnitIdOf(")]
    assert "TryAddLog(" in body, "没有走统一写经历入口（它内部才有回读）"
    try_body = code[code.find(" static bool TryAddLog("):]
    try_body = try_body[: try_body.find(" static bool PendingHasValue(")]
    assert "PendingHasValue" in try_body, "写经历没有回读暂存层"
    assert "未落库" in try_body, "读不到时必须明说未落库"


def test_no_empty_row_when_no_drama():
    """一场战斗可能完全没有剧情 —— 那时**不许**写空条目（会显示成「…：」）。"""
    code = _code(_read(FOLLOWUP))
    body = code[code.find("private static void WriteDungeonDrama"):]
    body = body[: body.find("private static bool EnsureDramaLogTemplate")]
    assert "Items.Count == 0" in body, "没有判「窗口内没有剧情」就往下写（会写空条目）"
    assert "buf.Count == 0" in body, "没有判「过滤后为空」（私窗全被滤掉时会写空条目）"
    assert "lines.Count == 0" in body, "没有判「格式化后为空」"


# ---------------------------------------------------------------- F. 模板

def test_template_runtime_registration_and_readback():
    """模板双保险：表里有就用表里的；没有就运行时克隆注册；注册完必须回读。

    10-25 抽出通用版 `EnsureLogTemplate(id, key, ch, label)`：副本见闻一条 + 决斗三条
    共用同一份"查表 → 克隆 → 设 id/keyID/ch → AddItem → 回读"。断言因此落在通用版上，
    另有一条钉住"副本那条确实走通用版、正文是纯 `{0}`"。
    """
    # 11-19（A4）：通用版从 `TeamBattleFollowup` 提到 `game/RoleLogTemplate.cs`（唯一实现），
    # 修掉了"只有 NpcDuel 那份补按键索引"的漂移。断言因此落到新文件上。
    code = _code(_read(CS / "game" / "RoleLogTemplate.cs"))

    at = code.find(" bool EnsureLogTemplate(")
    assert at >= 0, "通用模板注册入口不见了（副本/决斗四条模板共用那一份）"
    body = _brace_body(code, at)
    assert "GetItem(id)" in body, "没有先查表里有没有"
    assert ".Clone()" in body, "没有克隆现成模板（直接改原行会连同行/重伤/击杀一起改坏）"
    assert "AddItem(" in body, "没有 AddItem 注册"
    assert "row.id = id" in body, "没有设 id"
    assert "row.keyID = key" in body, "没有设 keyID（字符串 key 的 AddLogData 查的是这个）"
    assert "row.ch = ch" in body, "没有写模板正文"
    # 回读
    assert body.count("GetItem(id)") >= 2, "注册后没有回读确认"

    followup = _code(_read(FOLLOWUP))
    wrap_at = followup.find(" bool EnsureDramaLogTemplate(")
    assert wrap_at >= 0, "副本见闻的包装函数不见了"
    wrap = _brace_body(followup, wrap_at)
    assert 'RoleLogTemplate.EnsureLogTemplate(ModIds.LogTeamDungeonDrama, DramaLogKey, "{0}",' in wrap, \
        "副本见闻没有走通用版，或正文不是纯 {0} 占位（自由文本无处可放）"


def test_template_id_is_registered_in_modids():
    """模板 id 必须登记在 `ModIds`（ID 空间唯一事实来源），且**不许**与既有段冲突。"""
    # ★ 必须去注释 ★：文件头注释里也写着「`Mod + 904`」，用原文搜会让断言在注释上命中（假绿）
    ids = _code(_read(MODIDS))
    assert "LogTeamDungeonDrama" in ids, "ModIds 里没有登记 LogTeamDungeonDrama"
    assert "LogTeamDungeonDramaKey" in ids, "ModIds 里没有登记 key 常量"
    import re
    m = re.search(r"LogTeamDungeonDrama\s*=\s*Mod\s*\+\s*(\d+)", ids)
    assert m, "LogTeamDungeonDrama 没有用 `= Mod + N` 的形式登记（偏移必须是显式常数）"
    off = int(m.group(1))
    assert off == 904, \
        f"LogTeamDungeonDrama 的偏移应为 904（表里那行是 MID&904，改了就对不上），实际 {off}"
    # 必须落在本 mod 的私有段内（0..999），否则会踩别人的 id
    assert 0 <= off <= 999, f"偏移 {off} 超出私有段上界 999"
    # 不许与既有登记冲突（同一偏移被两个常量用 = 迟早撞号）
    all_offs = re.findall(r"internal const int \w+\s*=\s*Mod\s*\+\s*(\d+)", ids)
    dup = [x for x in set(all_offs) if all_offs.count(x) > 1 and int(x) == off]
    assert not dup, f"偏移 {off} 被多个常量重复使用（ID 撞号）"


def test_template_row_exists_in_table():
    """表里也要有这行（长期方案）。没有也能跑（运行时注册兜底），但表是兜底的那一层。"""
    d = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/RoleLogLocal.xlsx")
    if not d.is_file():
        return
    try:
        from openpyxl import load_workbook
    except ImportError:
        return
    wb = load_workbook(d, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    by_key = {}
    for r in rows[3:]:
        if not r or not r[1]:
            continue
        by_key[str(r[1]).strip()] = r
    key = "eventLogTeamDungeonDrama"
    assert key in by_key, f"RoleLogLocal 里缺 {key}（表是长期兜底那一层）"
    row = by_key[key]
    assert str(row[0]).strip() == "MID&904", f"{key} 的 id 应为 MID&904（实际 {row[0]!r}）；ID 一旦入档不可改"
    ch = str(row[14] or "")
    assert ch == "{0}", f"{key} 的 ch 必须是纯 {{0}}（自由文本整段塞进去）：{ch!r}"


# ---------------------------------------------------------------- 生命周期

def test_world_change_and_save_are_hooked():
    """换世界清账、存档落盘 —— 与 `UnitIdRegistry` / `DramaSegmentRecorder` 同纪律。"""
    code = _code(_read(MODMAIN))
    assert "DramaLedger.OnWorldChanged()" in code, \
        "换存档没有清账本 —— 会把上一档的剧情写进这一档的履历（静默错内容）"
    assert "DramaLedger.Flush()" in code, "存档没有落盘账本"
    # 顺序：清账必须与段清理在同一段（都在进入世界那个 handler 里）
    i_seg = code.find("DramaSegmentRecorder.OnWorldChanged()")
    i_led = code.find("DramaLedger.OnWorldChanged()")
    assert 0 <= i_seg < i_led, "账本清理不在段清理之后（两者应成对出现在同一段）"


def test_ledger_never_throws_into_gameplay():
    """账本是附加价值：任何异常都不许冒泡到战斗/剧情链。

    判据 = **每个对外入口的函数体内部**都有 try。按函数体切，不能全文件搜 `catch`
    （那样只要有一个方法包了 try，其余全裸奔也会通过 —— 假绿）。
    """
    code = _code(_read(LEDGER))
    for fn in ("NotePage", "NoteOption", "Mark", "BeginWindow", "EndWindow",
               "TakeBattleWindow", "OnWorldChanged", "Flush"):
        at = code.find("internal static " + fn + "(")
        # 允许返回类型中间夹着东西（`internal static bool BeginWindow(`）
        if at < 0:
            at = code.find(" " + fn + "(")
        assert at >= 0, f"找不到账本入口 {fn}"
        body = code[at:]
        nxt = body.find("\n        internal static ", 10)
        if nxt > 0:
            body = body[:nxt]
        assert "try" in body and "catch" in body, \
            f"{fn} 的函数体里没有 try/catch —— 账本异常会打断战斗/剧情链"


def test_flush_is_batched_not_per_page():
    """战斗中每页都落盘会掉帧 ⇒ 必须攒批。"""
    code = _code(_read(LEDGER))
    assert "FlushEveryEntries" in code, "没有按条数攒批"
    assert "FlushEverySeconds" in code, "没有按时间攒批"
    assert "File.AppendAllText" in code, "不是追加写（每次重写整个文件会越来越卡）"
    assert "File.WriteAllText(path" not in code, "用了整文件重写 —— 账本只许追加"


def test_ledger_capacity_is_bounded_and_trim_is_loud():
    """容量必须有上限，且**裁掉要留痕**（静默丢内容是本工程最怕的失败形态）。

    ★ 断言必须落到 `Trim()` 的**函数体内部**★：只搜 `"DroppedTotal" in core` 是假绿
    （属性声明本身就让字符串存在），真正要钉的是"裁的时候真的加了这个数"。
    """
    core = _code(_read(LEDGER_CORE))
    assert "MaxEntries" in core, "没有容量上限（会无限涨）"
    # 断言收敛到 Trim 的函数体（按**花括号配对**切，不能靠"下一个 ///
    #   —— Trim 后面紧跟的是方法而不是注释，靠注释切会切失败、body 变成文件剩余全部，
    #   于是断言在别的方法上命中 ⇒ 假绿）
    at = core.find("private void Trim()")
    assert at >= 0, "找不到 Trim()（容量裁剪的唯一实现点）"
    body = _brace_body(core, at)
    assert "RemoveRange" in body, "Trim 没有真的丢弃最旧的条目"
    assert "DroppedTotal" in body, \
        "Trim 里没有累加 DroppedTotal —— 裁掉的内容会被**静默**遗忘（本工程最怕的失败形态）"
    assert "DroppedTotal > 0" in core, "没有任何地方读 DroppedTotal（记了但没人看 = 等于没记）"
    assert "Trimmed" in core, "取窗口时没有「账本被裁过」的标记（读的人无从知道结果是否完整）"


def test_render_caps_are_documented():
    """写进经历的内容有上限（否则会把别的经历埋掉），且丢了多少要能说清。"""
    core = _read(FORMAT_CORE)
    assert "MaxLines" in core, "没有行数上限"
    assert "MaxTotalChars" in core, "没有总字数上限"
    assert "DroppedCount" in core, "没有「丢了几条」的统计（超限会静默截断）"

def test_solo_battle_leaves_no_trace():
    """单人战斗（切磋 / 单人副本）**什么都不做**，也不许在账本里留下痕迹。

    三层防线，缺一层都会出问题：
      ① `BattleEndFront` 判不出队友 ⇒ `_armed=false` ⇒ `BattleExit` 直接 return（不写经历）；
      ② `BattleExit` 里的关窗**在 return 之前**跑，所以单人战斗也会走到它 ——
         但它必须幂等（没开过窗就什么都不做），否则会插一根**孤儿 end**；
      ③ 孤儿 end 的后果不是"多一行日志"，而是把**真正那场**的配对堵死
         （抽取是"从最后一个 end 往前找 begin"，途中撞到 end 就停）⇒ 履历里剧情永远为空。
    这条同时解释了为什么 `EndWindow` 的幂等判据不能用"调用方保证"替代。
    """
    # ① 不写经历
    code = _code(_read(FOLLOWUP))
    assert "if (!_armed) return;" in code, \
        "BattleExit 没有在无队友时早退 —— 单人战斗也会写经历/归队"

    # ② 关窗在早退之前，且必须幂等
    core = _code(_read(LEDGER_CORE))
    at = core.find("internal bool EndWindow")
    assert at >= 0
    body = _brace_body(core, at)
    assert "if (!_windowOpen) return false;" in body, \
        "EndWindow 不判「窗开过没有」—— 单人战斗会插孤儿 end，把下一场真战斗的配对堵死"

    # ③ 单人战斗不会开窗。
    #    10-25 把直塞抽成 `InjectInto()` 之后，代码的**文本顺序**不再等于调用顺序
    #    （`InjectInto` 定义在 `Prefix` 前面）。所以这里按**结构**断言：
    #      · 窗开在 `InjectInto` 尾部（`★直塞完成★` 之后）；
    #      · `InjectInto` 的入口只有两个 —— 副本走 `Prefix`（先取名单，空名单 `return false` 取消本模组选人），
    #        决斗走前面那个分支（只有玩家点"同意"才调）。
    inj = _code(_read(INJECT))
    at_prefix = inj.find("private static bool Prefix(")
    at_inj = inj.find("private static bool InjectInto(")
    assert at_prefix >= 0 and at_inj >= 0, "直塞结构变了，需重新核对"

    inject_body = _brace_body(inj, at_inj)
    assert "DramaLedger.BeginWindow(" in inject_body, \
        "开窗不在 InjectInto 里（★直塞完成★之后）—— 那时还不知道有没有队友"

    prefix = _brace_body(inj, at_prefix)
    at_mates = prefix.find("var mates = TeamTool.Members(")
    assert at_mates >= 0, "直塞入口的队友名单取法变了，需重新核对"
    at_early = prefix.find("if (mates == null || mates.Count == 0) return false;")
    at_call = prefix.find("return InjectInto(data, mates, dupSlots);")
    assert at_early > at_mates, "开窗之前没有「没有队友就早退」的判据 —— 单人副本也会开窗"
    assert at_call > at_early, "早退必须发生在调 InjectInto 之前（否则单人副本照样开窗）"
