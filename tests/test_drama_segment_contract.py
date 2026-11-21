"""剧情相遇分段器契约（09-22）：一次当面相遇的**多页原文**攒成一段，点「对话」时整段投给模型。

**用户报的症状**：点原生剧情窗的「对话」，NPC 只知道**屏幕上此刻那一句** —— 一次相遇往往翻好几页
（切磋是「约战 → 打完 → 战后两页」这个量级），先发生的事模型全看不见；玩家打完仗再点，前面那几句
已经随页翻过去了。

**为什么要有这个文件**：判据表本身由 C# 单测锁着（`csharp/tests/DramaSegmentCoreTests`，与核同一份
源码、百项断言），但**接线错了表现全是静默的**：
  · 页没记上 ⇒ 回到"只看见一句"，日志里一句错都没有；
  · 投重复 ⇒ 同一件事喂两遍，模型以为"又发生了一次"；
  · 闸 1 / 闸 1.5 挡下的页被投出去 ⇒ 与 tool result 重复（模型复读）；
  · 跨语言的键漏一个（C# 发 `lines`、Python 读 `drama_lines`）⇒ 帧照到、功能静默失效。
这些在真机上只表现为"NPC 说话怪怪的"。所以接线必须在源码层面钉住。

钉十件事：
  A. 三条路径都往分段器记页（正常 / 闸 1 / 闸 1.5），且后两条是"照记不投"
  B. 点击时取**增量**；没有段时退回旧的单页注入（入口不许哑掉）；反复点不重投
  C. 采样四件套 + "唯一实现"复用（`DramaWindowName` / `isBattle` / `UiVisible` / `ActionShowing`），不另写判据
  D. 换世界清空（内容是旧世界的）
  E. 跨语言键成对（C# 写什么，Python 就读什么）
  F. Python 侧的包装与上限：整段、带归属、超限明写省略、信箱补投带着它
  G. 论道型多窗交互（09-23 实机）：**判据 = 关窗时动作 UI（`ActionCourseInfo`）亮 ⇒ 判交接**
     （挂起 → 回程，页间隙不切段）；**不许用"同人 + 时间近 + 未投"的续接当兜底** ——
     实机把"闲聊 + 0.5s 后的论道开场"并成一段一起投，模型据此说出"我推辞了论道"（已删；
     事故回归测试在核单测 ⑭）
  H. 场景类交互（邀约）页间隙（09-23 实机）：**结构性判据，不设时间窗** —— 关窗那刻 B1+B2 亮 ⇒
     直接挂起；回程额外要求"到达时仍在场景"（散场后的同人页另开一段）；`A↑`（关窗后才起的
     动作 UI）也算交接证据（核单测 ⑯ 是这些边界的回归测试）。
     另：**「只有我没有对方」的页不注入按钮**（`NpcTagPresent`，只用名签判），但内容照记
  I. 玩家选择进上下文（09-23）：**选项按钮文本 → 段里一行**（`Kind=="option"`，Python 渲染成
     "玩家选择了：…"；不许用 `opt.text` 那种本地化键）；投递帧带 `kind`；点击那一刻采 `action`
     （`NpcInitiativeMonitor.ActionActive`：存在性、不看层顶）→ Python 包装点明"这场交互还在进行中、
     别重复发起同款动作工具"（核单测 ⑰ 是选项行的回归测试）
"""

from __future__ import annotations

import re
import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """去掉文档注释与行注释 —— 断言"不许出现某实现"时，注释里提到它不算（本仓注释很长）。"""
    src = re.sub(r"///.*", "", src)
    return re.sub(r"//.*", "", src)


# ---------- A. 三条路径都记页 ----------


def test_all_three_paths_feed_the_recorder():
    """正常页 / 闸 1（工具执行期）/ 闸 1.5（我方动作后续）都要记页，后两条标"照记不投"。

    漏了正常页 ⇒ 分段器永远是空的（点了只投一句，与不装这个功能一样）；
    漏了闸 1/闸 1.5 ⇒ 那些页的原文**只**存在于 tool result 里，玩家之后再点「对话」
    会把同一件事又喂一遍（模型当成"又发生了一次"）。
    """
    src = _read(CS / "UI" / "DramaAiOption.cs")

    # 正常路径：本页入段，可投（说话人与成品句都用同一次判定/取值的结果，不重算）
    assert "NoteSegment(ui, id, data, npc, noInject: false, speakerOverride: pageSpeaker," in src, \
        "正常注入路径没有把本页交给分段器（分段器会一直是空的）"
    assert "textOverride: _pendingText);" in src, \
        "成品句没有复用 `_pendingText`（重取一次就可能与真正发出去的那一句不同源）"
    # 说话人实时判定的结果必须被**接住**再传下去（否则分段器要再判一次 → 两处判据漂移）
    assert "string pageSpeaker = DetectSpeaker(ui, data, root, out string sdiag);" in src, \
        "注入路径没有接住说话人判定结果 —— 分段器只能自己再判一次（两套判据）"

    # 闸 1：工具执行期弹的窗 —— 照记（正文"投/不投"按链政策），且**本页归属要建、选项照记**
    m = re.search(r"if \(DramaGate\.SuppressAiOption\)(.*?)\n                \}", src, re.S)
    assert m, "找不到闸 1（SuppressAiOption）分支"
    body1 = m.group(1)
    assert "NoteSegment(__instance, id, data, npc1, noInject1);" in body1, \
        "闸 1 挡下的页没有记进分段器"
    assert "SetPendingContext(__instance, data, id, npc1);" in body1, \
        "闸 1 页没有建本页归属 —— 它的选项会被吞掉（09-23 用户定：选项必须投）"
    assert "DramaNativeClaim.CurrentChainUid()" in body1, \
        "闸 1 无归属页没有借链目标 uid 的兜底"
    assert body1.index("NoteSegment(") < body1.index("return;"), \
        "记页落在 return 之后了 —— 那几页永远不会被记"

    # 闸 1.5：认领页 —— 记页的"投/不投"按链政策（contentViaResult），按钮按政策（injectOnClaimed）
    assert "NoteSegment(ui, id, data, npc, contentViaResult, claimWho);" in src, \
        "闸 1.5 认领页没有记进分段器（或没用政策决定正文投不投）"
    assert "string claimWho = DetectSpeaker(ui, data, root, out string claimDiag, npc);" in src, \
        "认领页的说话人判定结果没有接出来 —— 分段器会记不出谁在说"


