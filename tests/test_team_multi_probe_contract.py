"""多人组队探针（TeamMultiProbe）的接线契约。

**为什么需要这份测试**：`TeamMultiProbe` 的回答的是一道**只有实测能答**的问题 ——
"组队 mod 的 `addBattleNPCData_MID&003_0` 里那个索引 `0`，是否真的只会上场一个人"。
反编译只有签名没有方法体，官方文档只说"索引"不给行为，所以必须进游戏数单位。

它和 `TeamProbe` 一样是**静默失效型**代码：接线一断（FrameTick 没挂、开关没人读、
日志写不出来），表现是"跑了一趟游戏什么都没拿到"，而不会报错。所以把关键接线锁住。

被锁住的六件事：

  1. **必须挂 `DiagSwitches.FrameTick`**。`_diag_*.txt` 系列在 09-28 有过全工程失效事故
     （类头写"每 2 秒复查"但从来没有调用点）。新探针同样必须挂上去。
  2. **必须挂在 Init 期自检**。TeamProbe 上线时吃过"空有 DLL、日志一条不出"的亏，
     根因分不清是"部署晚于启动"还是"没进世界 ⇒ 帧回调不跑"。Init 期同步自检是唯一
     不依赖帧回调的确认手段。
  3. **心跳循环、无一次性闸**。用户两次明确要求："探针要心跳式的，不能只跑一遍"。
     正确做法是**让每个动作幂等**，而不是把探针停掉。
  4. **自带帧节流**。`FrameTick` 是**逐帧**调用，不节流十几分钟能写出 5MB 日志
     （10-17 真实事故）。节流必须在 Tick 最前面。
  5. **气运 ID 绝不硬编码**。mod 的实际 ID = 它的 `excelMID` + 偏移，作者重新导出就会变。
     必须按名字反查（`ScanLuckTable`）。
  6. **独立落盘**。本机游戏一开就没网，结论必须落成文件、关游戏后离线可读。

另锁两处**已经踩过的引擎 API 坑**（写错就是编译不过，或运行期静默失败）：
  · 挂气运必须 `WorldFactory.CreateLuckInID` → `u.CreateLuck(luckData)`，
    `WorldUnitBase` **没有** `CreateLuck(int)` 重载；
  · 摘气运是 `DestroyLuck(WorldUnitLuckBase)`，**没有** `RemoveLuck`。
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MULTI = ROOT / "csharp" / "diag" / "team" / "TeamMultiProbe.cs"
DIAG = ROOT / "csharp" / "app" / "DiagSwitches.cs"
MAIN = ROOT / "csharp" / "app" / "ModMain.cs"


def _src(p: Path) -> str:
    assert p.exists(), f"缺文件：{p}"
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 接线

def test_hooked_into_frame_tick():
    """必须挂在 FrameTick 上，否则开关文件摆着也没人读（09-28 事故同款）。"""
    diag = _src(DIAG)
    assert "TeamMultiProbe.Tick()" in diag, "TeamMultiProbe.Tick() 未挂到 DiagSwitches.FrameTick"


def test_frame_tick_call_is_guarded():
    """探针异常不得拖垮 DiagSwitches 主流程。"""
    diag = _src(DIAG)
    idx = diag.index("TeamMultiProbe.Tick()")
    window = diag[max(0, idx - 120): idx + 40]
    assert "try" in window and "catch" in window, "TeamMultiProbe.Tick() 未包 try/catch"


def test_selfcheck_called_at_init():
    """Init 期必须打自检行 —— 唯一不依赖帧回调的"我们进游戏了"确认手段。"""
    main = _src(MAIN)
    assert "TeamMultiProbe.SelfCheck()" in main, "ModMain 未在 Init 调 TeamMultiProbe.SelfCheck()"


def test_selfcheck_is_before_frame_tick_dependency():
    """自检必须在 Init 期、且不依赖 g.timer.Frame。"""
    main = _src(MAIN)
    i_self = main.index("TeamMultiProbe.SelfCheck()")
    i_ws = main.index("_wsClient = new WsClient")
    assert i_self < i_ws, "自检应早于 WS 客户端初始化（WS 失败也要能看到探针已加载）"


# ---------------------------------------------------------------- 心跳语义

def test_no_one_shot_gate():
    """不得有一次性闸 —— 用户两次要求"心跳式、不能只跑一遍"。"""
    src = _src(MULTI)
    for bad in ("_armed", "_done", "_executed", "_fired"):
        assert f"private static bool {bad}" not in src, f"出现了疑似一次性闸字段 {bad}"


def test_tick_has_own_throttle():
    """自带帧节流，且必须在 Tick 最前面（10-17 写出 5MB 日志的事故）。"""
    src = _src(MULTI)
    assert "RecheckFrames" in src, "缺 RecheckFrames 节流常量"
    # 只看 Tick 方法体内部：注释里也出现过同样文本，直接 index 会命中注释。
    i_tick = src.index("internal static void Tick()")
    body = src[i_tick: src.index("private static void Parse()")]
    i_throttle = body.index("if (--_timer > 0) return;")
    i_file = body.index("File.Exists(ProbePath)")
    assert i_throttle < i_file, "节流必须是 Tick 的第一件事（早于读开关文件）"


def test_every_action_is_idempotent():
    """每个动作用幂等保证循环安全，而不是靠"只跑一次"。"""
    src = _src(MULTI)
    # give：一槽一枚气运后，幂等分两层
    #   ① 每槽先查再挂（`HasLuckAny(u, luckId) || AddLuck(...)`）—— 挂着就不再挂
    #   ② 一次性闸 `_giveDone`（心掉模式下每 ~2s 一拍，不设闸会原地反复"清空再挂"）
    # 10-19：挂载统一走 TeamTool.AddLuck（两层都写：allLuck 运行时层 + addLuck 存档层），
    # 幂等仍然是"先查 HasLuckAny 再挂"。
    assert "HasLuckAny(u, luckId) || (TeamTool.AddLuck(u, luckId, out addErr2) && HasLuckAny(u, luckId))" in src, \
        "give 未按号位做幂等检查 / 没走 TeamTool.AddLuck（旧探针挂载只进存档层，条件看不见）"
    assert "_giveDone" in src, \
        "give 没有一次性闸 —— 心跳每拍都会先清空再挂，气运被反复刷新"
    # clear：一槽一枚后是"三枚都扫、一枚都没有才算幂等"
    assert "HasAnySlotLuck(u)" in src, \
        "clear 未做幂等检查（应按号位逐枚摘，摘完再确认一枚不剩）"


# ---------------------------------------------------------------- 不硬编码

def test_luck_not_hardcoded():
    """气运 ID 必须按名字反查。硬编码 -2004589xxx 会在 mod 重新导出那天静默指错。"""
    src = _src(MULTI)
    assert "-2004589" not in src, "出现了硬编码的组队 mod 气运 ID"
    assert "-2004589739" not in src, "出现了硬编码的组队 mod excelMID"
    assert "ScanLuckTable" in src, "缺按名反查气运的实现"


def test_luck_lookup_uses_localization():
    """气运的 name 字段是国际化 key，必须过 GameTool.LS 才能比对中文。"""
    src = _src(MULTI)
    assert "GameTool.LS" in src, "按名反查未过 GameTool.LS（拿到的是 key 不是中文）"


# ---------------------------------------------------------------- 落盘

def test_independent_log_file():
    """独立落盘：游戏一开就没网，结论必须能离线读。"""
    src = _src(MULTI)
    assert "team_multi.log" in src, "缺独立日志文件"
    assert "team_multi_summary.txt" in src, "缺结论摘要文件"


def test_all_output_goes_through_out():
    """所有输出统一走 Out()，否则会漏进 MelonLoader 后台线程日志而丢失。"""
    src = _src(MULTI)
    assert src.count("private static void Out(string msg)") == 1, "Out 定义应唯一"
    # Out 内部不得递归调用自己
    i = src.index("private static void Out(string msg)")
    body = src[i: i + 900]
    assert "ModMain.P(" in body, "Out 应写 ModMain.P"


# ---------------------------------------------------------------- 引擎 API 契约

def test_luck_creation_tries_multiple_strategies():
    """挂气运必须**多策略尝试**，不能只赌一条路。

    背景（10-17 实测）：`WorldFactory.CreateLuckInID(id)` 造出来的对象 `luckData` 是 null
    （只装了 luckConf 配置），于是 `u.CreateLuck(lk.luckData)` 直接 NullReferenceException。
    引擎里能造 LuckData 的口子还有 `new DataUnit.LuckData()` 与
    `PropertyData.AddAddLuck(LuckData)`（反射实证），但**方法体看不见**，无法静态判断哪条对。
    所以契约是"逐条试、试通了记下来"，而不是继续猜。
    """
    src = _src(MULTI)
    assert "WorldFactory.CreateLuckInID" in src, "应尝试 WorldFactory.CreateLuckInID 这条路"
    assert "AddAddLuck" in src, "应尝试 PropertyData.AddAddLuck 这条路"
    assert "new DataUnit.LuckData()" in src, "应尝试直接构造 LuckData 这条路"
    assert "_luckStrategy" in src, "试通后应记住策略，避免每拍重复试错"


def test_luck_creation_verifies_by_addluck():
    """成功判据必须是 `TeamTool.InAddLuck`（面板可见），不是 `SafeGetLuck`（运行时对象）。

    10-18 面板事故：探针用 SafeGetLuck 判成功 → 报"挂上了"，而 NPC 面板后天气运空白。
    详见 `TeamTool.AddLuck` 的 ★ 段。
    """
    src = _src(MULTI)
    i = src.index("private static bool TryStrategy(")
    body = src[i: src.index("private static bool DelLuck(")]
    assert "TeamTool.InAddLuck" in body, \
        "探针没有用 InAddLuck 判成功（会继续报假成功、面板却看不见）"
    assert body.count("TeamTool.InAddLuck") >= 3, \
        "三条策略都要用 InAddLuck 复查"


def test_luck_removal_uses_destroy():
    """引擎里是 DestroyLuck，没有 RemoveLuck。"""
    src = _src(MULTI)
    assert "DestroyLuck" in src, "摘气运应用 DestroyLuck"
    assert ".RemoveLuck(" not in src, "引擎没有 RemoveLuck（写了会编译不过）"


def test_battle_detection_uses_isbattle():
    """战斗判据必须是 world.battle.isBattle；判 SceneBattle 是否为空是恒真的错判据。"""
    src = _src(MULTI)
    assert "isBattle" in src, "缺 isBattle 判据"


def test_scene_battle_accessed_by_reflection():
    """SceneBattle 没有 static 成员，编译期引用会连累整个 DLL；必须反射兜底。"""
    src = _src(MULTI)
    assert "FindSceneBattle" in src, "缺 SceneBattle 反射获取"
    assert "SceneBattle.Instance" not in src, "不得用不存在的 SceneBattle.Instance"


def test_npc_name_via_propertydata():
    """NPC 名必须走 data.unitData.propertyData.GetName()，WorldUnitBase 没有 name 属性。"""
    # 11-19 复核：探针里的 `SafeName` 已收口到 `UnitIdentity.Name`（全仓唯一取名字实现），
    # 不再自己抄一份 `data.unitData.propertyData.GetName()`。判据改成"走那个唯一实现"。
    src = _src(MULTI)
    assert "UnitIdentity.Name(" in src, "NPC 名应走 UnitIdentity.Name（唯一实现）"
    identity = (ROOT / "csharp" / "game" / "UnitIdentity.cs").read_text(encoding="utf-8")
    assert "propertyData?.GetName()" in identity, "唯一实现里应走 propertyData.GetName()"


# ---------------------------------------------------------------- 探测覆盖面

def test_dumpbattle_counts_npc_units():
    """dumpbattle 必须真的数 NPC 单位 —— 这是"上来几个人"的唯一直接证据。"""
    src = _src(MULTI)
    assert 'case "dumpbattle"' in src, "缺 dumpbattle 动作"
    assert "npcCount" in src, "dumpbattle 未统计 NPC 单位数"


def test_give_supports_multiple_names():
    """give 必须支持多人（这是本探针存在的理由）。"""
    src = _src(MULTI)
    assert "TargetNames" in src, "缺多目标名单解析"
    assert ".Replace('，', ',')" in src or 'Replace("，", ",")' in src, \
        "名单解析应容忍中文逗号"


# ---------------------------------------------------------------- 去重 / 性能（10-18）

def test_dedup_uses_both_storages():
    """幂等判据必须**两套存储都查**。

    10-18 事故：写入改成了 `addLuck`，而判重还在用 `SafeGetLuck`（运行时对象）
    ⇒ 判据恒为"没有"⇒ 每 2 秒重复挂一次 ⇒ NPC 面板出现 6 个「结伴而行」+ 变卡。
    """
    src = _src(MULTI)
    assert "HasLuckAny" in src, "缺 HasLuckAny（两套存储都查）"
    body = src[src.index("private static bool HasLuckAny"):]
    body = body[: body.index("private static int DedupeLuck")]
    assert "SafeGetLuck" in body or "GetLuck" in body, "HasLuckAny 没查运行时对象"
    assert "InAddLuck" in body, "HasLuckAny 没查 addLuck 列表"
    # givemap 的判重不许再直接用 SafeGetLuck
    gm = src[src.index("private static void DoGiveNear"):]
    gm = gm[: gm.index("// ───────────────────────── action=give")]
    # 一槽一枚后判据按号位走：追加前必须 `HasLuckAny(u, slotId)` 确认"这一枚真的挂上了"，
    # 否则 AddLuck 失败也会被算作已挂（旧事故的同款形态）。
    assert "HasLuckAny(u, slotId)" in gm, "givemap 判重没走 HasLuckAny（按号位）"
    assert "TeamTool.SlotLuckId(newly" in gm, "givemap 没按号位取气运 id"
    assert "DedupeLuck(u, _luckTeam)" in gm, "givemap 没有清理存量重复"


def test_dedupe_exists_and_is_idempotent():
    """DedupeLuck 必须在：修判据不会清掉旧存档里已堆的重复条目。"""
    src = _src(MULTI)
    assert "private static int DedupeLuck" in src, "缺 DedupeLuck"
    body = src[src.index("private static int DedupeLuck"):]
    body = body[: body.index("/// <summary>", 10) if "/// <summary>" in body[10:] else len(body)]
    assert "RemoveAt" in body, "DedupeLuck 没真的删条目"


def test_near_units_avoids_full_scan():
    """挂载只取附近，不拉全图。

    `GetUnits(true)` 单次 ~100ms / 1556 个单位（实测见 RelationNetwork 注释），
    每 2 秒跑一次 = 持续卡顿（用户反馈"很卡"）。
    """
    src = _src(MULTI)
    assert "NearUnits" in src, "缺 NearUnits（按坐标快查）"
    assert "GetUnit(pt, range)" in src or "GetUnit(point" in src, "NearUnits 没用引擎快查"
    gm = src[src.index("private static void DoGiveNear"):]
    gm = gm[: gm.index("// ───────────────────────── action=give")]
    assert "NearUnits(px, py, _radius)" in gm, "givemap 仍在拉全图"
    # 去掉注释后再查：注释里允许提到 GetUnits（说明历史），代码里不许有
    code = "\n".join(l for l in gm.splitlines() if not l.strip().startswith("//"))
    assert "GetUnits(true)" not in code, "givemap 的**代码**里不该再有全图遍历"


def test_probe_has_idle_throttle():
    """空闲期必须降频 —— 不能一直按 2 秒碰 IL2CPP 对象。"""
    src = _src(MULTI)
    assert "IdleFrames" in src and "IdleAfterBeats" in src, "缺空闲降频常量"
    assert "_quietBeats" in src, "缺静默计数"
    tk = src[src.index("internal static void Tick()"):]
    tk = tk[: tk.index("private static void Parse()")]
    assert "IdleFrames" in tk, "Tick 里没用空闲节拍"


# ---------------------------------------------------------------- auto 模式（10-18）

def test_auto_action_exists():
    """`auto` = 不在战斗挂气运 / 一进战斗只报状态（不自动 dump）。

    为什么需要：反复出现"进副本时开关还停在 givemap"⇒ 副本内零记录，
    只能靠肉眼数人。一个开关管两件事，一趟跑完。

    ★ 10-21 用户拍板：战斗中**不再自动 dump** ★
      实测一次 dump ≈59 行 / 300~360ms（内部 `GetUnits(true)` 遍历所有单位），
      每 5 拍一次 = 持续掉帧。现在战斗内只打一条**恒定文本**的状态行，
      要现场快照就手动改 `action=dumpbattle`。
      对应地，`Beat` 的去重是**整行文本比较**，所以状态行里绝不能带递增拍数
      （旧文案带拍数 ⇒ 每拍都"不一样" ⇒ 去重永不命中 ⇒ 一场刷 128 行）。
    """
    src = _src(MULTI)
    assert 'case "auto"' in src, "缺 auto 动作"
    seg = src[src.index('case "auto"'):]
    seg = seg[: seg.index("default:")]
    assert "DoGiveNear" in seg, "auto 没覆盖「不在战斗 → 挂气运」这种情形"
    assert "g.world.battle" in seg, "auto 没判是否在战斗中"
    assert "battle-wait" in seg, "auto 没覆盖「在战斗 → 报状态」这种情形"
    assert "dumpbattle" in seg, "战斗内应提示改 action=dumpbattle 手动取快照"


def test_auto_does_not_dump_in_battle():
    """战斗内**不许**自动 dump：那是一次 300ms+ 的全图遍历，会把帧率打下去。

    10-21 之前的实现是"每 5 拍 dump 一次"，真机表现为持续掉帧。
    契约：auto 分支里不出现 `DoDumpBattle`，且 dump 输出本身要有硬上限。
    """
    src = _src(MULTI)
    seg = src[src.index('case "auto"'):]
    seg = seg[: seg.index("default:")]
    assert "DoDumpBattle" not in seg, \
        "auto 又在战斗内自动 dump 了（10-21 因掉帧关闭，需手动 action=dumpbattle）"
    assert "_battleBeats" in seg, "auto 没记战斗内经历了几拍"
    assert "MaxDumpLines" in src, "诊断输出没有硬上限（战斗中会写出几十 MB）"


# ───────────── 触发器自检：必须"纯只读、不挂补丁"（10-18 闪退事故）─────────────
#
# 背景：曾经写过 `DungeonTriggerWatch`，在启动时用 Harmony 给
# `ConfDungeonTrigger.GetItem` / `IsTrigger` 挂补丁 + 全程序集反射扫描，
# **直接导致游戏启动闪退**（Player.log 停在 BattleEndHook 之后，一行输出都没写）。
# 下面的测试把这个教训钉死：允许读，禁止挂。

def _src(path):
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8")


def test_trigger_check_exists_and_is_readonly():
    """只查**代码行**，不查注释 —— 头注释里解释"为什么不用 Harmony"是正当的。"""
    from pathlib import Path
    lines = Path("/mnt/f/agent_loop/csharp/app/TriggerSelfCheck.cs").read_text(
        encoding="utf-8").splitlines()
    code = "\n".join(l for l in lines if not l.lstrip().startswith(("//", "///", "*", "/*")))
    for bad in ("Harmony", "PatchAll", "[HarmonyPatch"):
        assert bad not in code, f"TriggerSelfCheck 代码里不得使用 {bad}（会重演 10-18 启动闪退）"
    # 也不得出现任何挂载动作
    assert ".Patch(" not in code, "TriggerSelfCheck 不得调用 .Patch()"


def test_trigger_check_is_not_hooked_at_startup():
    src = _src("/mnt/f/agent_loop/csharp/app/ModMain.cs")
    assert "DungeonTriggerWatch" not in src, (
        "ModMain 不得再引用 DungeonTriggerWatch —— 它在启动期挂补丁导致闪退")
    assert "TriggerSelfCheck" not in src, (
        "TriggerSelfCheck 不能在 ModMain 里启动即跑 —— 必须等进副本后由探针触发")


def test_trigger_check_reflects_engine_functions():
    src = _src("/mnt/f/agent_loop/csharp/app/TriggerSelfCheck.cs")
    # 必须真的去调引擎的 GetItem / IsTrigger（这是权威答案来源）
    assert '"GetItem"' in src, "自检必须调用引擎的 GetItem 取候选清单"
    assert '"IsTrigger"' in src, "自检必须调用引擎的 IsTrigger 取判定结果"
    # 拿不到时必须明确说"不可用"，不能默认成 false（拿不到 ≠ 没有）
    assert "不可用" in src, "IsTrigger 拿不到时要明确标注不可用"
    assert "不代表引擎没有" in src or "不要读成" in src, (
        "拿不到候选清单时必须声明「不等于引擎没有」")


def test_trigger_check_runs_once_per_battle():
    src = _src("/mnt/f/agent_loop/csharp/app/TriggerSelfCheck.cs")
    assert "_done" in src and "if (_done) return;" in src, "自检每局只跑一次，避免每拍反射"


def test_probe_calls_trigger_check_with_dungeonbase():
    src = _src("/mnt/f/agent_loop/csharp/diag/team/TeamMultiProbe.cs")
    assert "TriggerSelfCheck.RunOnce" in src, "探针必须在拿到 battleData 后调用触发器自检"
    assert 'm.Name == "dungeonBaseItem"' in src, "自检必须用 WorldBattleData 上的 dungeonBaseItem"


# ───────────── 实验可追溯：配置快照 + 实验记录表（10-18 用户明确要求）─────────────
#
# 背景：来回试了 10+ 次，却因为"没记录"而无法确认"那次只加进 1 个人"用了什么结构，
# 只能重试。此后每次启动必须自动落一份配置快照，并维护实验流水
# （2026-10-21 起在 docs/archive/2026-10-18-组队实验记录表.md，入口见 docs/features/team.md）。

def test_probe_writes_config_snapshot():
    src = _src("/mnt/f/agent_loop/csharp/diag/team/TeamMultiProbe.cs")
    assert "team_config_snapshot.txt" in src, "探针必须落配置快照，便于事后确认每趟跑的是什么"
    assert "FlushSnapshot" in src, "快照要有落地函数"
    assert "File.WriteAllText(f, sb.ToString()" in src, "快照要真的写文件"
    # 表验证的内容必须进快照（否则快照是空的）
    assert 'Snap("  [触发器] "' in src or 'Snap("  [气运] "' in src, \
        "表验证的输出必须走 Snap（同时进日志与快照）"


def test_experiment_log_exists_and_has_structure():
    """10-18 用户明确要求：实验要留档（"失败也要记"）。

    2026-10-21 文档整合后，实验流水搬进了 `docs/archive/`，
    入口文档是 `docs/features/team.md`。这里两条都查：
      · **归档流水**还在（那可是真机烧出来的，不许丢）；
      · **入口文档**存在且指向归档（否则没人知道历史去哪了）。
    """
    from pathlib import Path
    p = Path("/mnt/f/agent_loop/docs/archive/2026-10-18-组队实验记录表.md")
    assert p.exists(), "实验记录表没了（docs/archive/2026-10-18-组队实验记录表.md）"
    txt = p.read_text(encoding="utf-8")
    # 每条实验要有：结构、预期、观测、结论
    for need in ("预期", "观测", "结论"):
        assert need in txt, f"实验记录表缺少「{need}」列"
    # 至少登记了 E01..E06
    for tag in ("E01", "E02", "E03", "E04", "E05", "E06"):
        assert tag in txt, f"实验记录表缺少 {tag}"
    # 必须显式写清"失败也要记"
    assert "失败也要记" in txt, "记录表要写明失败也要记"

    # 入口文档必须在，并且告诉读者历史去哪了
    entry = Path("/mnt/f/agent_loop/docs/features/team.md")
    assert entry.exists(), "组队整合文档 docs/features/team.md 不存在"
    etxt = entry.read_text(encoding="utf-8")
    assert "docs/archive/" in etxt, \
        "整合文档没写历史文档搬去哪了 —— 读者会以为历史结论被删了"


# ───────────────────── 10-19：日志口径与"伪 0 防线" ─────────────────────
# 用户当日三条要求：①日志位置写进文档 ②同一日志不行就重跑 ③日志必须覆盖、不许跨天累计。
# 这批钉位把三条要求固化进代码与文档，防止下次又"315KB 混多日"或"调不到引擎却宣称 0 人"。


def test_team_multi_log_overwrites_per_session():
    """自检（会话开头）必须**整文件覆盖**重写日志 —— 用户 10-19：""日志怎么能累计？必须要覆盖"。"""
    src = _src(MULTI)
    assert "File.WriteAllText(f, head" in src, "自检时必须先 WriteAllText 写会话头（覆盖旧内容）"
    assert "本局日志从此处覆盖重写" in src, "会话头要写明覆盖语义"
    # 会话头之后继续 append（同一会话内仍追加）
    i_overwrite = src.index("File.WriteAllText(f, head")
    i_append = src.index("File.AppendAllText(f,", i_overwrite)
    assert i_append > i_overwrite, "覆盖写之后应继续 append 当会话内容"


