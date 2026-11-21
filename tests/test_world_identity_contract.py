"""存档身份「补发/补投」的**跨语言接线契约**（09-18）。

## 这条链在解决什么

`world_id`（= 玩家 unitID）只活在 Python 进程内存里，而"补发身份"原先**只有**
`save_happened` / `load_happened` 两条事件。Python 重启（配置面板保存 / BrainLink 拉起 /
崩溃重生）后身份为空 → 那段时间的读写全落**扁平目录**：开窗看不见本存档历史，
下一次存档还会把短账本**整份覆盖**进真存档（静默丢记忆）。

行为层由 `tests/test_world_identity_recovery.py` 钉（11 条，含"修复前会覆盖"的对照）。
本文件只钉**接线**：C# 有没有在连接时补发、save/load 会不会再被静默吞掉、
Python 那三道闸门还在不在。这一层防的是"有人把接线改回去"——改回去之后行为测试
可能仍然全绿（它直接调 handler，不经过 C# 的发送路径），而实机又坏了。

## 钉的十条

  ① C# 重连回调里**同时**做两件事：补发 `world_hello` + 补投排队帧；
  ② `save_happened` / `load_happened` 走 `SendWorldFrame`（不再裸调 `SendRequest`）；
  ③ `SendWorldFrame` 未连接时**排队**（而不是像 `WsClient.SendRequest` 那样直接回 ok:false）；
  ④ 排队有三条规则且**有独立单测**（未连接排队 / 超限丢最旧 / 补投时仍未连接则不烧帧）
     —— 规则被切进零 Unity 依赖的 `WorldFrameOutbox`，跑 `csharp/tests/WorldFrameOutboxTests`；
  ⑤ `FillWorldIdentity` 取不到身份就**不写键**（不许猜、不许写空串）；
  ⑥ Python `world_hello` 分支存在、缺身份时用 INFO（主菜单是常态，不能刷假告警）；
  ⑦ `world_hello` **只在换档时**丢弃（重连不许抹掉未固化增量）；
  ⑧ `save_happened` 换档时**先丢弃再固化**（顺序反了就是那条静默覆盖）；
  ⑨ `flush_all` 按账本**出处**对账，出处不符一律跳过；
  ⑩ 生产形态判据来自 `AGENT_LOOP_PPID`（与看门狗同源），且 `require_world` 真的传进了
     AgentLoop 与 ContactsService。

⚠ 诚实边界：C# 那几条是**源码级**契约（`ModMain.cs` 的接线），不是编译期保证 ——
   真正的时序要等实机（Python 重启后立刻聊天）验收。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODMAIN = ROOT / "csharp" / "app" / "ModMain.cs"
OUTBOX = ROOT / "csharp" / "bridge" / "WorldFrameOutbox.cs"
OUTBOX_TESTS = ROOT / "csharp" / "tests" / "WorldFrameOutboxTests"
WS_PY = ROOT / "transport" / "ws_channel.py"
LOOP_PY = ROOT / "agent_loop.py"
CONTACTS_PY = ROOT / "storage" / "contacts_store.py"
SERVER_PY = ROOT / "scripts" / "server.py"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """剥注释。**判位移之前必须剥** —— 注释里会写"以前是这么写的"，
    拿它当代码会把旧写法误判成还在用（其他契约文件踩过这个坑）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return src


