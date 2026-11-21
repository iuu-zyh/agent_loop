"""增量落盘（11-15）—— 存档不再全量重写整个账本。

## 为什么改

用户："存档为什么是全量重写？这样时间消耗太大了"。

全量重写是 `open(path, "w")` 把整份 log 重新序列化。成本 **O(全部)**，而存档是**同步**
发生在游戏主线程那次 `save_happened` 里：

    轮数    文件大小    全量重写     纯追加
    200     0.63M      4.7ms      0.08ms
    1000    3.16M     26.4ms      0.19ms
    4000   12.67M     91.3ms      0.13ms

20 个 NPC × 1000 轮 ≈ 半秒 ⇒ 玩家"一按存档就卡一下"，越玩越久越卡。

能改成追加的根本原因：**账本只追加不改写**（删除=追注记、压缩=追 replace，
既有事件一个字段都不动）。既然内存里的 log 只往后长，重写整份就是纯浪费。

## 这个文件守什么

增量落盘最坏的失败不是"慢"，而是**静默错位**：把新增事件接到一份
**不是同一条账本前缀**的文件尾部，于是新旧两段历史拼在一起，不报错、只是内容错乱。
所以每条 `_try_append` 的放弃条件都要单独钉住（任何一条不满足都必须退回全量重写）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from agent_loop.session.session import Session                        # noqa: E402
from agent_loop.storage.persistence import (                          # noqa: E402
    load_session, save_session, session_path_for,
)

TMP = os.path.join(tempfile.gettempdir(), "persist_append")


def _fresh():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    return TMP


def _mk(npc: str, turns: int, start: int = 0) -> Session:
    s = Session(npc)
    for t in range(start, start + turns):
        s.append("user/message", {"role": "user", "id": f"u{t}",
                                  "content": [{"type": "text", "text": f"第{t}问"}]},
                 {"surfaceOp": "append"})
        s.append("assistant/message", {"message": {"role": "assistant", "id": f"a{t}",
                                                   "content": [{"type": "text",
                                                                "text": f"第{t}答"}]}},
                 {"surfaceOp": "append"})
    return s


def _disk_events(npc: str) -> int:
    p = session_path_for(npc, TMP)
    with open(p, encoding="utf-8") as f:
        return sum(1 for ln in f if ln.strip() and not ln.startswith('{"session"'))


# ============================================================ ① 基本行为

def test_first_save_is_full_and_later_saves_append():
    """首落全量（没有旧内容可接），之后只写新增。"""
    _fresh()
    s = _mk("首落", 5)
    r1 = save_session(s, TMP)
    assert r1["mode"] == "full", r1

    for t in range(5, 8):
        s.append("user/message", {"role": "user", "id": f"u{t}",
                                  "content": [{"type": "text", "text": f"第{t}问"}]},
                 {"surfaceOp": "append"})
    r2 = save_session(s, TMP)
    assert r2["mode"] == "append", r2
    assert r2["written"] == 3, f"应只写 3 条新增，实际 {r2}"

    # 读回必须完全一致
    back = load_session("首落", TMP)
    assert len(back.log) == len(s.log)
    assert back.log[-1]["data"]["content"][0]["text"] == "第7问"


def test_second_save_without_changes_writes_nothing():
    """没有新事件时必须 `written=0`（存档按钮连点也不该反复写盘）。"""
    _fresh()
    s = _mk("无变化", 5)
    save_session(s, TMP)
    r = save_session(s, TMP)
    assert r["mode"] == "append" and r["written"] == 0, r


def test_append_is_much_cheaper_than_rewrite():
    """增量必须显著快于全量（这是改动的全部意义）。

    判据用**相对倍数**：1000 轮账本（约 3MB）追加与全量的耗时比。
    追加是 O(新增)、全量是 O(全部)，长账本上差 1~2 个数量级。
    """
    _fresh()
    s = _mk("性能", 1000)
    save_session(s, TMP)                       # 先把全量落一次

    s.append("user/message", {"role": "user", "id": "new",
                              "content": [{"type": "text", "text": "新的一条"}]},
             {"surfaceOp": "append"})
    t0 = time.perf_counter()
    save_session(s, TMP)                       # 追加
    append_ms = time.perf_counter() - t0

    t0 = time.perf_counter()
    save_session(s, TMP, append=False)         # 强制全量
    full_ms = time.perf_counter() - t0

    assert append_ms * 3 < full_ms, (
        f"追加 {append_ms*1000:.2f}ms vs 全量 {full_ms*1000:.2f}ms —— 增量没生效？")


# ============================================================ ② 每条放弃条件

def test_truncated_file_is_rebuilt():
    """文件被截短（行数 < 内存）⇒ 追加是对的（那确实是前缀），但读回必须完整。"""
    _fresh()
    s = _mk("截断", 10)
    save_session(s, TMP)
    p = session_path_for("截断", TMP)
    lines = open(p, encoding="utf-8").readlines()
    open(p, "w", encoding="utf-8").writelines(lines[:6])     # header + 5 条
    save_session(s, TMP)
    assert len(load_session("截断", TMP).log) == len(s.log), "截断后没补全"


def test_swapped_ledger_falls_back_to_full_rewrite():
    """★ 最危险的一种：文件被换成**另一份** seq 同样连续的账本 ★

    只校验 seq 是**不够**的（那种情况下 seq 完全连续），会把两段历史拼在一起、
    不报错、只是内容错乱。所以还要比最后一条的**内容指纹**。
    """
    _fresh()
    s = _mk("换账本", 10)
    save_session(s, TMP)
    p = session_path_for("换账本", TMP)

    # 造一份"别人的"账本，seq 也从 0 开始连续
    other = Session("换账本")
    for t in range(6):
        other.append("user/message", {"role": "user", "id": f"x{t}",
                                      "content": [{"type": "text", "text": f"别人的{t}"}]},
                     {"surfaceOp": "append"})
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"session": {"id": "换账本", "header": s.header}},
                           ensure_ascii=False) + "\n")
        for i, ev in enumerate(other.log[:6]):
            e = dict(ev)
            e["seq"] = i
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    r = save_session(s, TMP)
    assert r["mode"] == "full", f"没识破换账本（会静默错位）：{r}"
    back = load_session("换账本", TMP)

    def _text(ev):
        """取一条事件的正文（user/message 把消息放在 data 本体，
        assistant/message 放在 data.message —— 两种形状都要认）。"""
        d = ev.get("data") or {}
        msg = d.get("message") if isinstance(d.get("message"), dict) else d
        for b in (msg.get("content") or []):
            if isinstance(b, dict) and b.get("type") == "text":
                return str(b.get("text") or "")
        return ""

    assert all(_text(e).startswith("第") for e in back.log), \
        f"读回的内容仍混着别人的账本：{[_text(e) for e in back.log[:4]]}"


def test_half_written_tail_falls_back_to_full_rewrite():
    """上次写一半（最后一行是**残 JSON**）⇒ 必须全量重写。

    注意这条其实是**冗余防线**：残 JSON 在 `json.loads` 那一步就失败了，seq 校验会先拦住。
    真正只能靠"尾部换行检查"的是下一条（合法 JSON 但缺换行）。
    """
    _fresh()
    s = _mk("半行", 5)
    save_session(s, TMP)
    p = session_path_for("半行", TMP)
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"seq":999,"type":"broken"')          # 故意不补换行
    r = save_session(s, TMP)
    assert r["mode"] == "full", f"半行没被识别：{r}"
    back = load_session("半行", TMP)
    assert len(back.log) == len(s.log), "半行污染了账本"
    assert back.log[-1]["seq"] == len(s.log) - 1


def test_legal_json_without_trailing_newline_falls_back():
    """★ 只有"尾部换行检查"能拦住的一种 ★

    文件末尾是一条**完全合法的 JSON**、但**没有换行结尾**：
      · `json.loads` 会成功 ⇒ seq 校验、指纹校验**都会通过**；
      · 若无换行检查，追加写入会把它和下一个事件**粘成同一行**
        → 那一行变成非法 JSON → **整份账本从那里往后读不出来**（静默损坏）。
    真实成因：写盘过程中被强杀/断电，恰好停在"写完 JSON 但还没写换行"那一字节。
    """
    _fresh()
    s = _mk("缺换行", 5)
    save_session(s, TMP)
    p = session_path_for("缺换行", TMP)
    raw = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(raw.rstrip("\n"))     # 去掉末尾换行

    # 前提确认：末行仍是合法 JSON，所以别的校验拦不住
    tail = open(p, encoding="utf-8").read().splitlines()[-1]
    assert json.loads(tail)["seq"] == len(s.log) - 1, "前提：末行是合法 JSON"

    r = save_session(s, TMP)
    assert r["mode"] == "full", f"合法 JSON 但缺换行时没退回全量 —— 追加会粘成坏行：{r}"

    # 每一行都必须能单独解析（否则账本从此读不出来）
    with open(p, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                json.loads(ln)          # 抛异常即失败
    assert len(load_session("缺换行", TMP).log) == len(s.log)


def test_file_longer_than_memory_falls_back():
    """磁盘事件数**多于**内存 ⇒ 文件不是内存账本的前缀 ⇒ 必须全量重写。

    成因：同一份账本被两个进程先后写过（旧进程没退干净），或读档后手工删过内存事件。
    若不加这条判断，会按 `log_events[k:]` 切片 —— 而 `k > len(log)` 时切片为空，
    于是"什么都没写"却报告成功，磁盘上那份多出来的尾巴**永远清不掉**。
    """
    _fresh()
    s = _mk("超长", 3)
    save_session(s, TMP)
    p = session_path_for("超长", TMP)

    with open(p, "a", encoding="utf-8") as f:
        for i in range(5):
            f.write(json.dumps({"seq": len(s.log) + i, "type": "user/message",
                                "data": {"role": "user", "id": f"extra{i}",
                                         "content": [{"type": "text", "text": "多出来的"}]}},
                               ensure_ascii=False) + "\n")
    assert _disk_events("超长") == len(s.log) + 5

    r = save_session(s, TMP)
    assert r["mode"] == "full", f"文件比内存长时没退回全量：{r}"
    assert _disk_events("超长") == len(s.log), "多出来的尾巴没被清掉"


def test_interrupted_closer_is_not_appended_repeatedly():
    """读档补的 `turn/end:interrupted` 只能落一次 —— 反复存不能让账本无限长。

    `load_session` 会往内存事件表**尾部补一条**（磁盘上本来没有）。如果落盘时
    不加判断地"把内存全写一遍"，每次读档+存档就会多一条，账本会缓慢膨胀。
    行数游标天然解决它：补的那条只写一次，之后磁盘行数与内存对齐 ⇒ `up-to-date`。
    """
    _fresh()
    s = Session("崩溃")
    s.append("turn/start", {"turn": 1})
    s.append("user/message", {"role": "user", "id": "u1",
                             "content": [{"type": "text", "text": "话"}]},
             {"surfaceOp": "append"})
    save_session(s, TMP)                    # 磁盘上：回合未闭合（模拟崩溃）
    n0 = _disk_events("崩溃")

    loaded = load_session("崩溃", TMP)       # 读回时会补 turn/end
    assert len(loaded.log) == n0 + 1, "前提：读回应补一条 turn/end"

    save_session(loaded, TMP)
    assert _disk_events("崩溃") == n0 + 1, "补的那条落盘了"
    for _ in range(3):
        save_session(loaded, TMP)
    assert _disk_events("崩溃") == n0 + 1, "反复存档把账本写长了（重复追加）"


def test_atomic_full_rewrite_leaves_no_partial_file():
    """全量重写走临时文件 + 原子替换：中途崩溃不留半份账本。

    旧实现直接 `open("w")`，写到一半崩 = 整份历史变半截，那比"没存上"更糟。
    """
    _fresh()
    s = _mk("原子", 5)
    save_session(s, TMP)
    p = session_path_for("原子", TMP)
    save_session(s, TMP, append=False)
    assert p.exists()
    assert not p.with_suffix(p.suffix + ".tmp").exists(), "临时文件没清掉"
    assert len(load_session("原子", TMP).log) == len(s.log)


# ============================================================ ③ 与真实存档路径一致

def test_flush_all_uses_append_on_the_second_save():
    """`AgentLoop.flush_all`（真实存档钩子）第二次起必须走增量。"""
    from agent_loop.agent_loop import AgentLoop
    from agent_loop.llm.stub_client import StubLlmClient
    from agent_loop.transport.bridge import StubGameBridge

    shutil.rmtree(TMP, ignore_errors=True)
    bridge = StubGameBridge()
    NPC = "存档测试"
    bridge.seed_unit(NPC, {"realm": "元婴", "player": {
        "name": "韩立", "relation": "道友", "intim": 60, "same_grid": True}})
    AgentLoop.reset_for_test(storage_root=TMP, bridge=bridge,
                             llm=StubLlmClient(fn=lambda req: {"text": "好", "tool_calls": []}))
    loop = AgentLoop()
    agent = loop.create(NPC)
    for i in range(5):
        agent.session.append("user/message", {"role": "user", "id": f"u{i}",
                                              "content": [{"type": "text", "text": "话"}]},
                             {"surfaceOp": "append"})
    loop.flush_all()                       # 首次：全量
    n1 = _disk_events(NPC)

    agent.session.append("user/message", {"role": "user", "id": "new",
                                          "content": [{"type": "text", "text": "新"}]},
                         {"surfaceOp": "append"})
    loop.flush_all()                       # 第二次：应增量
    assert _disk_events(NPC) == n1 + 1, "flush_all 没走增量（或写多了）"
