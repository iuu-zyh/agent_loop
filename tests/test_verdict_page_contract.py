"""处置页说明：**跨语言接线契约**（09-24 用户需求，甲案）。

用户需求原文
------------------------------------------------------------------------------------
「攻击中玩家战胜了，npc如果给物品求饶的话也要追加说明……让 npc 不要重复调用，
文本里的道具走系统内置，由玩家考虑是否接受，但是不能影响后续玩家要求其赠送别的道具」；
口径拍板（甲案）：「如果在后续页的话是会将选项也注入的啊，不用担心」⇒ **只认"当前页就是处置页"**。

判据（配表事实，不是文本匹配）
------------------------------------------------------------------------------------
`BattleOutcome.IsVerdictDrama` 白名单：**20403** = NPC 送东西求饶（5 个处置位）/
**20404** = NPC 任凭处置（4 个位）。点「对话」那一刻台账早已被清（处置页当场收口），
所以必须走**只读口** `IsVerdictDramaId`（只看页号）—— `IsVerdictPage` 要求"攻击台账在飞"，
收口后恒 false，用它会让说明永不出现（且静默）。

本文件钉死的五件事
------------------------------------------------------------------------------------
  ① 页号白名单与只读口（改了等于换了判据，配表事实不许动）；
  ② 点对话那一刻用 `IsVerdictDramaId(_pendingDramaId)`（**不是** `IsVerdictPage`）；
  ③ 帧键成对：C# 写 `frame["verdict"]` ↔ Python 读 `msg.get("verdict")`（错一个字母完全静默）；
  ④ Python 包装参数 `verdict_page` 一路接到 `_VERDICT_NOTE`；缺项默认 False（老 C# 兼容）；
  ⑤ 文案必须覆盖用户点名的四要素：别调工具 / 道具走游戏内置 / 由玩家决定 / **只限这一页**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """去注释、保留字符串（照 `test_contact_hotkey_contract.py` 的同款实现：
    工程注释常引用旧实现，直接 in 判断会假绿）。"""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    break
                j += 1
            out.append(src[i:j + 1])
            i = j + 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _body(src: str, signature: str) -> str:
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError(f"方法体未闭合：{signature}")


BATTLE = _strip_comments(_read("csharp/drama/BattleOutcome.cs"))
OPTION = _strip_comments(_read("csharp/UI/DramaAiOption.cs"))
WS = _strip_comments(_read("csharp/bridge/WsClient.cs"))
CHAN = _strip_comments(_read("transport/ws_channel.py"))
INIT = _read("agent/initiative.py")          # 文案在注释里也说明用途，故不去注释


# ---------------------------------------------------------------------------
# ① 判据本体：页号白名单 + 只读口
# ---------------------------------------------------------------------------


def test_verdict_drama_ids_are_config_facts():
    """20403/20404 是配表事实（处置页族），不是措辞匹配 —— 不许改、不许抄第二份。"""
    assert "dramaId == 20403 || dramaId == 20404" in BATTLE, "处置页页号白名单被改了"
    assert "public static bool IsVerdictDramaId(int dramaId)" in BATTLE, \
        "只看页号的只读口不见了（点对话那一刻要用它，见文件头）"


def test_click_uses_page_id_reader_not_live_ledger():
    """★核心★ 点「对话」那一刻必须用 `IsVerdictDramaId`（只看页号）。

    用 `IsVerdictPage` 会怎样：它要求"攻击台账在飞"，而处置页**当场收口**会把台账清掉
    ⇒ 那一刻恒 false ⇒ 说明**永不出现，且没有任何报错**（正是本工程最怕的失败形态）。
    """
    trigger = _body(OPTION, "private static void Trigger()")
    assert "BattleOutcome.IsVerdictDramaId(_pendingDramaId)" in trigger, \
        "点对话时没有判'当前页是不是处置页'"
    assert "IsVerdictPage" not in trigger, \
        "用了要求台账在飞的 IsVerdictPage —— 处置页收口后恒 false，说明会静默永不出现"
    assert "actionActive, verdictPage" in trigger, "判出来了却没传给 SendGameDrama"
    assert "处置页=" in trigger, "发起日志没带判据结果（真机没法一眼核对）"


def test_click_marks_only_current_page():
    """甲案：判据输入必须是**当前页** `_pendingDramaId`（翻到后页再点不该带说明）。"""
    trigger = _body(OPTION, "private static void Trigger()")
    i = trigger.index("IsVerdictDramaId(")
    assert "_pendingDramaId" in trigger[i:i + 80], \
        "判据不是当前页号（甲案要求只认当前页；后页那些回合里选项/台词本来就会随段增量投进去）"


# ---------------------------------------------------------------------------
# ② 帧键成对（错一个字母 = 完全静默）
# ---------------------------------------------------------------------------


def test_verdict_frame_key_pairs_across_languages():
    assert 'if (verdict) frame["verdict"] = true;' in WS, "C# 没把 verdict 写进帧（或键名变了）"
    # 形参在签名行上（`_body` 只取方法体，故这里查全文）
    # 09-28 扩参：尾部再追加 `string unitId = null`（说话人精确身份）—— 跟到新尾参。
    assert "bool verdict = false, string unitId = null)" in WS, \
        "SendGameDrama 缺 verdict 形参（或 09-28 扩的 unitId 被删）"
    assert 'bool(msg.get("verdict", False))' in CHAN, \
        "Python 没读 verdict 帧键（或键名与 C# 不成对；缺项默认 False = 老 C# 兼容）"
    # 09-28：`npc_unit_id` 追加为**末位**实参（说话人精确身份）—— 三项都要在，顺序错也会静默。
    # 10-16：尾部又扩了 `same_grid`（方位，可选），故这里**不再要求** `npc_unit_id` 是收尾那个
    #   `)`（原写法把"末位"当契约，一扩参就红）。要守的是"三项都还在调用串里"。
    i = CHAN.index('_run_initiative(str(msg.get("npc_id"')
    call = CHAN[i : i + 900]
    assert 'bool(msg.get("action", False)),' in call, "action 没进 _run_initiative"
    assert 'bool(msg.get("verdict", False)),' in call, "verdict 没进 _run_initiative"
    assert 'str(msg.get("npc_unit_id", "") or "")' in call, "npc_unit_id 没进 _run_initiative"


def test_python_param_chain_reaches_note():
    assert "verdict: bool = False" in CHAN, "handle_initiative 缺 verdict 形参"
    assert "verdict_page=verdict" in CHAN, "没把 verdict 透给 format_game_drama_message"
    assert '({"verdict": True} if verdict else {})' in CHAN, "source 审计没记 verdict"
    assert "def format_game_drama_message" in INIT and "verdict_page: bool = False" in INIT, \
        "包装函数缺 verdict_page 形参"
    assert "_VERDICT_NOTE" in INIT, "说明常量不见了"


# ---------------------------------------------------------------------------
# ③ 文案四要素（用户点名）
# ---------------------------------------------------------------------------


def test_note_covers_user_requirements():
    """四个不能少的点：别调工具 / 道具走游戏内置 / 由玩家决定 / **只限这一页**。"""
    note = INIT[INIT.index("_VERDICT_NOTE = ("):]
    note = note[:note.index("\n)") + 2]
    for key, why in (
        ("不要调用任何工具", "没点明'别动手'（这正是用户要治的病）"),
        ("item_acquire", "没点名收下类工具（09-24 真机就是它）"),
        ("游戏内置流程", "没点明'献物/求饶走游戏内置'"),
        ("全由玩家决定", "没点明'处置权在玩家'"),
        ("只限这一页", "没点明'只限这一页'"),
        ("照常照办", "没点明'不影响后续赠送别的道具'（用户点名要的）"),
    ):
        assert key in note, f"_VERDICT_NOTE 少了：{key}（{why}）"


def test_note_is_appended_not_replacing():
    """两条说明并存：`in_action` 与 `verdict_page` 各管各的（来源不同、互不替代）。"""
    i = INIT.index("note = (_IN_ACTION_NOTE if in_action else \"\")")
    line = INIT[i:i + 120]
    assert "_VERDICT_NOTE if verdict_page else" in line, "两条说明不是相加关系（会互相顶掉）"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
