"""「战前 / 战中」闸：C# 判据细节 + 跨语言与接线契约（10-25 用户拍板）。

## 背景（一句话）

真机 10-25 18:51：NPC 寻仇那条 `attack` 被排队 → 玩家自己点了同一页上的「战斗」→
排队那条 **18:51:28.687 落刀，又开了一次同一页** ⇒ 两场架叠在一起 ⇒ 之后在处置页点
「对话」，段里只剩「不投」的行 → `TakeDelta` 返回 0 → `Trigger` 静默返回，
玩家看到的是"点了没反应"。

## 用户拍板的形态（别自作主张扩大）

* **即将进入战斗时，只不许"会再开一场仗 / 会把玩家挪走"的四样**：
  `world_ai_action` 的 `attack`/`spar`、`movement` 的 `teleport`/`summon` —— **当场拒绝**。
* 其余动作（赠予/讨要/传功/邀约/论道/双修/行走）**照旧排队**，只读一律照常。
  **不是一刀切禁全部工具**（用户明确反对粗粒度）。
* 拒绝时回一句话即可：**不要**主动往回合里塞"你现在正忙、不能调用工具"的提示。

## 本文件钉什么

C# 的规则本身由 `csharp/tests/BattleFlowCoreTests`（85 项断言，含真机时间线）锁住。
这里钉的是那些**跨不过编译期、错了也完全静默**的东西：

  ① C# 三个挂点真的存在，且都**恒放行 + try/catch**（异常顺着游戏点击链往上炸，玩家就点不动）；
  ② `ClickOption` 的钩子必须是 **prefix**（引擎在它内部同步跑完选项效果 —— postfix 会晚于整条链）；
  ③ 工具层的闸必须在**排队闸之前**判拒绝（否则攻击会进队列、战斗结束后再打一次）；
  ④ 判定拒绝的那一段必须读 `BattleFlowGate`，**不许**在工具层另写一份 op 白名单（两套判据必然分叉）；
  ⑤ 拒绝走 `Fail(...)`，**不许**返回 `null`（null = 退回等待 = "延后执行"，正是要避免的语义）；
  ⑥ 落盘文件名与两个内置编号在位（100003 = 寻仇页「战斗」，212042 = 切磋「好，就让我和你切磋一下」）；
  ⑦ 换存档要复位状态、**但不清表**（编号是配表事实，不随存档变）；
  ⑧ 诊断行在（点「对话」那一刻要能看出战场态，否则排障只能靠猜）。
"""
from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ① 三个挂点都在，且恒放行 + 吞异常
# ---------------------------------------------------------------------------

def test_three_hooks_exist_and_never_block():
    """三个挂点是三个案子的唯一入口，缺一个就漏一类战斗。

    真机 10-25 的分工（一页日志里全都在）：
      · `CreateOptions` → 寻仇页 20203 摆着 100003 / 切磋页 21204 摆着 212042
      · `ClickOption`   → 玩家点「战斗」那一刻（18:51:28.123 / 30.328）
      · `DramaFunction.OptionsFunction` → 进副本（`IntoDungeon` 4 次）
    """
    hooks = _read(CS / "state" / "BattleFlowHooks.cs")
    assert 'HarmonyPatch(typeof(UIDramaBase), "CreateOptions")' in hooks, \
        "页级挂点（CreateOptions）没了 —— 寻仇/切磋那两类再也点不亮"
    assert 'HarmonyPatch(typeof(UIDramaBase), "ClickOption")' in hooks, \
        "点击挂点（ClickOption）没了 —— 婉拒/确认都判不出来"
    assert 'TypeByName("DramaFunction")' in hooks, \
        "剧情命令挂点（DramaFunction，按名反射）没了 —— 副本那类学不到编号"
    # 恒放行：拦下来玩家就点不动选项
    m = re.search(r"private static bool Prefix\(ConfDramaOptionsItem __0, out int __state\)(.*?)\n        \}", hooks, re.S)
    assert m, ("ClickOption 的钩子签名变了（必须是 Prefix(ConfDramaOptionsItem, out int __state) —— "
               "__state 是把 prefix 的作用域传到 postfix 的唯一通道）")
    body = m.group(1)
    assert "return true;" in body, "ClickOption 钩子必须恒放行（拦下来玩家点不动选项）"
    assert "catch" in body, "ClickOption 钩子没有 try/catch（异常会顺着游戏点击链往上炸）"
    # 作用域必须闭合：prefix 开、postfix 关。只开不关 ⇒ _clickDepth 只增不减，
    # 之后所有"引擎自己发起的进副本命令"都会被当成玩家点的，学出错误的开战按钮。
    assert "EnterClickScope" in body, "ClickOption prefix 没有开启点击作用域"
    assert re.search(r"private static void Postfix\(int __state\)", hooks), \
        "ClickOption 缺 postfix —— 作用域永不闭合（会学出错误的开战按钮）"
    assert "ExitClickScope" in hooks, "缺 ExitClickScope"
    # CreateOptions 的 postfix 也必须吞异常
    m2 = re.search(r"private static void Postfix\(UIDramaBase __instance\)(.*?)\n        \}", hooks, re.S)
    assert m2 and "catch" in m2.group(1), "CreateOptions 钩子没有 try/catch"


