#!/usr/bin/env python3
"""变异验证 —— 存档身份「补发/补投」（世界身份丢失，09-18）。

**为什么必须有这个脚本**：这条链的失效方式**全是静默**的 ——
  · C# 重连不补发身份 ⇒ Python 重启后整段窗口落扁平目录（无报错，只是"NPC 忘了"）；
  · save/load 帧未连接时被丢 ⇒ 整局没有存档身份（跨档串号）；
  · save_happened 换档不先丢弃 ⇒ 从扁平目录读来的短账本**整份覆盖**真存档（静默丢记忆）；
  · flush 不按出处对账 ⇒ 同一件事从另一个入口再来一次；
  · 未定档闸门丢了 ⇒ 又往"谁都不属于"的扁平目录写（跨档串号）。
每一条回退一处，确认行为测试 / 源码契约真的变红。

两道保险绳（与 `mutate_check_room_speakers.py` 同）：
  · 跑前先跑**未变异的基线**，基线不绿直接退出（否则"抓到"可能是环境问题）；
  · 跑完逐字节 md5 校验没留残留（`_mutate_lib.guard` 另挂 SIGINT/SIGTERM/atexit 还原）。

用法：
    python3 scripts/dev/mutate_check_world_identity.py            # 全部
    python3 scripts/dev/mutate_check_world_identity.py ① ⑤         # 只跑名字含这些子串的
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
LOOP = ROOT / "agent_loop.py"
CONTACTS = ROOT / "storage" / "contacts_store.py"
SERVER = ROOT / "scripts/server.py"
MODMAIN = ROOT / "csharp/app/ModMain.cs"
OUTBOX = ROOT / "csharp/bridge/WorldFrameOutbox.cs"

# 行为层（直接调 handler / loop）+ 接线契约层（源码级）一起跑：一个变异只要让**任一条**
# 变红就算被捕获 —— 两者覆盖的是同一条链的两端。
PYTEST = [sys.executable, "-X", "utf8", "-m", "pytest",
          "tests/test_world_identity_recovery.py", "tests/test_world_identity_contract.py", "-q"]

# C# 队列规则那几条走**真单测**（WorldFrameOutbox 是零 Unity 依赖的纯逻辑，见该文件头）：
# 队列入队/补投/上限的失效都在 ModMain 这类 Unity 代码里测不动，所以规则被切进了那个类。
CS_TEST = [sys.executable, "-X", "utf8", "scripts/dev/run_csharp_tests.py",
           "--only", "WorldFrameOutboxTests", "--quiet"]

MUTATIONS = [
    # ---------- world_hello（连接时补发身份） ----------
    (
        "① world_hello 不切命名空间（补发身份等于没补）",
        WS,
        [('            changed = self._apply_world(p, why="world_hello", missing_level="info")',
          '            changed = False')],
    ),
    (
        "② world_hello 无条件丢弃（重连把未固化增量抹掉）",
        WS,
        [("            changed = self._apply_world(p, why=\"world_hello\", missing_level=\"info\")\n"
          "            dropped = 0\n"
          "            if changed:",
          "            changed = self._apply_world(p, why=\"world_hello\", missing_level=\"info\")\n"
          "            dropped = 0\n"
          "            if True:")],
    ),
    (
        "③ world_hello 不补载群名录（重启后群列表空到下次读档）",
        WS,
        [("            nr = self.loop.load_rooms()\n"
          "            log.info(\"world_hello：存档身份已补发",
          "            nr = 0\n"
          "            log.info(\"world_hello：存档身份已补发")],
    ),
    (
        "④ world_hello 缺身份报 WARNING（开机没进世界是常态，刷假告警）",
        WS,
        [('missing_level="info"', 'missing_level="warning"')],
    ),
    # ---------- save_happened（换档先丢弃再固化） ----------
    (
        "⑤ save_happened 换档不丢弃（短账本覆盖真存档 —— 09-18 那条 bug）",
        WS,
        [("            changed = self._apply_world(p, why=\"save_happened\")\n"
          "            dropped = 0\n"
          "            if changed:",
          "            changed = self._apply_world(p, why=\"save_happened\")\n"
          "            dropped = 0\n"
          "            if False:")],
    ),
    (
        "⑥ save_happened 先固化后切档（顺序反了：覆盖照旧发生）",
        WS,
        [('            changed = self._apply_world(p, why="save_happened")',
          '            self.loop.flush_all()\n'
          '            changed = self._apply_world(p, why="save_happened")')],
    ),
    (
        "⑦ save_happened 完全不固化（另一半：增量永远不落盘）",
        WS,
        [("            n = self.loop.flush_all()\n"
          "            log.info(\"save_happened：固化 %d 个 agent 会话（存档 %s）\"",
          "            n = 0\n"
          "            log.info(\"save_happened：固化 %d 个 agent 会话（存档 %s）\"")],
    ),
    # ---------- flush_all（出处对账 + 未定档闸门） ----------
    (
        "⑧ flush 不按账本出处对账（跨命名空间覆盖从另一个入口回来）",
        LOOP,
        [("            if self._namespace.get(a.id, cur) != cur:\n"
          "                skipped.append(a.id)\n"
          "                continue",
          "            if False:\n"
          "                skipped.append(a.id)\n"
          "                continue")],
    ),
    (
        "⑨ 出处登记不写（对账两边都取默认值 → 闸门退化成空转）",
        LOOP,
        [('        self._namespace[npc_id] = self.world_id or ""',
          '        self._namespace.pop(npc_id, None)')],
    ),
    (
        "⑩ flush 的未定档闸门丢了（生产形态又往扁平目录写）",
        LOOP,
        [("        if self.require_world and not cur:\n"
          "            log.warning(\"flush 跳过",
          "        if False:\n"
          "            log.warning(\"flush 跳过")],
    ),
    (
        "⑪ create 未定档仍从扁平目录恢复账本（读到别人的记忆）",
        LOOP,
        [("            if self.require_world and not self.world_id:\n"
          "                # 未定档（Python 刚重启、C# 还没补发身份）：**不**从扁平目录恢复账本 ——",
          "            if False:\n"
          "                # 未定档（Python 刚重启、C# 还没补发身份）：**不**从扁平目录恢复账本 ——")],
    ),
    (
        "⑫ 删群的未定档闸门丢了（写进扁平目录）",
        LOOP,
        [("        if self.require_world and not self.world_id:\n"
          "            # 未定档：写 index / 删流水都会落到扁平目录",
          "        if False:\n"
          "            # 未定档：写 index / 删流水都会落到扁平目录")],
    ),
    # ---------- 通讯录（同一道闸门） ----------
    (
        "⑬ 通讯录的未定档闸门永远不落（加好友又写进扁平目录）",
        CONTACTS,
        [("        if not (self.require_world and not self.world_id):\n            return False",
          "        if True:\n            return False")],
    ),
    (
        "⑭ list_contacts 不查闸门（未定档时读到扁平目录的「别人的好友」）",
        CONTACTS,
        [("        if self._blocked():\n"
          "            return []\n"
          "        data = self._load()\n"
          "        return list(data.get(\"contacts\", []))",
          "        data = self._load()\n"
          "        return list(data.get(\"contacts\", []))")],
    ),
    # ---------- 装配层：生产形态判据 ----------
    (
        "⑮ 装配层不传 require_world（生产形态的闸门根本没装上）",
        SERVER,
        [("            require_world=game_launched,\n", "")],
    ),
    (
        "⑯ 通讯录没接 require_world（会话闸住了、好友照旧串）",
        SERVER,
        [("        contact_service=ContactsService(loop.storage_root, require_world=loop.require_world),",
          "        contact_service=ContactsService(loop.storage_root),")],
    ),
    # ---------- C# 接线 ----------
    (
        "⑰ C# 重连不补发身份（Python 重启窗口照旧丢档）",
        MODMAIN,
        [("                SendWorldHello();\n", "")],
    ),
    (
        "⑱ C# 断线期间不补投 save/load（帧被静默吞掉）",
        MODMAIN,
        [("                FlushPendingWorldFrames();\n", "")],
    ),
    (
        "⑲ load_happened 改回裸 SendRequest（未连接即静默丢）",
        MODMAIN,
        [('                bool sent = SendWorldFrame("load_happened", p);',
          '                _wsClient?.SendRequest("load_happened", p, _ => { }, 10000);\n'
          '                bool sent = true;')],
    ),
    (
        "⑳ save_happened 改回裸 SendRequest（同上，从另一条断）",
        MODMAIN,
        [('                bool sent = SendWorldFrame("save_happened", p);',
          '                _wsClient?.SendRequest("save_happened", p, _ => { }, 10000);\n'
          '                bool sent = true;')],
    ),
    (
        "㉑ 取不到身份也把 world_id 写进帧（Python 侧会当「提供了但为空」）",
        MODMAIN,
        [("                if (!string.IsNullOrEmpty(wid))", "                if (true)")],
    ),
]

# ---------------------------------------------------------------------------
# C# 侧：回退队列规则本身（跑 csharp/tests/WorldFrameOutboxTests 的真单测）
# ---------------------------------------------------------------------------

CS_MUTATIONS = [
    (
        "㉒ 未连接时不排队（帧直接消失 → 整局没有存档身份）",
        OUTBOX,
        [("            _queue.Add(new KeyValuePair<string, T>(method, payload));\n"
          "            return false;",
          "            return false;")],
    ),
    (
        "㉓ 排队超上限丢**最新**（最后一次读档的帧永远投不出去）",
        OUTBOX,
        [("                KeyValuePair<string, T> old = _queue[0];\n"
          "                _queue.RemoveAt(0);",
          "                KeyValuePair<string, T> old = _queue[_queue.Count - 1];\n"
          "                _queue.RemoveAt(_queue.Count - 1);")],
    ),
    (
        "㉔ 补投时不看连接状态（把帧原地烧掉，之后再也不会补）",
        OUTBOX,
        [("            if (!_connected()) return 0;\n", "")],
    ),
    (
        "㉕ 补投顺序反了（先存档后读档 = 刚固化的内容又被丢掉）",
        OUTBOX,
        [("            var frames = new List<KeyValuePair<string, T>>(_queue);",
          "            var frames = new List<KeyValuePair<string, T>>(_queue);\n"
          "            frames.Reverse();")],
    ),
    (
        "㉖ 补投后不清队列（同一批帧被反复重投）",
        OUTBOX,
        [("            _queue.Clear();          // 先摘下来再发：`send` 里若回头调 Send 不会重复入队\n", "")],
    ),
    (
        "㉗ 直发时吞掉发送异常（失败与成功再也分不开）",
        OUTBOX,
        [("                _send(method, payload);\n                return true;",
          "                try { _send(method, payload); } catch { }\n                return true;")],
    ),
    (
        "㉘ 上限不夹取（传 0 时「丢最旧」去取空队列的头 → 越界）",
        OUTBOX,
        [("            MaxPending = maxPending < 1 ? 1 : maxPending;",
          "            MaxPending = maxPending;")],
    ),
    (
        "㉙ 构造参数为 null 也放行（延迟到运行期空引用崩溃）",
        OUTBOX,
        [("            if (connected == null) throw new ArgumentNullException(\"connected\");\n"
          "            if (send == null) throw new ArgumentNullException(\"send\");\n", "")],
    ),
]


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _backup(p: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / ("mutate_worldid_" + p.name)
    shutil.copy2(p, tmp)
    return tmp


def _check(name: str, path: Path, repls, cmd) -> bool:
    bak = _backup(path)
    try:
        src = path.read_text(encoding="utf-8")
        for old, new in repls:
            if old not in src:
                print("  ✗ %s：待替换片段不存在（脚本要更新）：%r" % (name, old[:70]))
                return False
            src = src.replace(old, new)
        path.write_text(src, encoding="utf-8")
        r = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if r.returncode == 0:
            print("  ✗ %s：测试仍然通过 —— 这条判据没有被任何用例覆盖" % name)
            return False
        out = (r.stdout or "") + (r.stderr or "")
        red = re.findall(r"^FAILED \S+::(\S+)", out, re.M)
        if not red:
            red = re.findall(r"^  ✗ (.+)$", out, re.M)      # C# 单测的失败行格式
        print("  ✓ %s：测试如实变红（%s）" % (name, "、".join(red[:3]) or "见输出"))
        return True
    finally:
        shutil.copy2(bak, path)


def main() -> int:
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    picked_py = [(n, p, r) for n, p, r in MUTATIONS if not only or any(o in n for o in only)]
    picked_cs = [(n, p, r) for n, p, r in CS_MUTATIONS if not only or any(o in n for o in only)]
    files = sorted({p for _n, p, _r in picked_py + picked_cs})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    before = {p: _md5(p) for p in files}
    print("[存档身份补发/补投]  Python 行为+契约 %d 条 / C# 队列单测 %d 条"
          % (len(picked_py), len(picked_cs))
          + ("  仅跑：%s" % "、".join(only) if only else ""))

    # 两道基线：两层各自先在**未变异**的树上跑一遍。任何与变异无关的红（环境缺 dotnet、
    # 并发会话正好在改源码）都会被记成"抓到"，所以基线不绿必须直接退。
    for label, cmd in (("Python 行为+契约", PYTEST), ("C# 队列单测", CS_TEST)):
        base = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        if base.returncode != 0:
            print("★ %s 基线就是红的 —— 先修源码，变异结果不可信：" % label)
            print(((base.stdout or "") + (base.stderr or ""))[-2000:])
            return 2
    print("  基线绿（两层在未变异的源码树上都通过）")

    ok = True
    for name, path, repls in picked_py:
        ok &= _check(name, path, repls, PYTEST)
    for name, path, repls in picked_cs:
        ok &= _check(name, path, repls, CS_TEST)

    after = {p: _md5(p) for p in files}
    if after != before:
        for p in files:
            if after[p] != before[p]:
                print("★ 未还原：%s（%s → %s）" % (p.relative_to(ROOT), before[p], after[p]))
        ok = False
    else:
        print("  逐字节还原校验通过（%d 个文件 md5 一致）" % len(files))

    total = len(picked_py) + len(picked_cs)
    hit = total if ok else total - 1
    print("== %d/%d 条变异被如实捕获 ==" % (hit, total))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