def test_recorder_does_not_reimplement_any_judgement():
    """接线层**只采样、只转发**：说话人 / 成品句 / 找 NPC 的判据一律不在这里重写。

    本仓的老教训：同一件事两处判据 ⇒ 迟早漂移，而且日志里看着都"像实测"。
    """
    rec = _code_only(_read(CS / "drama" / "DramaSegmentRecorder.cs"))
    for forbidden in ("DetectSpeaker", "PortraitProbe", "ExtractText", "FindNpcOf"):
        assert forbidden not in rec, f"分段器接线层自己实现了 {forbidden} —— 判据必须只有一份（在 UI 层）"
    assert "new DramaSegmentCore(" in rec, "接线层没有用纯逻辑核 —— 判据会脱离单测覆盖"
    assert "ModIds.Mod" in rec, "私有段范围没有用 ModIds（我方自制窗会被当原生剧情记进去）"


# ---------- B. 点击：取增量、不重投、无段时退回单页 ----------


def test_click_injects_only_the_delta():
    """点击 = 投**还没投过**的增量；有段但无新增 ⇒ 只开面板、不重复投递。"""
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert "JArray lines = TakeSegmentDelta(unit);" in src, "点击时没有向分段器取增量"
    assert "lines != null && lines.Count == 0" in src, \
        "没有区分「没有段」与「有段但没新增」—— 反复点会把这页重投一遍"
    # 09-22 追加 busy：链在收尾（`DramaGate.ChainHold`）时这一轮只许说话 + 只读，
    # 免得在"处置页之后还有尾页"的那十几秒里插进一个新动作（契约见 test_battle_chain_hold_contract.py）
    # 09-24 扩参：末尾追加 `verdictPage`（当前页是处置页 → 附说明，契约见 test_verdict_page_contract.py）
    # —— 断言拆成两段，本契约的口径（增量 + busy/action 接线）不变。
    assert "ws.SendGameDrama(name, wireText, _pendingDramaId, speaker, lines, DramaGate.ChainHold," in src, \
        "增量没有随事件发出去，或没有把「链在收尾」当 busy / 「动作进行中」当 action 带出去"
    # 09-28 扩参：尾部再挂 `DramaNativeClaim.UidOf(unit)`（本页 NPC 的精确身份）
    assert "actionActive, verdictPage, DramaNativeClaim.UidOf(unit));" in src, \
        "busy 之后的 action/verdict/unitId 三个标记接线断了" \
        "（模型收不到「进行中/处置页」两种事实，或说话人身份又只能按名猜）"
    # 单页支路要发的文本：本页成品句取不到时，退回段里最后那句非空的（否则退化成"只有舞台指令"）
    assert "wireText.Length == 0 && lines != null && lines.Count > 0" in src, \
        "没做「本页取文本失败 → 用段里最后那句」的兜底"
    # 取增量的实现：没有段 ⇒ 返回 null（调用方退回单页注入）；有段 ⇒ 空表也算"有段"
    m = re.search(r"private static JArray TakeSegmentDelta\(WorldUnitBase unit\)(.*?)\n        \}", src, re.S)
    assert m, "找不到 TakeSegmentDelta"
    body = m.group(1)
    assert "if (!DramaSegmentRecorder.TryTakeDelta(uid, out delta)) return null;" in body, \
        "没有段时没有返回 null —— 入口会哑掉（点了什么都不发生）"
    for key in ('["seq"]', '["drama_id"]', '["speaker"]', '["name"]', '["text"]'):
        assert key in body, f"增量缺字段 {key}（Python 侧读不到）"


def test_no_inject_flag_travels_with_the_page_and_lands_on_the_line():
    """「照记但不投」必须**跟着页**走，并且落在**行**上（09-22 从段级下沉）。

    两个都是血泪点：
      · 跟着页走 —— 页会被**扣住**（关窗待定期间来的），ε 到点时它可能开成**新段**；记完再给
        活动段打标记就会打在上一段身上（新段反而可投 ⇒ 工具链原文被重复投给模型）；
      · 落在**行**上 —— 段可以是**混合**的（处置页不投 + 之后的原生尾页该投；处置页正是开段那一页）。
        段级标记会一票否决整段 ⇒ 尾页永远投不出去，而日志只显示"本段无新增"，看起来跟正常一样。
    """
    core = _read(CS / "drama" / "DramaSegmentCore.cs")
    assert "bool noInject, float now)" in core, "核的 OnPage 没有 noInject 形参"
    assert "rec.NoInject = noInject;" in core, "标记没有落到页记录上"
    assert "line.NoInject = rec.NoInject;" in core, "标记没有落到**行**上（Append 里没有传播）"
    assert "NoteNoInject" not in core, \
        "旧的「记完再给活动段打标记」写法还在 —— 被扣住的页会把标记打在上一段身上"
    assert not re.search(r"public int Clicks;\s*\n\s*public bool NoInject;", core), \
        "段级 NoInject 又回来了 —— 混合段会被一票否决（尾页投不出去，且日志看不出异常）"
    m = re.search(r"internal List<SegmentLine> TakeDelta\(string unitId, float now\)(.*?)\n        \}", core, re.S)
    assert m, "找不到 TakeDelta"
    body = m.group(1)
    assert "if (l.NoInject) continue;" in body, "TakeDelta 没认行级「不投」标记"
    assert "if (seg.NoInject) return result;" not in body, "TakeDelta 还在用段级标记一票否决"


def test_verdict_page_records_but_does_not_deliver():
    """处置页：**照记不投**（原文已当 tool result 给过模型），**但按钮要留着**。

    玩家得能接着和正在求饶的 NPC 说话；而那句求饶文不该再被投一遍（不然就是"同一件事说两次"）。
    这是"记页"与"注入按钮"第一次分家：此前这种页只能靠"跳过 return 落到正常路径"兼职，
    于是它必然被标成可投 ⇒ 与 tool result 重复。
    """
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert "NoteSegment(ui, id, data, npc, contentViaResult, claimWho);" in src, \
        "处置页没有按链政策记（内容随结果送达的链 ⇒ 照记不投）"
    assert "segmentRecorded = true;" in src and "if (!segmentRecorded)" in src, \
        "处置页会在正常路径里被记第二遍（同一页两条，且第二条变成可投）"
    assert "本页放行注入" in src, "处置页不再放行注入 —— 那页就没有「对话」按钮了"
    # ★先记页、再收口★（09-23 修）：收口要读段区间，先收口 ⇒ 结果里缺处置页这一行
    claim0 = src.index("if (DramaNativeClaim.TryClaim(claimUid, ui))")
    assert src.index("NoteSegment(ui, id, data, npc, contentViaResult, claimWho);", claim0) \
        < src.index("ResolveAtVerdictPage(", claim0), \
        "处置页是「先收口后记页」—— 收口读区间时会缺这一行"


