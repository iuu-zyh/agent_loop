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
用户另拍板"选项一落定就回，不等战斗"，`OnEnd` 在同意路径要等整场打完，满足不了。

本文件锁四件事：
  A. C# 的 `case "spar"` 走挂起（不是立即 Ok）+ 兜底钩子在
  B. **硬编码的选项 id 与游戏配表逐字对齐**（配表在才查，不在则 skip）
  C. 渲染层三态各自不同、且字段缺失不替玩家编"婉拒"
  D. `attack` 不受影响（`UnitActionRoleAttack` 连 OnEnd 都没有，接不了这条链）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

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
    src = _read(CS / "ToolExecutor.cs")
    m = re.search(r'case "spar":(.*?)\n\s*case "attack":', src, re.S)
    assert m, "ToolExecutor.cs 里找不到 case \"spar\" 分支"
    body = m.group(1)
    assert "DeferNativeUnitAction" in body, "case \"spar\" 不再走挂起（DeferNativeUnitAction）"
    assert "DrillChoiceGate.Arm" in body, "case \"spar\" 没有武装选项门（DrillChoiceGate.Arm）"
    assert "MakePendingMarker" in body, "case \"spar\" 没有返回 pending 标记 → 工具会立刻回结果"
    # 挂起之前不许出现"直接 Ok 旧形态"
    assert not re.search(r"CreateAction\(drill,\s*true\);\s*\n\s*return Ok", body), \
        "case \"spar\" 又退回成 CreateAction 后立即 Ok"


def test_drill_onend_hook_registered():
    """`UnitActionRoleDrill.OnEnd` 兜底钩子必须在（玩家没选就关窗时用它收口）。"""
    src = _read(CS / "UnitActionHooks.cs")
    assert re.search(r'HarmonyPatch\(typeof\(UnitActionRoleDrill\),\s*"OnEnd"\)', src), \
        "UnitActionHooks.cs 缺 UnitActionRoleDrill 的 OnEnd postfix（未选就关窗会白等 120s）"
    # 铁律：postfix 必须吞异常，否则顺着游戏自身动作结束链往上炸
    m = re.search(r"class UnitActionDrillHook(.*?)\n    \}", src, re.S)
    assert m and "try {" in m.group(1) and "catch" in m.group(1), \
        "UnitActionDrillHook 的 Postfix 没有 try/catch（会让游戏动作结束不掉）"


def test_choice_gate_hooks_click_option():
    """选项门必须挂在 `UIDramaBase.ClickOption` 上，且未武装时零打扰。

    反编依据：`ClickOption` 只声明在 `UIDramaBase`，子类无覆写 ⇒ 挂基类覆盖全部剧情窗变体。
    """
    p = CS / "UI" / "DramaDrillChoice.cs"
    assert p.is_file(), "缺 UI/DramaDrillChoice.cs"
    src = _read(p)
    assert re.search(r'HarmonyPatch\(typeof\(UIDramaBase\),\s*"ClickOption"\)', src), \
        "没有挂 UIDramaBase.ClickOption"
    assert "if (slot < 0) return;" in src, \
        "Observe 开头没有\"未武装直接返回\"—— 玩家自己开的剧情窗会被误判成选边"
    m = re.search(r"private static void Postfix\(UIDramaBase __instance, ConfDramaOptionsItem __0\)(.*?)\n        \}", src, re.S)
    assert m and "try {" in m.group(1) and "catch" in m.group(1), \
        "ClickOption 的 Postfix 没有 try/catch（会顺着游戏自身点击链往上炸）"


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
    yes = _render({"op": "spar", "target": "姜萌", "accepted": True})
    no = _render({"op": "spar", "target": "姜萌", "accepted": False})
    miss = _render({"op": "spar", "target": "姜萌"})
    assert len({yes, no, miss}) == 3, f"三态没有区分开：{yes!r} / {no!r} / {miss!r}"
    assert "应下" in yes and "姜萌" in yes
    assert "婉拒" in no and "姜萌" in no
    assert "婉拒" not in miss and "应下" not in miss, \
        "字段缺失被当成了否定 —— 契约：字段缺失 ≠ 否定，不许替玩家编一个婉拒"


def test_render_spar_pending_is_not_a_verdict():
    """`pending`（AutoConfirm/未定案）不得被渲染成任何一方的立场。"""
    t = _render({"op": "spar", "target": "姜萌", "pending": True})
    assert "婉拒" not in t and "应下" not in t, t
    assert "等待" in t, t


# ---------- D. attack 不受影响 ----------


def test_attack_still_immediate():
    """攻击保持立即返回：`UnitActionRoleAttack` 没有 OnEnd、也没有落定字段，接不了选择链。"""
    src = _read(CS / "ToolExecutor.cs")
    m = re.search(r'case "attack":(.*?)\n\s*case "shuang_xiu":', src, re.S)
    assert m, "找不到 case \"attack\" 分支"
    body = m.group(1)
    assert "DeferNativeUnitAction" not in body and "MakePendingMarker" not in body, \
        "case \"attack\" 被改成挂起 —— 那个动作类没有可等的结局信号，会挂到超时"
    t = _render({"op": "attack", "target": "姜萌"})
    assert "攻击" in t and "婉拒" not in t and "应下" not in t, t
