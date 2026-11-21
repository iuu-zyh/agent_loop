#!/usr/bin/env python3
"""变异验证 —— LLM 超时语义（存活看门狗 vs 整趟挂钟，09-18 重做）。

**为什么必须有这个脚本**：这一层改的是"什么时候判超时"，而它的失效形态**全是静默的** ——
整趟挂钟回归后，慢速长流会在半途被砍断（09-17 实机：姜萌的思考流推了 382 token，第 45 秒
被杀，群聊该成员整轮丢失），日志里只有一行"超过 45.0s 未完成"，看代码还以为它在量"块间隔"。
所以这里把每个判据逐个回退，确认 `tests/test_llm_retry.py` 真的抓得住。

覆盖的判据（回退 → 期望变红的用例）：
  ①/②/③  整趟挂钟回归、首字↔停顿用错看门狗、看门狗被整体关掉
  ④/⑤    首字/停顿看门狗各自的失效
  ⑥      阶段切换（首块前后）
  ⑦/⑧    看门狗到点时 close 流 / httpx 背板取值 / idle 回落 timeout
  ⑨/⑩/⑪  绝对上限：收缩看门狗 / 不作用于非流式 / 0 值 = 不设顶 / 非流式看门狗
  ⑫/⑬    看门狗错误必须是可重试的 TimeoutError
  ⑭/⑮/⑯  配置默认值（timeout=120、idle_timeout=60、total_budget=0）
  ⑰/⑱/⑲/⑳ 面板侧：两行标签运行期改名 / 回填默认值与后端一致（跨语言漂移是静默的）/
          硬顶那行"0 = 不设限"必须真的提交

用法：
    python3 scripts/dev/mutate_check_llm_timeout.py

注意：会**临时改源码再还原**（备份在系统临时目录）。脚本自带两道保险绳：跑之前先跑一次
**未变异的基线**（源码树本来就不绿时直接退出——否则"故障点"会被当成"变异被抓到"），
跑完再逐字节校验 md5（确认没把变异留在源码树里）。末行必须是"全部变异都被捕获" + "还原校验通过"。
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
CLIENT = ROOT / "llm/openai_client.py"
LOADER = ROOT / "config" / "config_loader.py"
AB_PANEL = ROOT / "csharp/UI/AbConfigPanel.cs"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"

# 每个变异跑这三个文件（一个进程）：行为层 + 配置默认值层 + 面板侧契约层
PYTEST = [sys.executable, "-X", "utf8", "-m", "pytest",
          "tests/test_llm_retry.py", "tests/test_config_loader.py",
          "tests/test_llm_timeout_ui_contract.py", "-q"]

MUTATIONS = [
    (
        "① 整趟挂钟回归（把流式分支重新包进 wait_for(self.timeout)）——09-17 事故的原形",
        CLIENT,
        [(
            "                    return await self._call_stream(\n"
            "                        oai_messages, oai_tools, on_token or _noop, stats,\n"
            "                        on_reasoning=on_reasoning, budget_deadline=budget_deadline)",
            "                    return await self._guarded(\n"
            "                        self._call_stream(\n"
            "                            oai_messages, oai_tools, on_token or _noop, stats,\n"
            "                            on_reasoning=on_reasoning, budget_deadline=budget_deadline),\n"
            "                        self.timeout, \"整趟挂钟\")",
        )],
    ),
    (
        "② 首字阶段用停顿看门狗（等于让 idle_timeout 给首字续命）",
        CLIENT,
        [(
            "                self.idle_timeout if first_seen else self.timeout, budget_deadline)",
            "                self.idle_timeout, budget_deadline)",
        )],
    ),
    (
        "③ 中途阶段用首字看门狗（等于用首字那个更短的值去卡吐字间隔）",
        CLIENT,
        [(
            "                self.idle_timeout if first_seen else self.timeout, budget_deadline)",
            "                self.timeout, budget_deadline)",
        )],
    ),
    (
        "④ 首字看门狗关掉（开流那一步不设上限）",
        CLIENT,
        [(
            "        stream = await self._guarded(\n"
            "            asyncio.to_thread(self._open_stream, oai_messages, oai_tools),\n"
            "            self._watchdog_seconds(self.timeout, budget_deadline),\n"
            "            \"首字超时：模型一直没回应\")",
            "        stream = await asyncio.to_thread(self._open_stream, oai_messages, oai_tools)",
        )],
    ),
    (
        "⑤ 队列看门狗关掉（吐字中途不设上限，卡死只能等 httpx 背板）",
        CLIENT,
        [(
            "            watch = self._watchdog_seconds(\n"
            "                self.idle_timeout if first_seen else self.timeout, budget_deadline)",
            "            watch = None",
        )],
    ),
    (
        "⑥ 看门狗放弃时不 close 流（读线程要拖到 httpx 背板到点才退出）",
        CLIENT,
        [(
            "            except TimeoutError:\n"
            "                # 尽力把还在阻塞的读线程叫醒（幂等、可重入，失败无副作用）：\n"
            "                # 不 close 的话它要等 `_httpx_read_timeout()` 那道背板才退出。\n"
            "                _close_quietly(stream)\n"
            "                raise",
            "            except TimeoutError:\n"
            "                raise",
        )],
    ),
    (
        "⑦ httpx 背板与看门狗同值（两者抢跑 → 报错文案退化成 SDK 的 read timeout）",
        CLIENT,
        [("        return sum(vals) if vals else None", "        return max(vals) if vals else None")],
    ),
    (
        "⑧ idle_timeout 不再回落 timeout（只设了 timeout 的用户中途没有看门狗）",
        CLIENT,
        [(
            "        self.idle_timeout = (_positive_or_none(idle_timeout)\n"
            "                             if idle_timeout is not None else self.timeout)",
            "        self.idle_timeout = None",
        )],
    ),
    (
        "⑨ 绝对上限不再收缩看门狗（设了上限也压不住总耗时）",
        CLIENT,
        [(
            "        for c in (phase, None if budget_deadline is None else (budget_deadline - monotonic())):",
            "        for c in (phase,):",
        )],
    ),
    (
        "⑩ 绝对上限开始约束非流式（后台压缩会被玩家等待预算卡死）",
        CLIENT,
        [(
            "        if not streaming or not self.total_budget:",
            "        if not self.total_budget:",
        )],
    ),
    (
        "⑪ 非流式路径的首字看门狗拿掉（压缩挂住就无限等）",
        CLIENT,
        [(
            "                resp = await self._guarded(\n"
            "                    asyncio.to_thread(self._call, oai_messages, oai_tools),\n"
            "                    self._watchdog_seconds(self.timeout_nonstream, budget_deadline),\n"
            "                    \"首字超时：模型一直没回应\")",
            "                resp = await asyncio.to_thread(self._call, oai_messages, oai_tools)",
        )],
    ),
    (
        # ★ 这一条**两处一起回退** ★：`_budget_deadline` 里"0 = 不设顶"有两道冗余防护
        #   （`not self.total_budget` 与 `b > 0`），只改一处等于没改（实测过：仍然全绿）。
        #   一起改掉才等价于"有人把 0 的语义弄丢了"——那会让每次请求拿到一个"此刻到期"的
        #   硬顶、当场放弃，是最隐蔽的一种回归。
        "⑫ total_budget=0 不再当「不设顶」（两处冗余防护一起回退 → 每次请求当场放弃）",
        CLIENT,
        [
            ("        if not streaming or not self.total_budget:",
             "        if not streaming or self.total_budget is None:"),
            ("        return started + b if b > 0 else None",
             "        return started + b"),
        ],
    ),
    (
        "⑬ 看门狗错误不再抛可重试的 TimeoutError（零产出超时失去自动重试）",
        CLIENT,
        [(
            "            raise TimeoutError(f\"{what}：{float(seconds):.0f}s 内没有任何新内容\"\n"
            "                               \"（存活看门狗，与整趟耗时无关）\") from e",
            "            raise RuntimeError(f\"{what}：{float(seconds):.0f}s 内没有任何新内容\") from e",
        )],
    ),
    (
        "⑭ 首字看门狗默认值退回 45s（慢模型刚开口就被判死）",
        LOADER,
        [('        "timeout": 120.0,', '        "timeout": 45.0,')],
    ),
    (
        "⑮ 停顿看门狗默认值被删（中途停顿失去独立判据）",
        LOADER,
        [('        "idle_timeout": 60.0,', '        "idle_timeout": 0.0,')],
    ),
    (
        "⑯ 绝对上限默认值被打开（整趟挂钟重新变成默认判据）",
        LOADER,
        [('        "total_budget": 0.0,', '        "total_budget": 150.0,')],
    ),
    (
        "⑰ 面板不再改那两行标签（玩家看到的还是预制件里烧死的旧措辞）",
        AB_PANEL,
        [
            ('            RelabelRow(root, Llm + "LlmTimeoutRow", "首字超时秒（即时生效）");\n', ''),
            ('            RelabelRow(root, Llm + "LlmBudgetRow", "硬顶秒·0=不设限");\n', ''),
        ],
    ),
    (
        "⑱ 面板回填的首字看门狗默认值退回 45（玩家一保存就把 45 写回后端）",
        PRESENTER,
        [('ToStringInvariant(llm["timeout"]) ?? "120"', 'ToStringInvariant(llm["timeout"]) ?? "45"')],
    ),
    (
        "⑲ 面板回填的绝对上限默认值退回 100（显示一个后端根本不用的值）",
        PRESENTER,
        [('ToStringInvariant(llm["total_budget"]) ?? "0"',
          'ToStringInvariant(llm["total_budget"]) ?? "100"')],
    ),
    (
        "⑳ 硬顶那行退回「0 = 不提交」（玩家填 0 关不掉手编过的 150，「改了却没生效」）",
        PRESENTER,
        [("                submitLlmBudget = true;",
          "                submitLlmBudget = llmBudget > 0;")],
    ),
]


def _backup(p: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / ("mutate_" + p.name)
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


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def main() -> int:
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    picked = [(n, p, r) for n, p, r in MUTATIONS if not only or any(o in n for o in only)]
    files = sorted({p for _n, p, _r in picked})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    before = {p: _md5(p) for p in files}
    print("[Python 契约 · tests/test_llm_retry.py + tests/test_config_loader.py + "
          "tests/test_llm_timeout_ui_contract.py]"
          + ("  仅跑：%s" % "、".join(only) if only else ""))

    # ── 基线：先跑一次**未变异**的测试 ──
    # 红了就说明当前源码树本来就不绿，后面每一条"变红"都不成立（假捕获）。这不是假想：
    # 本脚本第一版跑到一半被外部打断，把 ② 那条变异留在了 openai_client.py 里，
    # 于是之后的每一轮都"变红"——故障点被当成证据用了。基线是唯一的解药。
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

    # ── 收尾：逐字节还原校验（改源码再还原的脚本必须自证没留残留）──
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
