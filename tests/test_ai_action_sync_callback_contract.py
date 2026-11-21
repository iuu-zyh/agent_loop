# -*- coding: utf-8 -*-
"""论道/双修「同步回调」契约 —— 防的是**结果被静静丢掉 + 空等 120s**。

## 事故（真机 10-21，Player.log 20:11 / 20:13 两次）

    20:11:47  [AiActionProbe] op=lun_dao npc=江宇文 energy=44 ...
    20:11:47  [TalkOutcome] 开始跟踪 slot=2 op=lun_dao npc=江宇文 uid=rIsCQs 锚点=246.0
    20:11:47  [AiAction] lun_dao 完成回调 ok=False slot=2      ← 紧挨着上一行
    20:11:47  [TalkOutcome] 槽位结束（op=lun_dao）→ 清状态
    20:11:47  [AiAction] lun_dao 已挂起（slot=2 ...）           ← 反而排在「完成回调」之后
    ...
    20:13:47  RPC call_tool 超时（120.0s，等 C# 主线程无应答）→ 工具失败：请求超时

「已挂起」排在「完成回调」**后面** = 回调是在 `ActionStart(...)` 里面**同步**跑完的。

## 机理

`WsClient` 的挂起登记发生在 `ExecuteRequest` **返回之后**：

    var task = _dispatcher.Enqueue(() => ExecuteRequest(method, p));
    JObject response = (JObject)task.Result;          // ← 工具方法在这里才返回
    if (response["__pending__"] == true) { _deferred[reqId] = slot; return; }

而同步回调发生在 `task.Result` **之前**。所以：

  · 那一刻 `DramaGate.Resolve(slot, ...)` 触发的 `OnDramaResolved` 反查
    `_deferred` 里的 req_id **一定查不到** ⇒ 结果被 `if (reqId == null) return;` 丢掉；
  · 但 `Resolve` 已经把 slot 从 `_pending` 摘走 ⇒ 过期 Tick 也不会再管它。
  ⇒ 那一帧 response 永远不发 → Python 干等满 120s 判「请求超时」。
  **这就是"论道静默"的真因，与"玩家拒绝"无关。**

## 引擎为什么会同步回调 false

`WorldUnitAIAction1037::ActionStart` @0x45E2210：
    al = WorldUnitAITool::MovePoint(npc, toUnit);   // 先走到玩家那儿
    if (!al) { onEndCall(false); return; }          // 走不过去 ⇒ **同步** false、界面根本没弹
    ... 绑 onEndCall、开论道 ...                     // 成功路径这里**不回调**

`MovePoint` 的返回值 = `UnitActionMoveNPC.moveComplete`（+0x40），而它由
`moveDis / (grade+5) > Random(0, 100.0f)` 决定：距离远 ⇒ 真的走过去 ⇒
`moveComplete` 保持 false ⇒ `ActionStart` 返回 false。

⇒ 同一个 bool 混了两件事：「引擎没接」与「他还没走到你跟前」。

## 本文件钉三件事

  ① 同步回调**不许**走 `DramaGate.Resolve`（必须先 `Cancel` 并把结果直接 return）；
  ② 同步/异步必须由 `ActionStart` 是否返回过来区分（`startReturned` 标志）；
  ③ 移动闸探针存在、只读、且**在 ActionStart 之前**取坐标（事后取会被瞬移抹平）。
"""
import io
import _cs_tools
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 工具层源码（多文件聚合，类名前缀已抹平）—— 下面直接当**文本**用，不当路径用
TOOL_SRC = _cs_tools.flat_src()

METHOD_HEADER = " JObject StartToUnitAiActionDeferred("


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _strip_comments(text):
    """去掉 // 行注释（保留字符串里的 // 是过度工程：本文件断言的都是标识符）。"""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