def test_click_verdict_is_written_back_before_taking_the_delta():
    """点击时重判的归属要**先写回**、再取增量（顺序反了，投出去的那批还是页时刻的旧归属）。

    为什么需要写回：页事件发生在 `InitData`，而立绘压暗是 UI 侧逐页刷的 —— 刚回来的那一页
    读到的可能是**上一页**的明暗，于是"这句是谁说的"有反的可能（本仓最怕那类反事实）。
    点击那一刻本页已画好，那一次判定才作数（`DramaAiOption` 原话）。
    """
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert "NoteSegmentClick(unit, speaker);" in src, "点击时没有把重判结果写回段里"
    assert src.index("NoteSegmentClick(unit, speaker);") < src.index("JArray lines = TakeSegmentDelta(unit);"), \
        "写回晚于取增量 —— 这一批里当前页还是页时刻的旧归属"
    assert "DramaSegmentRecorder.NoteClickSpeaker(uid, _pendingDramaId, speaker," in src, \
        "写回没有带上页号（核里要按页号找那一条）"
    assert "private static string SpeakerSideName(string speaker, WorldUnitBase npc)" in src, \
        "名字没有按说话人那一侧取 —— 会渲染出「玩家（NPC 名）」这种反事实"
    # 接线层 → 核
    rec = _read(CS / "drama" / "DramaSegmentRecorder.cs")
    assert "internal static void NoteClickSpeaker(string unitId, int dramaId, string speaker, string name)" in rec
    assert "Core.OverrideSpeaker(unitId, dramaId, speaker, name, Now());" in rec
    core = _read(CS / "drama" / "DramaSegmentCore.cs")
    assert "internal void OverrideSpeaker(string unitId, int dramaId, string speaker, string name, float now)" in core
    m = re.search(r"internal void OverrideSpeaker\(string unitId, int dramaId, string speaker, string name, float now\)(.*?)\n        \}", core, re.S)
    assert m, "找不到 OverrideSpeaker"
    body = m.group(1)
    assert "if (string.IsNullOrEmpty(speaker)) return;" in body, \
        "判不出时没有拒绝覆盖 —— 会把已知的归属降级成未知"
    assert "seg.Lines[i].Name = name ??" in body, "只改了归属没改名字（同一个反事实换个字段）"


def test_recorder_take_delta_distinguishes_missing_and_empty():
    """核对核与接线的分工：`TryTakeDelta` = false（没有段）vs true+空表（有段但没新增）。"""
    rec = _read(CS / "drama" / "DramaSegmentRecorder.cs")
    assert "if (!Core.HasAnySegment(unitId)) return false;" in rec, \
        "没有用 HasAnySegment 区分「没有段」—— 两种情况会被混成一种"
    assert "lines = Core.TakeDelta(unitId, Now());" in rec, "没有从核取增量"
    core = _read(CS / "drama" / "DramaSegmentCore.cs")
    assert "internal bool HasAnySegment(string unitId)" in core, "核里没有 HasAnySegment"
    # 工具链的段（NoInject）**不算**能投的段，但**算**"有段"——否则会被当成"没有段"退回单页
    m = re.search(r"internal bool HasAnySegment\(string unitId\)(.*?)\n        \}", core, re.S)
    assert m and "NoInject" not in m.group(1), \
        "HasAnySegment 把 NoInject 段排除了 —— 工具链的段会退回单页注入（正是要避免的重复）"


def test_wire_carries_lines_only_when_present():
    """`lines` 只在**真有增量**时带上：空数组与缺项对 Python 是两件事（缺项 = 走单句路径）。"""
    wc = _read(CS / "bridge" / "WsClient.cs")
    assert "if (lines != null && lines.Count > 0) frame[\"lines\"] = lines;" in wc, \
        "WsClient 没有把增量挂到帧上（或无条件挂了空数组）"
    # 09-28 扩参：尾部再追加 `string unitId = null`（说话人精确身份，契约见
    # test_initiative_identity_contract.py）—— 本断言跟到新尾参，口径（可选参数不许少）不变。
    assert "JArray lines = null, bool busy = false, bool action = false," in wc \
        and "bool verdict = false, string unitId = null)" in wc, \
        "SendGameDrama 的签名没有可选 lines/busy/action（或后续扩的 verdict/unitId）—— 老调用点会编译不过"
    assert 'if (busy) frame["busy"] = true;' in wc, \
        "busy 没有落到帧上 —— 「处置页上边聊边被发起新动作」那条路会一直通着"


# ---------- C. 采样与"唯一实现" ----------


def test_sampling_reuses_the_single_implementations():
    """四件套：W 复用 `DramaWindowName()`（唯一实现）、B1 `isBattle`、B2 复用 `UiVisible`、
    A 复用 `ActionShowing()`（层顶 `ActionCourseInfo`，判"这次关窗是不是同一场交互的下一页"的主判据）。"""
    rec = _read(CS / "drama" / "DramaSegmentRecorder.cs")
    assert "NpcInitiativeMonitor.DramaWindowName()" in rec, \
        "剧情窗可见性没有复用唯一实现 —— 判据会与忙判据/面板闸门漂移"
    assert "isBattle" in rec, "没有读逻辑战斗位"
    assert "NpcInitiativeMonitor.UiVisible(ui.GetUI<UIBattleInfo>(UIType.BattleInfo))" in rec, \
        "战斗界面可见性没有复用 NpcInitiativeMonitor.UiVisible"
    assert "NpcInitiativeMonitor.ActionShowing()" in rec and "Core.Tick(w, b1, b2, a, Now())" in rec, \
        ("动作 UI（ActionCourseInfo）位没有采/没有喂给核 —— 论道那种页间关窗的多窗交互会被切碎，"
         "前文（含「论道请求」）静默丢，只剩最后一页")
    na = _read(CS / "npc" / "NpcInitiativeMonitor.cs")
    assert "internal static bool UiVisible(UnityEngine.MonoBehaviour uiObj)" in na, \
        "UiVisible 不是 internal（分段器用不了，只能再抄一份可见性判据）"
    assert "internal static bool ActionShowing()" in na, "ActionShowing 不是 internal/不存在"
    # ★10-25 第二步★ 判据实现搬到 `state/WorldStateFacts.cs`（`na` 那边只剩一行转发）。
    # 层顶名字族这条**保证不变**，只是换了住址 —— 盯新址，别让它退回按类名查。
    facts = _read(CS / "state" / "WorldStateFacts.cs")
    assert 'IndexOf("ActionCourseInfo"' in facts, \
        "动作 UI 判据不是层顶名字族（按类名查会像 UIMapBattlePre 那样恒 false 且不报错）"
    assert re.search(r"internal static bool ActionShowing\(\)\s*=>\s*WorldStateFacts\.ActionShowing\(\);", na), \
        "NpcInitiativeMonitor.ActionShowing 必须是一行转发（不许把实现搬回来）"
    # 帧回调必须**常驻**：关窗 → 收口这一步要靠它，页停了也不能停
    assert "g.timer.Frame(new Action(Tick), 1, true)" in rec, "帧回调没有注册（或不是每帧常驻）"


# ---------- D. 换世界清空 ----------