def test_getunits_reflection_failure_retries_and_never_lies():
    """反射一次没调到必须重跑（5 次），且失败**不许**宣称『选出 0 人』。"""
    src = _src(MULTI)
    assert "attempt <= 5" in src, "DoGetUnits 反射失败要重试 5 次"
    assert "这不算" in src or "无法判断" in src, "反射没调到要明说『无法判断而非 0 人』"
    # Count 读失败 = 探针侧读不出，不是引擎答 0
    assert "countKnown" in src and "两回事" in src, "Count 读失败要与『选出 0 人』明确区分"


def test_getunits_auto_reask_after_attach():
    """用户要求"不行就重跑"：挂载完成后引擎没答出 ≥1 人 ⇒ 自动复查（10s 一问，至多 6 次）。"""
    src = _src(MULTI)
    assert "MaxUnitAsks = 6" in src, "自动复查次数上限"
    assert "_nextUnitAskAt = DateTime.UtcNow.AddSeconds(10)" in src, "墙钟排程（_beats 在静默拍里不递增，不能拍基排程）"
    assert "自动复查第" in src, "复查要有日志行"
    # 计数要在挂载完成那一拍重置并入列（改开关即重新从第 1 问开始）
    assert "_unitAskTries = 0;" in src and "_unitCountOk = false;" in src


