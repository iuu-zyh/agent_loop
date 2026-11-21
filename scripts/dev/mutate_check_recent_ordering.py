#!/usr/bin/env python3
"""变异验证 —— 通讯录「最近」排序（10-21 用户报障）的修复。

**为什么必须有这个脚本**：这次的 bug 极容易"看着修好了、实际没修"——
它不报错、不缺数据，只是**顺序不对**。而顺序不对很容易被一句
"排序大概是按名字吧"糊过去。真正的验收线是：**把修复逐个回退，测试必须变红**。

报障原文
--------
「NPC 自主交互之后出现横幅、传按钮有红点，这时候点开通讯录去看 NPC 发的话，
  但是 NPC 属于的那一行在通讯录里顺序没有变。」

根因：排序用的时间戳取自 `max(会话文件 mtime, 内存时间戳)`，而 mtime 是
「上次**存档**」的时刻 —— `flush_all` 一次重写全部 agent，十几个文件落在同一秒
（真机实测姜萌 .9534145 / 江宇文 .9564289），截断成整秒后**全相等** ⇒ 排序退化。
修法（用户拍板 ②+③）：落盘真实互动时刻 + 补刷新缺口。不做 ①"提高 mtime 精度"
（治标：mtime 仍然是存档时刻）。

钉住的四组不变量
----------------
  A. 排序判据：`last` **优先**，mtime 只在 `last` 缺失时兜底（**不许写 max**）。
  B. 两条**真回复**路径（玩家消息 / 主动开口）都必须记；系统提示帧不许记。
  C. 落盘往返 / 跨存档隔离 / 未定档不记 / 坏文件兜底 / 缓存失效。
  D. 模拟复盘：真机数据代进旧判据能复现"排序退化"，新判据免疫存档。

用法
----
    python3 scripts/dev/mutate_check_recent_ordering.py          # 全跑（默认）
    python3 scripts/dev/mutate_check_recent_ordering.py py       # 只跑 Python 行为层
    python3 scripts/dev/mutate_check_recent_ordering.py contract # 只跑源码契约层

注意：会**临时改源码再还原**（改前先把原字节读进内存），跑完做逐字节还原校验。
⚠ 跑之前确认没有别的会话/编辑器正在改这些源码 —— 脚本的还原会覆盖别人的编辑
（`_mutate_lib.guard` 已挂 SIGINT/SIGTERM/atexit，但那只保护自己的变异）。
"""

from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE_PY = ROOT / "storage" / "contacts_store.py"
WS_PY = ROOT / "transport" / "ws_channel.py"
STORE_CS = ROOT / "csharp/contacts/ContactStore.cs"
DUTY_CS = ROOT / "csharp/contacts/ContactDuty.cs"
CONTACT_CS = ROOT / "csharp/UI/ContactPresenter.cs"

PY_BEHAVIOR = ["tests/test_contacts_store.py", "tests/test_contacts_rpc.py"]
PY_CONTRACT = ["tests/test_recent_ordering_contract.py"]


def _env() -> dict:
    return {**os.environ, "PYTHONPATH": str(ROOT.parent)}


def run_pytest(files) -> int:
    """跑若干测试文件。**cwd 必须是 `ROOT.parent`、路径必须带 `agent_loop/` 前缀**
    （理由见 `mutate_check_manual_identity.py` 的同名函数：cwd 会被塞进 `sys.path[0]`，
    命中同名外壳 `agent_loop.py` 而不是包目录 ⇒ 整层在收集阶段就红 ⇒ 假绿）。"""
    return _mutate_lib.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         *[ROOT.name + "/" + f for f in files]],
        cwd=str(ROOT.parent), capture_output=True, text=True, env=_env()).returncode


