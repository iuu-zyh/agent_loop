"""忙标阶段契约：让玩家分得清「在等谁」。

用户 11-21 的需求（原话）：

    「模型慢」的话可以有反馈，就像是 C端 30s 不回的时候就会显示（跟着设置的参数值去走），
    要区分好模型在思考还是没出来首字还是卡在 C端，就是改一下当前的 npc 回应中这个，
    变为不同阶段显示不同

三段对应的数据源（都在 Python 手里，C# 猜不出来）：

    snapshot  等 C# 取快照（get_context）—— 「卡在 C 端」就是它
    think     等模型首字（ttft）
    stream    模型正在吐字

本文件钉住四件事：
  A. Python 侧阶段序列正确、成对、不重不漏；
  B. 阈值来自配置 `ui.busy_hint_after`（**不是**日志的 slow_ms），且能归一/夹取；
  C. C# 侧三个文案齐全、秒数按阈值决定、`SetBusy(false)` 会清阶段；
  D. 阶段帧**必须**刷新忙标自愈时钟（否则 240s 后被误清成空闲）。

为什么 D 单独拎出来：`BusyWatchdog` 的判据是"多久没收到帧"，而一个等模型 5 分钟的
回合期间只有这几条阶段帧 —— 不刷它，第 240 秒忙标会被自愈逻辑清掉，玩家以为回完了。
"""

import asyncio
import ast
import inspect
import re

import pytest

from agent_loop.agent.dialogue_agent import DialogueAgent
from agent_loop.agent.system_prompt import SystemPrompt
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.session.session import Session
from agent_loop.transport.bridge import StubGameBridge

NPC = "林婉清"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _run(text="你好", llm=None, bridge_ctx=None):
    """跑一回合，返回 (阶段序列, agent)。"""
    bridge = StubGameBridge()
    bridge.seed_unit(NPC, {"realm": "金丹", "now": {"text": "1年3月5日"}})
    if bridge_ctx is not None:
        bridge.get_context = bridge_ctx
    a = DialogueAgent(session=Session(id=NPC, header={"id": NPC}),
                      system_prompt=SystemPrompt.instance(),
                      llm=llm or EchoLlmClient(), bridge=bridge)
    phases = []
    a.on_wait_phase = lambda ph, en: phases.append((ph, bool(en)))
    a.send(text)
    try:
        asyncio.run(a.run_until_idle())
    except Exception:
        # 回合失败也要能检查阶段序列（"异常路径有没有退出阶段"正是要测的东西）
        pass
    return phases, a


_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    """读仓库内文件。**必须锚在测试文件所在目录**，不能用 cwd ——
    pytest 从 `agent_loop` 的**父目录**跑（`cd /mnt/f && pytest agent_loop/...`），
    相对路径 `csharp/UI/...` 在那里取不到（第一版全部 FileNotFoundError）。"""
    return (_ROOT / rel).read_text(encoding="utf-8")


def _cs(path: str) -> str:
    return _read(path)


def _cs_code_only(src: str) -> str:
    """剥注释（C# 行注释），结构断言只看代码。"""
    return "\n".join(ln.split("//", 1)[0] for ln in src.splitlines()
                     if ln.split("//", 1)[0].strip())


# ---------------------------------------------------------------------------
# A. Python 侧：序列正确、成对
# ---------------------------------------------------------------------------

def test_phase_sequence_is_complete_and_paired():
    """一回合必须依次报：snapshot → think → stream，且每个进都有对应的出。"""
    phases, _ = _run()
    assert phases == [
        ("snapshot", True), ("snapshot", False),
        ("think", True), ("think", False),
        ("stream", True), ("stream", False),
    ], f"阶段序列不对：{phases}"


def test_every_enter_has_an_exit():
    """进/出必须配平 —— 少一个「出」就等于忙标永远停在那句话上。"""
    phases, _ = _run()
    from collections import Counter
    opens = Counter(p for p, en in phases if en)
    closes = Counter(p for p, en in phases if not en)
    assert opens == closes, f"进/出不配平：进={dict(opens)} 出={dict(closes)}"


