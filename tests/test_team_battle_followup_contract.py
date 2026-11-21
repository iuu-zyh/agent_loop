"""`TeamBattleFollowup` 契约 —— 组队打完副本之后的两件收尾事。

用户口径（10-18）：
  ① 组队状态下结束战斗后，**队员位置应与玩家一致**（把队员传送到玩家处）；
  ② 把「一起组队打了副本」写进**经历**，让 agent 日后自己知道。

这两件事都发生在一次**游戏事件**里，错了没人报错、只会静默不生效（或者更糟：闪退），
所以全部钉成契约。历史上已经踩过两次：
  · `DungeonTriggerWatch` 用 Harmony 挂配置热路径 + 启动期全程序集扫描 → **游戏启动闪退**
  · 订阅了事件却把委托存在局部变量里 → 委托被 GC 回收 → **订阅成功但永不回调**
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
FOLLOWUP = CS / "tools" / "TeamBattleFollowup.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _code(src: str) -> str:
    """剥掉 `//` 行注释（注释里写 API 名是合法的，扫描要只看真代码）。"""
    return "\n".join(ln[: ln.find("//")] if "//" in ln else ln for ln in src.splitlines())


def test_followup_exists_and_hooked_at_startup():
    assert FOLLOWUP.exists(), "没有 TeamBattleFollowup.cs"
    modmain = _code(_read(CS / "app" / "ModMain.cs"))
    assert "TeamBattleFollowup.EnsureInstalled()" in modmain, \
        "ModMain 没挂 TeamBattleFollowup —— 战后归队/写经历永远不会发生"


def test_no_harmony_only_game_events():
    """不许用 Harmony。10-18 那次闪退就是 Harmony 挂配置热路径造成的。"""
    code = _code(_read(FOLLOWUP))
    assert "Harmony" not in code, "TeamBattleFollowup 用了 Harmony（10-18 闪退教训）"
    assert "g.events.On(" in code, "没有走游戏自己的事件分发"


def test_subscribes_the_two_events_it_needs():
    """`BattleEndFront` 判「有没有队友」，`BattleExit` 才动手。

    真机实测：`BattleEndFront` 时 `npcUnits` 还在（3 人），`BattleEnd`/`BattleExit` 已经是 0。
    所以顺序反了 = 永远判不出「这场是不是组队打的」。
    """
    code = _code(_read(FOLLOWUP))
    assert "EBattleType.BattleEndFront" in code, "没订阅 BattleEndFront（那一拍才看得到队友）"
    assert "EBattleType.BattleExit" in code, \
        "没订阅 BattleExit —— 在 BattleEnd 动手会被游戏自己的战后收场盖掉"


def test_handlers_are_strongly_referenced():
    """委托必须存**字段**。存局部变量 = 被 GC 回收 = 订阅了也永不回调。"""
    code = _code(_read(FOLLOWUP))
    assert re.search(r"private\s+static\s+Action<ETypeData>\s+_frontHandler", code), \
        "BattleEndFront 的委托没有存静态字段（会被 GC）"
    assert re.search(r"private\s+static\s+Action<ETypeData>\s+_exitHandler", code), \
        "BattleExit 的委托没有存静态字段（会被 GC）"


def test_participant_detection_uses_slot_luck_not_npcCount():
    """判「这是队友」必须查号位气运，不能靠 `npcUnits` 非空。

    副本本来就自带 NPC，`npcUnits` 非空完全正常 —— 拿它当判据会在**任何**副本里
    给无关 NPC 写"我们一起下过副本"。
    """
    code = _code(_read(FOLLOWUP))
    assert "TeamTool.SlotOf(" in code, "没用 SlotOf 判队友（会被副本自带 NPC 骗）"
    assert "IsPlayer(" in code, "没排除玩家（玩家不是自己的队友）"


def test_dedups_by_unit_id():
    """同一人可能被塞进容器两次（老配置 `addBattleNPCData` 不去重）。

    不去重的后果：对同一个人传两次、写两条一模一样的经历。
    """
    code = _code(_read(FOLLOWUP))
    assert "unitID" in code and "Contains(" in code, "成员快照没按 unitID 去重"


def test_teleport_uses_engine_action_and_verifies():
    """传送只走引擎动作，且**返回码非 0 就算没成功**，还要回读坐标复核。"""
    code = _code(_read(FOLLOWUP))
    assert "UnitActionMoveNPC" in code, "没用引擎动作传送"
    assert "CreateAction(" in code, "没落刀"
    assert re.search(r"ret\s*!=\s*0", code), "没检查 CreateAction 返回码（会谎报已归队）"
    # 回读复核：返回 0 也要读坐标，否则"动作创建成功但没动"会被当成成功
    assert "SamePoint(" in code, "没有回读坐标复核"
    # 明确不用 SetPoint 硬写（绕过寻路/格子索引，风险未知）
    assert "SetPoint(" not in code, "用了 SetPoint 硬写坐标（绕过引擎，风险未知）"


def test_never_throws_out_of_callbacks():
    """事件回调里抛异常会打断游戏自己的分发循环 —— 必须自己兜住。"""
    code = _code(_read(FOLLOWUP))
    # 两个回调 + 写经历入口都要有 catch
    for fn in ("OnBattleEndFront", "OnBattleExit", "WriteJourneyLogs"):
        i = code.index(f"private static void {fn}(")
        body = code[i:]
        nxt = body.find("\n        private static", 10)
        if nxt > 0:
            body = body[:nxt]
        assert "catch" in body, f"{fn} 没有兜异常"


def test_writes_journey_log_to_mates_only():
    """经历**只写给 NPC 队友**，玩家自己那条不写。

    ★ 10-25 用户口径反转（原契约是「玩家 + 每个队友」）★
      原话：「战斗的经历写入只写入到 npc 里面，不写玩家自身的」。
      为什么要写成"断言不许出现"而不是"断言出现某个开关"：
      留一个默认关闭的开关，下次有人"顺手打开"就会悄悄变回旧行为；
      而旧行为在这条口径下是**错的**（玩家能在自己的经历面板里看到一行他不需要的记录）。
    """
    code = _code(_read(FOLLOWUP))
    assert "AddLogData(" in code, "没写经历"
    body = code[code.index("private static void WriteJourneyLogs("):]
    assert "for (int i = 0; i < _mates.Count; i++)" in body, \
        "没给每个队友各写一条（只写了一处 = 有人读不到）"
    # 不许对 player 调用写经历（同行那条与剧情那条都不许）
    assert "WriteOne(logMgr, player" not in body, \
        "给玩家自己写了同行经历 —— 用户明确只要 NPC"
    assert "TryAddLog(logMgr, player" not in body, \
        "给玩家自己写了经历（TryAddLog 直调）—— 用户明确只要 NPC"
    # `player` 仍应出现（要拿它构造 UnitConditionData / 取玩家名做同行名单）
    assert "playerName" in body, \
        "玩家名还要用在队友那条的同行名单里（不该整个删掉）"


def test_only_acts_when_a_team_battle_happened():
    """没队友参战的战斗（切磋、单人副本）必须**什么都不做**。"""
    code = _code(_read(FOLLOWUP))
    body = code[code.index("private static void OnBattleExit("):]
    assert re.search(r"if\s*\(!\s*_armed\s*\)\s*return", body), \
        "OnBattleExit 没有 `if (!_armed) return` 的早退 —— 会在所有战斗里都动队友"
    assert "_mates.Count == 0" in code, "BattleEndFront 没在 0 队友时早退"


def test_journey_log_uses_rolelog_key_and_verifies_landing():
    """10-19 真机定案：`AddLogData` 第二位必须是 `RoleLogLocal.keyID`，不是正文。

    旧版把「与…同闯秘境，并肩杀出」整句当 key 传 ⇒ 引擎 `找不到日志：…`
    （`LogItemData:AddLogsMain`）后静默丢弃，而托管层不抛异常 ⇒ 四行 "写经历" 全是假成功。
    契约：① 用固定 key；② values 走 `{0}`/`{1}` 占位；③ 写后回读暂存层，未落库必须明说。

    ★ 10-21 起有**两套**模板（击杀战报），占位符个数不同 ★
      · 无击杀 → `eventLogTeamDungeon` / `...Hurt`，模板「与{0}同闯秘境，并肩作战。」
        ⇒ values 传 **1** 个：`new string[] { matesValue }`
      · 有击杀 → `eventLogTeamDungeonKill`，模板「与{0}同闯秘境，斩杀{1}。」
        ⇒ values 传 **2** 个：`new string[] { matesValue, killSummary }`
    两边都必须按占位符个数传，多传/少传都会渲染出半句或 `{1}` 原样。

    另有**回落**：击杀模板若没落库（表里缺 `MID&3`），必须退回原模板，
    否则"有击杀"这一场里所有人的同行经历全丢 —— 那比没有击杀统计更糟。
    """
    code = _code(_read(FOLLOWUP))
    assert "eventLogTeamDungeon" in code, "没用 RoleLogLocal.keyID（正文当 key 会被静默丢弃）"
    assert "JourneyLogKey" in code, "缺统一 key 常量"
    # ① 单占位模板：values 必须是长度 1 的数组
    assert "new string[] { matesValue }" in code, "无击杀模板没按单个 {0} 占位传"
    # ② 双占位模板：击杀战报传两个值
    assert "new string[] { matesValue, killSummary }" in code, \
        "击杀模板没按 {0}/{1} 两个占位传（会渲染出半句）"
    assert "AddLogData(unit, key" in code, "AddLogData 第二位不是 keyID"
    assert "GetCacheWriteLog" in code, "写后没回读暂存层（无法发现假成功）"
    assert "未落库" in code, "回读失败时必须明说未落库，不许继续报成功"
    # ③ 击杀模板写失败必须回落，不许整条经历一起丢
    assert "fallback" in code.lower() or "回落" in code, \
        "击杀模板失败时没有回落到原模板"


def test_kill_template_row_exists_in_table():
    """击杀模板 `MID&3` 必须真的在 `RoleLogLocal.xlsx` 里，且占位符是 `{0}`/`{1}`。

    为什么值得单独钉：代码写得再对，表里没有这行 ⇒ 引擎 `找不到日志` 后静默丢弃，
    玩家侧只看到"有击杀的那一场经历全没了"，而且**托管层不报错**。
    三个 key 的分工（表 + 代码必须一致）：

    | key | 模板 | values |
    |---|---|---|
    | `eventLogTeamDungeon` | 与{0}同闯秘境，并肩作战。 | `[同行者名单]` |
    | `eventLogTeamDungeonHurt` | 与{0}同闯秘境，战斗中身负重伤。 | `[同行者名单]` |
    | `eventLogTeamDungeonKill` | 与{0}同闯秘境，斩杀{1}。 | `[同行者名单, 战绩]` |
    """
    from pathlib import Path

    d = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/RoleLogLocal.xlsx")
    if not d.is_file():
        return  # 不在本机就没有表可查（测试仍应通过）
    try:
        from openpyxl import load_workbook
    except ImportError:
        return
    ws = load_workbook(d, data_only=True)[load_workbook(d, read_only=True).sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    # 列：A=id B=keyID … O=ch P=tc Q=en R=kr
    by_key = {}
    for r in rows[3:]:
        if not r or not r[1]:
            continue
        by_key[str(r[1]).strip()] = r

    expect = {
        "eventLogTeamDungeon": ("{0}",),
        "eventLogTeamDungeonHurt": ("{0}",),
        "eventLogTeamDungeonKill": ("{0}", "{1}"),
    }
    for key, phs in expect.items():
        assert key in by_key, f"RoleLogLocal 里缺 {key}（引擎会静默丢弃这条经历）"
        ch = str(by_key[key][14] or "")
        assert ch, f"{key} 的中文正文是空的"
        for ph in phs:
            assert ph in ch, f"{key} 的模板缺占位符 {ph}：{ch!r}"
    # 击杀模板必须恰好两个占位符（多一个少一个都会渲染错）
    kill_ch = str(by_key["eventLogTeamDungeonKill"][14] or "")
    assert kill_ch.count("{") == 2, f"击杀模板占位符个数不对（应恰好 2 个）：{kill_ch!r}"


def test_journey_log_forces_merge_and_invalidates_panel_cache():
    """10-19 用户实测：数据落库了，但面板「经历」要等月结/存档才显示。

    反汇编定案：`AddLogData` 只进暂存层；`WriteLogData → MerageAddLog →
    DataUnitLog.LogData::AddLog` 只追加 `allLog`，不失效 `_allLogData` /
    `lastUpdateLogDataMonth`，面板读的是解码缓存。
    契约：写完必须主动 `WriteLogData`，把解码缓存置脏，再回读 `allLogData` 验证。
    """
    code = _code(_read(FOLLOWUP))
    assert "ForceMergeToPanel" in code, "没有立即合并到面板的入口"
    assert "WrittenUnits" in code or "writtenUnits" in code, "没有收集成功写入的 unit"
    assert "WriteLogData" in code, "没走引擎的合并链（SaveLogData 同款）"
    assert "lastUpdateLogDataMonth" in code, "没把 DataUnitLog.LogData 的解码缓存置脏"
    assert "GetLogDataSync" in code and "allLogData" in code, "合并后没回读面板数据"
    assert "✅已显示" in code and "合并后仍读不到" in code, "回读结果必须如实报，不许猜"
    assert "try" in code and "catch" in code, "合并失败不能影响归队/战斗"


def test_hurt_mate_gets_luck_auto_leaves_and_is_blocked_from_reinvite():
    """10-19 需求：副本里倒下的队友要挂「内外重伤」、自动离队、重伤未愈不能重新入队。

    判据不能只看配置链的 6009（`unitAddEffect_MID&003` 走条件组，探针实测 0 人）；
    这里在 `BattleEndFront` 用战场单位 `isDie` 判，`BattleExit` 兜底补挂。
    """
    code = _code(_read(FOLLOWUP))
    assert "SnapshotDeadMates" in code, "没有从战场单位快照倒下的队友"
    assert "GetAllUnitIncludeDie" in code, "没读含尸体的战场单位列表"
    assert "isDie" in code and "worldUnitData" in code, "没按 isDie + unitID 映射到世界 NPC"
    assert "ApplyHurtAndLeave" in code, "没有重伤处理入口"
    assert "TeamTool.AddLuck(m, hurtId" in code, "倒下的队友没有兜底挂重伤气运"
    assert "TeamTool.RemoveAllSlotLucks" in code, "重伤后没有摘掉全部号位气运（不会自动离队）"
    assert "TeamTool.SyncTeamState(0)" in code, "离队后没刷新玩家侧「组队中」"

    team = _code(_read(CS / "tools" / "TeamTool.cs"))
    body = team[team.index("private static JObject Invite("):]
    body = body[: body.index("private static JObject Dismiss(")]
    assert "HurtLuckId()" in body, "邀请入口没查重伤气运 id"
    assert "HasLuck(mate, hurtId)" in body, "邀请入口没拦重伤未愈的 NPC"
    assert "身负重伤" in body, "重伤拦截没有明确提示"


def test_hurt_journey_log_uses_different_text_key():
    """重伤 NPC 的经历必须和普通同行不同（用户明确要求）。"""
    code = _code(_read(FOLLOWUP))
    assert "eventLogTeamDungeonHurt" in code, "没有重伤经历文案键"
    assert "HurtLogKey" in code, "没有重伤 key 常量"
    assert "战斗中身负重伤" in code, "重伤经历文案没写清楚"
