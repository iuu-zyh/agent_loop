# -*- coding: utf-8 -*-
"""同格人物进 L1 上下文的契约（11-11）

用户原话：「有玩家反映 npc 都不知道同一格地区有谁，这可以新加一下，当前 npc 自身的简讯
是包括自己的坐标的是吧，但是没有包含同格是否谁，可以新加一段，然后查同格有谁，然后就是
按照好感度和仇恨度默认列出前六个，每个都是前六个」

随后澄清的排序口径：
「按绝对值排序，好感度高的取前六，好感度低的也取前六，如果人很多（在城镇里的话那就只显示
12 个，然后括号还有多少人就行，同时显示的每个人需要括号标出好感度」

## 本文件钉住

  A. C# 侧：按 **NPC 自己**的坐标扫（不是玩家那格）—— 两者常不同
  B. C# 侧：**两个方向各取 6**，最多 12 人，超出只报人数
  C. C# 侧：**玩家必须被排除**（他在「玩家」段单独有一份）
  D. C# 侧：没人时**一个键都不写**（载荷与改动前逐字节相同）
  E. Python 侧：并进「所在」段，带关系与好感；没有时本段一字不变
  F. 性能：必须优先走快查 API，全量扫描只作兜底

⚠ 诚实边界：`UnitSnapshot` / `GameContext` 依赖 Unity/IL2CPP 类型，**WSL 里跑不起来**，
   所以这里做的是**源码级**判据（正则/子串），不是真实执行。
   真实行为只能上机看 —— 但"口径写错"（比如忘了只取 6、忘了排除玩家）是源码级的，
   这份文件正是防那个。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "csharp" / "game" / "UnitSnapshot.cs"
CTX = ROOT / "csharp" / "game" / "GameContext.cs"
REL = ROOT / "csharp" / "game" / "RelationNetwork.cs"
PROMPT = ROOT / "agent" / "system_prompt.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _brace_body(text: str, at: int) -> str:
    i = text.index("{", at)
    depth, k = 0, i
    while k < len(text):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
        k += 1
    raise AssertionError("大括号不配平")


def _method(code: str, sig: str) -> str:
    at = code.find(sig)
    assert at >= 0, f"找不到 {sig}"
    return _brace_body(code, at)


# ---------------------------------------------------------------- A 用 NPC 自己的坐标

def test_scans_at_npc_own_coordinates():
    """A：必须按 **NPC 自己** 的坐标扫，不能用 `CollectSameGridUnits`（那个写死玩家坐标）。

    两者常不同格：NPC 在城里逛街、玩家在城外，此时"玩家那格有谁"与"他眼前有谁"完全两回事。
    """
    code = _code(_read(SNAP))
    body = _method(code, "internal static string SameGridText(")
    assert "CollectSameGridAt(" in body, "没有走「任意坐标」那条收集链（会拿到玩家那格的人）"
    assert "CollectSameGridUnits(" not in body, \
        "用了 CollectSameGridUnits —— 它内部写死 g.world.playerUnit 的坐标，答案会是玩家那一格"
    # 坐标必须取自 self
    assert "self.data.unitData.pointX" in body and "self.data.unitData.pointY" in body, \
        "坐标没有取自传入的 self"


def test_collect_at_accepts_arbitrary_coords():
    """A：`CollectSameGridAt` 必须真的按传入坐标查，且优先快查。"""
    code = _code(_read(REL))
    body = _method(code, "public static int CollectSameGridAt(")
    assert "ConsumePointUnits(" in body, "快查路径不见了"
    assert "ScanAllUnits(" in body, "全量兜底不见了（快查失败就一个人都收不到）"
    # 参数名必须是 px/py（不能用实例里的玩家坐标）
    head = _read(REL)[_read(REL).index("public static int CollectSameGridAt("):]
    head = head[: head.index("{")]
    assert "int px" in head and "int py" in head, "签名里没有任意坐标参数"


# ---------------------------------------------------------------- B 两个方向各取 6

def test_top_six_per_side_capped_at_twelve():
    """B：**好感最高 6 + 好感最低 6**，最多 12 人（用户 11-11 明确口径）。"""
    code = _code(_read(SNAP))
    body = _method(code, "internal static string SameGridText(")
    assert "TopPerSide = 6" in body, "每侧取 6 这个口径没写死在代码里"
    assert "TopPerSide * 2" in body, "没有 12 人上限（只有一侧的 6）"

    # 先从头取（最亲）、再从尾取（最恨）—— 顺序与方向都要对
    i_head = body.find("for (int i = 0; i < all.Count && byName.Count < TopPerSide")
    i_tail = body.find("for (int i = all.Count - 1; i >= 0 && byName.Count < TopPerSide * 2")
    assert i_head > 0 and i_tail > 0, "两个方向的取数循环不见了"
    assert i_head < i_tail, "应当先从最亲那头取，再从最恨那头取"


def test_rest_count_reported():
    """B：超出 12 人时要报"还有人"，不能默默截断（城镇里人多是常态）。"""
    code = _code(_read(SNAP))
    body = _method(code, "internal static string SameGridText(")
    assert "rest = all.Count - picked.Count" in body, "没有算未展示人数"
    assert '"，另" + rest + "人"' in body, "没有把未展示人数写进文本"


def test_each_person_shows_intim():
    """B：每个人都要带好感度（用户明确要求「括号标出好感度」）。"""
    code = _code(_read(SNAP))
    body = _method(code, "internal static string SameGridText(")
    assert '"，好感" + picked[i].Intim' in body, "条目里没有好感度"


# ---------------------------------------------------------------- C 排除玩家

def test_player_excluded_from_same_grid():
    """C：玩家不许出现在同格列表里 —— 他在「玩家」段单独有一份。

    这条靠 `RelationNetwork` 收集链里的 `TryAdd`（内部 `IsPlayerUnit`）保证，
    本测试钉住"用的确实是那条链"，并顺带守住 `IsPlayerUnit` 判据别被人换掉。
    """
    rel = _code(_read(REL))
    # TryAdd 里必须用 IsPlayerUnit（IL2CPP 下 ReferenceEquals 会静默失效）
    tryadd_at = rel.find("private static void TryAdd(")
    assert tryadd_at > 0, "TryAdd 不见了"
    tryadd = _brace_body(rel, tryadd_at)
    assert "IsPlayerUnit(" in tryadd, \
        "TryAdd 没有排除玩家（旧版用 ReferenceEquals，IL2CPP 下对列表元素不成立，玩家会被收进来）"

    # 收集链必须经 TryAdd
    body = _method(_code(_read(REL)), "public static int CollectSameGridAt(")
    assert "ConsumePointUnits(" in body or "ScanAllUnits(" in body


# ---------------------------------------------------------------- D 没人时不写键

def test_no_key_written_when_nobody_around():
    """D：身边没人时 `same_grid_units`/`same_grid_rest` **一个都不写**。

    与 `others`（群聊同席）同一套纪律：单聊且无旁人时，L1 载荷必须与改动前逐字节相同 ——
    否则每次改这里都会把全量回归的基线全冲掉。
    """
    code = _code(_read(CTX))
    at = code.find("if (sgRaw.Count > 0)")
    assert at > 0, "没有按「有同格者才写」的门控"
    gated = _brace_body(code, at)
    assert 'raw["same_grid_units"] = sgRaw' in gated, "same_grid_units 没在门控里"
    assert 'raw["same_grid_rest"] = sgRest' in gated, "same_grid_rest 没在门控里"
    # 门控外不许再有无条件赋值
    outside = code[:at] + code[at + len(gated):]
    assert 'raw["same_grid_units"] =' not in outside, "门控外还有一处无条件赋值"


# ---------------------------------------------------------------- F 性能：必须真的走快查

def test_collect_at_triggers_the_quick_mode_probe():
    """F：`CollectSameGridAt` **必须自己**触发首查对照 —— 11-20 修的 bug。

    ★ 这条为什么必须存在 ★
    文件头早就写了「F. 性能：必须优先走快查 API，全量扫描只作兜底」，
    但**从来没有对应的测试**（下面那条 `test_collect_at_accepts_arbitrary_coords`
    只检查"两个分支都在"，而分支在 ≠ 会走）。

    真实后果（11-20 那份 Player.log 铁证）：
      · 16 次 `[MAIL-PROBE] 同格段`（= 16 次 get_context），**0 次** `同格首查对照`；
      · `_sameGridQuickMode` 是 int、默认 0，而它的赋值只在 `ProbeSameGridOnce()` 里；
      · 那个函数的**唯一调用点**在 `CollectSameGridUnits()`（"玩家那一格有谁"，
        只有自主交互 / 通讯录走），`get_context` 这条链根本不经过它；
      · 于是 `CollectSameGridAt` 守着两个永远不成立的 `if`，**每次都全量扫 1556 个单位**
        （本文件上方注释：单次约 100ms）。快查实现了，"启用快查"那一步没接上。

    这条断言的就是那一句：本方法自己也要问一次"该走哪条路"。
    """
    code = _code(_read(REL))
    body = _method(code, "public static int CollectSameGridAt(")
    assert "ProbeSameGridOnce(" in body, (
        "CollectSameGridAt 没有触发首查对照 —— `_sameGridQuickMode` 永远停在 0，"
        "每一轮 get_context 都会全量扫全图（~100ms/次）")
    # 必须早于快查分支（晚了这一帧还是走全量）
    assert body.index("ProbeSameGridOnce(") < body.index("_sameGridQuickMode == 1"), \
        "首查对照必须早于快查判断，否则本次调用仍然全量"
    # 只探一次：靠 _sameGridProbeDone 把关，不许每次调用都跑对照（那本身就要全量扫一遍）
    assert "_sameGridProbeDone" in body, "没有用 `_sameGridProbeDone` 把关 —— 每次调用都会做一次全量对照"


def test_probe_is_shared_not_duplicated():
    """F：两条入口共用**同一个**探测结论，不许各探各的。

    各探一遍的后果不是"更慢一点"，而是**两个入口可能得出不同结论**（不同格的
    API 行为未必一致）⇒ 一个走快查一个走全量，而屏幕上分不出是哪条路漏了人。
    """
    code = _code(_read(REL))
    assert code.count("if (!_sameGridProbeDone) ProbeSameGridOnce(") == 2, \
        "只有一个入口触发首查对照（两条入口都要，且都走同一个 _sameGridProbeDone 门闸）"
    assert code.count("private static void ProbeSameGridOnce(") == 1, \
        "ProbeSameGridOnce 被复制了一份 —— 两套结论会各自漂移"


def test_probe_uses_caller_coords_not_player():
    """F：对照必须用**传入坐标**，不许回落到玩家坐标。

    NPC 与玩家常常不同格（传音/远程对话）。拿玩家的格去验证 NPC 那格，
    等于用另一件事的结论给这一件事背书。
    """
    code = _code(_read(REL))
    body = _method(code, "private static void ProbeSameGridOnce(")
    assert "playerUnit" not in body, \
        "对照里读了玩家单位 —— 必须只用传入的 px,py（NPC 与玩家常不同格）"
    for needle in ("ScanAllUnits(null, null, px, py", "SafePointUnits(false, px, py)",
                   "SafePointUnits(true, px, py)"):
        assert needle in body, f"对照没有在同一坐标上比对：缺 `{needle}`"


# ---------------------------------------------------------------- E Python 侧

def test_python_renders_into_where_segment():
    """E：Python 必须把它并进「所在」段（本段是"还有谁在场"的唯一出口），且带关系+好感。"""
    src = _read(PROMPT)
    at = src.find("def format_where_segment(")
    assert at > 0
    # Python 不能用 `_brace_body`：f-string 里的 `{` 会把大括号配平打断。
    # 按缩进切到下一个同级 def 为止。
    nxt = src.find("\n    def ", at + 1)
    nxt2 = src.find("\n    @classmethod", at + 1)
    ends = [x for x in (nxt, nxt2) if x > 0]
    body = src[at: min(ends) if ends else len(src)]
    assert 'same_grid_units' in body, "「所在」段没有读同格数据"
    assert 'same_grid_rest' in body, "没有读未展示人数"
    assert "同一格" in body, "成文里没有「同一格」字样"
    assert "好感" in body, "条目里没有好感度"


def test_python_empty_case_is_byte_identical():
    """E：没有同格数据时，「所在」段必须与改动前**一字不变**（恒非空的撤回语义）。

    若本段只在"有人"时才有值，则"有→无"方向段会变空 → 不发消息 →
    转录里那条旧状态永远留着（历史症状：群聊→单聊时 NPC 还以为李四站在旁边）。
    """
    from agent_loop.agent.system_prompt import SystemPrompt as SP
    text = SP.format_where_segment({"point": {"x": 5, "y": 6}}, None, [], {})
    assert text.endswith("。"), text
    assert "此刻并无他人在场，只有玩家与你相对" in text, text
    assert "同一格" not in text, text