def test_world_reload_drops_the_segments():
    """段的内容属于**刚离开的那个世界**（对方编号换档即失效）—— 与 UnitIdRegistry 同一个钩子。"""
    mm = _read(CS / "app" / "ModMain.cs")
    assert "DramaSegmentRecorder.OnWorldChanged();" in mm, \
        "换存档没有清空分段 —— 新档里点「对话」会喂上一档那场相遇的原文"
    assert mm.index("DramaSegmentRecorder.OnWorldChanged();") > mm.index("load_happened"), \
        "清理必须挂在进世界事件（读档/新建）那条路上"
    rec = _read(CS / "drama" / "DramaSegmentRecorder.cs")
    assert "Core.OnWorldChanged(Now());" in rec, "接线层的 OnWorldChanged 没有落到核上"


# ---------- E. 跨语言键成对 ----------


def test_lines_keys_agree_across_the_language_boundary():
    """C# 写的键 = Python 读的键。错一个字母**完全静默**（帧照到、功能失效）。"""
    cs_src = _read(CS / "UI" / "DramaAiOption.cs") + _read(CS / "bridge" / "WsClient.cs")
    for key in ('["lines"]', '["speaker"]', '["name"]', '["text"]', '["seq"]'):
        assert key in cs_src, f"C# 侧没写 {key}"

    ws = _read(ROOT / "transport" / "ws_channel.py")
    assert 'msg.get("lines")' in ws, "Python 没解 lines 键"
    assert 'str(msg.get("speaker", "") or "")' in ws, "speaker 键的解构被改了（归属判据全在这条路上）"
    assert "speaker: str = \"\", lines=None" in ws, "handle_initiative 没有 lines 形参"
    assert "lines=rec.get(\"lines\")" in ws, \
        "信箱补投没带 lines —— 「最多晚到」到了却只有一句（半丢）"

    init = _read(ROOT / "agent" / "initiative.py")
    for key in ('it.get("speaker")', 'it.get("name")', 'it.get("text")'):
        assert key in init, f"Python 侧没读 {key}（与 C# 写的对不上）"


# ---------- F. Python 侧的包装 ----------


def test_multi_line_packaging_is_bounded_and_honest():
    """整段注入要有上限，且**明写省略**（静默丢会让模型以为这就是全部经过）。"""
    init = _read(ROOT / "agent" / "initiative.py")
    assert "_MAX_DRAMA_LINES = 40" in init, "整段注入没有上限 —— 一段病态的链能把上下文挤爆"
    assert "句已略" in init, "超限是静默丢的（模型会以为看到的就是全部）"
    assert "def format_game_drama_message(text: str = \"\", speaker: str = \"\", lines=None," in init, \
        "format_game_drama_message 没有 lines/in_action 形参"
    # ≥2 句才走多句支路：1 句必须逐字退回原有单句措辞（那是真机一轮轮调出来的）
    assert "if len(rows) <= 1:" in init, "没有保留单句支路 —— 常见情形（只有一句）文风会变"


# ---------- G. 论道型多窗交互：A 判据（正确接法）与"时间窗续接"禁令（09-23 事故）----------
# ★09-23 实机补充★：论道一次实测被切成 3 段（22811 / 22813 / 22831+22848+22847+22815），
# 玩家在最后一页点「对话」只投得到最近那段 —— 前两句（含"论道请求"）静默丢失，模型看着像凭空开始。
# 根因：原生多窗交互每页都是独立窗、页间**没有转场起跳**，而分层判据当时写的是"关过窗"。
# 修法：**关窗那一刻动作 UI（`ActionCourseInfo`）亮着 ⇒ 判交接**（走挂起 → 回程，页间隙不切段）。
# 曾试过"同人 + 20s 内 + 未投 ⇒ 续接"的兜底，**实机把它判死**：闲聊（A 灭干净收口）后 0.5s 的
# 论道开场被并进闲聊段，8 条一起投，模型把刚完成的论道读成"我推辞了论道"、回复里凭空道歉。


def test_no_time_window_merge_next_to_a_clean_close():
    """A 判据必须在；时间窗续接必须不在（它分不出"同一场的下一页"与"另一件事"）。"""
    core = _read(CS / "drama" / "DramaSegmentCore.cs")
    code = _code_only(core)
    assert "RejoinSeconds" not in code and "FindRejoinable" not in code, \
        ("时间窗续接又回来了 —— 实机曾把「闲聊 + 0.5s 后的论道开场」并成一段，"
         "模型据此说出「我推辞了论道」（核单测 ⑭ 是这条的回归测试）")
    assert 'if (_a) PushEdge("A", now);' in code, \
        ("关窗时没有把动作 UI 电平记成交接证据 —— 论道那种页间关窗的多窗交互会被当干净关窗切段，"
         "玩家只能投到最后一页")
    na = _read(CS / "npc" / "NpcInitiativeMonitor.cs")
    assert "internal static bool ActionShowing()" in na, "动作 UI 判据（ActionShowing）不存在"


# ---------- H. 场景类交互（邀约）页间隙：**结构性判据，不设时间窗** ----------
# ★09-23 实机补充★：邀约（进一个场景互动）与论道不同 —— 进场景后 B1（逻辑战斗位）/B2（战斗界面）
# **从进到出一直亮着**，页间"点选项 → 关窗 → 新页"没有任何起跳边沿；关窗到新页的间隔由玩家的
# 阅读/演出决定（实测 0.53s ~ 6.64s，还可能更长）。若沿用 1s 待定窗：每轮互动都被切成 1~2 条的小段，
# 玩家点「对话」只看到半句来半句去。
# 判据 = **关窗那刻 B1+B2 亮 ⇒ 这次关窗只是页间隙 ⇒ 直接挂起（`SuspendByScene`）**；
# 回程（同人新页到达）**额外要求"到达时 B1+B2 仍亮"** —— 人还在场景里，间隙多长都接得住；
# 人已散场（场景态解除），之后来的同人页是**另一件事**，宁可另开一段（`RoutePage` 的 `SceneSuspend` 分支）。
# ★为什么不用时间窗（放宽也不行）★：页间隙长度由玩家决定，固定窗接不住长间隙；放宽窗（试过 4s）
# 会把"散场之后"的事并进来（与 G 组那条事故同形）。**判据里不许再出现秒数。**
# 另：`A↑`（关窗**之后**才起跳的动作 UI，实测 0.5s）也是交接证据 —— 旧实现只看"关窗那刻的电平"会漏。