def test_verify_phase_getunits_uses_two_param_overload():
    """ProbeGetUnits（进世界的表验证）与 DoGetUnits 同一签名坑：必须找 (String, NpcConditionData) 2 参。"""
    src = _src(MULTI)
    assert src.count("ps0.Length == 2 && ps0[0].ParameterType == typeof(string)") >= 1 \
        or "ps.Length == 2 && ps[0].ParameterType == typeof(string)" in src, "两条路径都要用 2 参重载"
    assert "attempt <= 3" in src, "表验证路径反射失败也要重跑"
    assert "本次不下人数结论" in src, "表验证路径 Count 读不出也不许下 0 人结论"


def test_log_locations_written_into_docs():
    """日志位置必须写进文档（用户 10-19）：探针日志、Player.log、以及"Melon 日志里没有 P()"防坑。

    2026-10-21 整合后，日志位置搬进了**入口文档** `docs/features/team.md` §八。
    这类"日常要查"的信息不该只留在归档流水里 —— 归档是给追溯结论用的，不是查日志用的。
    """
    log_doc = _src(ROOT / "docs" / "features" / "team.md")
    for frag in ("Player.log", "team_multi.log", "ModAssets", "MelonLoader"):
        assert frag in log_doc, f"组队文档缺日志位置说明：{frag}"
    # 那条防坑必须还在：别再往 MelonLoader 日志里翻我们的 P()
    assert "永远不出现在这里" in log_doc or "别再往这翻" in log_doc, \
        "「我们的 P() 不在 MelonLoader 日志里」这条防坑被删了"


