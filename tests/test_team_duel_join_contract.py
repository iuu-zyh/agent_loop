"""决斗参战（type=40，攻击/寻仇）契约测试（10-25）

为什么要有它：这条链上**每一个判据错在真机上都是静默的** ——

  · 拒绝时"放行原方法" ⇒ 原方法走 `ConfNpcCondition MID&003`（条件就是三枚号位气运），
    把队友重新选出来塞进容器 = **变相同意**，玩家点"我一个人"却还是三个人上场；
  · 副本那条路被顺手改动 ⇒ 现有组队下副本全部回归（这条是用户已经在用的功能）；
  · 经历模板 id 被改 ⇒ 老存档里那条经历指向别的模板或查不到（存档存的是数字 id）；
  · 判据里出现"等 N 秒" ⇒ 把"玩家点得慢"和"玩家不打算打"混为一谈（用户 10-25 明确否掉）；
  · 确认窗立绘传反 ⇒ 不报错、不抛异常，玩家只会看到两个自己（本仓踩过两次）。

跑法：python3 -m pytest tests/test_team_duel_join_contract.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSHARP = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CSHARP / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 拒绝分支

def test_refuse_branch_preserves_original_container_and_skips_own_selection():
    """拒绝带队友时跳过本模组选人，保留原版敌人和剧情角色。"""
    src = _read("tools/TeamContainerInject.cs")
    # 决斗分支必须存在，且以 type==40 为判据
    assert "ModIds.DungeonTypeDuel" in src, "决斗分支不见了（type=40 的判据）"
    assert "dungeonBaseItem" in src, "决斗分支必须从 dbi 读类型（配表事实），不要改用重载名推断"

    # 找到决斗分支那一段
    i = src.index("★ 决斗分支")
    j = src.index("// ① 没组队就完全不干预", i)
    seg = src[i:j]

    assert "TeamJoinGate.TakeConsent()" in seg, "决斗分支必须取玩家表态"
    assert "dJoinAnswer.Yes" in seg or "JoinAnswer.Yes" in seg, "同意才注入"
    assert ".Clear()" not in seg, "拒绝不能清空原版共享 NPC 容器"
    assert re.search(r"return false", seg), "拒绝必须 return false（跳过原方法）"


def test_decided_flag_is_per_battle():
    """`_decided` 必须每场清一次 —— 否则第二场决斗永远不再注入/不再拒绝。"""
    src = _read("tools/TeamContainerInject.cs")
    assert "_decided = false;" in src, "缺少 _decided 的清零点"
    # 清零必须发生在 ResetForBattle（BattleEndFront 开头）里
    i = src.index("internal static void ResetForBattle()")
    j = src.index("}", src.index("_decided = false;", i))
    assert i < src.index("_decided = false;", i) < j


# ---------------------------------------------------------------- ② 副本路径不变

def test_dungeon_path_still_untouched():
    """普通副本仍注入真实队友，空队必须取消本模组选人，防止克隆原版 NPC。"""
    src = _read("tools/TeamContainerInject.cs")
    tail = src[src.index("// ① 没组队就完全不干预"):]
    assert "if (mates == null || mates.Count == 0) return false;" in tail, "空队必须取消本模组选人"
    assert "return InjectInto(data, mates, dupSlots);" in tail, "有队友必须走直塞"


def test_inject_into_is_shared_by_both_paths():
    """注入逻辑只许有一份（副本与决斗共用），否则两条路会各自漂移。"""
    src = _read("tools/TeamContainerInject.cs")
    assert src.count("private static bool InjectInto(") == 1
    assert "★直塞完成★" in src, "原有直塞日志不许丢（真机排障靠它）"
    assert "DramaLedger.BeginWindow" in src, "剧情账本的战斗窗口起点必须还在直塞里"


# ---------------------------------------------------------------- ③ 经历模板 id

def test_log_template_ids_are_pinned():
    """经历模板的数字 id 一旦入档不能改（存档存的是 id）。"""
    src = _read("game/ModIds.cs")
    assert "internal const int LogTeamDuel = Mod + 905;" in src
    assert "internal const int LogTeamDuelDrama = Mod + 906;" in src
    assert "internal const int LogTeamDuelHurt = Mod + 907;" in src
    assert 'internal const string LogTeamDuelKey = "eventLogTeamDuel";' in src
    assert 'internal const string LogTeamDuelDramaKey = "eventLogTeamDuelDrama";' in src
    assert 'internal const string LogTeamDuelHurtKey = "eventLogTeamDuelHurt";' in src
    # 与已有副本段不冲突
    assert "Mod + 904" in src


def test_duel_uses_own_wording_not_dungeon():
    """决斗必须用自己的文案：不许再出现"同闯秘境/斩杀"这类副本措辞。"""
    src = _read("tools/TeamBattleFollowup.cs")
    assert "DuelJourneyTpl" in src and "并肩应战" in src
    assert "DuelDramaTpl" in src and "共历此战" in src
    assert "WriteDuelLogs" in src
    # 分类判据是 dbi.type == 40
    i = src.index("private static int ReadBattleKind()")
    seg = src[i:i + 900]
    assert "dungeonBaseItem" in seg and "DungeonTypeDuel" in seg
    # 决斗分支必须真的被调用
    assert "if (_kind == KindDuel) WriteDuelLogs(player);" in src


# ---------------------------------------------------------------- ④ 没有秒数兜底

def test_core_has_no_time_based_fallback():
    """判据核里不许出现任何"等 N 秒"的东西（用户 10-25 明确否掉）。"""
    src = _read("state/TeamJoinCore.cs")
    for bad in ("DateTime", "Time.", "unscaledTime", "Stopwatch", "Thread.Sleep", "TimeSpan"):
        assert bad not in src, f"判据核里出现了时间判据：{bad}"
    # 生命周期只认状态转移
    assert "OnFlowStateChanged" in src
    assert "BattleFlowState.InBattle" in src and "BattleFlowState.Idle" in src


def test_gate_only_asks_on_pre_window():
    """只在应战窗亮着时问（其余时机零开销），且不重复弹。"""
    src = _read("tools/TeamJoinGate.cs")
    assert "BattlePreShowing()" in src
    assert "if (!pre) { _lastWhy = \"\"; return; }" in src or "if (!pre)" in src
    assert "ShouldAsk(" in src, "判据必须在核里（gate 不做第二套判断）"


# ---------------------------------------------------------------- ⑤ 立绘位

def test_confirm_window_portrait_sides():
    """确认窗立绘：左位 = 玩家、右位 = 对手（传反了只会看到两个自己）。"""
    src = _read("tools/TeamJoinGate.cs")
    i = src.index("ShowConfirmSimple(")
    seg = src[i:i + 400]
    # 第一个实参是段基址，第二是 left（玩家），第三是 right（attacker）
    assert "ModIds.DramaTeamJoinBase" in seg
    lines = [ln.strip() for ln in seg.splitlines()]
    assert any("player," in ln for ln in lines), "左位必须是玩家"
    assert any("attacker," in ln for ln in lines), "右位必须是对手"
    # 段位号固定
    m = _read("game/ModIds.cs")
    assert "internal const int DramaTeamJoinBase      = Mod + 200;" in m


# ---------------------------------------------------------------- ⑥ 配表事实

def test_dungeon_trigger_row_for_duel():
    """配表里必须有一行 `dungeonType=40` 的注入触发器，且 dramaFunction 留空。"""
    # 开发机（WSL）与 Windows 两种布局都认：只有本机没有 mod 工程目录时才跳过
    candidates = [
        Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/DungeonTrigger.xlsx"),
        Path("F:/mod/ModProject_Jgmg5L/ModProject/ModExcel/DungeonTrigger.xlsx"),
    ]
    xlsx = next((p for p in candidates if p.exists()), None)
    if xlsx is None:
        pytest.skip("本机没有 mod 工程目录，跳过配表断言（别的机器/GitHub 上正常）")
    import openpyxl
    ws = openpyxl.load_workbook(str(xlsx)).active
    rows = {}
    for r in range(4, ws.max_row + 1):
        rid = ws.cell(row=r, column=1).value
        if not rid:
            continue
        rows[str(rid)] = {
            "type": str(ws.cell(row=r, column=2).value or ""),
            "drama": str(ws.cell(row=r, column=7).value or ""),
            "battle": str(ws.cell(row=r, column=8).value or ""),
        }
    # 决斗那行：有人把 40 写进任意一行的白名单即可（新增行或并进 MID&001 都算过）
    has40 = any("40" in v["type"].split("|") for v in rows.values())
    assert has40, "DungeonTrigger 里没有任何一行的 dungeonType 包含 40（决斗不会被注入）"
    duel_rows = [k for k, v in rows.items() if "40" in v["type"].split("|")]
    # 表里有 addBattleNPCData 才可能注入
    assert any("addBattleNPCData" in rows[k]["battle"] for k in duel_rows), \
        "命中 40 的行里没有 addBattleNPCData ⇒ 队友永远进不了决斗"


# ---------------------------------------------------------------- ⑦ 战斗内剧情 → 队友经历
#
# 为什么这几条要钉死：这条链的失败**全是静默的**（引擎只打一行 `找不到日志：…` 然后丢弃），
# 而它写的是 NPC 的记忆 —— 写错人（写到玩家身上）、写进我们自己的确认窗、写混模板，
# 都不会报错，只会在几天后的对话里表现为"他记得一件没发生过的事"。

def test_battle_drama_is_written_to_mates_only():
    """战斗内剧情只写 NPC 队友，玩家自己那条不写（用户 10-25 口径）。"""
    src = _read("tools/TeamBattleFollowup.cs")
    i = src.index("private static void WriteDungeonDrama(")
    seg = src[i:src.index("private static bool EnsureDramaLogTemplate()", i)]

    assert "for (int i = 0; i < _mates.Count; i++)" in seg, "必须逐队友写"
    assert "TryAddLog(logMgr, mate," in seg, "经历要写到 mate 身上"
    assert "TryAddLog(logMgr, player" not in seg, "战斗剧情不许写到玩家身上（用户明确不要）"
    assert "EnsureLogTemplate(" in seg, "模板必须先确保就位（缺失时引擎会静默丢弃）"


def test_duel_drama_uses_its_own_template():
    """决斗见闻走决斗模板（`与{0}共历此战：{1}`），且确实由 WriteDuelLogs 接上。"""
    src = _read("tools/TeamBattleFollowup.cs")
    i = src.index("private static void WriteDuelLogs(")
    seg = src[i:src.index("private static void WriteDungeonDrama(", i)]
    assert "WriteDungeonDrama(logMgr, player, true)" in seg, "决斗必须接着写见闻"

    d = src[src.index("private static void WriteDungeonDrama("):]
    d = d[:d.index("private static bool EnsureDramaLogTemplate()")]
    compact = "".join(d.split())
    assert "ModIds.LogTeamDuelDrama,ModIds.LogTeamDuelDramaKey" in compact, \
        "决斗见闻必须用决斗那两行模板（id + keyID），不许回落到副本那条"
    assert "?ModIds.LogTeamDuelDramaKey:ModIds.LogTeamDuelDramaKey" not in compact
    assert "ModIds.LogTeamDuelDramaKey:DramaLogKey" in compact, "两条路各用各的 key"


def test_drama_window_skips_events_and_private_pages():
    """窗口里的东西不是全都该进记忆：事件钉子不是内容，我方弹的窗也不该被记住。"""
    src = _read("tools/TeamBattleFollowup.cs")
    i = src.index("private static void WriteDungeonDrama(")
    seg = src[i:src.index("private static bool EnsureDramaLogTemplate()", i)]
    assert 'e.Kind == "event"' in seg, "事件钉子必须过滤（否则经历里出现 battle_begin）"
    assert "e.Private" in seg, "私有窗（我方弹的确认窗）必须过滤"

    # 我方确认窗的段号必须落在账本判定的"私有"区间（Mod .. Mod+999），否则过滤不掉
    led = _read("drama/DramaLedger.cs")
    assert "dramaId >= ModIds.Mod && dramaId <= ModIds.Mod + 999" in led
    ids = _read("game/ModIds.cs")
    assert re.search(r"internal const int DramaTeamJoinBase\s*=\s*Mod \+ 200;", ids), \
        "确认窗段号必须留在 Mod+200，否则队友会记得「你问他愿不愿意参战」这句"


def test_ledger_window_closes_before_any_early_return():
    """`BattleExit` 第一件事必须是关窗 —— 后面有两处提前 return，晚一步就永远关不上。

    真机症状（注释里写过）：窗一直开着 ⇒ 下一场 `BeginWindow` 被幂等判据挡掉 ⇒
    "打第二场起剧情再也没记过"，而且没有任何报错。
    """
    src = _read("tools/TeamBattleFollowup.cs")
    i = src.index("private static void OnBattleExit(ETypeData e)")
    seg = src[i:i + 2000]
    assert "DramaLedger.EndWindow(" in seg
    assert seg.index("DramaLedger.EndWindow(") < seg.index("if (!_armed) return;"), \
        "关窗必须早于 `_armed` 判断（单人战斗也要关）"


def test_drama_summary_is_bounded():
    """经历面板是给人扫一眼的：格式化必须有上限，且截断多少条要说出来。"""
    src = _read("drama/DungeonDramaCore.cs")
    for k in ("MaxLines", "MaxTextChars", "MaxTotalChars", "HardLimit"):
        assert re.search(r"internal const int %s\s*=" % k, src), f"缺少上限 {k}"
    f = _read("tools/TeamBattleFollowup.cs")
    assert "DungeonDramaCore.DroppedCount(" in f, "超上限丢掉多少条必须进日志（不静默）"


# ---------------------------------------------------------------- ⑧ 复核补的四条（10-26）
#
# 这一组全部来自"代码复核"，不是新需求：每一条都对应一个**会静默失效**的写法。

def test_own_window_visibility_read_from_ui_not_from_flag():
    """`ownWindowVisible` 只能读 UI 实况 —— 不许拿"我们以为自己正在问"当输入。

    老写法 `_askOpen = true` 只在回调里回落；玩家若直接点了应战（窗被引擎拆掉、
    回调不跑），它就**永久为真** ⇒ 此后所有决斗都不再问，而且没有一行报错。
    """
    src = _read("tools/TeamJoinGate.cs")
    # 只允许文档里提到旧名字，不许再有字段/赋值
    assert not re.search(r"bool\s+_askOpen\b", src), "自持状态的 `_askOpen` 又回来了"
    assert not re.search(r"_askOpen\s*=", src), "还在给 `_askOpen` 赋值"

    i = src.index("bool ownVisible = false;")
    assert "OwnDramaWindowVisible()" in src[i:i + 200], \
        "`ownVisible` 必须来自 UI 实况（OwnDramaWindowVisible），不是我们的记忆"

    # 问完必须核对"窗到底上来了没有"（探针要靠这一行回答 U3）
    assert "_askPendingCheck" in src and "CheckWindowShown" in src
    assert src.index("if (_askPendingCheck)") < src.index('if (!pre) { _lastWhy = ""; return; }'), \
        "核对必须放在 `!pre` 早退之前 —— 我们的窗会盖住应战窗（层顶换人 ⇒ pre 变假）"


def test_option_rows_registered_at_runtime():
    """窗口行注册了、**两个选项行**没注册 ⇒ 按钮画得出来、点下去没回调（静默）。

    依据：`ClickOption(<ConfDramaOptionsItem>)` 按选项 id 反查 `ConfDramaOptions`。
    """
    src = _read("tools/TeamJoinGate.cs")
    assert "private static void EnsureOptionRows(" in src, "缺少选项行兜底注册"
    assert "g.conf.dramaOptions" in src
    assert "row.id = targets[i]" in src
    assert "opts.AddItem(row)" in src
    assert "回读不到" in src, "注册后没回读（不确认就等于不知道）"

    # 两条分支（表里有窗 / 运行时注册窗）都必须过一遍
    hit = src.index("_windowState = 1;")
    assert "EnsureOptionRows(" in src[hit:hit + 400], "用表里那行时也要保证选项行在"
    hit2 = src.index("_windowState = 2;")
    assert "EnsureOptionRows(" in src[hit2:hit2 + 400], "运行时注册窗口后也要补选项行"


def test_trigger_probe_asks_engine_istrigger():
    """自检必须问引擎"这条到底会不会执行"（`IsTrigger`），不能只数候选条数。

    只数条数的话，`候选 1 条` 也可能是 `IsTrigger=FALSE`（条件/时机不对）——
    那和"没导出"是**两种完全不同的故障**，探针必须一次分清。
    """
    src = _read("tools/TeamJoinGate.cs")
    assert "JudgeTrigger" in src
    assert '"IsTrigger"' in src, "没有调引擎的 IsTrigger"
    assert "TRUE（引擎判定它会执行）" in src
    assert "FALSE（引擎判定它不执行" in src
    assert "**不在候选里**" in src, "候选清单里没有我们那条时要明说（= 没导出/被过滤）"
    # 我们自己那条的 id 必须指向 MID&004
    assert "int ours = ModIds.Mod + 4;" in src


def test_decided_latch_is_scoped_to_duel_only():
    """`_decided` 残留不许误伤普通副本；决斗分支出异常必须 **fail-closed**。

    旧写法把 `if (_decided) return false;` 放在判类型**之前**：上一场决斗没正常收尾
    （读档/异常，两个清零点都没跑到）⇒ 下一场普通副本被直接跳过 ⇒ 队友不进副本。
    """
    src = _read("tools/TeamContainerInject.cs")
    assert "bool isDuel = false;" in src, "缺少「本场是不是决斗」的局部标记"
    i = src.index("isDuel = ddbi != null && ddbi.type == ModIds.DungeonTypeDuel;")
    seg = src[i:i + 1200]
    assert "if (isDuel && _decided)" in seg, "幂等判据必须收窄到「这一场确实是决斗」"

    # 异常不能掉回复本分支（那里 `mates` 非空就会直塞 = 没问过玩家就带人）
    assert "if (isDuel)" in src[src.index("catch (Exception dex)"):][:900]
    assert "决斗分支异常 ⇒ 按未同意处理" in src
    assert "nu2.Clear()" not in src, "异常不能清空原版敌人容器"

def test_attacker_comes_from_page_then_attack_chain():
    """「对手是谁」两条判据：先看**页面**，再用**我方攻击链**兜底（仅 `op == attack`）。

    为什么必须补第二条：工具发起的攻击，开场页可能是在"工具执行期"开的（闸 1 那一支），
    那时 `LastDramaUnit` 可能没被更新（还停在上一个说话的人）⇒ 确认窗的立绘/人名、
    以及后来写进队友经历的那个名字**全是错的**，而且一点异常都没有。
    动作工具由 busy 键串行化，`CurrentChainUid()` 只在恰好一张凭证时给值，再要求
    `op == attack` —— 这就是"此刻正在对你动手的那个人"，是事实不是猜。
    """
    src = _read("tools/TeamJoinGate.cs")
    i = src.index("private static WorldUnitBase PickAttacker()")
    seg = src[i:src.index("private static bool IsFoe(", i)]

    assert "DramaAiOption.LastDramaUnit" in seg, "页面判据被删了"
    assert "DramaNativeClaim.CurrentChainUid()" in seg, "缺少攻击链兜底"
    assert 'DramaNativeClaim.OpOf(chain) == "attack"' in seg, "必须限定 op=attack（别的链与应战窗无关）"
    assert "UnitLookup.Resolve(chain)" in seg, "凭证只给 uid，必须解析成单位"
    assert "页面上判不出" in seg and "以链为准" in seg, "两条兜底路径都要留痕（否则出错无从查）"

    foe = src[src.index("private static bool IsFoe("):]
    foe = foe[:foe.index("private static string NameOf")]
    for k in ("u == null", "u.data == null", "TeamTool.IsPlayer(u)", "TeamTool.SlotOf(u) >= 0"):
        assert k in foe, "IsFoe 少了硬判据：" + k

def test_world_change_uses_native_pointer():
    """换世界判据必须是**原生物件指针**，不许用 `ReferenceEquals`。

    真机 10-26 13:0x 踩到的形态（`Player.log` 3555 行）：IL2CPP 每次取 `g.world`
    都可能给一个**新的托管包装**（同一个 native 对象）⇒ `ReferenceEquals` 恒 false
    ⇒ 每 0.25s 一次 `Reset()`：`_asked` / `_answer` 被反复清空 ⇒ **确认窗一直重弹**，
    玩家点过的"同意"也在下一次 Reset 里被抹掉（注入层只会看到 None），而且整个过程
    只有两行日志交替刷屏，看不出是 bug。
    """
    src = _read("tools/TeamJoinGate.cs")
    assert "ReferenceEquals(w, _lastWorld)" not in src, "又用托管包装做世界身份判据了"
    assert "_lastWorldPtr" in src and ".Pointer" in src, "必须比原生物件指针"
    # 首次进世界不算换档（否则每次启动都会多打一行 Reset 日志）
    assert "bool hadWorld = _lastWorldPtr != IntPtr.Zero;" in src
    # 明确挡住"回到 ReferenceEquals"的重构
    assert "reference-equals" not in src

def test_trigger_probe_checks_both_rows():
    """自检必须同时查 **两条**触发器：`MID&004`（注入）与 `MID&002`（重伤链）。

    真机 10-26 的教训：只查 MID&004 时日志打「✅ 在候选里」，而弹 UI 那半条链
    其实是断的（`MID&002.dungeonType` 没有 40 ⇒ 6009 没挂到队友 ⇒ 队友倒下不开 190）。
    一个"✅"掩盖了另一条断链 —— 正是本仓最忌的形态。
    """
    src = _read("tools/TeamJoinGate.cs")
    i = src.index("private static void ProbeDuelTriggerOnce(")
    seg = src[i:i + 6000]
    assert "ModIds.Mod + 4" in seg, "缺少注入行 MID&004"
    assert "ModIds.Mod + 2" in seg, "缺少重伤链 MID&002"
    assert "hurtRow" in seg
    assert "重伤链" in seg, "重伤链缺失时必须有一句明确的话（不能只有一个 ✅）"
    assert "没有 UI" in seg, "要说清后果：代码会补挂 buff，但**没有弹窗**"


def test_mid002_whitelist_includes_duel_only():
    """配表事实：`40` 必须在 `MID&002` 里，但**不许**在 `MID&001` / `MID&003` 里。

    为什么各归各位：
      · `MID&001` 含 `setDramaUnitData_*`，会改剧情单位表 ⇒ 决斗的处置页 20403 被换掉；
      · `MID&003` 含 `clearBattleNPCDatas` ⇒ 会把刚注入的队友清掉；
      · `MID&002` 只挂死亡回调（`unitAddEffect_3/4/5`），不碰容器 ⇒ 决斗里安全且必要。
    """
    candidates = [
        Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/DungeonTrigger.xlsx"),
        Path("F:/mod/ModProject_Jgmg5L/ModProject/ModExcel/DungeonTrigger.xlsx"),
    ]
    xlsx = next((p for p in candidates if p.exists()), None)
    if xlsx is None:
        pytest.skip("本机没有 mod 工程目录，跳过配表断言")
    import openpyxl
    ws = openpyxl.load_workbook(str(xlsx)).active
    rows = {}
    for r in range(4, ws.max_row + 1):
        rid = ws.cell(row=r, column=1).value
        if not rid:
            continue
        rows[str(rid)] = (str(ws.cell(row=r, column=2).value or "")).split("|")

    assert "40" in rows.get("MID&002", []), \
        "MID&002 的 dungeonType 没有 40 ⇒ 决斗里队友倒下不会弹 190、表也不挂重伤（只剩代码补挂）"
    assert "40" not in rows.get("MID&001", []), \
        "MID&001 不许含 40：它的 setDramaUnitData 会改剧情单位表，把决斗处置页 20403 换掉"
    assert "40" not in rows.get("MID&003", []), \
        "MID&003 不许含 40：它是 clearBattleNPCDatas，会把刚注入的队友清掉"