def test_no_duplicate_exit():
    """不许出现重复的「出」（第一版就多报了一条 think 离开）。"""
    phases, _ = _run()
    closes = [p for p, en in phases if not en]
    assert len(closes) == len(set(closes)), f"有重复的离开帧：{closes}"


def test_phase_exits_even_when_llm_raises():
    """LLM 抛异常时也必须退出阶段 —— 否则忙标停在「对方正在斟酌…」，玩家以为卡死。"""
    class BoomLlm(EchoLlmClient):
        async def generate(self, *a, **kw):
            raise RuntimeError("上游 500")

    phases, _ = _run(llm=BoomLlm())
    closes = [p for p, en in phases if not en]
    assert "think" in closes, f"LLM 异常后 think 没退出：{phases}"
    assert "snapshot" in closes, "snapshot 没退出"


def test_phase_exits_when_snapshot_raises():
    """取快照抛异常时 snapshot 也要退出。"""
    def boom(*a, **kw):
        raise RuntimeError("桥炸了")

    phases, _ = _run(bridge_ctx=boom)
    closes = [p for p, en in phases if not en]
    assert "snapshot" in closes, f"取快照失败后 snapshot 没退出：{phases}"


def test_stream_starts_on_first_token_not_at_request():
    """`stream` 必须在**首字到达之后**才进 —— 提前进等于骗玩家"已经在说了"。"""
    src = inspect.getsource(DialogueAgent._step)
    # 进入 stream 的两处都在 `_first_token_t` 判定之内
    for m in re.finditer(r'_emit_wait_phase\("stream", True\)', src):
        before = src[:m.start()]
        # 往回找最近的 if 条件
        idx = before.rfind("if tok and not _first_token_t:")
        assert idx != -1, "stream 进入不在首字判定里"
        assert m.start() - idx < 400, "stream 进入离首字判定太远，可能不在同一分支"


# ---------------------------------------------------------------------------
# B. 阈值：来自 ui.busy_hint_after，不是 logging.slow_ms
# ---------------------------------------------------------------------------

def test_config_key_exists_with_default():
    from agent_loop.config.config_loader import DEFAULT_CONFIG
    ui = DEFAULT_CONFIG["ui"]
    assert "busy_hint_after" in ui, "配置里没有 busy_hint_after"
    assert ui["busy_hint_after"] == 10.0, f"默认值变了：{ui['busy_hint_after']}"


def test_config_key_is_in_whitelist():
    """不在白名单 = `get_config` 带不出去 = C# 永远拿不到它。"""
    from agent_loop.config.config_store import UI_WHITELIST
    assert "busy_hint_after" in UI_WHITELIST["ui"], \
        f"ui 白名单里没有它：{UI_WHITELIST['ui']}"


def test_config_key_coerces_and_clamps():
    from agent_loop.config.config_store import _coerce_value
    assert _coerce_value("ui", "busy_hint_after", 15) == 15.0
    assert _coerce_value("ui", "busy_hint_after", "7.5") == 7.5
    assert _coerce_value("ui", "busy_hint_after", 0) == 0.0, "0 = 关闭，是合法值"
    assert _coerce_value("ui", "busy_hint_after", 99999) == 600.0, "上界没夹"
    assert _coerce_value("ui", "busy_hint_after", -5) == 0.0, "负数应夹到 0"


def test_get_config_exposes_it():
    """端到端：`get_config` 的返回值里必须真有这个键（C# 就是读它）。"""
    from agent_loop.config import config_store
    cfg = config_store.get_config()
    assert "busy_hint_after" in (cfg.get("ui") or {}), f"get_config 没带出来：{cfg.get('ui')}"