# ───────────────────── 10-19 A/B：隔离"三枚不同气运"变量 ─────────────────────
# 用户质问"一个气运时三个人成功了，三个气运就不行" ⇒ ①同轮双变量是实验违规；
# ②attach 的"成功"只验存档层（InAddLuck），运行时层从未被验证 —— 这轮起分层诊断。


def test_sentinel_luck_option_for_ab_probe():
    """哨兵必须支持 luck=all（人人挂 1 号位那枚），复刻成功时结构。"""
    src = _src(MULTI)
    assert 'k == "luck"' in src, "缺 luck= 解析"
    assert '_luckMatch' in src, "缺 luck 模式字段"
    assert '_luckMatch = (luckMode == "all")' in src, "只认 slot/all 两档"


def test_givemap_honors_all_luck_mode():
    """luck=all 时每人都挂 SlotLuckId(0)（1 号位那枚）。"""
    src = _src(MULTI)
    assert '_luckMatch == "all"' in src and "SlotLuckId(0, out" in src, "givemap 没有按模式切换气运"


def test_givemap_prints_layer_state_per_member():
    """挂上后必须逐人打印分层状态 —— 这是"面板看得见/条件组看不见"分裂的直接证供。"""
    src = _src(MULTI)
    assert '"[层] "' in src and "TeamTool.LuckLayerState(u, slotId)" in src, \
        "成员挂载后要打 [层] 分层诊断行"


