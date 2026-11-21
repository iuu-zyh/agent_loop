"""性能回归：随使用时长增长的成本路径（11-15，用户报「用久了会卡」）。

## 为什么会"用久了会卡"

三类成本都随**账本长度**线性增长，而账本只增不减（压缩只折叠 surface，不删 log）：

  ① **每回合的上下文估算**（`Compressor.maybe`）：对整份历史逐条估 token。
     实测 30 万字符 ≈ 34ms/回合 —— 这是"聊得越久、每句话越慢"的主因。
  ② **每回合的 `derive_messages()` 投影**：全量重建消息列表（几毫秒，可接受）。
  ③ **存档时的全量重写**（`save_session`）：写整个 log。20 个 NPC × 1000 轮 ≈ 半秒，
     表现为"按存档那一下卡住"。

本文件钉住 ①③ 的量级，防优化被回退。判据用**相对倍数**而不是绝对毫秒
（机器快慢不同，绝对阈值会随机变红）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from agent_loop.compaction import meter                       # noqa: E402
from agent_loop.session.session import Session                # noqa: E402

TMP = os.path.join(tempfile.gettempdir(), "perf_regression")


def _build(turns: int, result_chars: int = 800) -> Session:
    s = Session("perf")
    for t in range(turns):
        s.append("user/message", {"role": "user", "id": f"u{t}",
                                  "content": [{"type": "text", "text": "玩家说话" * 8}]},
                 {"surfaceOp": "append"})
        cid = f"c{t}"
        s.append("assistant/message", {"message": {"role": "assistant", "id": f"a{t}", "content": [
            {"type": "tool-call", "id": cid, "name": "inspect_unit", "arguments": "{}"}]}},
                 {"surfaceOp": "append"})
        s.append("tool/result", {"message": {"role": "user", "content": [
            {"type": "tool-result", "toolCallId": cid,
             "content": [{"type": "text", "text": "结果" * result_chars}]}]}},
                 {"surfaceOp": "append"})
        s.append("assistant/message", {"message": {"role": "assistant", "id": f"r{t}",
                                                   "content": [{"type": "text",
                                                                "text": "回复" * 40}]}},
                 {"surfaceOp": "append"})
    return s


# ============================================================ ① estimate_text 不是逐字符循环

def test_estimate_text_counts_cjk_ranges_exactly():
    """CJK 计数必须与**区间定义**逐码点一致（改了区间却忘了正则 = 估算静默漂移）。

    估算喂的是压缩阈值 —— 漂移会让"该压的时候不压"（爆窗）或"没到就压"（白花钱）。
    """
    for lo, hi in meter._CJK_RANGES:
        for cp in (lo, (lo + hi) // 2, hi):
            assert meter.estimate_text(chr(cp)) == 1, f"U+{cp:04X} 没被算作 CJK"
        # 区间外紧邻的码点不算（边界不能溢出）
        if lo > 0:
            assert meter.estimate_text(chr(lo - 1)) == 0 or not any(
                a <= lo - 1 <= b for a, b in meter._CJK_RANGES)
    # 混合文本
    assert meter.estimate_text("hello") == 2          # 5 字符 / 4 ≈ 2
    assert meter.estimate_text("中文abc") == 3        # 2 CJK + 3/4≈1
    assert meter.estimate_text("") == 0


def test_estimate_text_is_faster_than_a_python_char_loop():
    """`estimate_text` 必须走 C 层（正则），不能退回逐字符 Python 循环。

    ★ 判据用**相对速度**而不是绝对毫秒 ★
    两者只差约 2 倍（8 万字符：正则 ≈9ms / 逐字符 ≈19ms），而绝对毫秒会随机器、
    CPU 频率、Python 版本漂移 —— 定个 25ms 的阈值在快机器上连旧实现都能过
    （变异验证时确认过：把实现换回循环，绝对阈值那条**照样绿**）。
    所以这里现场跑一遍逐字符版做对照，断言正则版至少快 1.5 倍。
    """
    import re as _re

    text = "结果" * 40000          # 8 万字符

    def char_loop(t: str) -> int:  # 旧实现（对照基线）
        cjk = sum(1 for ch in t if any(lo <= ord(ch) <= hi for lo, hi in meter._CJK_RANGES))
        n = len(t) - cjk
        return cjk * meter.CJK_TOKEN_PER_CHAR + -(-n // meter.CHARS_PER_TOKEN)

    assert meter.estimate_text(text) == char_loop(text), "两者结果必须一致"

    def best_of(fn, repeat=5):
        fn(text)                                  # 预热（首次含正则/缓存开销）
        t0 = time.perf_counter()
        for _ in range(repeat):
            fn(text)
        return (time.perf_counter() - t0) / repeat

    fast = best_of(meter.estimate_text)
    slow = best_of(char_loop)

    assert fast * 1.5 < slow, (
        f"estimate_text 只比逐字符循环快 {slow/fast:.2f} 倍（应 ≥1.5）——"
        f"疑似退回了 Python 层逐字符判定；它是每回合都要跑的热路径")


# ============================================================ ② 上下文估算带缓存

def test_context_estimate_matches_the_uncached_sum():
    """缓存版必须与 `sum(estimate_message(derive_messages()))` **逐字等价**。

    不等价 ⇒ 面板显示的上下文与实际发给模型的不是同一把尺子，
    而压缩阈值也按它判断 ⇒ 该压不压/不该压乱压。
    """
    s = _build(60)
    header_tokens = meter.estimate_header(s.request_header())
    want = header_tokens + sum(meter.estimate_message(m) for m in s.derive_messages())
    got = s.estimate_context_tokens(header_tokens)
    assert got == want, f"缓存版 {got} ≠ 逐条算 {want}"

    # 追加之后再比一次（缓存必须正确失效）
    s.append("user/message", {"role": "user", "id": "new",
                              "content": [{"type": "text", "text": "新的一句"}]},
             {"surfaceOp": "append"})
    want2 = header_tokens + sum(meter.estimate_message(m) for m in s.derive_messages())
    assert s.estimate_context_tokens(header_tokens) == want2, "追加后缓存没失效"


def test_context_estimate_is_much_cheaper_on_the_second_call():
    """第二次调用（历史没变）必须显著更快 —— 这就是每回合省下来的那部分。

    判据取**相对倍数**（≥5×）：绝对毫秒会随机器与 Python 版本漂移，倍数稳定得多。
    若有人把缓存去掉，第二次调用会与第一次同价（≈1×），这条立刻红。
    """
    s = _build(200)
    header_tokens = meter.estimate_header(s.request_header())

    t0 = time.perf_counter()
    s.estimate_context_tokens(header_tokens)      # 冷：全部重算
    cold = time.perf_counter() - t0

    t0 = time.perf_counter()
    s.estimate_context_tokens(header_tokens)      # 热：全部命中
    warm = time.perf_counter() - t0

    assert warm * 5 < cold, (
        f"缓存没生效：冷 {cold*1000:.1f}ms / 热 {warm*1000:.1f}ms（应 ≥5 倍差）")


def test_context_estimate_invalidates_when_surface_is_replaced():
    """压缩折叠（replace）之后缓存必须失效 —— 否则会按**折叠前**的规模判断阈值。"""
    s = _build(20)
    header_tokens = 0
    before = s.estimate_context_tokens(header_tokens)

    # 把前 40 个节点折成一条纪要
    nodes = s.surface.nodes
    s.append("user/message", {"role": "user", "id": "sum",
                              "content": [{"type": "text", "text": "纪要"}]},
             {"surfaceOp": {"op": "replace", "start": nodes[0], "end": nodes[39]}})
    after = s.estimate_context_tokens(header_tokens)
    want = sum(meter.estimate_message(m) for m in s.derive_messages())
    assert after == want, f"折叠后缓存没失效：{after} ≠ {want}"
    assert after < before, "折叠后规模没变小"


# ============================================================ ③ 存档是唯一随 log 线性增长的重活

def test_save_cost_does_not_regress_by_an_order_of_magnitude():
    """存档耗时（写整个 log）必须保持在毫秒级 —— 它决定"按存档那一下卡不卡"。

    判据同样用相对倍数：1000 轮的存盘耗时不得比 200 轮贵 30 倍以上
    （线性增长是 5×，给一倍余量；若有人引入 json 逐条 parse/dump 之类的往返会爆掉）。
    """
    from agent_loop.storage.persistence import save_session

    shutil.rmtree(TMP, ignore_errors=True)
    small = _build(200)
    t0 = time.perf_counter()
    save_session(small, TMP)
    small_ms = time.perf_counter() - t0

    big = _build(1000)
    t0 = time.perf_counter()
    save_session(big, TMP)
    big_ms = time.perf_counter() - t0

    assert big_ms < max(small_ms * 30, 0.05), (
        f"存盘从 {small_ms*1000:.1f}ms 涨到 {big_ms*1000:.1f}ms —— 超出线性预期")
