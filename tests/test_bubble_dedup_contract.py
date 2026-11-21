"""气泡终稿去重契约（09-16）：同一段正文不许在屏幕上出现两遍。

**用户报的症状**：截图里两条 NPC 气泡**像素级完全相同**（8 行、每行宽度与居中位置全等、
行距 22px、段间距 44px 一致、整体偏移正好 242px），上下相邻、中间没有任何行。

**根因（两条独立成因，同一个洞）**：终稿正文会两路到达 UI —— `step(text)` 与 `npc_reply`；
拦住后者重复渲染的唯一防线是一句字符串比对：

    if (fullText == _lastRenderText && _curTurn == _lastRenderTurn) return;

它隐含"我记不记得画过 == 屏幕上有没有"。但往屏幕上放正文的有**三条**路：`FinishNpcBubble`、
`ReplaceHistory` 的 `AppendNpcStatic`（回放）、流式 `AppendDelta`。漏登记哪条，那句判据就在
那条之后失效：
  ① 回合已 `turn/end`、`npc_reply` 未到的空档里重开窗 → 回放（按 `history.py` 只回**已闭合**
     回合，所以这次是**包含**本回合的）把正文画了出来，却没登记 → `npc_reply` 再画一遍
     ⇒ 就是用户截图那两条。
  ② 模型在工具返回后复述自己的旁白（账本 `BOlQu6/赵勤.jsonl` 第 1 回合 step3/step4
     逐字节相同的 116 字）→ 流式门建的"重绘泡"没被撤掉。

**修法**：判定规则抽成零 Unity 依赖的 `UI/BubbleCommitPolicy.cs`（四档：Skip / 覆盖流式泡 /
覆盖末行 / 新建），由 C# 单测工程 `csharp/tests/BubbleCommitPolicyTests` 直接跑；真实接线由本
文件锁住 —— 两边合起来才完整：那边测"规则对不对"，这里测"接线有没有被改回去"。

本文件锁：
  A. 三条放正文的路都参与终稿记账（含回放）
  B. `FinishNpcBubble` 走四档策略，"文本相等就 return"的老判据已从 ChatWindow 里消失
  C. 判重命中时必须撤掉流式的重绘泡
  D. `ResetContent` 仍清零记账、且不碰 `_curTurn`
  E. 策略类保持零 Unity 依赖；单测工程链接的是**同一个源文件**而非副本
  F. 判重四条件（含"末行还活着"）写在策略里，不会被顺手放宽
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
WINDOW = CS / "UI" / "ChatWindow.cs"
POLICY = CS / "UI" / "BubbleCommitPolicy.cs"
TESTPROJ = CS / "tests" / "BubbleCommitPolicyTests"
RUNNER = ROOT / "scripts" / "dev" / "run_csharp_tests.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员的正文（含花括号）。签名必须唯一。"""
    assert src.count(signature) == 1, f"签名不唯一（{src.count(signature)} 次）：{signature}"
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
    raise AssertionError("大括号不配对：" + signature)


# ----------------------------------------------------------------------
# A. 三条放正文的路都参与终稿记账
# ----------------------------------------------------------------------

def test_replay_registers_what_it_drew():
    """回放必须登记它画出来的正文 —— 这是窗口 C 那条（用户截图）的唯一修法。

    反向要求也在：若这次回放**不含**本回合（未闭合回合不进投影），末条正文属于上一回合
    ⇒ `_lastRenderTurn` 落在旧回合 ⇒ `npc_reply` 仍会正常成泡（不能把回复吃掉）。

    ★ 11-20 起断言对象改成 `ApplyHistoryItem` ★
    回放被拆成"分帧构建"之后，那个 switch 整块搬进了 `ApplyHistoryItem`（每帧建几条），
    `ReplaceHistory` 只剩分流（同步建完 / 交给帧回调）。登记逻辑若在搬动中丢失，
    症状与改之前一模一样（同一段话画两遍），所以断言跟着搬 —— **只换函数体，
    不放松任何一条**；同时钉住"只有这一份 switch"，防止哪天有人复制一份到别处、
    两条路各自记账。
    """
    src = _read(WINDOW)
    body = _method(src, "private void ApplyHistoryItem(JToken token)")
    for needle in (
        "_lastRenderText = body;",
        "_lastRenderTurn = o[\"turn\"]?.Value<int>() ?? -1;",
        "_lastRenderRow = rm?.Row;",
    ):
        assert needle in body, f"回放未登记终稿记账，缺：{needle}"
    assert "_lastRenderText = shown;" in body, "群聊 peer 分支没登记它画上去的那一串"
    # 构建入口必须**都**经过 `ApplyHistoryItem`，不许再长出第二份 switch
    replay = _method(src, "public void ReplaceHistory(JArray items)")
    assert "ApplyHistoryItem(" in replay, "ReplaceHistory 没有走统一的那条逐条应用"
    assert 'case "peer"' not in replay, \
        "ReplaceHistory 里又长出了一份 switch —— 两条构建路径会各自记账，判重开始看运气"