def _csharp_body(src, header):
    """按花括号配对取方法体（C# 专用；跨语言复用会静默取错，见 test_framework 的自测）。"""
    at = src.find(header)
    assert at >= 0, "找不到方法签名：%s" % header
    i = src.find("{", at)
    assert i >= 0, "签名后没有花括号：%s" % header
    depth = 0
    for k in range(i, len(src)):
        ch = src[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[at:k + 1]
    raise AssertionError("方法体没闭合：%s" % header)


def _method_code():
    """取方法体并去掉注释行 —— 断言只针对**会执行的代码**，不针对注释里讲的历史。"""
    return _strip_comments(_csharp_body(TOOL_SRC, METHOD_HEADER))


# ---------------------------------------------------------------- 测试框架自测
def test_framework_extracts_the_whole_method_not_a_prefix():
    """自测：配对必须真的走到方法末尾，不能只取一段前缀。

    之所以自测：如果 `_csharp_body` 取短了，下面所有"某标识符不在方法里"
    的断言都会**假绿**（取不到自然就不存在）。
    """
    body = _csharp_body(TOOL_SRC, METHOD_HEADER)
    # 方法末尾的标志：返回挂起标记
    assert "DramaGate.MakePendingMarker(slot)" in body, \
        "取到的方法体不含结尾的 MakePendingMarker —— 配对提前结束了"
    # 方法里应当同时含开头（TryDefer）与结尾（MakePendingMarker）
    assert body.index("DramaGate.TryDefer(") < body.index("DramaGate.MakePendingMarker(slot)")


def test_framework_strip_comments_keeps_code_lines():
    """自测：去注释只删注释行，不能把代码一起吃光。"""
    sample = "        // 这是注释\n        int a = 1;\n"
    cleaned = _strip_comments(sample)
    assert "int a = 1;" in cleaned
    assert "这是注释" not in cleaned


# ---------------------------------------------------------------- ① 同步回调不走挂起
def test_sync_callback_does_not_resolve_into_an_unregistered_slot():
    """★ 核心：判定 `startReturned` 之后，**同步**分支必须先 Cancel 再直接返回。

    反过来的写法（先 Resolve）就是本次事故：结果发给一个还没登记 slot 的会话，
    `OnDramaResolved` 查不到 req_id 直接 return，response 永不发出。
    """
    code = _method_code()
    assert "startReturned" in code, \
        "没有 `startReturned` 标志 —— 无法区分同步/异步回调，同步那条又会退化成「结果被丢掉」"
    assert "if (!startReturned)" in code, \
        "没有对同步回调做分流（`if (!startReturned)` 分支）"

    # 同步分支必须"先 Cancel、后 return"，且**不许**在里面 Resolve
    sync_at = code.index("if (!startReturned)")
    # 同步分支的右花括号：从 sync_at 开始配对
    depth = 0
    started = False
    end = None
    for k in range(sync_at, len(code)):
        if code[k] == "{":
            depth += 1
            started = True
        elif code[k] == "}":
            depth -= 1
            if started and depth == 0:
                end = k
                break
    assert end is not None, "同步分支的花括号没闭合（本测试的锚点需要跟着改）"
    sync_block = code[sync_at:end]

    assert "DramaGate.Cancel(slot)" in sync_block, \
        "同步分支没有 Cancel(slot) —— 票据会留在表里，HasPending 假挂起把后续动作全挡住"
    assert "DramaGate.Resolve(" not in sync_block, (
        "★ 同步分支里出现了 DramaGate.Resolve —— 这正是 10-21「论道静默 120s」的成因："
        "此刻 WsClient 还没登记 _deferred[req_id]，Resolve 发出去没人认领，"
        "而 slot 又已被摘走，过期 Tick 也不会兜底，那一帧 response 永远不发。"
    )
    assert "return;" in sync_block, "同步分支没有提前 return（会继续走挂起返回路径）"

    # 清理动作必须在**这个分支里面**做。
    # ⚠️ 不许写成"整个方法里有没有 Disarm()" —— catch 分支里还有一处，
    #    分开查会被它满足（第一轮变异验证就是这样漏掉 2 条的）。
    assert "TalkOutcome.Disarm()" in sync_block, (
        "同步分支没有撤锚点 —— 动作没起来却留着区间锚点，下一场论道会把"
        "上一场的页读进结果（锚点是 `Time.unscaledTime`，不重置就一直有效）"
    )
    assert "DramaNativeClaim.Release(claimUid)" in sync_block, (
        "同步分支没有释放归因凭证 —— 券会留在 ClaimTTL 内继续误挡玩家的原生剧情窗"
        "（`DramaAiOption` 认券发『AI 对话』按钮的那套）"
    )


def test_sync_flag_is_initialized_false_and_set_after_actionstart():
    """★ 分流标志的生命周期：初值 false、`ActionStart` 之后才置 true。

    这条抓的是**假绿**：上面几条只能证明"分流代码存在"，但如果
    `startReturned` 初值就是 true（或永远没被置 true），那条同步分支就是**死代码** ——
    断言照样全绿，而线上行为退化成原 bug（同步结论走 Resolve ⇒ 被丢掉 ⇒ 空等 120s）。
    变异验证第一次跑就是栽在这里，故补上"可达性"这一条。
    """
    code = _method_code()
    decl = re.search(r"bool\s+startReturned\s*=\s*(true|false)\s*;", code)
    assert decl, "找不到 startReturned 的声明"
    assert decl.group(1) == "false", (
        "`startReturned` 初值是 true —— 同步分支变成死代码，同步回调会退回"
        "「Resolve 到未登记槽位 ⇒ 结果被丢掉 ⇒ 空等 120s」（10-21 原 bug）"
    )
    # 必须在 ActionStart **之后**才置 true（否则同步回调也会被判成异步）
    i_decl = decl.start()
    i_set = code.index("startReturned = true;")
    i_start = code.index("action.ActionStart(")
    assert i_decl < i_start < i_set, (
        "`startReturned = true` 的位置不对：必须落在 action.ActionStart(...) **之后**"
        "（在它之前置 true ⇒ 同步回调被当成异步 ⇒ 退回原 bug）"
    )


def test_async_callback_still_resolves_normally():
    """异步回调（论道界面走完）仍必须走 `DramaGate.Resolve` —— 别为了修同步把正常路砍了。"""
    code = _method_code()
    assert "DramaGate.Resolve(slot, result);" in code, \
        "异步收口不见了 —— 论道完成后补发 response 的正是这一句"
    # 顺序：分流在前，Resolve 在后
    assert code.index("if (!startReturned)") < code.index("DramaGate.Resolve(slot, result);"), \
        "Resolve 排在了同步分流之前 —— 同步回调会先撞上无人认领的 Resolve"


def test_sync_result_is_returned_to_the_model_immediately():
    """同步回调的结论必须**当场**作为工具结果 return（毫秒级），而不是挂起。"""
    code = _method_code()
    assert "syncResult = result;" in code, "同步回调的结论没有存进 syncResult（会被丢弃）"
    assert "if (syncResult != null) return syncResult;" in code, \
        "ActionStart 返回后没有把 syncResult 直接 return —— 模型仍要空等到超时"
    # 必须在 ActionStart 之后、且早于挂起返回
    i_start = code.index("action.ActionStart(")
    i_ret = code.index("if (syncResult != null) return syncResult;")
    i_pending = code.index("DramaGate.MakePendingMarker(slot)")
    assert i_start < i_ret < i_pending, \
        "顺序必须是 ActionStart → 检查 syncResult → 最后才返回挂起标记"


# ---------------------------------------------------------------- ③ 移动闸探针

# ⚠️ 2026-10-23 作废：原先这里钉着 `LogAiActionMoveProbe`（移动闸探针）的 5 条契约。
#    该探针已被**整体删除**（用户要求"摘掉诊断探针"，收尾见
#    `docs/archive/2026-10-22-论道静默失败/修法与清理.md` §6 清理清单）。
#
#    为什么是删而不是继续钉：
#      · 它存在的唯一目的，是观测 `UnitActionMoveNPC::Init` 按距离折算的 `day` 到底多少 ——
#        那个问题已经**定案**（61 格 ⇒ day=15，`Create` 只看 day>0 就扣光 residueDay）；
#      · 定案之后探针每跑一次只是把已知结论再打一行日志，而它读的是动作**内部**字段
#        （`moveDis`/`GetGrade`/`pointX`），属于最容易随游戏更新漂移的那一类反射；
#      · 留着它反而有成本：真机上每次动作都多一次坐标/境界读取 + 一行日志。
#
#    ⇒ 契约随之作废，**不是"测试忘了改"**。若将来又需要观测这条链，请连同本段一起重写，
#      并把新的判据写进 `docs/` 里 —— 不要再凭"日志里看过一次"就钉成护栏
#      （本轮 `test_ok_false_text_mentions_the_real_cause` 就是因为这个原因改过三次）。


def test_move_probe_is_gone():
    """反面钉子（替代上面被作废的 5 条）：探针**不许**被悄悄加回来。

    判据只查**代码行**（`_method_code` 已去注释）：本文件头几段注释里引用了探针名作历史说明，
    查全文会被自己的说明判红 —— 与「断言要对着会执行的东西」同一条纪律。
    """
    code = _method_code()
    for name in ("LogAiActionMoveProbe", "LogAiActionEnergyProbe", "LogGridProbe"):
        assert name + "(" not in code, (
            "诊断探针 `%s` 又出现在执行路径里了 —— 它已于 2026-10-23 按用户要求摘除"
            "（见 docs/archive/2026-10-22-论道静默失败/修法与清理.md §6）。要重新加，请先改那条文档和本断言。"
            % name
        )
