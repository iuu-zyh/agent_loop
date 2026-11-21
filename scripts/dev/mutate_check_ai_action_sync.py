#!/usr/bin/env python3
"""变异验证 —— 论道/双修「同步回调」静默 120s 的修复（10-21）。

**为什么必须有这个脚本**：这次的 bug 的症状是**什么都不发生**。
没有任何报错、没有任何日志异常、工具"看起来"也发出了 —— 只是模型永远收不到结果，
空等满 120 秒被判超时。它和"修好了"在表面上**完全一样**：
两种情况都是"屏幕上没有论道界面"。

所以验收线只能是：**把修复逐个回退，测试必须变红**。

## 事故（真机 10-21，Player.log 20:11 / 20:13 两次）

    20:11:47  [TalkOutcome] 开始跟踪 slot=2 op=lun_dao npc=江宇文 锚点=246.0
    20:11:47  [AiAction] lun_dao 完成回调 ok=False slot=2      ← 紧挨着上一行
    20:11:47  [TalkOutcome] 槽位结束（op=lun_dao）→ 清状态
    20:11:47  [AiAction] lun_dao 已挂起（slot=2 ...）           ← 反而排在「完成回调」之后
    20:13:47  RPC call_tool 超时（120.0s，等 C# 主线程无应答）→ 工具失败：请求超时

「已挂起」排在「完成回调」**后面** ⇒ 回调是在 `ActionStart(...)` 里**同步**跑完的。

## 机理

`WsClient` 的挂起登记发生在 `ExecuteRequest` 返回**之后**：

    JObject response = (JObject)task.Result;          // ← 工具方法在这里才返回
    if (response["__pending__"] == true) { _deferred[reqId] = slot; return; }

同步回调发生在 `task.Result` **之前**，于是那一刻的 `DramaGate.Resolve`：

  · `OnDramaResolved` 反查 `_deferred` 查不到 req_id ⇒ 结果被 `if (reqId == null) return;` 丢弃；
  · 但 slot 已被 Resolve 从 `_pending` 摘走 ⇒ 过期 Tick 也不会兜底。
  ⇒ 那一帧 response 永远不发 ⇒ Python 空等 120s 判超时。

## 引擎为什么会同步回调 false

`WorldUnitAIAction1037::ActionStart` @0x45E2210：
    al = WorldUnitAITool::MovePoint(npc, toUnit);
    if (!al) { onEndCall(false); return; }    // 走不过去 ⇒ 同步 false，界面根本没弹
    ... 绑 onEndCall、开论道 ...               // 成功路径这里**不回调**

返回值 = `UnitActionMoveNPC.moveComplete`（+0x40），由
`moveDis / (grade+5) > Random(0, 100.0f)` 决定（常量 0x18626FD40 = 0x42C80000）。

## 钉住的三组不变量

  A. 同步回调**不许** Resolve（必须 Cancel + 把结果直接 return 给模型）。
  B. 异步回调**仍要** Resolve（别为了修同步把正常路砍了）。
  C. 移动闸探针只读、在 ActionStart **之前**取坐标、读的字段足以判读。

用法
----
    python3 scripts/dev/mutate_check_ai_action_sync.py

注意：会**临时改源码再还原**（改前先把原字节读进内存），跑完做逐字节还原校验。
⚠ 跑之前确认没有别的会话/编辑器正在改 `ToolExecutor.cs` ——
（`_mutate_lib.guard` 已挂 SIGINT/SIGTERM/atexit，保护自己的变异）。
"""

from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL_CS = ROOT / "csharp" / "tools" / "ToolExecutor.cs"

TESTS = [
    "tests/test_ai_action_sync_callback_contract.py",
    "tests/test_action_refusal_contract.py",
    "tests/test_drama_segment_contract.py",
]


def _env() -> dict:
    return {**os.environ, "PYTHONPATH": str(ROOT.parent)}


def run_pytest() -> int:
    """跑测试。**cwd 必须是 `ROOT.parent`、路径必须带 `agent_loop/` 前缀**
    （理由见 `mutate_check_manual_identity.py`：cwd 会被塞进 `sys.path[0]`，
    命中同名外壳 `agent_loop.py` 而不是包目录 ⇒ 整层收集阶段就红 ⇒ 假绿）。"""
    return _mutate_lib.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         *[ROOT.name + "/" + f for f in TESTS]],
        cwd=str(ROOT.parent), capture_output=True, text=True, env=_env()).returncode