def test_scene_pages_gap_is_structural_not_a_time_window():
    """邀约型场景：页间隙 = 场景态挂起 + 场景内新页回程（结构判据）；不许退化成时间窗。"""
    code = _code_only(_read(CS / "drama" / "DramaSegmentCore.cs"))
    assert "SceneEpsSeconds" not in code and "_sceneAtClose" not in code, \
        ("场景态页间隙又用「放宽时间窗」实现了 —— 实测页间隙 0.53s ~ 6.64s 且由玩家的阅读/演出决定，"
         "放宽窗既接不住长的、又会把散场后的事误并（核单测 ⑯ 是回归测试）")
    assert "if (_b1 && _b2)" in code and "SuspendByScene(now);" in code, \
        "关窗那刻 B1+B2 亮没有直接挂起 —— 场景类交互的页间隙会被当干净关窗切段"
    assert "private void SuspendByScene(float now)" in code, "SuspendByScene 不在"
    assert "bool sceneStillOn = !seg.SceneSuspend || (_b1 && _b2);" in code, \
        ("回程没有「到达时仍在场景」这条约束 —— 散场之后的同人页会被并回邀约段（两场当成一场）")
    assert "seg.SceneSuspend = true;" in code and "seg.SceneSuspend = false;" in code, \
        "SceneSuspend 标记没有跟着挂起/回程成对设置"
    assert "场景已退出（同人新页在场景外到达）" in code, \
        "未回程的收口理由没有区分「场景已退出」（日志/对账分不出是哪种未回程）"


def test_action_ui_edge_is_evidence():
    """`A↑`（关窗**之后**才起跳的动作 UI）也要算交接证据（09-23 邀约实测：0.5s 后才起跳）。"""
    code = _code_only(_read(CS / "drama" / "DramaSegmentCore.cs"))
    assert "bool aRose = !_a && a;" in code, "Tick 没有采 A 的假→真边沿（旧实现只看关窗那刻的电平）"
    assert 'if (aRose) PushEdge("A↑", now);' in code, "A↑ 边沿没有记成交接证据"
    assert "动作 UI 起跳" in code, "A↑ 没有人话映射（日志里会像一条未知证据）"


# ---------- I. 玩家选择进上下文：选项行 + 「进行中」提示（09-23） ----------
# 用户报的两个割裂点：① 只记页面正文 ⇒ 模型"只知道疑问、不知道玩家选了什么"（选项不在
# `GetDialogueText` 里）；② 论道页上点「对话」，模型不知道交互还在进行 ⇒ 又发起一次论道。
# 判据/接线：C# `CreateOptions` postfix 给选项按钮挂监听（读**按钮上的文本**）→
# `DramaSegmentRecorder.NoteOption` → 核记一行（`Kind=="option"`）；投递帧带 `kind`；
# 点击那一刻采 `action`（`NpcInitiativeMonitor.ActionActive`，存在性、不看层顶）→ Python 包装提示。


def test_option_row_wiring_cs():
    """选项行：按钮文本监听 → Recorder.NoteOption → 核里 Kind=option（不许用 opt.text 那种本地化键）。"""
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert '"CreateOptions"' in src and 'typeof(UIDramaBase)' in src, \
        "没有挂 `UIDramaBase.CreateOptions` —— 选项按钮建出来的那一刻没人接（选择读不到文本）"
    assert "ClickUtils.Append(btn, () => OnOptionFromButton(btn));" in src, \
        "选项按钮没有**追加**监听（Append 保留游戏原回调；Attach 会清掉它），或没走「点击时实时读文本」入口"
    # ★09-23 实机翻车教训★：原来按 `_boundOptionPtr`（IL2CPP 指针）判"已绑"就跳过 —— 实测同一页
    # 第二次弹出时 `已绑跳过=5 新挂=2`，被跳过那 5 枚在页面重建后没有我们的回调，玩家点它们
    # **连「选项点击」日志都没有**（811013 / 811111 / 811221 三次选择静默丢失）。
    assert "else if (!string.IsNullOrEmpty(label))" not in src, \
        "「已绑就跳过」的分支又回来了 —— 实机它在页面重建后漏挂 5 枚（点击静默丢失）"
    assert "string label = ReadButtonLabel(btn.gameObject, out _);" in src, \
        "监听没有在点击那一刻实时读按钮文本（对象池复用后闭包里的旧文本会张冠李戴）"
    assert "if (label == _lastTapLabel && nowT - _lastTapAt < 0.35f) return;" in src, \
        "没有同文本去抖 —— 重复挂载/按钮复用会让一次点击记出多条选项"
    assert "DramaSegmentRecorder.NoteOption(uid, _pendingDramaId, label, pname);" in src, \
        "选项点击没有交给分段器（玩家选择进不了上下文）"
    rec = _read(CS / "drama" / "DramaSegmentRecorder.cs")
    assert "internal static void NoteOption(string unitId, int dramaId, string text, string name)" in rec, \
        "接线层没有 NoteOption"
    core = _code_only(_read(CS / "drama" / "DramaSegmentCore.cs"))
    assert 'line.Kind = "option";' in core and 'line.Speaker = "choice";' in core, \
        "核里没有把选项记成 option 行（speaker=choice）"
    assert "internal void NoteOption(string unitId, int dramaId, string text, string name, float now)" in core, \
        "核里没有 NoteOption"
    # 归属纪律：没有同人活动段 ⇒ 丢弃（不凭空造段）；空文本不记（判不出就闭嘴）
    m = re.search(r"internal void NoteOption\(string unitId, int dramaId, string text, string name, float now\)(.*?)\n        \}", core, re.S)
    assert m, "找不到 NoteOption 方法体"
    assert "string.IsNullOrEmpty(text)" in m.group(1) and "seg.Counterpart != unitId" in m.group(1), \
        "NoteOption 没有守卫（空文本/非本段对方都必须丢弃）"


def test_option_and_action_protocol_across_languages():
    """跨语言键成对：C# 发 `kind`/`action`，Python 读 `kind`/`action` 并渲染/提示。"""
    cs = _read(CS / "UI" / "DramaAiOption.cs") + _read(CS / "bridge" / "WsClient.cs")
    assert '["kind"]' in cs, "C# 投递帧没带 kind"
    assert '["action"] = true' in cs, "C# 投递帧没带 action"
    assert "internal static bool ActionActive()" in _read(CS / "npc" / "NpcInitiativeMonitor.cs"), \
        "ActionActive（动作进行中的存在性判据）不在"
    ws = _read(ROOT / "transport" / "ws_channel.py")
    assert 'bool(msg.get("action", False))' in ws, "Python 没解 action 字段"
    assert "in_action=action" in ws, "handle_initiative 没有把 action 传给包装"
    init = _read(ROOT / "agent" / "initiative.py")
    assert 'kind = str(it.get("kind") or "page")' in init, "Python 没解 lines 里的 kind"
    assert 'if kind == "option":' in init, "选项行没有单独渲染（会被当成台词）"
    assert "不要再调用同一个动作工具" in init, "「进行中」系统提示不在"