def test_not_reusing_logging_slow_ms():
    """★ 刻意不复用 `logging.slow_ms` ★

    那个键是**日志**阈值（Python 打 WARNING 用），语义是"该报警了"；
    而这里是"该让玩家看见了"。两者会想分开调（排障时把 slow_ms 调到 5s 看全部阶段，
    不代表玩家想每 5 秒看见一个数字）。而且 logging 块刻意不在 UI 白名单里。
    """
    from agent_loop.config.config_loader import DEFAULT_CONFIG
    from agent_loop.config.config_store import UI_WHITELIST
    assert "logging" not in UI_WHITELIST, "logging 进了白名单 ⇒ 本注释的前提变了，重审"
    assert DEFAULT_CONFIG["logging"]["slow_ms"] != DEFAULT_CONFIG["ui"]["busy_hint_after"] * 1000


# ---------------------------------------------------------------------------
# C. C# 侧：三段文案 + 秒数 + 清理
# ---------------------------------------------------------------------------

PRESENTER = "csharp/UI/ChatPresenter.cs"
WINDOW = "csharp/UI/ChatWindow.cs"
MODMAIN = "csharp/app/ModMain.cs"


def test_three_phase_texts_exist():
    src = _cs(PRESENTER)
    for phase, expect in (("snapshot", "正在读取人物状态"), ("think", "对方正在斟酌"),
                          ("stream", "正在回话")):
        assert f'case "{phase}"' in src, f"没有 {phase} 分支"
        # 文案本身
        i = src.find(f'case "{phase}"')
        assert expect in src[i:i + 120], f"{phase} 的文案里没有「{expect}」"


def test_phase_text_uses_config_threshold():
    """秒数只在超过阈值后追加，且阈值可关（<=0）。"""
    src = _cs_code_only(_cs(PRESENTER))
    assert "_busyHintAfter > 0f && elapsed >= _busyHintAfter" in src, \
        "秒数没有按配置阈值决定（或没处理 <=0 关闭）"


def test_seconds_come_from_local_clock_not_python():
    """秒数从**本地**进入时刻算：窗口没开时帧会晚到，那时该从玩家真看到那刻起算。"""
    src = _cs_code_only(_cs(PRESENTER))
    assert "_waitPhaseSince = Time.unscaledTime" in src, "没记本地进入时刻"
    assert "_waitPhaseSince" in src


def test_setbusy_false_clears_phase():
    """灭忙标必须清阶段：否则下一回合开局会顶着上一回合的「正在回话…（37 秒）」。"""
    src = _cs_code_only(_cs(PRESENTER))
    i = src.find("private void SetBusy(bool busy")
    assert i != -1
    blk = src[i:i + 1400]
    assert '_waitPhase = ""' in blk, "SetBusy(false) 没清阶段"
    assert '_waitPhaseShown = ""' in blk, "SetBusy(false) 没清已显示文案"


def test_wait_phase_refreshes_stale_clock():
    """★ 阶段帧必须刷新 `_lastFrameAt` ★（本文件头 D 段解释）。"""
    src = _cs_code_only(_cs(PRESENTER))
    i = src.find("private void ApplyWaitPhase(")
    assert i != -1
    blk = src[i:i + 1200]
    assert "_lastFrameAt = Time.unscaledTime" in blk, \
        "阶段帧没刷自愈时钟 ⇒ 等 5 分钟的回合会在第 240 秒被误清成空闲"


def test_wait_phase_handled_before_admission_filter():
    """阶段帧必须像 `tool_pending` 那样放在准入过滤**之前**：
    它没有 room_id，按内容帧的规矩会被群窗一律丢弃 —— 而群聊同样需要"在等谁"。"""
    src = _cs(PRESENTER)
    i_wait = src.find('ev0 == "wait_phase"')
    i_open = src.find('if (!_window.IsOpen) { DiagDrop(ev0, payload, "窗未开"); return; }')
    assert i_wait != -1, "没有 wait_phase 分支"
    assert i_open != -1
    assert i_wait < i_open, "wait_phase 排在「窗未开」守卫之后 ⇒ 关窗期间会被丢"


def test_config_pushed_to_all_presenters():
    """配置下发必须能到**所有** presenter 实例。

    ★ 不能走 `ModMain.PresenterInstance` ★：那个字段在 AB_UI 模式下从不被赋值
    （只有置 null），拿它设配置等于什么都没做。
    """
    src = _cs(PRESENTER)
    assert "ConfigureBusyHintAll" in src, "没有静态下发入口"
    assert "_livePresenters" in src, "没有实例表"
    mod = _cs(MODMAIN)
    assert "ConfigureBusyHintAll" in mod, "ModMain 没调下发入口"
    assert "busy_hint_after" in mod, "ModMain 没读配置键"