# (名字, 文件, old, new, 跑哪一层)
MUTATIONS = [
    # ══════════════════ A. 排序判据（最核心）══════════════════
    ("★判据改回「取大」：mtime 盖过真实互动时刻（= 原 bug 原样复发）",
     STORE_CS,
     "            if (last > 0) return last;                       // 有真实互动证据：绝不让 mtime 掺和",
     "            if (_sessionsLast.TryGetValue(npc, out var mm) && mm > last) last = mm;\n"
     "            if (last > 0) return last;",
     "contract"),
    ("★丢掉「有证据就直接返回」的早退（mtime 会掺和进来）",
     STORE_CS,
     "            if (last > 0) return last;                       // 有真实互动证据：绝不让 mtime 掺和",
     "            if (last > 0) return last + 0;                   // 语义等价但先读 mtime",
     "contract"),
    ("★不读服务端持久化的 last（只剩内存时间戳，重启即失）",
     STORE_CS,
     "            if (_serverLast.TryGetValue(npc, out var s) && s > last) last = s;",
     "            if (false) last = 0;",
     "contract"),
    ("★读服务端 last 但立刻被 mtime 覆盖（顺序写错）",
     STORE_CS,
     "            if (last > 0) return last;                       // 有真实互动证据：绝不让 mtime 掺和\n"
     "            if (_sessionsLast.TryGetValue(npc, out var m) && m > 0) return m;   // 兜底",
     "            if (_sessionsLast.TryGetValue(npc, out var m) && m > 0) return m;   // 兜底\n"
     "            if (last > 0) return last;",
     "contract"),
    ("★两个时间列合并成一个（语义不同却共用一格）",
     STORE_CS,
     "            _sessionsLast.Clear();\n            _serverLast.Clear();\n"
     "            if (mtimes != null)",
     "            _sessionsLast.Clear();\n"
     "            if (mtimes != null)",
     "contract"),
    ("★换存档不清服务端 last（上一档时刻带进新档）",
     STORE_CS,
     "            _serverLast.Clear();       // 服务端持久化的互动时刻同理：上一档的名字/时间不该带到新档",
     "",
     "contract"),

    # ══════════════════ B. 两条真回复路径都记 ══════════════════
    ("★主动开口那条不记（用户报的就是这条，等于没修）",
     WS_PY,
     "                            # 互动时间戳：**主动开口也算一次真实互动**（10-21 用户报障的那条路\n"
     "                            # 就是它 —— 横幅弹了、红点亮了，但「最近」里的位置不动）。\n"
     "                            # 与 `handle_message` 同一个位置、同一个理由（发送前先记）。\n"
     "                            self._touch_interaction(npc_id)\n",
     "",
     "contract"),
    ("★玩家消息那条不记（只剩主动开口）",
     WS_PY,
     "                            # 帧一到 C# 就可能立刻开面板，先记才不会读到旧值。\n"
     "                            self._touch_interaction(npc_id)\n",
     "                            # 帧一到 C# 就可能立刻开面板，先记才不会读到旧值。\n",
     "contract"),
    ("★系统提示帧也记成互动（什么都没说的人跳到第一位）",
     WS_PY,
     '        await self.ws.send_event("npc_reply", npc_id=npc_id, text=text, error=True)',
     '        self._touch_interaction(npc_id)\n'
     '        await self.ws.send_event("npc_reply", npc_id=npc_id, text=text, error=True)',
     "contract"),
    ("★先发帧、后记录（帧先到 C#，面板读到旧值）",
     WS_PY,
     "                            self._touch_interaction(npc_id)\n"
     "                            # `error` 必须是真布尔",
     "                            # `error` 必须是真布尔",
     "contract"),
    ("★把记录挪到 send_event 之后（同样是错的顺序）",
     WS_PY,
     '                                "npc_reply", npc_id=npc_id, text=reply,\n'
     "                                error=bool(is_fallback),\n"
     "                            )\n",
     '                                "npc_reply", npc_id=npc_id, text=reply,\n'
     "                                error=bool(is_fallback),\n"
     "                            )\n"
     "                            self._touch_interaction(npc_id)\n",
     "contract"),
    ("★helper 改成直接抛（记录失败炸掉回复路径）",
     WS_PY,
     "        try:\n            fn(npc_id)\n        except Exception:",
     "        try:\n            fn(npc_id)\n        except ZeroDivisionError:",
     "contract"),
    # ══════════════════ C. 落盘 / 隔离 / 缓存 ══════════════════
    ("★记录后不作废会话索引缓存（5s 内读到旧 last）",
     STORE_PY,
     "        self.invalidate_sessions_cache()",
     "        pass",
     "py"),
    ("★touch_interaction 直接写盘（与对话增量的存档语义脱钩）",
     STORE_PY,
     "        self._interactions[name] = sec\n        self._interactions_dirty = True",
     "        self._interactions[name] = sec\n        self._interactions_dirty = True\n"
     "        self.flush_interactions()",
     "py"),
    ("★save_happened 不固化互动时间戳（重启即丢）",
     WS_PY,
     "            self._flush_interactions()",
     "            pass",
     "contract"),
    ("★未定档也记（那段时间不知道属于哪个存档 ⇒ 串档）",
     STORE_PY,
     '        if self.require_world and not self.world_id:\n            return                                  # 未定档：宁可这次不记，也不串档\n',
     "",
     "py"),
    ("★换存档不清互动表（同名 NPC 串号）",
     STORE_PY,
     "            self._interactions = {}\n            self._interactions_loaded = False\n"
     "            self._interactions_dirty = False",
     "            self._interactions_dirty = False",
     "py"),
    ("★不写单调保护（乱序回包把顺序弄反）",
     STORE_PY,
     "        if sec <= prev:\n            return",
     "        if False:\n            return",
     "py"),
    ("★flush 无条件写盘（每次存档都重写一遍，且 dirty 永不落地）",
     STORE_PY,
     "        if not self._interactions_dirty:\n            return 0",
     "        if False:\n            return 0",
     "py"),
    ("★坏 JSON 直接抛（一次坏读让排序永久失效）",
     STORE_PY,
     "        except Exception:\n            log.warning(\"互动时间戳读取失败（%s）→ 本次回落会话文件 mtime 排序\",\n"
     "                        self._interactions_path, exc_info=True)\n            return",
     "        except FileNotFoundError:\n            return",
     "py"),
    ("★list_sessions 不回传 last（C# 永远拿到 0）",
     STORE_PY,
     '                    out.append({"npc_id": npc_id, "mtime": mtime,\n'
     '                                "last": int(self._interactions.get(npc_id, 0))})',
     '                    out.append({"npc_id": npc_id, "mtime": mtime})',
     "py"),
    ("★last 与 mtime 同源（回传 mtime 冒充 last）",
     STORE_PY,
     '                                "last": int(self._interactions.get(npc_id, 0))})',
     '                                "last": mtime})',
     "py"),
    ("★C# 解析 last 时读错键（静默全 0，看起来完全正常）",
     DUTY_CS,
     '                        long l = t?["last"]?.Value<long>() ?? 0;',
     '                        long l = t?["lastAt"]?.Value<long>() ?? 0;',
     "contract"),
    ("★C# 把 last 塞进 mtime 那一列（两列语义混掉）",
     DUTY_CS,
     "                        if (m > 0) mtimes.Add(new KeyValuePair<string, long>(npc, m));",
     "                        if (l > 0) { mtimes.Add(new KeyValuePair<string, long>(npc, l)); continue; }",
     "contract"),
    # ══════════════════ F. 分层（模拟复盘发现的真缺陷）══════════════════
    ("★「最近」排序不分层（刚存档就让没说过话的老 NPC 顶上来）",
     CONTACT_CS,
     "                recent.Sort((a, b) =>\n"
     "                {\n"
     "                    bool ha = ContactStore.HasInteraction(a.Name);\n"
     "                    bool hb = ContactStore.HasInteraction(b.Name);\n"
     "                    if (ha != hb) return ha ? -1 : 1;          // 已知永远在未知之前\n",
     "                recent.Sort((a, b) =>\n"
     "                {\n"
     "                    bool ha = true;\n"
     "                    bool hb = true;\n"
     "                    if (ha != hb) return ha ? -1 : 1;          // 已知永远在未知之前\n",
     "contract"),
    ("★分层方向反了（把「未知」排到「已知」前面）",
     CONTACT_CS,
     "                    if (ha != hb) return ha ? -1 : 1;          // 已知永远在未知之前",
     "                    if (ha != hb) return ha ? 1 : -1;",
     "contract"),
    ("★HasInteraction 把 mtime 也算成「有互动」（分层当场失效）",
     STORE_CS,
     "            if (_localLast.TryGetValue(npc, out t) && t > 0) return true;\n"
     "            return false;",
     "            if (_localLast.TryGetValue(npc, out t) && t > 0) return true;\n"
     "            if (_sessionsLast.TryGetValue(npc, out t) && t > 0) return true;\n"
     "            return false;",
     "contract"),
    ("★并列时不定序（列表每次重渲都可能抖）",
     CONTACT_CS,
     "                    return string.CompareOrdinal(a.Name ?? \"\", b.Name ?? \"\");",
     "                    return 0;",
     "contract"),
    ("★落盘路径与 contacts.json 混用（读侧契约被掺进第二类数据）",
     STORE_PY,
     'INTERACTIONS_FILE = "interactions.json"',
     'INTERACTIONS_FILE = "contacts.json"',
     "py"),
]