def test_button_not_injected_when_counterpart_is_absent():
    """「只有我没有对方」的页不注入按钮（09-23 截图实证）；但**内容照记**（这场交互的一部分）。"""
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert "private static bool NpcTagPresent(UIDramaBase ui, WorldUnitBase npc)" in src, \
        "「对方是否在场」判据不在 —— 悬空按钮会回来"
    at = src.index("if (!NpcTagPresent(ui, npc))")
    # 注入路径独有的标记（`SetPendingContext` 助手里的同名赋值会更早出现，不能用它当锚）
    assert at < src.index("// 正常注入页：本页归属（uid 从 unit 取）"), \
        "判据位置不对：必须在待处理上下文赋值之前（拦在注入前）"
    m = re.search(r"private static bool NpcTagPresent\(UIDramaBase ui, WorldUnitBase npc\)(.*?)\n        \}", src, re.S)
    assert m, "找不到 NpcTagPresent"
    body = m.group(1)
    assert "NameTagText(" in body, "判据必须只用名签（立绘探针是启发式，名签是游戏自己写的字）"
    assert "PortraitProbe" not in body and "DetectSpeaker(" not in body, \
        "别把立绘/说话人探针拖进在场判据 —— 那是另一套语义"
    # 命中分支里必须"照记段"（按钮不注入 ≠ 内容不记）
    hit = src[at:at + 900]
    assert "NoteSegment(" in hit, "命中「对方不在场」时没有照记段 —— 这页内容会静默丢"
    assert "return;" in hit, "命中后没有 return（会继续注入）"


# ---------- J. 段 = **唯一内容流**：工具结果读段区间（09-23 用户定） ----------
# 用户定调（逐字）："整体结构上段（唯一流：页 + 选项，追加序 = 时序），其余的 TalkOutcome 和
# BattleOutcome 不再去收集内容"；"选项必须投，不受 UI 文本'不投'影响"；"邀约进到场景之后
# 所有双方都在的 UI 互动需要注入对话按钮"；"链比段活得久 → 加心跳，每跳一次延长段的存活"。
# 本组钉住六件事：① 没有第二个内容收集器；② 结果经 ReadRange 读段、读走即消费；
# ③ 选项行不继承"不投"；④ 按钮/不投政策表（内容是否随结果送达）；⑤ 锚点早于动作启动 + 处置页先记后收口；
# ⑥ 链心跳与硬上限。


def test_single_content_stream_no_second_collector():
    """内容收集器只能有一个（段）：两个台账里不许再有内容数组与收集口。"""
    tx = _read(CS / "drama" / "TalkOutcome.cs")
    bx = _read(CS / "drama" / "BattleOutcome.cs")
    for name, src in (("TalkOutcome", tx), ("BattleOutcome", bx)):
        assert "NoteLine(" not in src and "NoteChoice(" not in src and "NoteDramaLine(" not in src, \
            name + " 里还有自建的内容收集口 —— 段必须是唯一内容流"
        assert "_lines.Add(" not in src and "new List<JObject>" not in src, \
            name + " 里还有自建内容数组"
        assert "TryReadRangeFromChain(" in src, name + " 的结果没有从段里读区间"
    assert "public static void FillResult(JObject data)" in tx, "论道的结果填充口不在"
    assert 'data["lines"] = DramaSegmentRecorder.LinesToJson(' in tx, "论道结果没有落成段的单流"
    assert "public static void Arm(int slot, string npcName, string op, string unitID)" in tx, \
        "论道台账没有拿目标 uid（区间读数的键）"
    # 渲染层成对：单流 + 旧形状兜底都在
    tr = _read(ROOT / "tools" / "text_render.py")
    assert 'if _s(ln.get("kind")) == "option":' in tr, "渲染层没认单流的 kind=option"
    assert '选择了：「' in tr, "选择没有渲染出口"
    assert 'after_drama_id") == did' in tr, "旧形状兜底没了（C# 升级不同步时会看不到选择）"
    # 选择不算"链上原话"：只用来判"打没打过"的 spoken 必须跳过 option 行
    m = re.search(r"def _battle_said\(data: Dict\[str, Any\]\) -> List\[str\]:(.*?)\n\n\ndef ", tr, re.S)
    assert m and 'if _s(it.get("kind")) == "option":' in m.group(1), \
        "`_battle_said` 把选择算成了台词（会据此误判「动过手」）"


def test_option_line_never_inherits_noinject():
    """★选项必须投（09-23 用户定）★：选项行不许跟随上一行的"不投"；唯一压制是"被结果读走"。"""
    core = _code_only(_read(CS / "drama" / "DramaSegmentCore.cs"))
    m = re.search(r"internal void NoteOption\(string unitId, int dramaId, string text, string name, float now\)(.*?)\n        \}", core, re.S)
    assert m, "找不到 NoteOption"
    body = m.group(1)
    assert "line.NoInject = false;" in body, "选项行还在继承「不投」（击杀/放过会丢）"
    assert "line.NoInject = seg.Lines" not in body, "继承写法还在"
    assert "internal List<SegmentLine> ReadRange(string unitId, float from, float until, bool consume)" in core, \
        "核里没有区间读数"
    m2 = re.search(r"internal List<SegmentLine> ReadRange\(string unitId, float from, float until, bool consume\)(.*?)\n        \}", core, re.S)
    assert m2, "找不到 ReadRange 方法体"
    assert "result[i].NoInject = true;" in m2.group(1), "读走没有标消费 —— 之后点对话会重投"
    assert "Delivered" not in m2.group(1), "区间读数碰了投递游标（两个消费者会互相抢）"
    assert "SeqGlobal" in core, "没有全局追加序（跨段归并的时序会乱）"
    assert "internal int CountRange(string unitId, float from, float until)" in core, "收口判据的行数口不在"


def test_chain_policy_buttons_and_noinject():
    """按钮与"正文不投"由**链政策**决定（内容会不会随工具结果送达），不再由"是不是认领页"决定。"""
    claim = _read(CS / "drama" / "DramaNativeClaim.cs")
    assert "public static bool ContentViaResultOf(string op)" in claim, "内容政策表不在"
    assert "public static bool InjectOnClaimedOf(string op)" in claim, "按钮政策不在"
    assert 'return op == "yao_yue";' in claim, "邀约（场景类交互）没有开按钮 —— 用户 09-23 明确要求"
    for op in ("lun_dao", "shuang_xiu", "spar", "attack", "yao_yue", "give_item", "ask_for", "chuan_gong"):
        assert op in claim, op + " 不在政策表里"
    assert "public static string CurrentChainUid()" in claim and "public static float AnchorOf(string unitID)" in claim
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert "DramaNativeClaim.InjectOnClaimedOf(chainOp)" in src, "认领分支没有按政策决定注入"
    assert "DramaNativeClaim.ContentViaResultOf(chainOp)" in src, "认领分支没有按政策决定正文投不投"
    # 政策只管按钮/正文；**记录**永远照做（页与选项一律入段）
    claim_start = src.index("if (DramaNativeClaim.TryClaim(claimUid, ui))")
    seg = src[claim_start:claim_start + 3000]
    assert "SetPendingContext(ui, data, id, npc);" in seg, "认领页没有建本页归属（选项会丢）"


