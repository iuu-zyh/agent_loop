"""读档打断回合的契约：中止要用中止来表达，不能塌成 KeyError。

对应 11-20 实机那条：

    19:34:40  rpc.get_context 已运行 30.0s 仍未结束
    19:35:15  load_happened：世界重载，丢弃 5 个 agent      ← 玩家读档
    19:36:10  RPC get_context 超时（120.0s）
    19:36:10  回合 395 以 aborted 收口：'step'              ← KeyError
              Traceback ... dialogue_agent.py:1183 in _turn

根因链（本文件逐环钉住）：

    load_happened → discard_all() → agent.dispose() → cancel()
        → `self.phase` 整个换成 {"kind":"idle", ...}  ⇒ **`step` 键消失**
    而 `_turn` 的循环体正卡在 `await self._pre_step(...)` 里等 get_context（上限 120s）
        → 恢复后回到 `self.phase["step"]` ⇒ KeyError

修法：`cancel()` 自增回合令牌（`_turn_epoch`），循环体在每个 `await` 返回点验
"我还是不是当前回合"（`_ensure_turn_live`），不是就抛 `TurnAborted`。

为什么不能只是"把 `self.phase["step"]` 改成 `.get("step", 0)`"：
那会让一个**已经作废的回合**继续往下跑 —— 它会往一个已被 `discard_all` 摘出名册的
对象里写 assistant/message，症状从"整轮报错"变成"静默污染存档"。
中止必须是**可观测的**，所以要有类型化的异常，而不是一个缺省值。
"""

import asyncio
import inspect

from agent_loop.agent.dialogue_agent import DialogueAgent, TurnAborted
from agent_loop.agent.system_prompt import SystemPrompt
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.session.session import Session
from agent_loop.transport.bridge import StubGameBridge

NPC = "林婉清"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _agent():
    """最小可用 agent（不落盘、不建 AgentLoop：本文件只测回合状态机）。"""
    sess = Session(id=NPC, header={"id": NPC})
    return DialogueAgent(session=sess, system_prompt=SystemPrompt.instance(),
                         llm=EchoLlmClient())


def _src_method(name: str, cls=None) -> str:
    """取某个方法的**函数体**源码（已剥 docstring 与注释）。

    为什么不用字符串切片：方法在类里缩进，按"顶格即结束"会一路读到类尾
    （本文件第一版就这么错，把 `dispose` 的正文也算进了 `_ensure_turn_live`）。
    这里用 AST 定位函数节点，再按行号切 —— 顺带能把 docstring 整段剔掉，
    免得注释里提到 `_abort` 就让"正文里不许出现 _abort"那条断言误红。
    """
    import ast

    cls = cls or DialogueAgent
    mod = inspect.getmodule(cls)
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    lines = src.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = list(node.body)
            # 剥 docstring：它是 Expr(Constant(str))
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            if not body:
                return ""
            start = body[0].lineno - 1
            end = body[-1].end_lineno
            return "\n".join(lines[start:end])
    raise AssertionError(f"没找到方法 {name}")


# ---------------------------------------------------------------------------
# A. 根因：cancel() 会把 phase 换成缺 step 的形态
# ---------------------------------------------------------------------------

def test_cancel_removes_step_key():
    """前提复现：`cancel()` 之后 `phase` 里**没有** `step`。

    这不是"要修的 bug"，而是既定语义（取消 = 回 idle）。本断言的作用是钉住
    "循环体不能假设 step 一直在" —— 一旦有人把 cancel 改成保留 step，
    下面那些保护就失去了被验证的前提，这里会先红。
    """
    a = _agent()
    a._wake()
    assert "step" in a.phase
    a.cancel()
    assert a.phase["kind"] == "idle"
    assert "step" not in a.phase, "cancel 之后 phase 仍带 step ⇒ 本文件的保护前提变了，重审"


def test_cancel_bumps_turn_epoch():
    """`cancel()` 必须推进令牌 —— 这是"中止"唯一的主动信号。"""
    a = _agent()
    before = a._turn_epoch
    a.cancel()
    assert a._turn_epoch == before + 1, "cancel 没推进令牌 ⇒ 在跑的回合无法察觉自己被取消"


def test_epoch_bump_precedes_phase_replace():
    """★ 顺序关键：先推令牌，再换 phase。

    反过来的话，若恰好在两行之间被切走，循环体会看到"phase 已换、令牌还是旧"
    ⇒ 它继续跑并读到缺键的 phase —— 也就是本 bug 的原样。
    """
    src = _src_method("cancel")
    i_epoch = src.find("_turn_epoch += 1")
    i_phase = src.find('self.phase = {"kind": "idle"')
    assert i_epoch != -1, "cancel 里没有推进令牌"
    assert i_phase != -1, "cancel 里没有回 idle"
    assert i_epoch < i_phase, "令牌必须在换 phase **之前**推进"


# ---------------------------------------------------------------------------
# B. 保护：每个 await 之后都验一次
# ---------------------------------------------------------------------------

