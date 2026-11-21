"""历史游标翻页（11-21）—— 协议契约测试。

背景：用户报「消息一多就很卡」，并指出根因不是渲染而是**每次都把全部历史传给客户端**。
方案是服务端只发最近 N 个回合 + 客户端上划时按游标请求更早的。

这个文件锁住三件事：
  ① 游标翻页必须能**逐条重建**全量历史（不重、不漏、顺序一致）；
  ② 无 seq 的合成回合不许每页重复返回（这是第一版实测踩到的真 bug）；
  ③ 两个 RPC（open_chat / get_history）都必须把游标透传下去。
"""

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from agent_loop.session.history import project_ui_history  # noqa: E402

_LEDGER = Path("/mnt/c/Users/<用户名>/.sessions/worlds/BOlQu6/%E5%A7%9C%E8%90%8C.jsonl")


# ---------------------------------------------------------------- 测试账本

class _Session:
    """最小 session 壳：`project_ui_history` 只读 `.log`。"""

    def __init__(self, log):
        self.log = log
        self.id = "测试"


def _load_real():
    """真机账本（姜萌，43 个单聊回合）。没有就跳过 —— 这机器上通常有。"""
    if not _LEDGER.exists():
        pytest.skip("真机账本不存在（换机器时跳过，其余用例仍然覆盖逻辑）")
    raw = [json.loads(l) for l in _LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    return _Session(raw[1:])   # 第 0 条是 header


def _synth(turn, seq_from, n_items=3, scene=""):
    """造一个回合：turn/start + step/start + n 条 user/message + turn/end。

    ⚠ 事件形状必须与真机账本一致（`_LEDGER` 实证）：
      · `user/message` 的正文在 **`data.content`**（不是 `data.message.content` ——
        那是 `assistant/message` 的形状）；
      · **必须有一条 `step/start`**：缺了它 `step_no` 走的是另一条路径，
        且实测会让整个回合的条目全部丢掉（第一版合成数据就漏了这条，产出 0 条目）。
    """
    evs = [
        {"seq": seq_from, "type": "turn/start", "data": {"turn": turn, "scene": scene}},
        {"seq": seq_from + 1, "type": "step/start", "data": {"step": 1}},
    ]
    for k in range(n_items):
        evs.append({"seq": seq_from + 2 + k, "type": "user/message",
                    "data": {"role": "user",
                             "content": [{"type": "text", "text": f"t{turn}-{k}"}]}})
    evs.append({"seq": seq_from + 2 + n_items, "type": "turn/end", "data": {"reason": {}}})
    return evs


def _synth_session(specs):
    """specs: [(turn, seq_from, n_items)] → session"""
    log = []
    for turn, seq_from, n in specs:
        log += _synth(turn, seq_from, n)
    return _Session(log)


def _key(item):
    return (item.get("kind"), item.get("text", "")[:80], item.get("seq"), item.get("turn"))


def _paginate(sess, page):
    """模拟 C# 的真实行为：第 1 页铺底，之后每页**插到最前**（= `LoadEarlier` 的顺序）。"""
    before_seq, before_turn = None, None
    acc = []
    for _ in range(200):
        out = project_ui_history(sess, max_turns=page, scene="",
                                 before_seq=before_seq, before_turn=before_turn)
        acc = [_key(i) for i in out["items"]] + acc
        nb, nt = out.get("next_before_seq") or 0, out.get("next_before_turn") or 0
        if nb <= 0 and nt <= 0:
            break
        before_seq, before_turn = nb, nt
    return acc


# ---------------------------------------------------------------- ① 重建完整性

def test_paging_rebuilds_full_history_exactly_on_real_ledger():
    """★ 核心契约 ★ 任意页长下，翻页都能逐条重建全量历史，且顺序一致。

    这是整个方案的地基：如果翻页会漏一条或重一条，用户上划就会看到
    "某句话凭空消失"或"同一句话出现两次" —— 比卡顿更严重。
    """
    sess = _load_real()
    base = [_key(i) for i in project_ui_history(sess, max_turns=0, scene="")["items"]]
    assert len(base) > 100, f"真机账本应该有足够多的条目，实际 {len(base)}"

    for page in (5, 10, 20, 40, 58, 100):
        got = _paginate(sess, page)
        assert got == base, f"每页 {page} 回合时，翻页结果与全量不一致（{len(got)} vs {len(base)}）"


def test_paging_never_loses_items_on_synthetic_ledger():
    """合成账本上也必须逐条一致（不依赖真机数据）。"""
    specs = [(14 + i, 200 + i * 20, 2 + (i % 3)) for i in range(30)]
    sess = _synth_session(specs)
    base = [_key(i) for i in project_ui_history(sess, max_turns=0, scene="")["items"]]
    for page in (1, 3, 7, 30):
        assert _paginate(sess, page) == base, f"每页 {page} 回合时不一致"


# ---------------------------------------------------------------- ② 无 seq 回合

def test_turn_without_seq_is_not_returned_twice():
    """★ 实测踩到的真 bug ★ 整回合没有 seq 时，不许每页重复返回。

    第一版游标只按 seq 判：
        `该回合最小 seq < 游标` ⇒ 无 seq 回合被当成"永远更早"⇒ 每页都返回。
    真机症状（姜萌 43 回合）：第 1 页 `[38..58]`、第 2 页 `[18..42]`，
    turn 38 与 42 在两页里都出现 —— 正是这两个回合没有 seq。

    修法：游标成对携带 `before_turn`，判据优先用回合号（见 `_turn_before_cursor`）。
    """
    # 造一个"中间某回合没有任何 seq"的账本：turn 20 的条目全部不带 seq
    log = []
    seq = 100
    for turn in range(10, 33):
        if turn == 20:
            # 合成回合：无 seq（模拟 initiative / turn_error）
            log.append({"type": "turn/start", "data": {"turn": turn, "scene": ""}})
            log.append({"type": "step/start", "data": {"step": 1}})
            log.append({"type": "turn/end", "data": {"reason": {}}})
            continue
        log += _synth(turn, seq, 2)
        seq += 10
    sess = _Session(log)

    base = [_key(i) for i in project_ui_history(sess, max_turns=0, scene="")["items"]]
    got = _paginate(sess, 3)
    assert got == base, "无 seq 回合导致翻页重复/遗漏"

    # 直接查：turn 20 那一回合在翻页结果里只该出现一次
    turns = [k[3] for k in got if isinstance(k[3], int)]
    # 用 per-turn item 计数判断（每回合条数固定）
    from collections import Counter
    c = Counter(turns)
    for t, n in c.items():
        assert n <= 12, f"turn {t} 出现了 {n} 次，疑似重复返回"


def test_cursor_returns_both_anchors():
    """游标必须**成对**返回 seq 与 turn —— 少一个就退化成"只靠 seq"。"""
    sess = _load_real()
    out = project_ui_history(sess, max_turns=20, scene="")
    assert "next_before_seq" in out, "必须返回 next_before_seq"
    assert "next_before_turn" in out, "必须返回 next_before_turn（无 seq 回合的兜底锚）"
    assert isinstance(out["next_before_seq"], int)
    assert isinstance(out["next_before_turn"], int)


def test_last_page_reports_no_more():
    """翻到头时两个锚都必须归 0（C# 据此撤掉「加载更早」入口）。

    不为 0 的后果：入口一直显示，点下去返回空列表 —— 看着能点、点了没反应。
    """
    sess = _synth_session([(1 + i, 100 + i * 10, 2) for i in range(5)])
    out = project_ui_history(sess, max_turns=100, scene="")
    assert out["next_before_seq"] == 0
    assert out["next_before_turn"] == 0


# ---------------------------------------------------------------- ③ 接线

def test_both_rpcs_pass_the_cursor_through():
    """两个 RPC 都必须把游标透传下去 —— 少一个就是"某一半永远翻不了页"。

    只看**代码行**：注释里也会出现 `_parse_cursor(` / `before_seq`，
    裸 `in` 会被注释骗过（这个坑本仓栽过三次，见 journal 第十一节）。
    """
    src = (_ROOT / "transport" / "ws_channel.py").read_text(encoding="utf-8")
    code = [ln.split("#")[0] for ln in src.splitlines()]

    # `_history_out` 的签名要收两个游标
    # ⚠ 签名可能**跨行**（本仓就是），所以要取"从 def 开始到出现 `:` 收尾"的那一段，
    #   不能只看含 `def` 的那一行（第一版就这么漏的）。
    lines = src.splitlines()
    sig_txt = None
    for i, ln in enumerate(lines):
        if "_history_out" in ln.split("#")[0] and "def " in ln:
            chunk = [ln]
            j = i
            while "->" not in lines[j] and j + 1 < len(lines) and j - i < 8:
                j += 1
                chunk.append(lines[j])
            sig_txt = " ".join(c.split("#")[0] for c in chunk)
            break
    assert sig_txt, "找不到 _history_out 定义"
    assert "before_seq" in sig_txt and "before_turn" in sig_txt, \
        f"_history_out 必须同时收 before_seq 与 before_turn：{sig_txt}"

    # ★ **每一个**调用点都必须传游标（不写死"两处"—— 实测有 3 处）★
    #   `delete_history` 也会回新历史（删完一次往返重放），漏了它删完就退回"全量灌"。
    calls = [ln for ln in code if "_history_out(" in ln and "def " not in ln]
    assert len(calls) >= 3, f"open_chat / get_history / delete_history 三处都该调，实际 {len(calls)}"
    for ln in calls:
        # 传了游标 = 末尾至少还有两个实参（`bseq, bturn` 或 `0, 0`）。
        # 断言"实参个数"而不是字面量 `bseq` —— `delete_history` 传的是 (0, 0)，
        # 那也是**正确**的分页行为（只取最近 limit 个，剩余靠加载更早）。
        args = ln[ln.index("_history_out(") + len("_history_out("):]
        args = args.rstrip().rstrip(")").rstrip()
        n_args = len([a for a in args.split(",") if a.strip()])
        assert n_args >= 6, \
            f"这处 _history_out 只传了 {n_args} 个实参，缺游标（应有 agent/npc/limit/scene/before_seq/before_turn）：{ln.strip()}"

    # 两个**读** RPC 要真的解析游标（delete_history 不需要：它固定 (0,0)）。
    # ⚠ 断言"解析结果被赋给 bseq/bturn"，不是"这一行出现过 _parse_cursor" ——
    #   变异验证实测：把 `bseq, bturn = self._parse_cursor(p)` 改成 `= 0, 0`
    #   （左边还留着 bseq/bturn），只看符号存在仍会通过。
    # ★ 必须**按 RPC 分段**查，不能全局计数 ★
    #   实测有 4 处 `_parse_cursor(`（单聊 2 + 群聊 2）。全局 `>= 2` 的写法在
    #   "只把 get_history 那处删掉"时仍然通过（另外 3 处还在）—— 变异验证实测漏报。
    #   正确判据：**每个带游标的 RPC 分支内**都必须出现一次真实解析。
    def _branch(method_name):
        try:
            i = code.index(next(l for l in code if l.strip() == f'if method == "{method_name}":'))
        except StopIteration:
            return ""
        # 取到下一个 `if method ==` 为止
        out = []
        for ln in code[i + 1:]:
            if ln.strip().startswith('if method =='):
                break
            out.append(ln)
        return "\n".join(out)

    for rpc in ("open_chat", "get_history", "open_room", "get_room_history"):
        body = _branch(rpc)
        assert body, f"找不到 {rpc} 分支（代码结构变了？请更新本测试）"
        assert "_parse_cursor(p)" in body, \
            f"{rpc} 分支里没有真的解析游标（`_parse_cursor(p)` 不在这段里）"
        lhs = [ln for ln in body.splitlines() if "_parse_cursor(p)" in ln]
        for ln in lhs:
            left = ln.split("=")[0]
            assert "seq" in left.lower(), \
                f"{rpc} 的游标解析结果没被接收：{ln.strip()}"


def test_projection_defaults_keep_unlimited_behavior():
    """不传游标时行为必须与改动前**完全一致**（limit=0 = 全部）。

    这条防的是"加了游标之后老调用悄悄变成截断" —— 那会让 `chat_cli` 与既有测试
    在无人察觉的情况下少看历史。
    """
    sess = _load_real()
    a = project_ui_history(sess, max_turns=0, scene="")
    b = project_ui_history(sess, max_turns=0, scene="", before_seq=0, before_turn=0)
    c = project_ui_history(sess, max_turns=0, scene="", before_seq=None, before_turn=None)
    assert [_key(i) for i in a["items"]] == [_key(i) for i in b["items"]]
    assert [_key(i) for i in a["items"]] == [_key(i) for i in c["items"]]


def test_seq_cursor_alone_repeats_turns_without_seq():
    """★ 只给 seq 游标**不够** —— 这是 `before_turn` 存在的证据 ★

    真机账本（姜萌 43 回合）里有 **2 个回合完全没有 seq**（turn 38 / 42，
    `initiative` / `turn_error` 这类合成内容）。只按 seq 翻页时它们无法被排除，
    于是会**每页重复返回**。

    这个用例把"不够"这件事**固定成契约**：如果有人把 `before_turn` 删掉，
    这个测试仍然会通过（因为它测的就是退化行为），但
    `test_paging_rebuilds_full_history_exactly_on_real_ledger` 会立刻失败 ——
    那条才是"必须两个锚"的守卫。

    这里真正的价值是**记录病因**：以后有人看到"为什么要两个游标"，
    跑一下这个用例就知道只用一个会撞到什么。
    """
    sess = _load_real()
    base = [_key(i) for i in project_ui_history(sess, max_turns=0, scene="")["items"]]

    before_seq = None
    acc = []
    for _ in range(100):
        out = project_ui_history(sess, max_turns=20, scene="", before_seq=before_seq)
        acc = [_key(i) for i in out["items"]] + acc
        nb = out.get("next_before_seq") or 0
        if nb <= 0:
            break
        before_seq = nb

    # 退化行为：条目**总数会多出来**（重复），这正是 bug 的形态
    assert len(acc) > len(base), (
        "只用 seq 游标时本应出现重复（这是已知缺陷，靠 before_turn 修掉）；"
        f"实际 {len(acc)} 条 vs 全量 {len(base)} 条 —— 若相等说明账本里没有无 seq 回合，"
        "请换一个有 initiative/turn_error 的账本再验证"
    )


def test_seq_cursor_predicate_uses_turn_end():
    """`before_seq` 判据必须用回合的**最大** seq（回合结束处）。

    用最小 seq 的后果：跨越游标的回合每页重复出现 —— 它的首条 seq 恒小于游标，
    而游标按最小 seq 推进永远推不过它。真机实测过（`[38..58]` / `[18..42]`）。
    """
    sess = _synth_session([(1 + i, 100 + i * 10, 3) for i in range(6)])
    full = project_ui_history(sess, max_turns=0, scene="")
    turns = [i.get("turn") for i in full["items"]]
    # 每个 turn 恰好 3 条
    from collections import Counter
    assert set(Counter(turns).values()) == {3}, f"合成数据应每回合 3 条，实际 {Counter(turns)}"

    # 用"第 3 回合的最大 seq"当游标：只应拿到 turn 1、2 两个回合
    third = [i for i in full["items"] if i.get("turn") == 3]
    third_max = max(i["seq"] for i in third)
    out = project_ui_history(sess, max_turns=10, scene="", before_seq=third_max)
    got = sorted({i.get("turn") for i in out["items"]})
    assert got == [1, 2], f"游标=第3回合末 seq 时应只返回 turn 1..2，实际 {got}"


def test_next_before_seq_points_at_oldest_turn_end():
    """`next_before_seq` 必须指向**本页最老回合的结束处**，否则下一页会重复。

    写成"本页最小 seq"的后果：下一页从"最老回合的第一条"继续往前找，
    而那个回合本身仍然 `最大seq > 游标`... 边界极易错位，实测会重复整回合。
    """
    sess = _synth_session([(1 + i, 100 + i * 10, 3) for i in range(6)])
    out = project_ui_history(sess, max_turns=2, scene="")
    got_turns = sorted({i.get("turn") for i in out["items"]})
    assert got_turns == [5, 6], f"最近 2 回合应是 5、6，实际 {got_turns}"

    # 第 5 回合的 3 条 seq 是 140,141,142 ⇒ 最老回合最大 seq = 142
    expect = max(i["seq"] for i in out["items"] if i.get("turn") == 5)
    assert out["next_before_seq"] == expect, \
        f"next_before_seq 应为本页最老回合的最大 seq={expect}，实际 {out['next_before_seq']}"

    # 用它翻下一页：应拿到 turn 3、4（不重复 5）
    nxt = project_ui_history(sess, max_turns=2, scene="",
                            before_seq=out["next_before_seq"])
    nxt_turns = sorted({i.get("turn") for i in nxt["items"]})
    assert nxt_turns == [3, 4], f"下一页应是 3、4（不许重复 5），实际 {nxt_turns}"


# ---------------------------------------------------------------- 群聊分页（11-21）

def _fake_room(msgs):
    """最小 Room 壳：`project_room_history` 只读 `visible_log()` / `unit_id_of` / 元信息。"""
    class _U:
        def __init__(self, seq, text, speaker="甲"):
            self.seq = seq
            self.uid = f"u{seq}"
            self.text = text
            self.speaker = speaker
            self.time = 0
            self.narration = False
            self.deleted = False

    class _R:
        id = "room1"
        title = "测试群"
        members = ["甲", "乙"]
        member_unit_ids = {}      # 房间级身份表；空 = 查不到（消费侧必须容错）
        deleted_seqs = set()      # 墓标；空 = 没删过

        def visible_log(self):
            return msgs

        def unit_id_of(self, name):
            return ""

        @property
        def last_seq(self):
            return msgs[-1].seq if msgs else 0

    return _R()


def test_room_paging_rebuilds_full_history_exactly():
    """群聊分页必须逐条重建全量（不重不漏）。

    群发言是平铺结构、每条都带 seq，所以**单锚（before_seq）就够** ——
    不像单聊要双锚（那里有"整回合无 seq"的合成内容）。
    """
    from agent_loop.room.room_history import project_room_history

    class _U:
        def __init__(self, seq):
            self.seq = seq
            self.uid = f"u{seq}"
            self.text = f"m{seq}"
            self.speaker = "甲"
            self.time = seq
            self.narration = False
    room = _fake_room([_U(i) for i in range(0, 200, 2)])   # 100 条，seq 有空洞

    base = [(i["kind"], i["text"], i["seq"]) for i in
            project_room_history(room, limit=0)["items"]]
    assert len(base) == 100

    for page in (5, 10, 33, 100):
        before = 0
        acc = []
        for _ in range(200):
            out = project_room_history(room, limit=page, before_seq=before)
            acc = [(i["kind"], i["text"], i["seq"]) for i in out["items"]] + acc
            nb = out.get("next_before_seq") or 0
            if nb <= 0:
                break
            before = nb
        assert acc == base, f"每页 {page} 条时群聊翻页结果与全量不一致"


def test_room_reports_next_before_seq_and_terminates():
    """群聊也要返回 `next_before_seq`，翻到头归 0（C# 据此撤掉入口）。"""
    from agent_loop.room.room_history import project_room_history

    class _U:
        def __init__(self, seq):
            self.seq = seq
            self.uid = f"u{seq}"
            self.text = f"m{seq}"
            self.speaker = "甲"
            self.time = seq
            self.narration = False
    room = _fake_room([_U(i) for i in range(10)])

    out = project_room_history(room, limit=5)
    assert len(out["items"]) == 5
    assert out["next_before_seq"] == 5, f"应指向第 6 条（seq=5），实际 {out['next_before_seq']}"

    # 一次取全部 ⇒ 到顶，锚归 0
    allout = project_room_history(room, limit=0)
    assert allout["next_before_seq"] == 0, "取全部时不该报游标（否则入口永远显示）"


def test_room_rpcs_pass_cursor():
    """`open_room` / `get_room_history` 都必须把游标透传给 `rooms.history`。

    只看**代码行**（注释里也有这些词）。
    """
    src = (_ROOT / "transport" / "ws_channel.py").read_text(encoding="utf-8")
    code = [ln.split("#")[0] for ln in src.splitlines()]
    calls = [ln for ln in code if "self.rooms.history(" in ln]
    assert len(calls) >= 2, f"open_room / get_room_history 两处都该调，实际 {len(calls)}"
    for ln in calls:
        assert "before_seq" in ln, f"这处 history 调用没传游标：{ln.strip()}"

    # ★ 还有**下一层**：`RoomFlow.history` → `project_room_history` ★
    #   只测 ws_channel 会漏掉这一层（变异验证实测：把 room_flow 的透传删掉，
    #   上面那些断言全过，因为 `before_seq` 参数收下了却没用）。
    flow_src = (_ROOT / "room" / "room_flow.py").read_text(encoding="utf-8")
    flow_code = [ln.split("#")[0] for ln in flow_src.splitlines()]
    # ⚠ 调用**跨行**（`before_seq=before_seq` 在下一行），所以要取"从出现位置起
    #   往后若干行"拼成一块再看 —— 只看那一行会漏（第一版就这么漏的）。
    joined = None
    for i, ln in enumerate(flow_code):
        if "project_room_history(" in ln and "import" not in ln:
            joined = " ".join(flow_code[i:i + 4])
            break
    assert joined, "找不到 project_room_history 调用"
    assert "before_seq" in joined, \
        f"RoomFlow.history 没把 before_seq 透给 project_room_history：{joined.strip()}"