def test_verify_tables_checks_slot2_slot3_conf_rows():
    """2/3 号位气运必须在运行时配置里有行（导出丢行只能靠 GetItem 在游戏里回答）。"""
    src = _src(MULTI)
    assert "TeamTool.SlotLuckId(slot, out string serr)" in src, "表验证要枚举 2/3 号位"
    assert "运行时表里有" in src and "运行时表里没有" in src, "存在/缺失都要明说"


def test_dogetunits_prefers_int_overload():
    """★10-19 续查二轮 Q8 定案：battle 链 AddBattleNPCData 调的是
    GetUnits(int 组id, null)@0x1859937F0 —— 探针必须先选 int 重载，
    String 版（剧情/奇遇触发武器那条，含级联清零语义）只能作为兜底并明告污染源。"""
    src = _src(MULTI)
    i_sig = src.index("private static void DoGetUnits()")
    body = src[i_sig:i_sig + 6000]
    assert "ps[0].ParameterType == typeof(int)" in body, "DoGetUnits 没有优先选 int 重载"
    i_int = body.index("typeof(int)")
    i_str = body.index("typeof(string)")
    assert i_int < i_str, "int 重载必须在 String 兜底之前选"
    assert "int 重载没找到" in body, "退到 String 版时必须打出污染源警告"
    assert "不能**对等战斗链" in body or "级联语义" in body, "警告要说清两条链语义不同"