def test_finish_registers_bookkeeping():
    """stream / npc_reply 两路定稿都要登记（原有行为，防回归）。"""
    body = _method(_read(WINDOW), "public void FinishNpcBubble(string fullText")
    assert "_lastRenderTurn = _curTurn;" in body
    assert "_lastRenderText = fullText;" in body


# ----------------------------------------------------------------------
# B. 四档策略 & 老判据已消失
# ----------------------------------------------------------------------

def test_finish_uses_policy_and_passes_all_inputs():
    src = _read(WINDOW)
    body = _method(src, "public void FinishNpcBubble(string fullText")
    assert "BubbleCommitPolicy.Decide(" in body, "终稿决策必须走 BubbleCommitPolicy"
    # 六个入参都要真的传进去（少传一个就是"判据被悄悄放宽"）
    for needle in (
        "fullText",
        "_lastRenderText",
        "_curTurn",
        "_lastRenderTurn",
        "_deltaBubble != null && _bubbleText != null",
        "_lastRenderRow != null",
    ):
        assert needle in body, f"Decide 少传了入参：{needle}"


def test_finish_has_four_actions():
    body = _method(_read(WINDOW), "public void FinishNpcBubble(string fullText")
    assert "case BubbleCommit.Skip:" in body
    assert "case BubbleCommit.OverwriteStreaming:" in body
    assert "case BubbleCommit.OverwriteLastRow:" in body
    # 只钉到实参前缀：`BeginNpcBubble` 可能继续长出可选参数（2026-09-16 长了 speaker），
    # 钉整串会让"加一个可选参数"变成一次假红。
    assert "BeginNpcBubble(fullText, initiative" in body, "CreateNew 档必须存在（首条正文的路径）"
    # 覆盖档：流式泡 / 末行，两个不同的目标。
    # ★ 只钉**目标 + 载荷**，不钉整表达式：09-17 起右侧包了一层 `BubbleTextCore.Tighten(...)`
    #   （折叠正文空行，见 docs/reference/csharp-iron-laws.md 2026-09-17-气泡观感三件事.md），钉字面量 `_bubbleText.text = fullText;`
    #   会把"给写泡加一层显示层整形"变成一次假红。语义没变：写的是**流式泡那个 Text**、
    #   写的是**终稿全文**。
    stream_case = body.split("case BubbleCommit.OverwriteStreaming:")[1] \
                      .split("case BubbleCommit.")[0]
    assert "_bubbleText.text =" in stream_case, "OverwriteStreaming 必须写流式泡（而不是别的 Text）"
    assert "fullText" in stream_case, "OverwriteStreaming 必须写入终稿全文"
    assert "SetRowText(_lastRenderRow, fullText)" in body, "OverwriteLastRow 必须写末行（不是新建）"


def test_old_string_equality_judgement_is_gone_from_chatwindow():
    """老判据 `fullText == _lastRenderText` 必须**只**存在于策略类里。

    留在 ChatWindow 里意味着有人又加了一条旁路判重 —— 那正是漏判的来源。
    """
    src = _read(WINDOW)
    assert "fullText == _lastRenderText" not in src, "ChatWindow 里又出现了字符串判重（应集中在策略类）"
    assert "fullText == _lastRenderText" in _read(POLICY), "策略类里应当有这条判据"


# ----------------------------------------------------------------------
# C. 判重命中要撤掉重绘泡
# ----------------------------------------------------------------------

def test_skip_destroys_redrawn_streaming_bubble():
    body = _method(_read(WINDOW), "public void FinishNpcBubble(string fullText")
    skip_at = body.index("case BubbleCommit.Skip:")
    nxt = body.index("case BubbleCommit.OverwriteStreaming:")
    skip_block = body[skip_at:nxt]
    assert "DestroyActiveStreamingBubble();" in skip_block, (
        "判重命中时必须撤掉流式重绘泡 —— 否则模型复述同一段旁白会在屏幕上留两遍"
    )
    # 撤的是"当前活跃流式泡"，而不是别的行
    d = _method(_read(WINDOW), "private void DestroyActiveStreamingBubble()")
    assert "DestroyRow(_deltaBubble);" in d


def test_destroy_row_unregisters_row():
    """销毁行必须与行注册表成对维护（`_rows` 与 Content 子节点同序），
    并让指向它的终稿记账失效（否则后续会判到一条不存在的行）。"""
    body = _method(_read(WINDOW), "private void DestroyRow(GameObject row)")
    assert "_rows.RemoveAt(i)" in body
    assert "if (_lastRenderRow == row) _lastRenderRow = null;" in body
    assert "TemplateUtils.DestroySafe(row);" in body