def test_chain_heartbeat_and_hard_ceiling():
    """段寿命由链驱动：心跳滑动预算；心跳停现行规则接管；硬上限不可续期。"""
    core = _code_only(_read(CS / "drama" / "DramaSegmentCore.cs"))
    assert "internal void RenewChain(string unitId, float now)" in core, "链心跳口不在"
    assert "ChainAlive(seg.Counterpart, now)" in core, "挂起期没有把心跳算进「链还在飞」"
    assert "SegmentHardCeilingSeconds" in core, "没有不可续期的硬上限（僵尸段会回来）"
    assert "if (seg.SuspendedAt >= 0f) seg.SuspendedAt = now;" in core, "心跳没有滑动挂起预算"
    bx = _read(CS / "drama" / "BattleOutcome.cs")
    assert "DramaSegmentRecorder.RenewChain(_uid);" in bx, "战斗台账没有发心跳（长战斗会被切段）"
    assert "DramaSegmentRecorder.ReleaseChain(_uid);" in bx, "链结束没有停心跳"
    rec = _read(CS / "drama" / "DramaSegmentRecorder.cs")
    assert "internal static void RenewChain(string unitId)" in rec and \
        "internal static void ReleaseChain(string unitId)" in rec, "接线层没有心跳口"


def test_ai_action_must_not_precreate_before_action_start():
    """★ 10-17 更正：论道/双修**绝不**在 `ActionStart` 之前调 `CreateActionInit()` ★

    这条测试的结论**反转**了 09-29 那版。原版要求"消费游戏裁决"（钉
    `Init → CreateActionInit → 锚点 → ActionStart` 的顺序），方向没错 ——
    论道/双修走 `WorldUnitAIActionBase` 这棵树，基类是 `Object`、**不继承**
    `UnitActionBase`，attack/spar/yao_yue 那套 `WorldUnitBase.CreateAction() : Int32`
    裁决码在类型上就接不到它身上；`ActionStart` 又是 void ⇒ 确实没有返回码可用。

    **但那个位置是错的，它让论道 100% 失败**（10-17 真机实锤）：

        [AiAction] lun_dao CreateActionInit 抛异常: NullReferenceException
        tool_result: {"success": false, "error": "lun_dao 启动失败: NullReferenceException"}

    两次重试同一 NRE（`slot=1`/`slot=2` 都栽在这里，模型被规则允许重试一次）。

    机理：`ActionStart` **内部自己会创建动作** —— 本仓旧快照
    `reference/decomp_tmp/built_types.txt:1456` 记着当年可跑通的形态，只有
    `Init(npc)` + `action.Init(ai,1,null)` + `ActionStart(cb)`，**没有**这一句。
    在它之前先创建一次 ⇒ 同一个 `WorldUnitAIAction1037` 被创建两遍，
    第二遍（`ActionStart` 内部那次）撞上空引用。

    ⇒ 那个探针不是"读到拒绝码"，而是"把动作对象弄坏"。

    现行结论：**引擎在这条树上不给码，就接受它不给** —— 改用
    `LogAiActionEnergyProbe` 的只读读数（energy / energyMax / costEnergy）为
    "启动前自证"攒事实基础（见 `NativeRefuseWhy` 的同一套思路：只报能自证的量，
    绝不猜码表）。

    钉三件事：
      ① `CreateActionInit` 不许出现在 `ActionStart` 之前（含整个方法体）；
      ② 顺序仍是 Init → 探针 → 锚点 → ActionStart（锚点早于启动这条铁律不变）；
      ③ 探针存在且**只读**（不许出现 `Fail(` —— 它是观测件，不是闸）。
    """
    tool = _cs_tools.tool_src()
    at = tool.index(" JObject StartToUnitAiActionDeferred(")
    seg = tool[at:at + 14000]   # 10-21：同步回调修复 + 移动闸探针把本方法拉到 ~12.2k 字符

    # ① 正文里不许再有"预先创建"这个动作（注释里讲历史是允许的，故只查非注释行）
    code_lines = [ln for ln in seg.splitlines() if not ln.strip().startswith("//")]
    code = "\n".join(code_lines)
    assert "CreateActionInit()" not in code, (
        "正文里又出现了 CreateActionInit() —— 它会在 ActionStart 之前预创建一次，"
        "导致同一个动作对象被创建两遍、第二遍抛 NullReferenceException（10-17 真机实锤，"
        "论道 100% 失败）。详见本测试 docstring 的机理说明。"
    )

    # ② 顺序：Init → 锚点 → ActionStart
    #   ⚠️ 2026-10-23：中间那个「精力探针」（`LogAiActionEnergyProbe`）已按用户要求**整体删除**
    #   （"摘掉诊断探针"，清理清单见 `docs/archive/2026-10-22-论道静默失败/修法与清理.md` §6）。
    #   它原先钉的是"探针排在锚点之前"——删掉之后这段顺序断言只剩两段，故随之收缩。
    #   删它的理由：`costEnergy` 在 1037 上从未被真机验证过，一个只会打日志、
    #   又不敢拿它拦动作的观测件，留着只是每次动作多几行反射读数。
    i_init = seg.index("action.Init(ai, 1, null);")
    i_arm = seg.index("TalkOutcome.Arm(slot, npcName, op, claimUid);")
    i_start = seg.index("action.ActionStart(")
    assert i_init < i_arm < i_start, (
        "顺序必须是 Init → 锚点 → ActionStart"
        "（锚点晚于启动 = 首窗 22821 进不了工具结果）"
    )

    # ③ 反面钉子：探针不许悄悄加回来（用户已明确要求摘除）
    for gone in ("LogAiActionEnergyProbe(", "LogAiActionMoveProbe(", "LogGridProbe("):
        assert gone not in code, (
            "诊断探针 `%s` 又出现在执行路径里了 —— 已于 2026-10-23 按用户要求摘除"
            "（docs/archive/2026-10-22-论道静默失败/修法与清理.md §6）。要重加请先改文档与本断言。" % gone
        )