def test_learning_is_scoped_by_call_stack_not_by_time():
    """★"学"的归属判据必须是**调用栈作用域**，不许退化成时间窗或"最近一次点击"★

    用户 10-25 明确否掉了时间窗（"点了无关选项后来自己去打副本就误判了"/"超过15s又有问题"）。
    真机栈 4 次一致：`ClickOption → OptionsFunction → IntoDungeon`，即进副本命令
    **同步跑在点击内部**。所以判据是"此刻还在不在 ClickOption 里"，不是"隔了多久"。
    """
    gate = _read(CS / "state" / "BattleFlowGate.cs")
    core = _read(CS / "state" / "BattleFlowCore.cs")
    m = re.search(r"internal static void NoteDungeonCommand\(string how\)(.*?)\n        \}", gate, re.S)
    assert m, "NoteDungeonCommand 不见了"
    body = m.group(1)
    # 归属走**作用域**（`ClickScope.OptionInScope`），不是"最近一次点击"
    assert "OptionInScope" in body, "学习判据没用点击作用域（退化成'最近一次点击'了？）"
    assert "不在任何点击作用域内" in body, "没有'作用域外一律不学'那条分支"
    # 作用域外不学 —— 这条是防"引擎自己发起进副本"被误记到某枚无关选项上
    assert "Probe.Outside" in body, \
        "没有'作用域外不学'的显式拒绝（会把引擎自己发起的进副本记到无关选项上）"
    # ★10-25 复核新增★ 作用域必须带**帧号**（= postfix 没跑到时的自愈）
    #   引擎的 ClickOption 内部抛异常 ⇒ Harmony 的 postfix 不跑 ⇒ 作用域永不闭合；
    #   此后"引擎自己发起的进副本命令"会被记到一枚早就点过的选项上 ⇒ 那一页永久被当成
    #   开战页 ⇒ 攻击/切磋/传送/召唤被持续误拒（而且落盘后跨存档）。
    #   帧号是对它的结构性否定：真机栈证明这条路是**同步调用**，同步 ⇒ 同一帧，零秒数。
    assert "Probe.Expired" in body, (
        "进副本学习没处理\"作用域是上一帧残留的\"这种情况 —— postfix 一旦没跑到，"
        "作用域就永久泄漏，学出错误的开战按钮（后果是**多挡**，不是少挡）"
    )
    assert re.search(r"EnterClickScope\(int optionId\)\s*\{[^}]*FrameNow\(\)", gate, re.S), \
        "EnterClickScope 没把当前帧号喂给作用域 —— 帧号是防泄漏的唯一依据"
    scope = re.search(r"internal struct ClickScope(.*?)\n    \}", core, re.S)
    assert scope, "ClickScope 不在核里（学习归因必须在纯逻辑层，才能被断言钉住）"
    sd = scope.group(1)
    assert "nowFrame" in sd and "_frame" in sd, "ClickScope 里没有帧号 —— 泄漏无法自愈"
    assert "Probe.Expired" in sd and "_depth = 0" in sd, \
        "帧号不符时必须**就地作废**（自愈），不能只是返回不命中"
    # 不许出现时间窗式的归属（"最近 N 秒内点过就算它"）
    assert "TotalSeconds" not in body, (
        "学习判据里出现了秒数 —— 用户 10-25 明确否掉了时间窗归因"
        "（'点了无关选项后来自己去打副本就误判了'）"
    )


def test_click_hook_is_prefix_not_postfix():
    """★别改回 postfix（09-22 真机实证，`DramaDrillChoice` 头注释记着）★

    引擎在 `ClickOption` **内部同步**跑完选项效果（切磋就是在这里直接 `IntoBattle`）。
    postfix 要等这一切跑完才轮到 —— 那时战斗都已经开起来了，"点了开战按钮"这一击
    就再也拿不到（我们正是靠它把战前升级成"已确认"的）。
    """
    src = _read(CS / "state" / "BattleFlowHooks.cs")
    assert "return true;     // 永不拦截" in src, \
        "ClickOption 钩子的'恒放行'注释被删了 —— 复核时看不出为什么不能拦"
    assert "prefix" in src.lower(), "BattleFlowHooks 里没有 prefix 字样"


# ---------------------------------------------------------------------------
# ② 工具层的闸：顺序、位置、语义
# ---------------------------------------------------------------------------