def test_presenter_registers_and_unregisters():
    """实例表要能登记（Init）与摘除（OnDestroy），否则会一直涨。"""
    src = _cs(PRESENTER)
    i_init = src.find("public void Init(ChatWindow window, WsClient ws)")
    assert i_init != -1
    assert "_livePresenters.Add" in src[i_init:i_init + 2000], "Init 没登记实例"
    i_des = src.find("private void OnDestroy()")
    assert i_des != -1
    assert "_livePresenters.RemoveAt" in src[i_des:i_des + 900], "OnDestroy 没摘除实例"


def test_only_one_ondestroy():
    """不许有两个 `OnDestroy`（第一版加重复了，编译期就撞）。"""
    src = _cs(PRESENTER)
    n = src.count("private void OnDestroy()")
    assert n == 1, f"OnDestroy 定义了 {n} 次"


def test_per_frame_refresh_is_throttled():
    """每帧刷新只在**文案真变化**时写 Text —— 否则 UGUI 每帧重建网格。"""
    src = _cs_code_only(_cs(PRESENTER))
    i = src.find("private void RefreshBusyPhaseText(")
    assert i != -1
    blk = src[i:i + 1200]
    assert "if (want == _waitPhaseShown) return;" in blk, "没有去重 ⇒ 每帧都写 Text"


def test_per_frame_refresh_hooked_into_update():
    src = _cs_code_only(_cs(PRESENTER))
    i = src.find("private void Update()")
    assert i != -1
    assert "RefreshBusyPhaseText()" in src[i:i + 400], "Update 里没挂每帧刷新"


# ---------------------------------------------------------------------------
# D. Python → C# 帧名一致
# ---------------------------------------------------------------------------

def test_wire_event_name_matches_between_sides():
    """Python 发的事件名与 C# 认的必须逐字相同。"""
    py = _read("transport/ws_channel.py")
    assert 'send_event("wait_phase"' in py, "Python 没发 wait_phase"
    cs = _cs(PRESENTER)
    assert 'ev0 == "wait_phase"' in cs, "C# 没认 wait_phase"


def test_phase_names_match_between_sides():
    """Python 的三个阶段名必须与 C# 的三个 case 完全一致。"""
    py = _read("agent/dialogue_agent.py")
    cs = _cs(PRESENTER)
    for phase in ("snapshot", "think", "stream"):
        assert f'_emit_wait_phase("{phase}"' in py, f"Python 没报 {phase}"
        assert f'case "{phase}"' in cs, f"C# 没有 {phase} 分支"


def test_hook_is_released_after_turn():
    """回合收尾必须归还钩子（避免残留引用指向已结束的回合）。"""
    py = _read("transport/ws_channel.py")
    assert "agent.on_wait_phase = None" in py, "回合收尾没归还 on_wait_phase"


def test_phase_is_not_a_step():
    """★ 阶段**不能**混进 `step` ★

    `step` 是账本事实（要落盘、要重放、C# 按 kind 分派）；阶段是当下状态。
    混进去会被帧队列重演一遍，并在账本里留一条没有语义的记录。
    """
    src = _read("agent/dialogue_agent.py")
    tree = ast.parse(src)
    body_src = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_emit_wait_phase":
            body = list(node.body)
            # 剥 docstring：它**应该**提到 _emit_step（那是在解释两者的区别），
            # 不剥的话解释越清楚测试越红。
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            lines = src.splitlines()
            body_src = "\n".join(lines[body[0].lineno - 1: body[-1].end_lineno])
            break
    assert body_src is not None, "没找到 _emit_wait_phase"
    assert "_emit_step" not in body_src, "_emit_wait_phase 走了 _emit_step ⇒ 会被当成账本事实重放"
    assert "session.append" not in body_src, "_emit_wait_phase 落了账本"
