"""project_ui_history 的开销解剖：到底扫了几遍账本、耗时多少。

    cd /mnt/f && PYTHONPATH=/mnt/f python3 agent_loop/scripts/dev/probe_history_cost.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agent_loop.agent_loop import AgentLoop            # noqa: E402
from agent_loop.transport.bridge import StubGameBridge           # noqa: E402
from agent_loop.session.history import project_ui_history      # noqa: E402
from agent_loop.llm.echo_client import EchoLlmClient   # noqa: E402

ROOT = "/tmp/agent_loop_probe_cost"
NPC, G1 = "林婉清", "g1"


class CountingLog(list):
    """数一遍：有谁在遍历 session.log。"""

    def __init__(self, *a):
        super().__init__(*a)
        self.iter_count = 0
        self.yielded = 0

    def __iter__(self):
        self.iter_count += 1
        for x in super().__iter__():
            self.yielded += 1
            yield x


def seed():
    b = StubGameBridge()
    b.seed_unit(NPC, {
        "realm": "金丹", "pos": "永宁州", "mood": 72,
        "player": {"name": "韩立", "realm": "筑基", "relation": "好友",
                   "intim": 120, "same_grid": True},
        "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"},
    })
    return AgentLoop.reset_for_test(storage_root=ROOT, bridge=b, llm=EchoLlmClient())


def build(n_turns):
    loop = seed()
    a = loop.create(NPC)
    for i in range(n_turns):
        a.set_scene(G1 if i % 3 == 1 else None)      # 每 3 轮插一个群聊回合
        a.send(f"第{i}问")
        asyncio.run(a.run_until_idle())
    a.set_scene(None)
    return loop, a


def main():
    print("═" * 78)
    print("① 谁在遍历 session.log —— 用计数代理实测")
    print("═" * 78)
    loop, a = build(6)
    cnt = CountingLog(a.session.log)
    a.session.log = cnt
    before_iter, before_yield = cnt.iter_count, cnt.yielded
    out = project_ui_history(a.session, max_turns=0, scene="")
    print(f"  账本事件数 = {len(cnt)}")
    print(f"  project_ui_history 触发了 {cnt.iter_count - before_iter} 次遍历，"
          f"共产出 {cnt.yielded - before_yield} 个事件")
    print(f"  → 遍历次数 × 账本长度 = {(cnt.iter_count - before_iter)} × {len(cnt)}"
          f" = {cnt.yielded - before_yield}")
    print(f"  返回 items = {len(out['items'])}")

    print()
    print("═" * 78)
    print("② 各阶段耗时（账本规模递增）")
    print("═" * 78)
    print(f"  {'回合数':>6} {'事件数':>7} {'items':>6} {'完整投影':>12} {'每次事件':>12}")
    for n in (10, 50, 200, 500):
        loop2, a2 = build(n)
        t0 = time.perf_counter()
        o = project_ui_history(a2.session, max_turns=0, scene="")
        dt = time.perf_counter() - t0
        ev = len(a2.session.log)
        print(f"  {n:>6} {ev:>7} {len(o['items']):>6} {dt * 1000:>10.3f} ms "
              f"{dt / ev * 1e6:>10.3f} µs")

    print()
    print("═" * 78)
    print("③ 同一份账本：只算 items 摊平（第二阶段的真实成本）")
    print("═" * 78)
    loop3, a3 = build(500)
    t0 = time.perf_counter()
    project_ui_history(a3.session, max_turns=0, scene="")
    full = time.perf_counter() - t0
    # 直接调一次"只扫描不摊平"的近似：用 scene='*' 之外的方式不好隔离，
    # 改为测「同一账本重复调用」的稳定值 + 与一次纯遍历的比值
    t0 = time.perf_counter()
    n = 0
    for _ in a3.session.log:
        n += 1
    raw = time.perf_counter() - t0
    print(f"  账本 {n} 个事件")
    print(f"  纯遍历一遍            = {raw * 1000:.3f} ms")
    print(f"  完整投影              = {full * 1000:.3f} ms")
    print(f"  投影 / 纯遍历          = {full / raw:.2f}×")
    print("  （投影做两遍遍历 + 造 item dict + 过滤 + 摊平，所以必然 > 2×）")

    print()
    print("═" * 78)
    print("④ 量级对照：这条路在真实请求里值多少")
    print("═" * 78)
    print(f"  一次投影（500 回合）≈ {full * 1000:.2f} ms")
    print(f"  一次 LLM 回合（实机 TTFT 4~13s，见 ChatPresenter 注释）≈ 4000~13000 ms")
    print(f"  → 投影占一次回合的 {full / 8.0 * 100:.4f}% ~ {full / 4.0 * 100:.4f}%")


if __name__ == "__main__":
    main()