def test_probe_dumps_battle_trigger_bases():
    """★10-19 五轮定案：`SceneBattle +0xC0 = List<BattleTriggerBase> battleTriggerBases`
    （Cpp2IL [FieldOffset] 实证）。这是「引擎为本次副本建了几个触发器实例」的直接观测点，
    比靠 `副本触发器：` 日志推断强得多（那条日志在战斗结束时才可能打）。
    契约：①必须读 +0xC0 那个字段；②拿不到要明说「没测到」而不是报 0；
    ③必须逐条打 row.id/triggerType 并标出我方三条在不在。"""
    src = _src(MULTI)
    assert "DumpBattleTriggerBases" in src, "缺 battleTriggerBases 诊断"
    assert "battleTriggerBases" in src, "必须读 SceneBattle 的 battleTriggerBases 字段"
    body = src[src.index("private static void DumpBattleTriggerBases()"):]
    body = body[: body.index("\n        /// <summary>")]
    assert "拿不到" in body and "不等于 0" in body, "拿不到时必须明说「没测到，不等于 0」"
    assert "triggerItem" in body, "要读 BattleTriggerBase.triggerItem 拿配置行"
    assert "triggerType" in body, "要打 triggerType"
    assert "★我方★" in body, "要在实例列表里标出我方三条"
    assert "一个触发器实例都没建" in body, "Count==0 要给出明确结论"