def test_tool_gate_runs_before_the_queue_gate():
    """★顺序即语义★：先判"当场拒绝"，再判"排队"。

    反了的话：攻击会先进 `DeferredActionQueue`（它判"此刻发不出去"就排队），
    等战斗链收场后**自动执行**——真机 10-25 18:51:28.687 那条落刀正是这么来的。
    "排队"的语义是"等会儿办"，而攻击/切磋/传送/召唤等会儿补做 = 把已发生的事再办一遍。
    """
    te = _cs_tools.tool_src()
    # 只从 `Execute(string name, JObject args, string npcId)` 的函数体里找 ——
    # 文件上方的文档注释里也写着 `DeferredActionQueue.ShouldQueue()`（判据说明），
    # 从文件头 `find` 会命中那句注释，得到"闸在排队闸之前"的假结论。
    i_fn = te.find("public JObject Execute(string name, JObject args, string npcId)")
    assert i_fn > 0, "找不到带 npcId 的 Execute（签名改了？）"
    i_reject = te.find("BattleFlowGate.VerdictOf(", i_fn)
    i_queue = te.find("DeferredActionQueue.ShouldQueue()", i_fn)
    assert i_reject > 0, "工具层没有接战前/战中闸（BattleFlowGate.VerdictOf）"
    assert i_queue > 0, "排队闸不见了"
    assert i_reject < i_queue, (
        "战前/战中闸排在了排队闸**之后** —— 攻击会先进队列、战斗结束后再打一次"
        "（10-25 18:51:22 入队 / 28.687 落刀那个故障会原样复现）"
    )
    assert "ToolVerdict.Reject" in te, "工具层没判 Reject"


def test_reject_returns_fail_not_null():
    """★拒绝必须返回 `Fail(...)`，不许返回 `null`★

    排队器把 `null` 解释成"执行时又忙起来了 → 退回等待（不判失败）"（09-24 用户拍板）。
    所以战前/战中如果在执行体里返回 `null`，攻击就变成"延后执行"而不是"作废"——
    语义正好相反，而且**不会有任何报错**。
    """
    te = _cs_tools.tool_src()
    m = re.search(r"战前/战中作废（不补做）(.*?)\n                \}\)\)", te, re.S)
    assert m, "排队执行体里没有'战前/战中作废'那一段"
    body = m.group(1)
    assert "return Fail(" in body, "排队执行体里拒绝时没有 return Fail（返回 null = 延后执行，语义相反）"
    assert "return null" not in body, "排队执行体里出现了 return null（那是'退回等待'，不是'作废'）"


def test_tool_layer_does_not_duplicate_the_op_whitelist():
    """工具层**只许问** `BattleFlowGate`，不许自己再写一份 op 白名单。

    两套判据必然分叉（本仓已有教训：`DramaWindowName` 与面板闸门各判一次，
    同一时刻结论相反）。op 分类的唯一实现是 `BattleFlowCore.Grade`。
    """
    te = _cs_tools.tool_src()
    i_fn = te.find("public JObject Execute(string name, JObject args, string npcId)")
    assert i_fn > 0
    # 锚点只用来**定位**那段代码，不锁写法：`if (IsActionTool(name))` 与
    # `bool isAction = IsActionTool(name);` 是同一个判据的两种排版（11-20 去重成后者）。
    i_gate = te.find("IsActionTool(name)", i_fn)
    assert i_gate > 0, "工具层的战前/战中闸不见了"
    body = te[i_gate:i_gate + 900]
    assert "BattleFlowGate.VerdictOf" in body, "工具层没问 BattleFlowGate"
    assert "BattleFlowGate.RejectTextOf" in body, "工具层没问 BattleFlowGate 要拒绝文案"
    assert '"attack"' not in body and '"spar"' not in body, (
        "工具层里出现了 op 白名单（attack/spar）—— 判据必须只有一份（BattleFlowCore.Grade）"
    )
    assert '"teleport"' not in body and '"summon"' not in body, \
        "工具层里出现了 op 白名单（teleport/summon）—— 同上"


# ---------------------------------------------------------------------------
# ③ 接线与落盘
# ---------------------------------------------------------------------------

def test_wiring_in_modmain():
    """主工程必须：接线（Attach）+ 两个按名反射的钩子 + 换存档复位。"""
    mm = _read(CS / "app" / "ModMain.cs")
    assert "BattleFlowGate.Attach()" in mm, "主工程没接战前/战中判据的帧采样"
    assert "BattleFlowFuncHook.EnsureInstalled()" in mm, "主工程没装剧情命令钩子（副本那类学不到）"
    assert "BattleStartHook.EnsureInstalled()" in mm, "主工程没装开打钩子"
    assert "BattleFlowGate.SetDir(" in mm, "主工程没告诉判据把'开战按钮表'写在哪"
    assert "BattleFlowGate.OnWorldChanged()" in mm, \
        "换存档没复位战前状态（旧世界那场仗的'已确认'会带到新世界）"