def _method_body(src: str, signature: str) -> str:
    """取 C# 方法的函数体（按大括号配平）。找不到返回空串。"""
    i = src.find(signature)
    if i < 0:
        return ""
    j = src.find("{", i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
    return ""


CS = _strip_comments(_src(MODMAIN))
WS = _strip_comments(_src(WS_PY))
LOOP = _strip_comments(_src(LOOP_PY))
CONTACTS = _strip_comments(_src(CONTACTS_PY))
SERVER = _strip_comments(_src(SERVER_PY))


def test_cs_reconnect_resends_identity_and_pending_frames():
    """① 重连回调必须两件事都做（少一件就还有一半窗口会丢档）。"""
    body = _method_body(CS, "private void OnWsReconnected()")
    assert body, "OnWsReconnected 不见了"
    assert "SendWorldHello()" in body, "重连不再补发存档身份 → Python 重启后整段窗口落扁平目录"
    assert "FlushPendingWorldFrames()" in body, "断线期间攒下的 save/load 不再补投"


def test_cs_save_load_go_through_the_queueing_sender():
    """②③ save/load 必须走 `SendWorldFrame` —— 裸 `SendRequest` 在未连接时静默丢帧。
    队列规则本身在 `WorldFrameOutbox`（行为由 C# 单测钉，见下一个用例）。"""
    for name in ("OnSaveDataEvent", "OnIntoWorldEvent"):
        body = _method_body(CS, "private void " + name + "(ETypeData e)")
        assert body, name + " 不见了"
        assert 'SendWorldFrame("' in body, \
            name + " 没走 SendWorldFrame（未连接时会静默丢帧，整局没有存档身份）"
        assert 'SendRequest("save_happened"' not in body
        assert 'SendRequest("load_happened"' not in body

    sender = _method_body(CS, "private bool SendWorldFrame(string method, JObject p)")
    assert "WorldOutbox.Send(" in sender, "SendWorldFrame 没接队列（未连接时无处可排）"

    # 队列的"怎么发/连没连"必须取自真 WS 客户端（不是常量、不是别的连接）
    prop = _method_body(CS, "private WorldFrameOutbox<JObject> WorldOutbox")
    assert "IsConnected" in prop, "队列没看连接状态"
    assert "SendRequest(" in prop, "队列的发送动作没接到 WsClient"


def test_cs_outbox_rules_and_their_unit_test_exist():
    """④ 队列三条规则 + 它们有独立单测（纯逻辑切出来的唯一理由就是"能测"）。

    规则本身（未连接排队 / 超限丢最旧 / 补投时仍未连接则不烧帧）跑
    `csharp/tests/WorldFrameOutboxTests`（net8.0，WSL 里能跑）。
    这里钉的是"规则还在 + 单测工程还链着同一份源码"。
    """
    outbox = _src(OUTBOX)
    assert "_queue.Add(" in outbox, "未连接时不再排队"
    assert "MaxPending" in outbox and "RemoveAt(0)" in outbox, "超限时不是丢最旧的那条"
    assert "if (!_connected()) return 0;" in outbox, "补投时不再看连接状态（会把帧原地烧掉）"
    assert "MaxPending = maxPending < 1 ? 1 : maxPending;" in outbox, "上限没夹取（0 会越界取头）"

    proj = OUTBOX_TESTS / "WorldFrameOutboxTests.csproj"
    assert proj.is_file(), "队列单测工程被删了（这层就再没有自动化覆盖）"
    assert "WorldFrameOutbox.cs" in _src(proj), "单测工程没链同一份源码（改副本 = 测了个假的）"


def test_cs_identity_is_omitted_when_unknown():
    """⑤ 取不到身份就**不写空串**：写空串会被 Python 当成"提供了但为空"，是误导。

    （10-18 三态）读不到时的处置从"什么都不写"细化为"按帧型决定"：世界事件（`load_happened`）
    **明确宣告** `identity:"unknown"`（那是"旧身份作废"的判据，见 `test_world_identity_failclosed.py`），
    连接事件（`world_hello`）仍然什么都不写。本契约钉的是**空串这条铁律不变**。
    """
    body = _method_body(CS, "private static void FillWorldIdentity(JObject p, bool worldEvent")
    assert body, "FillWorldIdentity 不见了（签名变了？本契约的判据必须跟着改，否则等于没有契约）"
    assert "if (!string.IsNullOrEmpty(wid))" in body, "取不到 world_id 也可能把键写进去"
    assert 'p["world_id"] = ""' not in body and 'p["world_id"] = string.Empty' not in body, (
        "写了空串 —— Python 会把它当成「提供了但为空」（与「没提供」是两件事），是误导"
    )
    assert 'p["identity"] = "unknown"' in body, "读不到身份时没有明确宣告（三态协议少一态 = 沿用旧档）"


def test_py_world_hello_branch_exists_and_is_soft_when_unidentified():
    """⑥ 缺身份用 INFO：主菜单里没有身份是**常态**，用 WARNING 会让每次开机都刷假告警。"""
    assert 'method == "world_hello"' in WS, "Python 侧没有 world_hello 分支"
    i = WS.find('method == "world_hello"')
    branch = WS[i : WS.find('method == "open_chat"', i)]
    assert 'missing_level="info"' in branch, "world_hello 缺身份时没降级为 INFO"
    assert "load_rooms()" in branch, "补发身份没顺带把群名录载回来（重启后群列表会空）"


def test_py_world_hello_discards_only_on_real_change():
    """⑦ 只在换档时丢弃 —— 重连（同一个档、Python 还活着）不许抹掉未固化增量。"""
    i = WS.find('method == "world_hello"')
    branch = WS[i : WS.find('method == "open_chat"', i)]
    assert "if changed:" in branch, "world_hello 无条件丢弃活体 → 重连会吞掉刚聊完的内容"
    assert "discard_all()" in branch


def test_py_save_discards_before_flush():
    """⑧ 顺序即正确性：先丢弃再固化。反了就是把短账本覆盖进真存档。"""
    i = WS.find('method == "save_happened"')
    branch = WS[i : WS.find('method == "load_happened"', i)]
    assert "discard_all()" in branch, "save_happened 换档时不再丢弃（那条静默覆盖回来了）"
    assert "flush_all()" in branch
    assert branch.find("discard_all()") < branch.find("flush_all()"), \
        "save_happened 先固化后丢弃 —— 顺序反了（覆盖已经发生）"


def test_py_flush_checks_ledger_provenance():
    """⑨ 出处对账：flush 不许把"在别的命名空间里读/建出来的账本"写进当前命名空间。"""
    i = LOOP.find("def flush_all(")
    body = LOOP[i : LOOP.find("def discard_all(", i)]
    assert "_namespace.get(" in body, "flush_all 不再按账本出处对账（跨命名空间覆盖回来了）"
    assert "require_world" in body, "flush_all 没有未定档闸门"


def test_py_require_world_is_wired_from_game_launch():
    """⑩ 生产形态判据与看门狗同源（`AGENT_LOOP_PPID`），且真的传进了两个服务。"""
    assert "def _parent_pid()" in SERVER, "server.py 没有统一的父进程判据"
    assert "require_world=game_launched" in SERVER, "AgentLoop 没收到 require_world"
    assert "require_world=loop.require_world" in SERVER, "ContactsService 没收到 require_world"
    # 未定档闸门本身
    i = LOOP.find("def create(")
    body = LOOP[i : LOOP.find("def _publish(", i)]
    assert "require_world and not self.world_id" in body, "create 在未定档时仍会读扁平目录"
    assert "require_world and not self.world_id" in CONTACTS, "通讯录没有未定档闸门"