LAYER_RUNNER = {"py": lambda: run_pytest(PY_BEHAVIOR),
                "contract": lambda: run_pytest(PY_CONTRACT)}


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
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    cases = [c for c in MUTATIONS if not only or c[4] == only]
    if not cases:
        print("没有匹配的变异（可用层：py / contract）")
        return 2

    files = sorted({c[1] for c in cases} | {STORE_PY})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    backup = {p: p.read_bytes() for p in files}
    before = {p: _md5(p) for p in files}

    def restore_all():
        for p in files:
            p.write_bytes(backup[p])

    # ── 脚手架自检：注入一个必然被发现的错 ──────────────────────────────
    # 用"touch_interaction 变成 no-op"：它会让「最近」永远停在 mtime，
    # 而 `test_list_sessions_prefers_last_over_mtime` 直接读数据，必然报红。
    print("=== 脚手架自检（touch_interaction 变 no-op） ===")
    if not _apply(STORE_PY,
                  "        sec = int(ts if ts is not None else time.time())",
                  "        return\n        sec = int(ts if ts is not None else time.time())"):
        print("自检失败：锚点失效，脚本已过期")
        return 2
    rc = run_pytest(PY_BEHAVIOR)
    restore_all()
    if rc == 0:
        print("★ 自检失败：no-op 注入竟然没报红 → 脚手架不可信，中止")
        return 2
    print("自检通过（no-op 注入被抓到）\n")

    # ── 基线自检：每层先在未变异树上跑一遍，必须绿 ────────────────────
    # 判定规则是"这一层红了就算抓到"，所以任何与变异无关的红都会被记成 ✅。
    print("=== 基线自检（未变异树上每层必须先绿） ===")
    for layer in sorted({c[4] for c in cases}):
        rc = LAYER_RUNNER[layer]()
        if rc != 0:
            print("★ 基线不绿 [%s] → 后面所有『抓到』都不可信，中止" % layer)
            return 2
        print("  基线绿 [%s]" % layer)
    print()

    caught = skipped = missed = 0
    for name, path, old, new, layer in cases:
        if not _apply(path, old, new):
            skipped += 1
            continue
        rc = LAYER_RUNNER[layer]()
        path.write_bytes(backup[path])
        if rc != 0:
            caught += 1
            print("  ✅ 抓到 [%s] %s" % (layer, name))
        else:
            missed += 1
            print("  ❌ 漏掉 [%s] %s  ← 这个测试是假绿" % (layer, name))

    bad = [p for p in files if _md5(p) != before[p]]
    restore_all()
    print()
    print("汇总：抓到 %d / 跳过 %d / 漏掉 %d（共 %d 条）" % (caught, skipped, missed, len(cases)))
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
