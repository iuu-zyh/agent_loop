#!/usr/bin/env python3
"""变异验证 —— 底部统计条（BG/StatsBar）的上下文那一项（09-18 用户反馈）。

**为什么必须有这个脚本**：这一层的失效全是**静默**的 —— 少一个字段、少一个分支，
界面只是"那一格没了"，不报错、不进日志。用户 09-18 的原话就是
"当前显示缓存率之类的，没有展示当前上下文和总上下文吗，去掉了吗？"，实际有三层原因：

  ① **渲染层**（最外层）：旧写法把 `contextWindow`（**总**上下文，配置事实）也塞进
     `if (pressureTokens > 0)` 里 ⇒ "还没实测过"的窗口连总量一起消失；
  ② **数据层**：实测值在三种情况下被**故意作废**（刚开窗/resume 的 agent 构造、压缩、删除），
     而"当前 surface 有多大"一直知道（`set_surface` 的估算）⇒ 必须单独暴露估算口径；
  ③ **路由层**：群聊的 `stats_update` 原先**没有 room_id** ⇒ 被群窗"room_id 相等"这条
     唯一准入判据整帧丢掉 ⇒ 群窗那行永远停在预制体默认文本（用户截图上那个 "None"）。
  ④ **口径层**（09-18 用户拍板）：群里每人一份账本与上下文，"各自的上下文/占用率"没有
     可加语义 ⇒ 群窗那一行只报**花费**（总/输入/输出/缓存率），且**只算群内回合**
     （成员 agent 同时服务单聊，必须在回合前后取差归集）—— 这一块由 `roomUsage` 承载。

每个变异回退一处，确认对应的行为测试 / 源码契约真的变红。两道保险绳同
`mutate_check_llm_timeout.py`：跑前先跑**未变异的基线**（源码本来不绿就直接退出），
跑完逐字节 md5 校验没留残留。

用法：
    python3 scripts/dev/mutate_check_stats_bar.py            # 全部
    python3 scripts/dev/mutate_check_stats_bar.py ① ③        # 只跑名字含这些子串的
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）

import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "transport" / "ws_channel.py"
STATS = ROOT / "session" / "stats.py"
ROOM = ROOT / "room" / "room.py"
WINDOW = ROOT / "csharp/UI/ChatWindow.cs"
PRESENTER = ROOT / "csharp/UI/ChatPresenter.cs"

PYTEST = [sys.executable, "-X", "utf8", "-m", "pytest",
          "tests/test_stats.py", "tests/test_chat_ui_contract.py", "tests/test_room.py", "-q"]

MUTATIONS = [
    (
        "① 群聊 stats_update 不带 room_id（被群窗准入判据整帧丢掉 → 那行永远刷不动）",
        WS,
        [('await self.ws.send_event("stats_update", npc_id=npc_id, **_extra, **(snap or {}))',
          'await self.ws.send_event("stats_update", npc_id=npc_id, **(snap or {}))')],
    ),
    (
        "② 总量（contextWindow）又被嵌进「当前值 > 0」⇒ 没实测时整项消失",
        WINDOW,
        [("                if (ctx > 0 || win > 0)", "                if (ctx > 0)")],
    ),
    (
        "③ 估算口径不接了（没实测时只能整段消失，而不是退到 ≈）",
        WINDOW,
        [("                if (ctx <= 0)\n"
          "                {\n"
          "                    ctx = LongOf(cp[\"estimatedTokens\"]);\n"
          "                    est = ctx > 0;\n"
          "                }\n", "")],
    ),
    (
        "④ 估算值不标 ≈（会被读成实测值）",
        WINDOW,
        [('if (ctx > 0) sb.Append(est ? "≈" : "").Append(FormatTok(ctx));',
          'if (ctx > 0) sb.Append("").Append(FormatTok(ctx));')],
    ),
    (
        "⑤ 群聊统计行不标说话人（被读成「这个群的统计」）",
        WINDOW,
        [("            if (IsRoomMode && stats[\"roomUsage\"] == null)\n"
          "            {\n"
          "                string who = (string)stats[\"npc_id\"] ?? \"\";\n"
          "                if (!string.IsNullOrEmpty(who)) line = who + \" ｜ \" + line;\n"
          "            }\n", "")],
    ),
    (
        "⑥ 整块重建（开窗/换群）不清统计行 → 上一个窗口的残影留在新窗口上",
        WINDOW,
        [('            if (_tokensBar != null) _tokensBar.text = "";\n', "")],
    ),
    (
        "⑦ 估算值恒为 None（数据层不暴露 surface 估算）",
        STATS,
        [("        estimated = self.surface_tokens if self.surface_tokens > 0 else None",
          "        estimated = None")],
    ),
    (
        "⑧ 估算占用率拿实测口径顶替（occupancyEstimated 变成 occupancy 的别名）",
        STATS,
        [("            occupancy_estimated = min(100, int(_math2.floor(\n"
          "                estimated / self.context_window * 100 + 0.5)))",
          "            occupancy_estimated = occupancy")],
    ),
    (
        "⑨ 群内回合不归集用量（本群那一行永远是 0 —— 群里看不到任何花费）",
        WS,
        [("                        _room.add_group_usage(_usage_delta(_u0 or {}, _usage_of(agent)))\n"
          "                        _room.add_group_usage({\"turns\": 1})\n", "")],
    ),
    (
        "⑩ 群内用量用成员**累计**顶替（把'我在这个 NPC 身上花的'当成'本群花的'）",
        WS,
        [("_room.add_group_usage(_usage_delta(_u0 or {}, _usage_of(agent)))",
          "_room.add_group_usage(_usage_of(agent))")],
    ),
    (
        "⑪ 群帧不带 roomUsage（C# 退回成员口径 ⇒ 群里显示的是某个人的账）",
        WS,
        [('                            snap["roomUsage"] = _room.group_usage_block()', '                            pass')],
    ),
    (
        "⑫ 开群回执不带 roomUsage（那一行停在预制体的 None，直到第一个成员说完）",
        WS,
        [('                out["roomUsage"] = room.group_usage_block()', '                pass')],
    ),
    (
        "⑬ 本群账不落盘（读档后那一行归零）",
        ROOM,
        [('                **({"group_usage": dict(self.group_usage)} if self.group_usage else {})}', '')],
    ),
    (
        "⑭ C# 不认本群口径（群窗又变成「某个成员的账」）",
        WINDOW,
        [("            var ru = stats[\"roomUsage\"] as JObject;\n"
          "            if (ru != null) return BuildRoomStatsLine(ru);\n", "")],
    ),
    (
        "⑮ 本群那一行偷偷带上「上下文/占用率」（把某个成员的数当全群的）",
        WINDOW,
        [('            sb.Append(" ｜ 不含各自单聊");',
          '            sb.Append(" ｜ 上下文 ").Append(FormatTok(LongOf(ru["contextWindow"])));')],
    ),
    (
        "⑯ 开群回执不刷统计行（群窗底部停在 None）",
        PRESENTER,
        [('            if (data["roomUsage"] is JObject roomUsageSeed) _window.SetStats(data);\n', "")],
    ),
]


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _backup(p: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / ("mutate_stats_" + p.name)
    shutil.copy2(p, tmp)
    return tmp


def _check(name: str, path: Path, repls) -> bool:
    bak = _backup(path)
    try:
        src = path.read_text(encoding="utf-8")
        for old, new in repls:
            if old not in src:
                print("  ✗ %s：待替换片段不存在（脚本要更新）：%r" % (name, old[:70]))
                return False
            src = src.replace(old, new)
        path.write_text(src, encoding="utf-8")
        r = _mutate_lib.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if r.returncode == 0:
            print("  ✗ %s：测试仍然通过 —— 这条判据没有被任何用例覆盖" % name)
            return False
        out = (r.stdout or "") + (r.stderr or "")
        red = re.findall(r"^FAILED \S+::(\S+)", out, re.M)
        print("  ✓ %s：测试如实变红（%s）" % (name, "、".join(red[:3]) or "见输出"))
        return True
    finally:
        shutil.copy2(bak, path)


def main() -> int:
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    picked = [(n, p, r) for n, p, r in MUTATIONS if not only or any(o in n for o in only)]
    files = sorted({p for _n, p, _r in picked})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    before = {p: _md5(p) for p in files}
    print("[统计条 · tests/test_stats.py + tests/test_chat_ui_contract.py + tests/test_room.py]"
          + ("  仅跑：%s" % "、".join(only) if only else ""))

    base = _mutate_lib.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if base.returncode != 0:
        print("★ 基线就是红的 —— 先修源码，变异结果不可信：")
        print((base.stdout or "")[-2000:])
        return 2
    print("  基线绿（未变异的源码树本来通过）")

    ok = True
    for name, path, repls in picked:
        ok &= _check(name, path, repls)

    after = {p: _md5(p) for p in files}
    if after != before:
        for p in files:
            if after[p] != before[p]:
                print("★ 未还原：%s（%s → %s）" % (p.relative_to(ROOT), before[p], after[p]))
        ok = False
    else:
        print("  逐字节还原校验通过（%d 个文件 md5 一致）" % len(files))

    print()
    print("全部 %d 个变异都被捕获" % len(picked) if ok
          else "★ 有变异未被捕获 / 有残留 —— 补测试或修实现")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