def test_persisted_ids_are_config_facts():
    """两个内置编号必须是配表事实，不许是猜的。

    * `100003`：10-25 寻仇页 20203 上那枚「战斗」—— 游戏自己在
      `[BattleOutcome] 链里点了选项 id=100003` 里打出来的；
    * `212042`：`DramaDialogue` id=21204 `options="212042|212041"` 的左位
      =「好，就让我和你切磋一下」（仓库里 `DramaDrillChoice.AgreeOptionId` 早就有它）。

    这两个是"第一闸今天就能生效"的全部依据 —— 删了它们，寻仇与切磋那两类
    就只剩"应战窗"和"已经在打"两个更晚的信号（10-25 那条攻击就挡不住了）。
    """
    gate = _read(CS / "state" / "BattleFlowGate.cs")
    assert "100003" in gate, "寻仇页那枚「战斗」（100003）不在内置表里"
    assert "212042" in gate, "切磋确认页那枚「同意应战」（212042）不在内置表里"
    assert "battle_flow_ids.json" in gate, "落盘文件名不见了（学到的编号存不下来）"
    # 负 id 必须被排除：本 mod 自制确认窗从 ModIds.Mod = -803158451 起
    assert "optionId <= 0" in gate or "optionId <= 0" in _read(CS / "state" / "BattleFlowCore.cs"), \
        "没有排除负 id —— 我方赠礼确认窗会把战前状态误清掉"


def test_state_reset_keeps_learned_ids():
    """换存档 **复位状态**、但**不清表**。

    状态（战前/已确认/应战窗）是旧世界那一刻的事实，必须清；
    编号是配表事实（同一个按钮编号在任何存档里都是同一枚按钮），清了等于每次读档重新学。
    """
    gate = _read(CS / "state" / "BattleFlowGate.cs")
    m = re.search(r"internal static void OnWorldChanged\(\)(.*?)\n        \}", gate, re.S)
    assert m, "OnWorldChanged 不见了"
    body = m.group(1)
    assert "_core.Reset()" in body, "换存档没复位状态"
    assert "_battleIds" not in body and "Clear()" not in body, \
        "换存档把学到的编号也清了（那是配表事实，不该随存档丢）"


# ---------------------------------------------------------------------------
# ④ 诊断行（排障全靠它）
# ---------------------------------------------------------------------------

def test_diagnostic_lines_at_the_silent_return():
    """点「对话」那一刻必须留下战场态 —— 包括"本段无新增内容"那条静默返回。

    10-25 用户报的就是这条静默返回（"我点对话…为什么没有反应"）。
    没有战场态，事后只能从别处的日志反推；有了它，一眼看出"当时系统认为在战前/战中吗"。
    """
    dao = _read(CS / "UI" / "DramaAiOption.cs")
    i_silent = dao.find("本段无新增内容，不重复投递")
    assert i_silent > 0, "那条静默返回不见了（找错文件或语义已改）"
    seg = dao[i_silent:i_silent + 400]
    assert "BattleFlowGate.Describe()" in seg, \
        "静默返回那一行没打战场态（排障时无法回答'当时是不是正在打'）"
    assert dao.count("BattleFlowGate.Describe()") >= 2, \
        "只有一处打了战场态（正常发起那条也要打，两条路都要能对账）"


def test_sample_throttle_is_declared():
    """采样必须节流：层顶扫描要跨语言读名，每帧全量算就是 10-21 那次卡顿的形状。"""
    gate = _read(CS / "state" / "BattleFlowGate.cs")
    assert "SampleSeconds" in gate, "采样节流常量不见了（会变成每帧扫 UI 层）"
    m = re.search(r"const double SampleSeconds = ([\d.]+);", gate)
    assert m and 0.0 < float(m.group(1)) <= 1.0, "采样节流值不合理（真机最短的空洞是 2.2 秒）"
    assert "TickProfiler.End(\"battleflow\"" in gate, "帧回调没进耗时采样（卡顿查不出来）"


def test_fallback_seconds_is_sixty_and_declared_as_cleanup_only():
    """兜底 60 秒：它**只负责清理**，不参与归因（用户 10-25 明确区分过这两件事）。"""
    core = _read(CS / "state" / "BattleFlowCore.cs")
    assert "DefaultFallbackSeconds = 60" in core, "兜底秒数不是 60（用户已拍板）"
    assert "不参与任何归因" in core, \
        "兜底与归因的区别没写清 —— 半年后会有人拿它去当'多久内开打就算它干的'用"
    # 兜底前必须有"续期"：还有证据就不该走
    assert "TotalSeconds > _fallbackSeconds" in core, "兜底判据不见了"