def test_probe_checks_all_three_slots_for_holders():
    """三槽异气运设计下，只查 SlotLuckId(0) 会漏掉 2/3 号位的人。
    契约：队员清单必须遍历全部槽位，并逐人打 LuckLayerState（两层分裂的直接证供）。"""
    src = _src(MULTI)
    body = src[src.index("private static void ListTeamLuckHolders()"):]
    body = body[: body.index("\n        /// <summary>")]
    assert "TeamTool.MaxMembers" in body, "必须遍历全部槽位，不能只查 1 号位"
    assert "SlotLuckId(s, out" in body, "要按槽取气运 id"
    assert "LuckLayerState(u, sid2)" in body, "逐人打两层状态"
    assert "[队员·分层]" in body, "分层行要有可 grep 的前缀"


# ───────────────── 10-19：每局运行归档 + 两人组队（pair）─────────────────
# 用户要求原话：
#   ①「日志现在是覆盖的，我需要备份，每次都要备份，和组队测试那样留存记录下来，
#      记录每次的运行的结构和配置，日志结果，日志原文」
#   ②「先把气运清掉，确定现在没人有这个气运了，然后按照位置随机选两个 npc
#      分别加上一号气运和二号气运」


def test_previous_run_is_archived_before_overwrite():
    """日志仍每会话覆盖，但**覆盖前必须先归档上一局**的四件套。"""
    src = _src(MULTI)
    assert "ArchivePreviousRun" in src, "缺每局归档实现"
    body = src[src.index("private static string ArchivePreviousRun()"):]
    body = body[: body.index("\n        private static bool FileExistsSafe")]
    assert "RunsDir" in body and '"runs"' in src, "归档目录必须是 logs/runs/<时间>/"
    assert "team_multi.log" in body, "归档必须含日志原文"
    assert "config_snapshot.txt" in body, "归档必须含运行时配置快照"
    assert "summary.txt" in body, "归档必须含日志结果（结论）"
    assert '"Player.log"' in body and "Player-prev.log" in src, \
        "归档必须含引擎原文（上一局 Player-prev.log）"
    assert "run.txt" in body, "归档必须写 run.txt（开关/队伍结构/时间边界）"
    # 归档必须早于覆盖写会话头，否则原文已被清空
    i_arch = src.index("ArchivePreviousRun()")
    i_head = src.index("File.WriteAllText(f, head")
    assert i_arch < i_head, "必须先归档再覆盖写会话头"