# A/B 两组的锚点（代码原文，含缩进；改源码时这里要跟着改）
SYNC_BLOCK_CANCEL = "                        try { DramaGate.Cancel(slot); } catch { }   // 收票：不留给过期 Tick"
RESOLVE_LINE = "                    DramaGate.Resolve(slot, result);"
GUARD_LINE = "                    if (!startReturned)"
RETURN_LINE = "                if (syncResult != null) return syncResult;"
PROBE_CALL = "                LogAiActionMoveProbe(npc, op, npcName, \"ActionStart前\");"

# (名字, old, new)
MUTATIONS = [
    # ══════════════ A. 同步回调不许走挂起通道（核心）══════════════
    ("★同步回调直接 Resolve（= 原 bug 原样复发：结果被丢掉、空等 120s）",
     GUARD_LINE,
     "                    if (false)"),

    ("★同步回调把结论丢掉、仍返回挂起标记（另一种「静默」写法）",
     "                        syncResult = result;\n"
     "                        try { DramaGate.Cancel(slot); } catch { }   // 收票：不留给过期 Tick",
     "                        result = null;\n"
     "                        try { DramaGate.Cancel(slot); } catch { }   // 收票：不留给过期 Tick"),

    ("★不 Cancel 票据（slot 留在表里，HasPending 假挂起挡住后续动作）",
     SYNC_BLOCK_CANCEL,
     "                        // 票据没收回（故意）"),

    ("★不撤锚点（动作没起来却留着区间锚点，下一场会读进上一场的页）",
     "                        try { TalkOutcome.Disarm(); } catch { }     // 动作没起来：撤锚点",
     "                        // 没撤锚点（故意）"),

    ("★不同步释放归因凭证（券还在 ClaimTTL 内误挡玩家的窗）",
     "                        try { DramaNativeClaim.Release(claimUid); } catch { }\n"
     "                        try { TalkOutcome.Disarm(); } catch { }     // 动作没起来：撤锚点",
     "                        try { TalkOutcome.Disarm(); } catch { }     // 动作没起来：撤锚点"),

    ("★同步分支不提前 return（会继续往下挂起）",
     "                        try { TalkOutcome.Disarm(); } catch { }     // 动作没起来：撤锚点\n"
     "                        return;",
     "                        try { TalkOutcome.Disarm(); } catch { }     // 动作没起来：撤锚点"),

    ("★startReturned 永远为 false（把正常异步回调也当同步 ⇒ 论道完成也收不到结果）",
     "                startReturned = true;",
     "                startReturned = false;"),

    ("★startReturned 永远为 true（同步回调被当异步 ⇒ 退回原 bug）",
     "                bool startReturned = false;",
     "                bool startReturned = true;"),

    ("★ActionStart 后不检查 syncResult（同步结论算出来了却不返回）",
     RETURN_LINE,
     "                if (false) return syncResult;"),

    ("★把「检查 syncResult」挪到挂起标记之后（顺序错，永远不会执行）",
     RETURN_LINE + "\n",
     ""),

    # ══════════════ B. 异步回调仍要 Resolve ══════════════
    ("★异步回调也不 Resolve 了（修同步把正常路一起砍掉：论道完成永远收不到）",
     RESOLVE_LINE,
     "                    // 不 Resolve（故意）"),

    ("★Resolve 排到同步分流之前（同步回调先撞上无人认领的 Resolve）",
     GUARD_LINE,
     "                    if (false)"),

    # ══════════════ C. 文案归因 ══════════════
    ("★文案退回「玩家婉拒或中途离开」（谎报 1）",
     '                        result = Fail(op + "没能生效：他还没能站到你面前，这次交互没能开始"',
     '                        result = Fail(op + "未能完成（玩家婉拒或中途离开）"'),

    ("★文案不点真实机理（说成「整月边界」，模型只会等着过月）",
     '                                    + "（论道/双修要先走到对方所在的位置，画面没有弹出任何界面）。"',
     '                                    + "（常见原因：此刻正处在整月边界上）。"'),

    ("★文案去掉「不代表对方拒绝」护拦（模型会当成玩家态度）",
     '                                    + "过一会儿再让他试一次即可 —— 这不代表对方拒绝");',
     '                                    + "过一会儿再让他试一次即可");'),

    # ══════════════ D. 移动闸探针 ══════════════
    ("★探针挪到 ActionStart 之后（瞬移到位后距离读成 0，判据被抹平）",
     PROBE_CALL + "\n",
     ""),

    ("★探针不读境界（判据是 moveDis/(grade+5)，缺 grade 无法判读）",
     '                    gradeRaw = npc.data.dynUnitData.GetGrade();',
     '                    gradeRaw = 0;'),

    ("★探针不读坐标（算不出距离）",
     "                    nx = nd.pointX; ny = nd.pointY; hasNpc = true;",
     "                    hasNpc = true;"),

    ("★探针去掉阈值标注（100 = Random 上界，没有它判读不了走哪个分支）",
     '                          + " 折算=" + ratio\n'
     '                          + "（>100 = 引擎必定走赶路分支 → ActionStart 同步回 false）");',
     '                          + " 折算=" + ratio);'),

    ("★探针变成会拦截（观测件改成了闸，与「只报能自证的量」的口径冲突）",
     "        private static void LogAiActionMoveProbe(WorldUnitBase npc, string op, string npcName, string stage)\n"
     "        {\n"
     "            try\n"
     "            {",
     "        private static void LogAiActionMoveProbe(WorldUnitBase npc, string op, string npcName, string stage)\n"
     "        {\n"
     "            if (op == \"lun_dao\") throw new System.Exception(\"Fail(\");\n"
     "            try\n"
     "            {"),

    ("★探针改调未核实的 MapTool.PointDis（引入编译期/运行期风险）",
     "                        disRaw = Math.Max(Math.Abs(nx - px), Math.Abs(ny - py));",
     "                        disRaw = MapTool.PointDis(new UnityEngine.Vector2Int(nx, ny),"
     " new UnityEngine.Vector2Int(px, py));"),

    ("★探针不再用唯一前缀（真机 grep 不到）",
     '                ModMain.P("[AiActionMove] " + stage',
     '                ModMain.P("[Probe] " + stage'),
]


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _apply(path: Path, old: str, new: str) -> bool:
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    if n != 1:
        print("      [跳过] 锚点出现 %d 次（期望 1）—— 源码变了，锚点失效" % n)
        return False
    path.write_text(src.replace(old, new), encoding="utf-8")
    return True


