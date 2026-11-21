"""切磋确认窗契约（09-14）：玩家选「应战 / 婉拒」必须给模型**不同**的话。

**用户报的症状**：切磋弹原生剧情窗（两个选项），但选哪个工具返回都一样 ——
「现在都是一样的，不太合适」。

**根因**：`ToolExecutor.WorldAiAction` 的 `case "spar"` 是 `CreateAction` 完就 `return Ok(...)`，
压根不看玩家选了什么；Python 侧又写死一句「已向你发起切磋，即将进入战斗/切磋界面」。
同一个 switch 里 `yao_yue`/`lun_dao`/`shuang_xiu`/`chuan_gong` 早就"等玩家选完再回"，
**切磋是唯一漏掉的那个**（反编 `unit_action_sigs.txt`：`UnitActionRoleDrill` 与
`UnitActionRoleInvite`/`TeachSkill` 同构，都有 `OnEnd()` + 落定字段 `isDrillComplete`）。

**修法**：判据取**选项本身**（`UI/DramaDrillChoice.cs` 盯 `UIDramaBase.ClickOption`），
不取 `isDrillComplete` —— 后者语义没有真机样本（同类字段 `isInviteComplete` 当年就把
"接受"误判成"拒绝"，用户踩中），而选项 id 是**配表里的确定事实**。

**09-22 两条更正**：当时另拍板的"选项一落定就回，不等战斗"，理由是「`OnEnd` 在同意路径要等
整场打完」—— 那个前提**是错的**（真机 Player.log 4896→4897：OnEnd 在进战斗那一刻就触发）。
  ① 判据必须挂 **prefix**：postfix 永远晚于兜底，真判据被静默丢弃 ⇒ 玩家应战并打完了，
     模型拿到的却是「但你未作答复」，于是它按"你没应战"演下去；
  ② 应战路径的结局改由 `BattleOutcome` **跨过整场战斗**去收 —— 战后那两页原文
     （实证 21401「厉害，我输了。」/ 21402「看来是我赢了！」）带归属当工具结果，
     谁赢由模型读原话判，我们不解析关键词。

本文件锁这些事：
  A. C# 的 `case "spar"` 走挂起（不是立即 Ok）+ 兜底钩子在 + 判据挂 **prefix**
  B. **硬编码的选项 id 与游戏配表逐字对齐**（配表在才查，不在则 skip）
  C. 渲染层各态各自不同、且字段缺失不替玩家编"婉拒"
  D. `attack` 不受影响（`UnitActionRoleAttack` 连 OnEnd 都没有，接不了这条链）
  E. 应战路径交给 `BattleOutcome`（不在此 Resolve、不吊销归因凭证）
  F. 结局靠战斗事件/证据 + 滑动续期，不靠放大超时；心跳两边对齐
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"

# 游戏配表（官方「配置（只读）Json格式」导出）。测试机没装游戏时 skip，不让套件硬依赖安装。
_CONF_CANDIDATES = [
    Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置（只读）Json格式"),
    Path("E:/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置（只读）Json格式"),
]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _conf_dir():
    for d in _CONF_CANDIDATES:
        if (d / "DramaDialogue.json").is_file():
            return d
    return None


def _load_conf_json(p: Path):
    raw = p.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)   # 官方导出带尾逗号
    return json.loads(raw)


# ---------- A. C# 侧走挂起 + 兜底钩子在 ----------


def test_spar_does_not_return_immediately():
    """`case "spar"` 不许再是"CreateAction 完立刻 Ok"。

    这是本次修复的**核心**：它一旦被改回去，两个选项又会给模型同一句话，
    而且**没有任何别的测试会红**（渲染层三态测试照样通过，因为它是单测渲染器）。
    """
    src = _cs_tools.tool_src()
    m = re.search(r'case "spar":(.*?)\n\s*case "attack":', src, re.S)
    assert m, "WorldActionTool.cs 里找不到 case \"spar\" 分支"
    body = m.group(1)
    assert "DeferNativeUnitAction" in body, "case \"spar\" 不再走挂起（DeferNativeUnitAction）"
    assert "DrillChoiceGate.Arm" in body, "case \"spar\" 没有武装选项门（DrillChoiceGate.Arm）"
    assert "MakePendingMarker" in body, "case \"spar\" 没有返回 pending 标记 → 工具会立刻回结果"
    # 挂起之前不许出现"直接 Ok 旧形态"
    assert not re.search(r"CreateAction\(drill,\s*true\);\s*\n\s*return Ok", body), \
        "case \"spar\" 又退回成 CreateAction 后立即 Ok"


def test_drill_onend_hook_registered():
    """`UnitActionRoleDrill.OnEnd` 兜底钩子必须在（玩家没选就关窗时用它收口）。"""
    src = _read(CS / "tools" / "UnitActionHooks.cs")
    assert re.search(r'HarmonyPatch\(typeof\(UnitActionRoleDrill\),\s*"OnEnd"\)', src), \
        "UnitActionHooks.cs 缺 UnitActionRoleDrill 的 OnEnd postfix（未选就关窗会白等 120s）"
    # 铁律：postfix 必须吞异常，否则顺着游戏自身动作结束链往上炸
    m = re.search(r"class UnitActionDrillHook(.*?)\n    \}", src, re.S)
    assert m and "try {" in m.group(1) and "catch" in m.group(1), \
        "UnitActionDrillHook 的 Postfix 没有 try/catch（会让游戏动作结束不掉）"


def test_choice_gate_hooks_click_option():
    """选项门必须挂在 `UIDramaBase.ClickOption` 的 **prefix** 上，且未武装时零打扰。

    反编依据：`ClickOption` 只声明在 `UIDramaBase`，子类无覆写 ⇒ 挂基类覆盖全部剧情窗变体。

    ★为什么必须是 prefix 而不是 postfix（09-22 真机实证，别改回去）★
    引擎在 `ClickOption` **内部同步**跑完选项效果 —— 切磋就是在这里直接 IntoBattle
    并触发 `UnitActionRoleDrill.OnEnd`（Player.log 4895→4899）。postfix 要等这一切跑完才轮到，
    那时兜底已经把槽位收口、`req_id` 已被 WsClient 摘走（`WsClient.cs:373`），
    **真判据被静默丢弃** —— 玩家明明应战并打完了，模型拿到的却是「但你未作答复」。
    """
    p = CS / "UI" / "DramaDrillChoice.cs"
    assert p.is_file(), "缺 UI/DramaDrillChoice.cs"
    src = _read(p)
    assert re.search(r'HarmonyPatch\(typeof\(UIDramaBase\),\s*"ClickOption"\)', src), \
        "没有挂 UIDramaBase.ClickOption"
    m = re.search(r"private static bool Prefix\(UIDramaBase __instance, ConfDramaOptionsItem __0\)(.*?)\n        \}", src, re.S)
    assert m, ("ClickOption 的钩子不是 `Prefix(UIDramaBase, ConfDramaOptionsItem)` —— "
               "改回 postfix 就等于把 09-22 那个'应战了却报未作答复'的 bug 放回去")
    assert "return true;" in m.group(1), "prefix 必须恒放行：拦下来玩家就点不动选项了"
    assert "try {" in m.group(1) and "catch" in m.group(1), \
        "ClickOption 的 prefix 没有 try/catch（异常会顺着游戏自身点击链往上炸）"
    assert "if (slot < 0) return;" in src, \
        "Observe 开头没有\"未武装直接返回\"—— 玩家自己开的剧情窗会被误判成选边"


def test_agree_path_hands_off_to_spar_outcome():
    """应战路径必须把登记**摘走**交给 `BattleOutcome`，不许在这里 Resolve。

    两条硬理由：
      · 在这里收口 = 在**进战斗那一刻**就交结果，那时还没有任何结局可言；
      · `UnitActionPending.Remove` 会顺手吊销归因凭证 → 战后窗的第二页被注入「AI 对话」按钮，
        而点了它只会得到「世界界面尚未就绪」（实证 5076-5084）。摘走（Take）不吊销。
    """
    src = _read(CS / "UI" / "DramaDrillChoice.cs")
    agree = re.search(r"if \(agree\)(.*?)// 婉拒", src, re.S)
    assert agree, "找不到应战分支（if (agree) …）"
    body = agree.group(1)
    assert "UnitActionPending.Take(" in body, "应战分支没有 Take 走登记 —— 进战斗时的 OnEnd 会抢着收口"
    assert "BattleOutcome.Arm(" in body, "应战分支没有把结局交给 BattleOutcome"
    assert "DramaGate.Resolve(" not in body, "应战分支在这里就 Resolve 了 —— 那时还没有战斗结局"
    assert "UnitActionPending.Remove(" not in body, "应战分支用了 Remove（会吊销归因凭证 → 战后窗长出对话按钮）"


def test_refuse_path_resolves_in_ok_shape():
    """婉拒分支必须回 `{success:true, data:{…}}`，不许递裸 data。

    这条是复核时抓到的：`WsClient.OnDramaResolved` 按 `result["success"]` 判成败（取不到即 false），
    `text_render.render` 又按 `res["data"]` 取字段 —— 递裸 data 时 Python 收到的是
    `{success:false, error:"未知错误"}`，于是**玩家婉拒**这条最要紧的分支又是一句模型读不懂的话，
    正是本次要根治的"两个选项给同一句话"。渲染层单测钉不住它（它绕过 C# 直接喂 data），
    所以在这里钉 C# 那一侧的形状。
    """
    src = _read(CS / "UI" / "DramaDrillChoice.cs")
    seg = src.split("// 婉拒", 1)
    assert len(seg) == 2, "找不到婉拒分支"
    refuse = seg[1].split("/// <summary>选项 → 立场", 1)[0]
    assert '["success"] = true' in refuse, "婉拒分支没有 success 位 → Python 判成工具失败"
    assert '["data"] = sparData' in refuse and "var sparData = new JObject" in refuse, \
        "婉拒分支的结局字段没有包进 data"
    assert '["accepted"] = false' in refuse, "婉拒分支丢了 accepted"
    # 09-23：内容改从**段区间**读（台账不再自己收），且必须读在吊销凭证/摘登记**之前** ——
    # 锚点 = 凭证首次登记时刻，早于 21204 提问页（顺序反了读到的就是空的或残缺的）。
    assert "DramaSegmentRecorder.TryReadRangeFromChain(puid, 0f, true, out range)" in refuse, \
        "婉拒分支没有读段区间（这场交互的原文/选择会缺）"
    assert refuse.index("TryReadRangeFromChain(puid, 0f, true, out range)") \
        < refuse.index("UnitActionPending.Remove(ptr)"), \
        "婉拒分支顺序反了：凭证吊销/摘登记在前，区间锚点取不到（战前那页读不到）"


def test_soft_battle_end_waits_for_the_player_to_finish_paging():
    """战后那几行是**玩家手动翻页**的：只靠轮询判出"打完了"时不许立刻收口。

    实证（Player.log 5012 / 5049 / 5088-5096）：21401 由 `TimerMgr` 弹出，21402 是玩家点「继续」
    经 `UIDramaDialogue:OnNextClick → NextDialogue` 出来的**同一个窗的第二页**，
    `BattleEndMgr.BattleEnd` 排在最后（点完最后一页才跑）。所以：
      · 抓到 `BattleEnd`（hard）且已有台词 → 立刻收口（它天然在所有台词之后）；
      · 只有 `isBattle`/`UIBattleInfo` 翻假（soft，旧注释就写着"结算期 isBattle 可能已落"）
        → 必须再等一段静默，否则第一句一到就收口：**第二句永远拿不到**，而且凭证已吊销
        → 第二页重新长出那颗没用的「AI 对话」按钮（用户报的原症状）。
    """
    so = _read(CS / "drama" / "BattleOutcome.cs")
    # 09-23：内容在段里（单流），"有没有内容"改问区间行数 `have`（`_lines` 台账已删）
    assert "_battleEndedHard && have > 0" in so, "硬信号那条收口规则不见了"
    assert "have > 0 ? SettleSeconds : AfterBattleEndGrace" in so, \
        "软信号退化成了'抓到第一句就收口' —— 战后窗的第二页会被切掉"
    assert "int have = Lines();" in so, "收口判据没有从段区间取内容行数"
    bh = _read(CS / "drama" / "BattleEndHook.cs")
    assert "NoteBattleEnded(" in bh and ", true)" in bh, \
        "BattleEndMgr 事件没报成硬信号 → 只能走静默等待，收口慢且不稳"


def test_narration_flag_crosses_the_language_boundary():
    """旁白标记必须**两端对得上**：Python 发 `narration` → C# 解构 → 画成「行动记录」行。

    跨语言那一环是最容易"改了这边忘了那边"的地方：Python 发了个 C# 不认识的键
    （或 C# 读了另一个键名）**完全静默** —— 帧照到、气泡照画，用户看到的就是那条
    "姜萌替唐炎说话"的泡。所以三处一起钉：
      · `_emit_say` 带 `narration` 键（只在真为旁白时）；
      · `ChatFrameTee.Decode` 解 `["narration"]` 并落到 `ChatFrame.Narration`；
      · `ApplyRoomSay` 的旁白分支调 `AppendActionRow`（直播），
        重开窗那条路由 `project_room_history` 发成 `tool_result` 条目走既有的行动记录分支。
    """
    flow = _read(ROOT / "room" / "room_flow.py")
    assert '"narration": True' in flow, "_emit_say 不发 narration 键 → C# 无从判断旁白"
    hist = _read(ROOT / "room" / "room_history.py")
    assert '"tool_result" if u.narration else' in hist, \
        "群窗投影没把旁白发成行动记录条目 → 重开窗会画成气泡或干脆不见"
    tee = _read(CS / "UI" / "ChatFrameTee.cs")
    assert 'p["narration"]' in tee, "Tee 没解构 narration（帧到了也当普通发言）"
    fq = _read(CS / "UI" / "ChatFrameQueue.cs")
    assert "public bool Narration;" in fq, "ChatFrame 没有 Narration 字段"
    cp = _read(CS / "UI" / "ChatPresenter.cs")
    assert "bool narration = false" in cp and "AppendActionRow(text)" in cp, \
        "ApplyRoomSay 没有旁白分支 → 直播仍会画气泡"
    cw = _read(CS / "UI" / "ChatWindow.cs")
    assert "public void AppendActionRow(string text)" in cw, "群窗缺行动记录行入口"


def test_outcome_waits_on_battle_signals_not_wall_clock():
    """结局收口必须靠**事件/证据**，不能靠把超时放大（09-22 拍板：战斗中不计，战后 60s 收口）。

    钉三件事：① 战斗判据取 isBattle 与 UIBattleInfo 两个位的**或**（对切磋都是零样本，
    只押一个 = 把功能压在未验证的单一事实上，同 `unitLeft/unitRight` 那次教训）；
    ② 槽位与归因凭证都要**滑动续期**（`DramaGate.Renew` + `ClaimFor`），否则一场超过 120s 的
    战斗会把槽位和凭证双双熬过期；③ 世界重载必须强制收口（busy 键 `ModIds.AiActionGate`
    是所有动作工具共用的，不释放 = 整套动作系统瘫到重启）。
    """
    so = _read(CS / "drama" / "BattleOutcome.cs")
    assert "NpcInitiativeMonitor.BattleActive()" in so, "没有用双判据战斗位"
    assert "DramaGate.Renew(" in so, "槽位没有滑动续期 —— 打久一点就会被 120s 掐断"
    assert "DramaNativeClaim.ClaimFor(" in so, "归因凭证没有续期 —— 战后窗会漏出对话按钮"
    assert "DramaGate.Resolve(" in so, "BattleOutcome 不自己收口，那谁收？"
    na = _read(CS / "npc" / "NpcInitiativeMonitor.cs")
    assert "internal static bool BattleActive()" in na, "BattleActive 双判据 helper 不见了"
    # ★10-25 第二步★ 双判据的**实现**搬到 `state/WorldStateFacts.cs`（`na` 只剩一行转发）。
    # 取或这条保证不变：盯新址，并要求两条位都在、且仍是取或。
    facts = _read(CS / "state" / "WorldStateFacts.cs")
    assert "isBattle" in facts and "UIBattleInfo" in facts, "战斗判据退化成了单一位"
    body = facts[facts.index("internal static bool BattleActive()"):]
    body = body[:body.index(chr(10) + "        }")]
    assert "IsBattleLevel() || BattleUiVisible()" in body, \
        "双判据不再是取或 —— 结算期 isBattle 已落而界面还在时会提前收口"
    mm = _read(CS / "app" / "ModMain.cs")
    assert "BattleOutcome.ForceClose(" in mm and "DramaGate.ForceResolveAll(" in mm, \
        "世界重载没有强制收口 —— 槽位会永久占住所有动作工具的 busy 键"


def test_heartbeat_keeps_both_sides_agreeing():
    """长等槽位要发心跳，Python 侧据此滑动 deadline（否则两边必然打架）。

    钉的是跨语言那一环：C# 每 30s 一条 `type:"pending"`；Python 收到就把 RPC 的截止时刻往后推，
    心跳断了 90s 内自己收口。少了这条，要么慢一点的切磋被 120s 掐成「请求超时」（而真结果
    几分钟后回来时已无人认领 = 唯一正确的那条被静默丢弃），要么对端真失联时干等到小时级。
    """
    wc = _read(CS / "bridge" / "WsClient.cs")
    assert "DramaGate.OnStillPending += " in wc, "WsClient 没订阅心跳事件"
    assert '["type"] = "pending"' in wc, "没有发 type:pending 帧"
    ch = _read(ROOT / "transport" / "ws_channel.py")
    assert 't == "pending"' in ch, "Python 没处理 pending 心跳帧"
    assert "_await_response" in ch and "asyncio.shield" in ch, \
        "Python 没做滑动等待（或用了会取消 future 的裸 wait_for —— 那等于把请求作废）"
    cp = _read(CS / "UI" / "ChatPresenter.cs")
    assert "tool_pending" in cp, "对话窗没把心跳喂给忙标自愈时钟（4 分钟会误清成空闲）"


def test_gate_declares_no_uncalibrated_field_read():
    """★不许读 `isDrillComplete`★ —— 它没有真机样本，同类字段已出过"接受被判成拒绝"的事故。

    这条不是洁癖：`isInviteComplete` 的教训写在 `ToolExecutor` 的原注释里
    （「旧判定 `!accepted || npcUpset` 把接受路径误报成拒绝」）。选项 id 是配表事实，不需要样本。
    """
    src = _read(CS / "UI" / "DramaDrillChoice.cs")
    code = re.sub(r"///.*", "", src)          # 去掉文档注释再查（注释里正解释为什么不读它）
    code = re.sub(r"//.*", "", code)
    assert "isDrillComplete" not in code, \
        "DramaDrillChoice 读了未校准的 isDrillComplete —— 应只用选项 id 判定"


# ---------- B. 硬编码的选项 id 必须与游戏配表对齐 ----------


def test_option_ids_match_game_conf():
    """把 C# 里硬编码的 212042/212041 钉到游戏配表上（配表不在则 skip）。

    这是本文件最有价值的一条：两个 magic number 一旦写错（或游戏改表），
    真机上表现为"门永远命不中 → 每次切磋都 120s 超时"，而**日志里看不出是 id 错了**。
    """
    conf = _conf_dir()
    if conf is None:
        pytest.skip("本机没有游戏配表导出，跳过配表一致性检查")

    src = _read(CS / "UI" / "DramaDrillChoice.cs")
    agree = int(re.search(r"AgreeOptionId\s*=\s*(\d+)", src).group(1))
    refuse = int(re.search(r"RefuseOptionId\s*=\s*(\d+)", src).group(1))

    dialogues = _load_conf_json(conf / "DramaDialogue.json")
    hit = [r for r in dialogues if str(r.get("options", "")) in
           (f"{agree}|{refuse}", f"{refuse}|{agree}")]
    assert hit, f"配表里没有任何剧情行的 options 等于 {agree}|{refuse} —— 两个 id 写错了？"

    row = hit[0]
    order = str(row["options"]).split("|")
    assert order[0] == str(agree), (
        f"配表里 {row['id']} 的 options 顺序是 {row['options']}，"
        f"第一位不是 AgreeOptionId={agree}。用户描述是「左边同意、右边不同意」——"
        f"左位 = options 第一位，别把应战和婉拒搞反")
    assert order[1] == str(refuse), f"配表里第二位不是 RefuseOptionId={refuse}"

    # 文案也对一遍：id 对但文案换了（游戏改文案）也该看见
    opts = _load_conf_json(conf / "DramaOptions.json")
    by_id = {str(r.get("id")): str(r.get("text", "")) for r in opts}
    assert "212042" in by_id[str(agree)] or by_id.get(str(agree), "").endswith("212042"), \
        f"选项 {agree} 的 text 不是 drama_option{agree}（配表结构变了？）"
    assert by_id.get(str(refuse), "").endswith(str(refuse)), \
        f"选项 {refuse} 的 text 不是 drama_option{refuse}"

    # C# 里的中文兜底串也要与配表文案一致（text 被 UI 解析成成品句时走这条）
    lt = _load_conf_json(conf / "LocalText.json")
    ch = {str(r.get("key")): str(r.get("ch") or "") for r in lt if isinstance(r, dict)}
    for key, cn in (("drama_option212042", "就让我和你切磋"), ("drama_option212041", "我现在没有空")):
        real = ch.get(key, "")
        assert cn in real, f"配表 {key} 的中文是「{real}」，与 C# 兜底串「{cn}」对不上"
        assert cn in src, f"C# 里缺中文兜底串「{cn}」（text 若被解析成成品句就认不出选项）"


# ---------- C. 渲染层三态 ----------


def _render(data):
    import importlib
    import sys

    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    tr = importlib.import_module(f"{ROOT.name}.tools.text_render")
    return tr.render_world_ai({}, data)


def test_render_spar_three_states_differ():
    """应战 / 婉拒 / 未答复 必须渲染成三句**不同**的话。"""
    yes = _render({"op": "spar", "target": "姜萌", "player": "唐炎", "accepted": True})
    no = _render({"op": "spar", "target": "姜萌", "player": "唐炎", "accepted": False})
    miss = _render({"op": "spar", "target": "姜萌", "player": "唐炎"})
    assert len({yes, no, miss}) == 3, f"三态没有区分开：{yes!r} / {no!r} / {miss!r}"
    assert "应下" in yes and "唐炎" in yes
    assert "婉拒" in no and "唐炎" in no
    assert "婉拒" not in miss and "应下" not in miss, \
        "字段缺失被当成了否定 —— 契约：字段缺失 ≠ 否定，不许替玩家编一个婉拒"


def test_render_spar_pending_is_not_a_verdict():
    """`pending`（AutoConfirm/未定案）不得被渲染成任何一方的立场。"""
    t = _render({"op": "spar", "target": "姜萌", "pending": True})
    assert "婉拒" not in t and "应下" not in t, t
    assert "等待" in t, t


# ---------- D. attack：09-22 也改成跨过战斗收口 ----------


def test_attack_now_crosses_the_battle():
    """`case "attack"` 必须挂起，且**在 `CreateAction` 之前**把登记摘走。

    旧文案"那个动作类没有可等的结局信号，会挂到超时"是**没找对信号**：结局信号在
    战后那一串原生剧情窗上（`DramaAiOption.InitData` 每页都看得见），不在 OnEnd 上。
    而摘走登记必须早于 `CreateAction` —— 攻击的前置窗是 `UIMapBattlePre`，**不是剧情窗**
    （真机栈帧 `IntoBattle ← UIMapBattlePre:<UpdateUI>b__10_3()`），`ClickOption` 那个
    时机在攻击上根本不存在；留着登记就会重演切磋 09-22 那个竞态（OnEnd 抢先收口成
    "没看到结局"，真结果回来时 req_id 已被摘走）。
    """
    src = _cs_tools.tool_src()
    m = re.search(r'case "attack":(.*?)\n\s*case "shuang_xiu":', src, re.S)
    assert m, "找不到 case \"attack\" 分支"
    body = m.group(1)
    assert "DeferNativeUnitAction" in body, "case \"attack\" 没挂起 —— 又退回「发了就回」"
    assert 'BattleOutcome.Arm(' in body and '"attack"' in body, "攻击结局没交给 BattleOutcome"
    assert "MakePendingMarker" in body, "没返回 pending 标记 → 工具会立刻回结果"
    take, create = body.find("UnitActionPending.Take(atk.Pointer"), body.find("wub.CreateAction(atk")
    assert 0 <= take < create, \
        "摘登记晚于 CreateAction —— 进战斗那一刻的 OnEnd 会抢先把它收口（切磋那个竞态的同一形状）"
    assert "BattleOutcome.Abort()" in body and "DramaGate.Cancel(" in body, \
        "CreateAction 抛异常时没回滚槽位 → busy 键被占住，整套动作系统瘫到重启"


def test_engine_refusal_is_not_a_sixty_second_wait():
    """`CreateAction` 的返回码必须被消费：**非 0 = 引擎拒绝、动作根本没创建**。

    09-24 真机两次复现（用户报"调了 attack 没反应"）：NPC「管星贤」精力 1/80 时引擎拒了
    （`[AttackProbe] IsCreate → 返回码 2`，且 `OnCreate` 再没出现），而我们丢了返回值
    ⇒ 工具挂满 60s 静默收口、只回"没看到结局"，模型以为"仗还没打"继续演。
    代码表见 `ToolExecutor` 的 1534-1573：`非 0 = 引擎拒绝且什么都没做`。

    契约三条：
      ① attack 与 spar（同族、同有 `costEnergy`）**都必须看**返回码；
      ② 非 0 要与 catch 走**同一套解武装**（槽位/台账/凭证一个不留，否则 busy 键被占住）；
      ③ 理由只许报**能自证的量**（该单位精力 + 本类动作 `costEnergy`），
         **不许翻译返回码**——码表语义零样本（已知那套是 `UnitActionRelationSet` 的，不能外推），
         猜码表就是造"引擎说 A、我们报 B"的假事实。
    """
    src = _cs_tools.tool_src()

    m = re.search(r'case "attack":(.*?)\n\s*case "shuang_xiu":', src, re.S)
    assert m, '找不到 case "attack" 分支'
    atk = m.group(1)
    assert "int atkRet" in atk and "atkRet != 0" in atk, \
        "attack 又把 CreateAction 的返回码丢了 —— 引擎拒绝会退化成 60s 静默收口"
    assert atk.count("BattleOutcome.Abort()") >= 2 and atk.count("DramaGate.Cancel(") >= 2, \
        "引擎拒绝那条路没解武装（只写在 catch 里了？）→ 槽位/台账留在飞"
    assert 'Refused("发起攻击", "发起战斗", wub, atk, atkRet)' in atk, \
        "拒绝时没给出理由 → 模型只能瞎猜（且文案要走共享的 Refused，别各写一份）"

    ms = re.search(r'case "spar":(.*?)\n\s*case "attack":', src, re.S)
    assert ms, '找不到 case "spar" 分支'
    spar = ms.group(1)
    assert "int drillRet" in spar and "drillRet != 0" in spar, \
        "spar 没看返回码（同族同罪：精力不足同样会被引擎拒）"
    assert spar.count("DramaGate.Cancel(") >= 2, "spar 拒绝那条路没解武装"
    assert 'Refused("发起切磋", "发起切磋", wub, drill, drillRet)' in spar, \
        "spar 拒绝时没给出理由（或没按切磋措辞 —— 战斗/切磋要各说各的）"

    why = re.search(r" static string NativeRefuseWhy\((.*?)\n        \}", src, re.S)
    assert why, "找不到 NativeRefuseWhy"
    body = why.group(1)
    assert "costEnergy" in body and '"energy"' not in body and ".energy" in body, \
        "理由没用引擎自己的两个数（精力 / costEnergy）"
    assert "精力不足" in body and "需要消耗精力" in body, \
        "精力不足这条（游戏说明原文 role_attribute_desc_energy）没写进理由"
    assert "返回码" not in body and "code" not in body.lower(), \
        "理由里出现了码表解读 —— 码表语义零样本，不许猜"

    # 只禁"**调用**"（`AttackProbe.xxx(...)`）；注释里保留 `[AttackProbe]` 这个名字是**证据出处**
    # （真机样本来自哪次打点），删掉反而让人查不到那条结论是怎么来的。
    assert "AttackProbe." not in src, "攻击探针已定叉退役，别在正式路径里留打点调用"
    assert "AttackProbe" not in _read(CS / "app" / "ModMain.cs"), "探针挂载没撤"


def test_no_createaction_site_discards_the_engine_code():
    """★ 全工具面不变量 ★ `CreateAction` 的返回码**一个调用点都不许丢**。

    `CreateAction(action,isTip)` 的语义是"非 0 = 引擎拒绝、什么都没做"（裁决码表在
    `ToolExecutor` 1534-1573，机器码级实证）。丢了它，三种伪造的结局就会出现：
      · **谎报成功**（偷取/传送/召唤/前往：直接回 `Ok(moved:true)` 之类，模型据此认定"事办成了"）；
      · **静默挂起**（邀约/传功/讨要/攻击/切磋：挂到 DramaGate 120s 或 BattleOutcome 60s，
        最后只回"玩家长时间未响应/没看到结局"，把"办不成"说成"玩家没理你"）；
      · 或者让 NPC 照着没发生的事继续演。

    09-24 用户两次撞上攻击那一版（引擎拒绝码 2 = 精力不足）。这里把不变量钉死：
    每个调用点必须形如 `<x> = <actor>.CreateAction(...)` 或 `return <actor>.IsCreateAction(...)`。
    注释行不计（`//`/`///`，里面有码表与解释）。
    """
    src = _cs_tools.tool_src()
    bad = []
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if s.startswith("//"):
            continue
        if "CreateAction(" not in s:
            continue
        head = s.split("CreateAction(", 1)[0]
        if "=" in head or "return" in head:
            continue
        bad.append((i, s))
    assert not bad, (
        "这些 CreateAction 调用点丢了引擎裁决码（拒绝会被当成成功或静默挂起）："
        + "；".join(f"L{n}: {t}" for n, t in bad)
    )
    # 共享文案只许一处：别让每个动作各写一份"精力不足"（失败帧直接进模型上下文）
    assert src.count(" string Refused(") == 1, "拒绝文案助手应只有一份"
    assert src.count("NativeRefuseWhy(") >= 8, "拒绝理由未被各动作共用（少于 8 处调用）"


def test_attack_close_criteria_are_config_ids():
    """攻击的收口判据只许是**配表确定事实**：battleExit 选项位 + 处置页页号。

    不许退回去猜文本（`RoleLogLocal` 是带 condition 与占位的语句池），也不许押在
    `BattleEnd`/OnEnd 这类"位置未验"的信号上。切磋那三条规则逐字保留 —— 它的尾页
    用的是 `100002「离开」`（`function=0`，**不带 battleExit**），所以这两条判据天然
    吃不到切磋；反过来若有人把 battleExit 拿去改切磋，就是拿未校准的东西换已校准的。
    """
    so = _read(CS / "drama" / "BattleOutcome.cs")
    assert "100004" in so and "100005" in so, "battleExit 通用位判据没了"
    assert "20403" in so and "20404" in so, "处置页页号判据没了"
    assert '_op == "attack"' in so, "两条新判据没被关在攻击这一支里（会串到切磋）"
    # 09-23：内容在段里（单流）—— 硬收口规则改问区间行数 `have`，规则本身逐字保留
    assert "_battleEndedHard && have > 0" in so, "切磋的硬收口规则被改掉了"


def test_attack_render_never_claims_a_verdict():
    """攻击的渲染：没结局就明写未知；收在处置页必须写"生死未定"；归属按 NPC 视角。

    ★视角这一条是 09-22 扳正的真 bug★：旧实现把**玩家**那句渲染成「你说：…」，
    于是赢了的 NPC 以为是自己认的输。读这段的是发起攻击的那个 NPC（`Agent.id` = 它）。
    """
    bare = _render({"op": "attack", "target": "郑南", "player": "唐炎"})
    assert "别据此推断" in bare and "婉拒" not in bare and "赢了" not in bare, bare
    lost = _render({"op": "attack", "target": "郑南", "player": "唐炎", "battle_seen": True,
                    "verdict_page": True,
                    "lines": [{"who": "player", "name": "唐炎", "text": "道友，你简直不堪一击！"},
                              {"who": "npc", "name": "郑南", "text": "还请在收下后饶我一条性命…"}]})
    assert "生死未定" in lost, lost
    assert "唐炎说：道友，你简直不堪一击！" in lost, f"玩家的话被安到 NPC 头上了：{lost}"
    assert "你说：还请在收下后饶我一条性命…" in lost, f"NPC 自己的话没写成「你」：{lost}"
    fled = _render({"op": "attack", "target": "郑南", "player": "唐炎", "battle_seen": True,
                    "battle_finished": True,
                    "lines": [{"who": "player", "name": "唐炎", "text": "嘿嘿！我走喽！有缘再会！"}]})
    assert "唐炎说：嘿嘿！我走喽！" in fled and "你说" not in fled, fled