def test_anchor_before_action_start_and_verdict_page_order():
    """锚点必须早于动作启动；处置页必须"先记页、再收口"（否则结果缺那一行）。"""
    tool = _cs_tools.tool_src()
    at = tool.index(" JObject StartToUnitAiActionDeferred(")
    seg = tool[at:at + 14000]   # 10-21：同步回调修复 + 移动闸探针把本方法拉到 ~12.2k 字符
    assert "TalkOutcome.Arm(slot, npcName, op, claimUid);" in seg, "论道/双修没有武装台账（带 uid）"
    assert seg.index("TalkOutcome.Arm(slot, npcName, op, claimUid);") < seg.index("action.ActionStart("), \
        "论道/双修的锚点晚于 ActionStart —— 首窗（22821）会进不了结果"
    assert "TalkOutcome.Disarm();" in seg, "启动失败没有撤锚点"
    src = _read(CS / "UI" / "DramaAiOption.cs")
    claim = src.index("if (DramaNativeClaim.TryClaim(claimUid, ui))")
    record = src.index("NoteSegment(ui, id, data, npc, contentViaResult, claimWho);", claim)
    verdict = src.index("ResolveAtVerdictPage(", claim)
    assert claim < record < verdict, "处置页是「先收口后记页」—— 收口读区间时会缺这一行"
    claim_cs = _read(CS / "drama" / "DramaNativeClaim.cs")
    assert "public static float AnchorOf(string unitID)" in claim_cs, "凭证锚点不在（战前页读不到）"
    assert "if (newChain) _claims[i].CreatedAt = now;" in claim_cs, \
        "新链没有重置锚点（旧凭证还在时会把上一场读进来）"


def test_no_npc_page_borrows_chain_uid():
    """无归属页（攻击逃跑页 20303）借链目标 uid 入段 —— 页与它的选择都不许丢。"""
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert "DramaNativeClaim.CurrentChainUid()" in src, "没有「当前链目标 uid」的兜底"
    assert "private static void SetPendingContextUid(" in src, "只有 uid 的页没有归属口"
    assert "private static void NoteSegmentByUid(" in src, "只有 uid 的页没有记录路径"
    off = src.index("无 NPC 页 ⇒ 借用链目标 uid=")
    seg = src[off - 600:off + 400]      # 建归属/记页在日志行**之前**
    assert "SetPendingContextUid(" in seg and "NoteSegmentByUid(" in seg, \
        "无归属页没有入段 —— 逃跑那句与「离开」会静默丢"
    assert "ClearPendingContext();" in src, "没有活动链时必须维持「不记 + 清上下文」"


def test_pending_context_attribution_is_always_explicit():
    """「记录进段的页」必须二选一：建立本页归属 or 显式清空（09-23 普遍化 + 无归属页兜底）。

    `_pending*` 只在当前页有效、且没有关窗清理，于是每个 Postfix 分支都要明确处置：
      · 闸1 页 / 认领页 / 对方不在场页 / 注入页 → `SetPendingContext`（uid 从 unit 取）；
      · 无归属页（`FindNpcOf` 判不出，如攻击逃跑页 20303）→ 有活动链就 `SetPendingContextUid`
        （借链目标 uid），没有才 `ClearPendingContext`（判不出就闭嘴）。
    归属的**唯一依据是 `_pendingUid`**（可以没有 unit 对象）；选项永远不受正文"不投"影响。"""
    src = _read(CS / "UI" / "DramaAiOption.cs")
    assert "private static void SetPendingContext(UIDramaBase ui, DramaData data, int id, WorldUnitBase npc)" in src, \
        "建本页归属的方法不在"
    assert "private static void SetPendingContextUid(UIDramaBase ui, DramaData data, int id, string uid)" in src, \
        "只有 uid 的页没有归属口"
    assert "private static void ClearPendingContext()" in src, "清空方法不在"
    assert "private static string _pendingUid" in src, "归属 uid 字段不在"
    # 认领分支：建归属必须在"记页"之前（选项才落得到本段）
    claim = src.index("闸1.5 认领（我方动作后续）dramaId=")
    assert src.index("SetPendingContext(ui, data, id, npc);", claim) \
        < src.index("NoteSegment(ui, id, data, npc, contentViaResult, claimWho);", claim), \
        "认领分支没有先建本页归属 —— 论道/邀约…的选项会丢或错记进别人的段"
    # 对方不在场页：建归属（这页是可投段，选项必须跟它一起进段）
    absent = src.index("对方不在场（NPC 名签缺失/链OFF）⇒ 不注入按钮")
    seg = src[absent:absent + 700]
    assert "SetPendingContext(ui, data, id, npc);" in seg and \
        seg.index("SetPendingContext(") < seg.index("return;"), \
        "对方不在场页没有建本页归属 —— 这页的选项会被丢弃（段里有问没答）"
    # 无归属页：借链目标 or 显式清空（两者都必须落在 return 之前）
    nopc = src.index("无 NPC 页 ⇒ 借用链目标 uid=")
    seg2 = src[nopc - 600:nopc + 400]   # 借链目标/建归属在日志行**之前**
    assert "SetPendingContextUid(" in seg2, "无归属页没有借链目标 uid 建归属（选项会丢）"
    assert "ClearPendingContext();" in src, "没有活动链时必须维持「不记 + 清上下文」"
    # 点击入口：归属以 `_pendingUid` 为准（不再从 unit 现算）
    assert 'string uid = _pendingUid ?? "";' in src, \
        "选项点击没有以 `_pendingUid` 为唯一归属依据"


def test_tool_result_reads_segment_range():
    """工具结果的内容来源：段区间（页 + 选择）；婉拒路径也要带；处置页之后的内容留给段。"""
    bo = _read(CS / "drama" / "BattleOutcome.cs")
    assert "DramaSegmentRecorder.TryReadRangeFromChain(_uid, _armedAt, true, out range)" in bo, \
        "BattleOutcome 收口没有读段区间（内容来源没切过来）"
    assert '["lines"] = lines,' in bo, "结果里没有单流 lines"
    assert "DramaSegmentRecorder.CountPages(_uid, _armedAt, Time.unscaledTime)" in bo, \
        "收口判据没有改用区间**页**数（旧的自建计数已删；选择不算「抓到台词」）"
    dc = _read(CS / "UI" / "DramaDrillChoice.cs")
    assert "DramaSegmentRecorder.TryReadRangeFromChain(puid, 0f, true, out range)" in dc, \
        "切磋婉拒路径没有读段区间（原文/选择会缺）"
    assert dc.index("TryReadRangeFromChain(puid, 0f, true, out range)") \
        < dc.index("UnitActionPending.Remove(ptr)"), \
        "婉拒路径顺序反了：凭证吊销/摘登记在前，区间锚点取不到"
    assert "DramaNativeClaim.Release(puid);" in dc, "婉拒路径读完没有吊销凭证"
    # 攻击：处置页收口之后的选择/尾页必须留在段里（用户 09-23 定「后面的交段」）
    src = _read(CS / "UI" / "DramaAiOption.cs")
    verdict = src.index("ResolveAtVerdictPage(")
    after = src[verdict:verdict + 600]
    assert "该有「对话」按钮" in after, "处置页没有放行「对话」按钮（后面的内容投不出去）"