def test_random_pair_action_is_gone():
    """`pair`（按位置随机抽人组队）**已删除**，10-21 用户明确要求。

    原实现 = 清运 → 全图复核 0 人 → 按位置随机抽 2 人 → 挂 1/2 号位。
    它会把"路过的任意 NPC"抽成队友，与救助副本的待救 NPC、剧情 NPC 直接冲突。
    用户原话：「我不要自动组队！删掉」。

    契约（防止哪天又被加回来）：
      ① 探针里不许再有 `pair` 动作与 `DoPair` / `ScanSlotHolders`；
      ② 默认动作必须是**只读**的 `scan`，不是会自动改状态的 `givemap`；
      ③ `default` 分支的可选动作清单里不许出现 pair。
    """
    src = _src(MULTI)
    assert 'case "pair"' not in src, "pair 动作又被加回来了（用户 10-21 明确删除）"
    assert "DoPair" not in src, "DoPair 实现又被加回来了"
    assert "ScanSlotHolders" not in src, "随机抽人的扫描函数又被加回来了"
    assert '_action = "scan"' in src, "默认动作不是只读的 scan —— 开关一碰就会改状态"
    seg = src[src.index("default:"):]
    seg = seg[: seg.index("\n")]
    assert "pair" not in seg, "动作清单里还列着 pair"


def test_getunits_summary_is_appended_not_overwritten():
    """`getunits` 的自动复查不许覆盖队伍结构摘要。

    10-19 真机：摘要被 10s 后自动 getunits 的 `Summary(...)` 整文件覆盖，
    归档里只剩「条件组实测 0 人」—— 结论丢了。改成追加。
    """
    src = _src(MULTI)
    body = src[src.index("private static void DoGetUnits()"):]
    body = body[: body.index('catch (Exception e) { Out("[getunits]')]
    assert 'SummaryAppend("=== 条件组实测' in body, \
        "DoGetUnits 仍在覆盖 summary（应 SummaryAppend，保留队伍结构摘要）"


def test_lowhp_switch_is_parse_only():
    """`lowhp` 的**行为**已删除（10-21），只保留开关解析与显示。

    原行为：在 `BattleUnitMgr.AddUnit` 后把队友战场单位的 `UnitDataBase.hp` 设为 1，
    用来强制队友倒地、走通"重伤 → 自动离队 → 经历"整条链。

    删除理由：让队友能被一击打倒**本身就是**反玩家的行为，而且它真的生效过一次
    ⇒ 队友在实测里平白重伤。真机也不需要它：敌人该打谁打谁，倒地会自然发生。
    但**解析必须留着** —— 老开关文件里残留 `lowhp=1` 时不许报"未知键"。
    """
    src = _src(MULTI)
    assert 'k == "lowhp"' in src, "开关解析没读 lowhp（老配置文件会报未知键）"
    assert "_lowHp" in src, "缺 lowhp 记录位"
    assert "ForceLowHp" not in src, "ForceLowHp 的实现又被加回来了（会把队友打成 1 血）"
    assert ".SetValue(data, 1)" not in src, "又在把战场单位 hp 强制设成 1"
    # 解析位必须明确标注"仅记录、无行为"，否则后来者会以为它还在生效
    assert "仅记录" in src or "仅保留显示" in src, \
        "lowhp 记录位没有标注「已无对应行为」—— 会误导后来者以为它还在生效"