def test_ensure_turn_live_raises_when_epoch_moved():
    a = _agent()
    a._active_epoch = a._turn_epoch
    a._ensure_turn_live()               # 同代：不抛
    a.cancel()                          # 令牌 +1，_active_epoch 落后
    try:
        a._ensure_turn_live()
    except TurnAborted:
        pass
    else:
        raise AssertionError("令牌已经变了却没抛 TurnAborted ⇒ 作废回合会继续往下写")


def test_turn_checks_after_each_await():
    """`_turn` 循环体里，两个会跨读档的 await 之后都必须有自检。

    `_pre_step`  → 等 get_context（120s，实机就是这里炸的）
    `_step`      → 等 LLM（最长 240s，日志里有）
    """
    src = _src_method("_turn")
    for anchor, why in (
        ("await self._pre_step(", "get_context 等待（11-20 那次 KeyError 的位置）"),
        ("await self._step(", "LLM 等待"),
    ):
        i = src.find(anchor)
        assert i != -1, f"没找到 {anchor}"
        tail = src[i:]
        # 该 await 之后的第一个语句块里必须出现自检
        nxt = tail.find("self._ensure_turn_live()")
        assert nxt != -1, f"{anchor} 之后没有自检 ⇒ {why} 跨读档后照样往下跑"
        # 且不能跑到下一个 await 之后才算数
        other = tail.find("await self.", 1)
        assert nxt < other if other != -1 else True, (
            f"{anchor} 的自检排到了下一个 await 之后 ⇒ 中间那段仍在裸跑")


def test_step_checks_after_llm_await():
    """`_step` 里的 LLM await 同样会跨读档：回来要先验，再往账本写。"""
    src = _src_method("_step")
    i_gen = src.find("await self.llm.generate(")
    assert i_gen != -1
    tail = src[i_gen:]
    i_check = tail.find("self._ensure_turn_live()")
    i_append = tail.find('self.session.append("assistant/message"')
    assert i_check != -1, "LLM 回来没有自检 ⇒ 会把 assistant 写进一个已作废的账本"
    assert i_append != -1
    assert i_check < i_append, "自检必须排在落账**之前**"


# ---------------------------------------------------------------------------
# C. 收口：TurnAborted 走 aborted，不走 error
# ---------------------------------------------------------------------------

def test_turn_aborted_branch_exists_and_re_raises():
    """`_turn` 必须**专门**接住 TurnAborted，且继续上抛。

    为什么必须上抛而不是 `return False`：`run_until_idle` 拿到 False 后会看
    `inbox.has_pending`，有 pending 就再起一个 turn —— 而此刻 agent 已经被
    `discard_all` 摘出名册，再跑一轮等于对着一个已作废的对象记账。
    """
    src = _src_method("_turn")
    assert "except TurnAborted" in src, "没有专门的 TurnAborted 分支 ⇒ 会掉进通用 except 打成 error"
    i = src.find("except TurnAborted")
    blk = src[i:i + 900]
    assert "raise" in blk, "TurnAborted 分支没有上抛"
    assert '"aborted"' in blk, "TurnAborted 没有认成 aborted（账本收口会写错）"


def test_abort_is_not_used_as_liveness_judgement():
    """★ `_ensure_turn_live` **不能**看 `_abort` ★

    `_abort` 的语义是"取消过"，`cancel()` 只写不清 —— 拿它当判据会让
    "取消过的 agent 永远跑不了下一轮"。令牌自增已完整覆盖中止这件事。
    """
    src = _src_method("_ensure_turn_live")
    body = "\n".join(ln for ln in src.splitlines()
                     if ln.strip() and not ln.strip().startswith("#") and '"""' not in ln)
    assert "_abort" not in body, (
        "自检判据里出现了 _abort —— 它只写不清，会让取消过的 agent 永久无法开新回合")


def test_cancelled_agent_can_still_run_again():
    """取消过的 agent 必须还能开新回合（令牌只拦"那一趟"，不封杀后续）。"""
    a = _agent()
    a.cancel()
    a.send("你好")
    asyncio.run(a.run_until_idle())
    assert a.phase["kind"] == "idle"
    assert any(ev.get("type") == "turn/end" for ev in a.session.log), "新回合没闭合"


# ---------------------------------------------------------------------------
# D. 上层：别把中止报成故障
# ---------------------------------------------------------------------------

def _code_only(src: str) -> str:
    """剥掉整行注释与行尾注释，只留代码（结构断言只看代码，不看注释）。

    必要性：这些断言是"某段代码里**不许**出现 X"，而注释里为了解释**为什么**不写 X，
    往往正好把 X 写一遍 —— 不剥注释，解释越清楚测试越红（第一版就这么红的）。
    """
    out = []
    for ln in src.splitlines():
        s = ln.split("#", 1)[0]
        if s.strip():
            out.append(s)
    return "\n".join(out)


def _block_after(src: str, anchor: str, size: int = 900) -> str:
    """取 `anchor` 之后的一段**代码**（已剥注释）。"""
    i = src.find(anchor)
    assert i != -1, f"没找到 {anchor}"
    return _code_only(src[i:i + size])