def test_cancel_active_bubble_still_works():
    body = _method(_read(WINDOW), "public void CancelActiveBubble()")
    assert "DestroyActiveStreamingBubble();" in body
    assert "ClearStreamingState();" in body


# ----------------------------------------------------------------------
# D. ResetContent 的语义
# ----------------------------------------------------------------------

def test_reset_content_clears_bookkeeping_but_keeps_cur_turn():
    body = _method(_read(WINDOW), "private void ResetContent()")
    assert "_lastRenderTurn = -1;" in body
    assert "_lastRenderText = null;" in body
    assert "_lastRenderRow = null;" in body
    assert "_curTurn = " not in body, (
        "ResetContent 不能碰 `_curTurn`（它是当前直播回合号，与屏幕内容无关）"
    )


# ----------------------------------------------------------------------
# E. 策略类可单测 + 单测工程链接同一源文件
# ----------------------------------------------------------------------

def test_policy_is_unity_free():
    """零 Unity 依赖是它能被 `dotnet run` 单测的前提。"""
    src = _read(POLICY)
    for banned in ("using UnityEngine", "MonoBehaviour", "GameObject", "UnityEngine."):
        assert banned not in src, f"策略类必须保持零 Unity 依赖，发现了：{banned}"


def test_unit_test_project_links_the_same_source():
    csproj = _read(TESTPROJ / "BubbleCommitPolicyTests.csproj")
    assert r"..\..\UI\BubbleCommitPolicy.cs" in csproj, "单测工程必须链接同一份策略源码"
    assert not (TESTPROJ / "BubbleCommitPolicy.cs").exists(), (
        "不许把策略复制成副本 —— 副本会与真源码漂移，单测就失去意义"
    )


def test_runner_script_exists():
    assert RUNNER.exists(), "缺 scripts/dev/run_csharp_tests.py（单测的跑法入口）"
    txt = _read(RUNNER)
    # 09-16 起 runner **自动发现** `csharp/tests/*/`（加新测试工程不必改脚本），
    # 所以断言发现机制在 + 本工程的 csproj 还在，而不是写死某个工程名
    assert "TESTS_DIR" in txt and 'glob("*/*.csproj")' in txt, "runner 必须自动发现测试工程"
    assert (TESTPROJ / "BubbleCommitPolicyTests.csproj").exists(), "气泡策略的单测工程不能消失"


def test_csproj_is_valid_xml():
    """csproj 注释里不能出现连续两个减号（XML 规范），否则工程整个加载失败：
    `error MSB4025: An XML comment cannot contain '--'`。写命令行参数时极易踩
    （`--quiet` / `--project` 都有），这里用解析器挡一道 —— 已经踩过两次。
    """
    import xml.etree.ElementTree as ET

    for p in (TESTPROJ / "BubbleCommitPolicyTests.csproj", CS / "AgentLoopBridge.csproj"):
        ET.parse(p)   # 解析失败即抛 → 测试变红


def test_mutation_checker_exists():
    """变异验证脚本必须留在仓库里 —— 否则"新增测试都做过变异验证"这句话不可复现。"""
    p = ROOT / "scripts" / "dev" / "mutate_check_bubble_dedup.py"
    assert p.exists(), "缺 scripts/dev/mutate_check_bubble_dedup.py"
    txt = _read(p)
    assert "test_bubble_dedup_contract.py" in txt and "BubbleCommitPolicyTests" in txt


# ----------------------------------------------------------------------
# F. 判重四条件（写在策略里，别被顺手放宽）
# ----------------------------------------------------------------------

def test_policy_skip_conditions():
    body = _method(_read(POLICY), "internal static BubbleCommit Decide(")
    skip_at = body.index("if (!string.IsNullOrEmpty(fullText)")
    cond = body[skip_at : body.index("{", skip_at)]
    assert "curTurn > 0" in cond, "回合号未知时不许判重"
    assert "curTurn == lastRenderTurn" in cond, "判据必须带回合号（跨回合复读要照常成泡）"
    assert "lastRowAlive" in cond, (
        "判重必须确认那条行还在屏幕上 —— 把'画过'当成'屏幕上有'正是本 bug 的根源"
    )
    assert "fullText == lastRenderText" in cond


def test_policy_overwrite_last_row_requires_same_turn_and_alive_row():
    body = _method(_read(POLICY), "internal static BubbleCommit Decide(")
    tail = body[body.index("if (hasStreamingBubble)") :]
    assert "curTurn == lastRenderTurn && lastRowAlive" in tail, (
        "覆盖末行必须限定同回合 + 行存活，否则会改写上一条不相干的泡"
    )


# ----------------------------------------------------------------------
# G. 行注册表 / 记账字段的注释锚点（防有人把语义注释删掉）
# ----------------------------------------------------------------------

def test_field_comment_states_screen_truth_invariant():
    src = _read(WINDOW)
    assert "屏幕真相" in src, "终稿记账的语义（记账 = 屏幕上最后一条正文泡）必须写在字段注释里"