def main() -> int:
    files = [TOOL_CS]
    _mutate_lib.guard(files)
    backup = {p: p.read_bytes() for p in files}
    before = {p: _md5(p) for p in files}

    def restore_all():
        for p in files:
            p.write_bytes(backup[p])

    # ── 脚手架自检：注入一个必然被发现的错 ──────────────────────────────
    # 用"startReturned 永远是 true"：它会让同步回调退回原 bug，新契约必然报红。
    print("=== 脚手架自检（startReturned 恒 true = 原 bug 原样复发） ===")
    if not _apply(TOOL_CS, "                bool startReturned = false;",
                  "                bool startReturned = true;"):
        print("自检失败：锚点失效，脚本已过期")
        return 2
    rc = run_pytest()
    restore_all()
    if rc == 0:
        print("★ 自检失败：原 bug 注入竟然没报红 → 脚手架不可信，中止")
        return 2
    print("自检通过（原 bug 注入被抓到）\n")

    # ── 基线自检：未变异树上必须先绿 ──────────────────────────────────
    print("=== 基线自检（未变异树上必须先绿） ===")
    rc = run_pytest()
    if rc != 0:
        print("★ 基线不绿 → 后面所有『抓到』都不可信，中止")
        return 2
    print("  基线绿\n")

    caught = skipped = missed = 0
    for name, old, new in MUTATIONS:
        if not _apply(TOOL_CS, old, new):
            skipped += 1
            continue
        rc = run_pytest()
        TOOL_CS.write_bytes(backup[TOOL_CS])
        if rc != 0:
            caught += 1
            print("  ✅ 抓到 %s" % name)
        else:
            missed += 1
            print("  ❌ 漏掉 %s  ← 这个断言是假绿" % name)

    bad = [p for p in files if _md5(p) != before[p]]
    restore_all()
    print()
    print("汇总：抓到 %d / 跳过 %d / 漏掉 %d（共 %d 条）"
          % (caught, skipped, missed, len(MUTATIONS)))
    if bad:
        print("★ 还原校验失败（已用内存备份覆盖）：" + str(bad))
        return 1
    print("还原校验：全部逐字节一致 ✓")
    if missed or skipped:
        return 1
    print("全部变异都被捕获 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