def _ws_src() -> str:
    from agent_loop.transport import ws_channel
    return inspect.getsource(ws_channel)


def _room_src() -> str:
    from agent_loop.room import room_flow
    return inspect.getsource(room_flow)


def test_ws_channel_does_not_fallback_on_abort():
    """单聊路径：读档打断**不能**给玩家回"传音法阵没接上"（那是在报假故障）。"""
    src = _ws_src()
    assert "from ..agent.dialogue_agent import TurnAborted" in src, "ws_channel 没导入 TurnAborted"
    blk = _block_after(src, "except TurnAborted", 700)
    assert "FALLBACK_TEXT" not in blk, (
        "TurnAborted 分支里出现了 FALLBACK_TEXT —— 读档不该被报成故障")


def test_room_flow_ends_round_on_abort():
    """群聊路径：一位成员被中止 ⇒ **整轮就地收尾**，不是"跳过她、其余照常"。

    判据是"谁的责任"：Exception = 她自己的事；TurnAborted = 整个世界被换掉了，
    剩下的人（agent 已被摘出名册）继续点名只会一个个撞同一堵墙。
    """
    src = _room_src()
    blk = _block_after(src, "except TurnAborted", 900)
    assert "break" in blk, "TurnAborted 之后没有 break ⇒ 还会继续点名后面的人"
    assert 'phase="error"' not in blk, (
        "TurnAborted 发了 phase=error —— 屏幕上会报一个不存在的故障")


def test_room_flow_imports_turn_aborted():
    src = _room_src()
    assert "TurnAborted" in src.split("\n\n\n")[0] or "import TurnAborted" in src, \
        "room_flow 没导入 TurnAborted"


# ---------------------------------------------------------------------------
# E. 端到端：读档那一刻正卡在 get_context
# ---------------------------------------------------------------------------

def test_abort_while_waiting_for_get_context():
    """★ 复现 11-20 实机：回合卡在 get_context 期间被 cancel ★

    断言三件事：
      ① 不再抛 KeyError（本 bug 的直接症状）；
      ② 抛的是 TurnAborted（中止被**类型化**了）；
      ③ 账本照常闭合 `turn/end:aborted`（不留半截回合）。
    """
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70,
                           "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"}})
    sess = Session(id=NPC, header={"id": NPC})
    agent = DialogueAgent(session=sess, system_prompt=SystemPrompt.instance(),
                          llm=EchoLlmClient(), bridge=bridge)

    started = []

    def slow_get_context(_npc_id, roster=None, **kw):
        """模拟"等 C# 主线程 120s"：这里同步地**在那次等待里**把 agent 取消掉。

        用同步桩而不是真 sleep：我们要复现的是"await 期间相位被换"这件事，
        而不是去测一个计时器。
        """
        started.append(1)
        agent.cancel()          # ← 读档：discard_all → dispose → cancel
        return {"raw": {"realm": "金丹", "now": {"text": "1年3月5日"}}}

    bridge.get_context = slow_get_context

    agent.send("你好")
    err = None
    try:
        asyncio.run(agent.run_until_idle())
    except TurnAborted:
        pass                    # 期望路径
    except KeyError as e:
        err = e
    except Exception as e:      # noqa: BLE001
        err = e

    assert started, "前提：get_context 应该被调用到（否则没在等，也就没复现）"
    assert err is None, (
        f"读档打断回合时抛了 {type(err).__name__}: {err} —— "
        "这正是 11-20 那条 `回合 395 以 aborted 收口：'step'` 的原样")
    assert any(ev.get("type") == "turn/end" for ev in sess.log), "账本没闭合 turn/end"
    ends = [ev for ev in sess.log if ev.get("type") == "turn/end"]
    assert ends[-1]["data"]["reason"]["kind"] == "aborted", (
        f"收口类型是 {ends[-1]['data']['reason']} —— 中止应记 aborted")


def test_no_keyerror_when_phase_replaced_midflight():
    """更直接的一条：把 phase 换成缺 step 的形态，循环体不许因此抛 KeyError。

    这条不依赖 cancel 的实现细节 —— 它直接模拟"等回来时 phase 已经换过"。
    """
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {"realm": "金丹", "now": {"text": "1年3月5日"}})
    sess = Session(id=NPC, header={"id": NPC})
    agent = DialogueAgent(session=sess, system_prompt=SystemPrompt.instance(),
                          llm=EchoLlmClient(), bridge=bridge)

    def swap_phase(_npc_id, roster=None, **kw):
        # 模拟 cancel() 的效果，但**只**动 phase 与令牌（不碰别处）
        agent._turn_epoch += 1
        agent.phase = {"kind": "idle", "last_turn": 0}
        return {"raw": {"realm": "金丹"}}

    bridge.get_context = swap_phase
    agent.send("你好")
    try:
        asyncio.run(agent.run_until_idle())
    except (TurnAborted, KeyError) as e:
        assert not isinstance(e, KeyError), f"仍是 KeyError: {e} —— 修法没覆盖到"
