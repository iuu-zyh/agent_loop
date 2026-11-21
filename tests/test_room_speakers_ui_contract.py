"""群聊发言人数上限的**面板侧**契约（09-18）：预制件补丁 / C# 三处 / 缺省值必须一致。

为什么要单钉一条：这一层的漂移是**静默**的 —— 预制件里没有那一行，玩家就只是"看不见这个设置"，
没有任何报错；缺省值写错，面板会显示一个后端根本不用的数（比如显示 3、实际生效 5）。
四处必须同名同位：
  ① `scripts/dev/prefab_patch_config_groups.py`：新增行 + 新增组标题 + 在 ORDER 里的位置
  ② `AbConfigPanel`：按同一路径找节点（老 AB 缺行时静默跳过，不能拖垮整个面板）
  ③ `ConfigUiBuilder`：代码版面板同款行（老 AB 的运行期兜底构建路径）
  ④ `ConfigPresenter`：回填缺省值 == `config_loader.DEFAULT_CONFIG["room"]`、
     留空不提交、填 <1 本地拦下、提交形状是 `{"room": {"max_speakers_per_round": N}}`
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from agent_loop.config.config_loader import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parent.parent
PATCH = ROOT / "scripts/dev/prefab_patch_config_groups.py"
AB_PANEL = ROOT / "csharp/UI/AbConfigPanel.cs"
BUILDER = ROOT / "csharp/UI/ConfigUiBuilder.cs"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"
REFS = ROOT / "csharp/UI/ConfigPanelRefs.cs"


def _patch_table(name: str):
    """从补丁脚本里取一张顶层常量表（NEW_ROWS / HEADERS / ORDER）。

    **不用 import**：那个脚本在 import 期会把 sys.stdout 换成 TextIOWrapper（Windows 控制台
    中文输出用），在 pytest 的捕获环境下会把 stdout 关掉，整份用例集体报
    `ValueError: I/O operation on closed file`。ast 解析源码即可，还顺手钉住了"表是字面量"。
    """
    tree = ast.parse(PATCH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"补丁脚本里找不到常量表 {name}")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- ① 预制件补丁

def test_patch_script_knows_the_row_and_its_group():
    rows = {n: (k, t, p, v) for n, k, t, p, v in _patch_table("NEW_ROWS")}
    assert "RoomSpeakersRow" in rows, "补丁脚本里没有这一行 —— 重打 AB 也补不出来"
    kind, label, ph, rval = rows["RoomSpeakersRow"]
    assert kind == "input", f"应当是输入行，实为 {kind}"
    assert ph == "3", f"占位符必须是默认值 3，实为 {ph!r}"
    assert rval is None, "真整数行不该改校验类型（PortRow 克隆源本来就是整数框）"
    assert "群聊" in label and "即时生效" in label, f"标签没写清是什么/是否即时生效：{label}"
    headers = dict(_patch_table("HEADERS"))
    assert headers.get("GroupHeader_Room") == "群聊", "缺【群聊】组标题"

    names = [n for n, _ in _patch_table("ORDER")]
    i_room, i_row = names.index("GroupHeader_Room"), names.index("RoomSpeakersRow")
    assert i_row == i_room + 1, "行必须紧跟在【群聊】标题下面"
    assert names.index("GroupHeader_Memory") < i_room < names.index("GroupHeader_Advanced"), \
        "【群聊】组应夹在「记忆与压缩」与「高级」之间（玩法类挨着，技术类在后）"
    print("✓ 补丁脚本：行 + 组标题 + 位置都对")


# ---------------------------------------------------------------- ②③ C# 节点路径

def test_ab_panel_finds_the_row_and_treats_it_as_optional():
    src = _read(AB_PANEL)
    assert 'FindComp<InputField>(root, Llm + "RoomSpeakersRow/Input")' in src, \
        "AbConfigPanel 没按约定路径找这一行（预制件补了也读不到）"
    i = src.index("private static System.Collections.Generic.List<string> MissingCritical")
    body = src[i: src.index("\n        }", i)]
    assert "RoomSpeakers" not in body, "这一行不能进 MissingCritical（老 AB 没有它时不该打不开面板）"


def test_code_built_panel_has_the_same_row():
    src = _read(BUILDER)
    assert 'MakeSectionHeader(llmContentRt, "GroupHeader_Room", "群聊")' in src, "代码版面板没有【群聊】组"
    m = re.search(r'refs\.RoomSpeakersInput = MakeFormRow\(llmContentRt, "RoomSpeakersRow", "([^"]+)", "(\d+)"'
                  r', InputField\.ContentType\.IntegerNumber\)', src)
    assert m, "代码版面板没有同款整数行（写法变了？）"
    assert "群聊" in m.group(1) and "即时生效" in m.group(1), f"代码版标签不对：{m.group(1)}"
    assert int(m.group(2)) == DEFAULT_CONFIG["room"]["max_speakers_per_round"], \
        "代码版面板的占位默认值与 config_loader 不一致"
    assert "public InputField RoomSpeakersInput;" in _read(REFS), "ConfigPanelRefs 没有这一行的字段"


# ---------------------------------------------------------------- ④ 回填 / 提交

def test_panel_fallback_equals_python_default():
    pres = _read(PRESENTER)
    m = re.search(r'SetText\(_refs\.RoomSpeakersInput, data\["room"\]\?\["max_speakers_per_round"\]'
                  r'\?\.ToString\(\) \?\? "(\d+)"\)', pres)
    assert m, "找不到回填那一行（键名或写法变了？）"
    assert int(m.group(1)) == DEFAULT_CONFIG["room"]["max_speakers_per_round"], \
        f"面板缺省 {m.group(1)} != config_loader 的 {DEFAULT_CONFIG['room']['max_speakers_per_round']}"


def test_blank_means_do_not_submit_and_below_one_is_refused():
    pres = _read(PRESENTER)
    i = pres.index("if (_refs.RoomSpeakersInput != null")
    seg = pres[i: pres.index("bool hasPortraits", i)]
    assert "submitRoomSpeakers = true;" in seg, "填了值却不提交"
    assert "roomSpeakers < 1" in seg and "StatusWarn" in seg, "小于 1 没有本地拦下（后端只会夹到 1，用户以为填了 0）"
    # 块构建：只在真给了新值时提交（留空 = 不改这一项）
    assert re.search(r'if \(submitRoomSpeakers\) cfg\["room"\] = new JObject \{ \["max_speakers_per_round"\]'
                     r' = roomSpeakers \};', pres), "提交形状不对（应写进 config 的 room 块）"


def test_tooltip_has_no_markdown_and_explains_the_semantics():
    """UGUI 的 Text 会把 ** 原样画出来（本仓库踩过），说明里也不许把"名额"说成"每轮人数"。"""
    pres = _read(PRESENTER)
    i = pres.index("RegisterRow(_refs?.RoomSpeakersInput,")
    tip = pres[i: pres.index("RegisterRow(_refs?.InitiativeEnabledToggle,", i)]
    assert "**" not in tip, "悬停说明里混进了 markdown 粗体（UGUI 会画出星号）"
    assert "弃权" in tip and "不占名额" in tip, "没写清弃权/出错不占名额（玩家会以为点名人数就是上限）"
    assert "即时生效" in tip, "没写清改完什么时候生效"
    print("✓ 面板侧：四处同名同位、缺省值与后端一致、留空不提交、<1 拦下")


if __name__ == "__main__":
    test_patch_script_knows_the_row_and_its_group()
    test_ab_panel_finds_the_row_and_treats_it_as_optional()
    test_code_built_panel_has_the_same_row()
    test_panel_fallback_equals_python_default()
    test_blank_means_do_not_submit_and_below_one_is_refused()
    test_tooltip_has_no_markdown_and_explains_the_semantics()
    print("\n群聊发言人数上限的面板契约全部通过")
