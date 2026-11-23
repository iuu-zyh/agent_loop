"""断连/超时的**可见性**与**及时收口**契约（11-20 立）。

## 这份测试守什么

真机日志（11-20 那份）暴露了一条完整的故障链，三个环节各自都能独立咬人：

    T+0    Python 发 get_context → C# 收下
    T+30   慢告警：`rpc.get_context 已运行 30.0s`
    T+32   C# 连接断了（日志：`C# 连接已断开`）
    T+120  Python 才终于 `RPC get_context 超时（120.0s）`

中间那段 **90 秒是纯空转**：连接都没了，那个 future 的应答永远不可能回来 ——
req_id 是连接内配对，新连接的 req_id 从 1 重来。

三个环节对应三条断言：

  ① Python 断连时必须**立刻**把挂起的 RPC 判死（否则每个空转 90 秒）；
  ② 玩家必须**看得见**"连接断了"（否则"没回复"和"连不上"长得一模一样）；
  ③ C# 侧必须能区分"排队久"还是"执行久"（否则那 30 秒永远查不出来）。

## 为什么 ③ 是必须的

`get_context` 慢的两种原因修法完全不同：
  · 排队久 → 主线程被别的活占着，要找那个活 / 给 job 限流；
  · 执行久 → get_context 内部某一段慢（比如全图扫描）。
此前 C# 只有一个 `disp.job` 的**总**耗时，那里面混着所有 job，分不出是哪种。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "transport" / "ws_channel.py"
CLIENT = ROOT / "csharp" / "bridge" / "WsClient.cs"
PRESENTER = ROOT / "csharp" / "UI" / "ChatPresenter.cs"
PROFILER = ROOT / "csharp" / "app" / "TickProfiler.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _py_method(src: str, name: str) -> str:
    """取 Python 方法体（按缩进配平）。

    实现要点：签名行可能带返回注解（`-> None:`），也可能跨多行 —— 所以从签名行之后
    逐行吃，直到遇到**缩进不深于签名**的非空行为止。空行不算结束。
    """
    m = re.search(r"^(\s*)(async\s+)?def\s+" + re.escape(name) + r"\s*\(", src, re.M)
    assert m, f"找不到方法 {name}"
    line_start = src.rfind("\n", 0, m.start()) + 1
    line_text = src[line_start:src.find("\n", line_start)]
    assert not line_text.lstrip().startswith("#"), f"{name} 命中的是注释里的伪签名"
    # ★ 起点必须是 `def` 这个 token 本身 ★
    # `(\s*)` 是贪婪的，会把签名前的换行也吃掉 ⇒ m.start() 落在 "\n" 上而不是 "d" 上。
    # 用它当起点会让 lines[0] == "\n"，循环第一条就判"缩进回到签名层"、方法体整个丢掉。
    def_at = src.index("def", m.start())
    indent = len(m.group(1).split("\n")[-1])
    lines = src[def_at:].splitlines(keepends=True)
    out = [lines[0]]
    for ln in lines[1:]:
        stripped = ln.strip()
        if not stripped:
            out.append(ln)          # 空行属于方法体
            continue
        cur = len(ln) - len(ln.lstrip())
        if cur <= indent:
            break                   # 缩进回到签名层 = 方法结束
        out.append(ln)
    return "".join(out)


# ----------------------------------------------------------------------
# ① 断连立刻收口挂起的 RPC
# ----------------------------------------------------------------------

def test_disconnect_drops_pending_rpcs():
    """断连时必须把所有挂起的 RPC 立刻判死。

    不做的话：C# 那条连接没了，它手里没回的 `get_context` 应答**永远不会回来**
    （req_id 是连接内配对，新连接从 1 重来），而 `_pending` 里的 Future 没人 set 没人
    cancel ⇒ `_await_response` 只能等满 `request_timeout`（默认 120s）。
    真机 8 次，每次空转 90 秒。
    """
    ws = _read(WS)
    assert "_drop_pending_on_disconnect" in ws, "断连清理方法不存在"
    # 必须在断连的 finally 里被调用（那才是所有出口）。
    # 锚点用**实际那行日志**，不能用 `"C# 连接已断开"` —— 那句话在注释里出现过好几次，
    # index() 会命中第一处注释，切出来的片段根本不含 finally 块（假红）。
    call_at = ws.index('log.warning("C# 连接已断开')
    fin_at = ws.rindex("finally", 0, call_at)
    seg = ws[fin_at:call_at]
    # 断言必须精确到接收者：裸调用在方法外是 NameError，而它正处在 finally 里 ——
    # 于是「判死」与下一行断连日志会**一起被跳过**。只查方法名的旧写法对这种形态恒为真。
    assert "self._drop_pending_on_disconnect()" in seg, "断连出口没有调用清理（或漏了 self.）"
    # 必须早于那行日志（先清再报，日志顺序才与事实一致）
    assert ws.index("self._drop_pending_on_disconnect()") < call_at, "清理排在了断连日志之后"

    body = _py_method(ws, "_drop_pending_on_disconnect")
    assert "set_exception" in body, "没有把挂起的 future 判死"
    assert "ConnectionError" in body, "判死用的异常类型不对（要与 TimeoutError 区分开）"
    # 已经 done 的必须跳过：心跳槽可能刚被正常应答填过
    assert ".done()" in body, "没有跳过已完成的 future（会抛 InvalidStateError）"
    # 清理路径自己绝不许再抛
    assert "except Exception" in body, "清理路径没有兜异常（会把断连处理本身炸掉）"


def test_drop_pending_marks_futures_dead():
    """真跑一次清理：挂起的 future 必须变 done(ConnectionError)，并从表里摘掉。

    与上面那条互补 —— 上面守「这个方法在断连出口被正确调用」，这条守「它真的把
    future 判死了」。缺任何一边，90 秒空转都会回来。
    """
    from agent_loop.transport.ws_channel import WsServer

    async def scenario():
        ws = WsServer()
        fut = asyncio.get_running_loop().create_future()
        ws._pending["req-1"] = fut
        ws._drop_pending_on_disconnect()
        assert fut.done(), "挂起的 future 没有被判死"
        with pytest.raises(ConnectionError):
            fut.result()
        assert "req-1" not in ws._pending

    asyncio.run(scenario())


def test_request_catches_connection_error():
    """`request()` 必须接住 `ConnectionError`，保持「失败也返回 dict」的契约。

    ★ 这条是配套的硬要求 ★ 加 `set_exception` 之后，如果 `request()` 只捕获
    `asyncio.TimeoutError`，那个 `ConnectionError` 会**冒到调用方** ——
    于是"连接断了"升级成"回合异常"（整轮作废），比原来的"超时后按工具失败继续"**更糟**。
    加一半等于把事情变坏。
    """
    ws = _read(WS)
    body = _py_method(ws, "request")
    assert "except ConnectionError" in body, \
        "request 没接住 ConnectionError —— 断连会升级成回合异常（比超时更糟）"
    # 两条失败出口都要返回 dict（不能 raise）。
    # 切到**下一个同级 except** 为止（不能用 split("except ")：那会把分支首行一起切掉）。
    at = body.index("except ConnectionError")
    nxt = body.find("\n        except ", at)
    seg = body[at:] if nxt < 0 else body[at:nxt]
    assert "return {" in seg, "ConnectionError 分支没有返回失败 dict"
    assert '"success": False' in seg, "ConnectionError 分支没按失败契约返回"
    # 文案要能让玩家/模型分辨"连接没了"和"等太久"
    assert "断开" in seg, "失败文案没有说明是连接问题"


def test_disconnect_summary_is_logged():
    """清理要留一行日志（否则"为什么这次很快就失败了"事后无从判断）。"""
    body = _py_method(_read(WS), "_drop_pending_on_disconnect")
    assert "log.warning" in body, "清理没留痕"
    assert "判死" in body or "挂起" in body, "日志里没说清处理了几条"


# ----------------------------------------------------------------------
# ② 玩家看得见"断了" / "回来了"
# ----------------------------------------------------------------------

def test_wsclient_exposes_disconnected_event():
    """`WsClient` 必须有 `Disconnected` 事件，且**只在真的断过之后**才发。

    只发初次连接失败（Python 没起）时不该弹"断线了"—— 那时 `Connected` 从没触发过，
    弹出来只会让人更困惑。
    """
    src = _read(CLIENT)
    assert "public event Action Disconnected;" in src, "没有 Disconnected 事件"
    assert "_everConnected" in src, "没有记录'曾经连上过'的门闸"
    # 置位在连上那一刻
    conn = src[src.index("_everConnected = true;"):]
    assert "FireConnected()" in conn[:200], "_everConnected 置位后应紧跟 FireConnected"
    # 广播必须走 Dispatcher（后台线程碰 UI 会炸）
    body = src[src.index("private void FireDisconnected()"):]
    body = body[: body.index("\n        }")]
    assert "_dispatcher.Enqueue" in body, "FireDisconnected 没走 Dispatcher（后台线程碰 UI 会崩）"
    # 必须在 finally 里发，且在 `_ws = null` 之后（订阅方查 IsConnected 必须已是 false）。
    # 从调用点往回找到那个 finally 的开头，整段里必须同时有 `_ws = null;` 与这次调用。
    at = src.index("if (_everConnected) FireDisconnected();")
    fin_at = src.rindex("finally", 0, at)
    seg = src[fin_at:at]
    assert "_ws = null;" in seg, "FireDisconnected 要放在 _ws=null 之后（订阅方查 IsConnected 必须已是 false）"


def test_presenter_shows_disconnect_to_the_player():
    """`ChatPresenter` 必须在窗口里给出**看得见**的提示。

    此前断线在 UI 层零痕迹：玩家发了消息，屏幕不动，分不清"模型在想"和"连不上"。
    """
    src = _read(PRESENTER)
    init = src[src.index("public void Init(ChatWindow window, WsClient ws)"):]
    init = init[: init.index("\n        }")]
    assert "_ws.Disconnected +=" in init, "Init 没有订阅 Disconnected（提示落不到窗口上）"
    assert "_ws.Connected +=" in init, "Init 没有订阅 Connected（恢复时该提示一句）"

    lost = src[src.index("private void OnTransportLost()"):]
    lost = lost[: lost.index("\n        }")]
    assert "AppendSystemNotice(" in lost, "断线时没有给玩家任何提示"
    assert "SetBusy(false)" in lost, "断线没灭忙标（连接没了不可能再有收口帧，转圈是纯误导）"
    # 没在等的时候不许打扰（重连只需 2 秒，插红字只会制造焦虑）
    assert "return" in lost and ("_busy" in lost or "HasActiveBubble" in lost), \
        "断线提示没有按'是否正在等回复'门控"


def test_presenter_clears_stale_state_on_recovery():
    """恢复时必须清掉断线期间的暂存帧（它们属于已经死掉的那条连接）。"""
    src = _read(PRESENTER)
    back = src[src.index("private void OnTransportBack()"):]
    back = back[: back.index("\n        }")]
    assert "_heldDuringBuild.Clear()" in back, "恢复时没清断线期间的暂存帧（会补画到新连接上）"
    assert "AppendSystemNotice(" in back, "恢复时没有告诉玩家一声"


# ----------------------------------------------------------------------
# ③ C# 侧能分清「排队久」还是「执行久」
# ----------------------------------------------------------------------

def test_wsclient_measures_queue_and_exec_separately():
    """`get_context`/`call_tool` 必须分段计时：排队 vs 执行。

    真机 8 次 `get_context` 超时，C# 侧只有 `disp.job` 的总耗时 —— 那里面混着所有 job，
    看不出那 30~120 秒是"排在队列里"还是"真在执行"。两者修法完全不同。
    """
    src = _read(CLIENT)
    assert "ws.wait." in src, "没有测'排队'那一段"
    assert "ws.exec." in src, "没有测'执行'那一段"
    # 排队耗时要写在**主线程开始执行的那一刻**（lambda 内部），不是入队时
    enq = src[src.index("var task = _dispatcher.Enqueue("):]
    enq = enq[: enq.index("});") + 3]
    assert "_waitMs[0] = TickProfiler.ElapsedMs(" in enq, \
        "排队耗时没有在执行开始那一刻写入（写在入队处恒为 0）"
    # 慢请求要有单独一行可 grep 的日志
    assert "★慢请求★" in src, "没有慢请求专用日志行"
    assert "排队=" in src and "执行=" in src, "慢请求日志没有分段落值"


def test_tickprofiler_can_add_presampled_ms():
    """`TickProfiler.AddMs` 必须存在且安全（分段计时用两次 Begin 会互相覆盖）。"""
    src = _read(PROFILER)
    assert "internal static void AddMs(" in src, "缺少 AddMs（分段计时无法并进体检表）"
    body = src[src.index("internal static void AddMs("):]
    body = body[: body.index("\n        }")]
    assert "if (!Enabled) return;" in body, "AddMs 没尊重总开关"
    assert "catch" in body, "AddMs 没兜异常（采样器不许把主循环带走）"


def test_drop_retrieves_the_exception_it_sets():
    """判死之后必须**立刻取一次**异常，否则 asyncio 会打 "exception was never retrieved"。

    有两个竞态下没人 await 那个 future：① 那条 RPC 刚超时返回、pop 与遍历交错；
    ② 调用方协程已被取消。那时 asyncio 在 GC 阶段往 stderr 打一条 warning ——
    打包后它会混进日志，看起来像"断连时抛了个没人管的异常"，排障时是纯噪声。
    """
    body = _py_method(_read(WS), "_drop_pending_on_disconnect")
    assert "set_exception" in body
    set_at = body.index("set_exception")
    # 取用必须在 set 之后（同一次迭代里）
    after = body[set_at:]
    assert "fut.exception()" in after, \
        "判死后没有取用异常 —— asyncio 会打 'exception was never retrieved'，污染日志"
    # 取用要兜异常（future 可能已被取消，.exception() 会抛 CancelledError）
    seg = after[after.index("fut.exception()"):]
    assert "except" in seg[:200], "取用异常没有兜 —— 已取消的 future 会抛 CancelledError"


def test_drop_explains_why_not_cancel():
    """必须说明为什么用 set_exception 而不是 cancel（这是个反直觉的选择）。

    `CancelledError` 在 3.8+ 是 `BaseException` 子类，`except ConnectionError` 接不住 ——
    改成 cancel 会让"连接断了"升级成"回合异常"。下一个改这段的人必须看得到这条。
    """
    body = _py_method(_read(WS), "_drop_pending_on_disconnect")
    assert "CancelledError" in body or "cancel" in body, "没说明为什么不用 cancel"
