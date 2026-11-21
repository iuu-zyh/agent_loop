# -*- coding: utf-8 -*-
"""压缩纪要「展示 + 修改」的跨语言契约（10-31 用户需求）。

用户原话（拆成可断言的四条）：
  ① 「压缩后产出的总结是不展示的，我需要可以选进行展示……UI 形式可以参考内心思量那样的，
     默认不是展开形式，点击可以展开」→ 折叠行、默认收起、点开看全文；
  ② 「新增一个按钮，就是修改压缩后的信息的按钮」→ 行内「修改」钮；
  ③ 「点击之后……左侧出现新的区域，可以占据半个 UI 的宽……一个大型输入框……
     内置发送按钮」→ 左侧半宽面板 + 多行文本域 + 发送钮；
  ④ 「需要发送成功有提示（不是发送消息，是指修改后的压缩信息传回到了 python 侧）」
     → 系统提示行 + **不发玩家消息**。

## 为什么这里全是"读源码"的断言

这个功能的每一环都**天生静默失败**，而且失败形态都长得像"没问题"：
  · 事件名在传输层白名单里漏一个 → 帧被丢，面板永远不出现（零报错）；
  · 折叠行根节点挂了 Button → 点「修改」顺带把行收起来（看着像手滑）；
  · 编辑面板挂错父级/锚错边 → 跑到屏幕外或盖住顶栏 ✕（只能靠 ESC 退出）；
  · 文本域忘了 `MultiLineNewline` → 回车直接提交，长纪要根本改不了；
  · 发送走成 `SendPlayerMessage` → 玩家凭空多说了一句话（还进账本）；
  · 成功后没提示 → 玩家不知道到底传没传回去。

C# 侧这些都没法在纯 Python 里跑，所以与 `test_tool_sidebar_contract.py` /
`test_chat_skip_button_contract.py` 同一套做法：抠方法体 + 断言关键行。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WS = ROOT / "transport" / "ws_channel.py"
VIEWS = ROOT / "csharp" / "UI" / "ChatItemViews.cs"
PANEL = ROOT / "csharp" / "UI" / "CompactEditPanel.cs"
WIN = ROOT / "csharp" / "UI" / "ChatWindow.cs"
PRES = ROOT / "csharp" / "UI" / "ChatPresenter.cs"
WSC = ROOT / "csharp" / "bridge" / "WsClient.cs"
COMPRESS = ROOT / "compaction" / "compress.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _method(src: str, header: str) -> str:
    """按 `header` 抠出一个方法体（签名到配对的大括号）。找不到直接失败。

    与 `test_tool_sidebar_contract.py::_method` 同款：本仓库的 C# 契约测试都靠它 ——
    方法被改名/删掉时这里红，而不是让后面的断言在空串上静默通过。
    """
    i = src.find(header)
    assert i >= 0, "找不到签名：%s" % header
    j = src.find("{", i)
    assert j >= 0, "签名后没有花括号：%s" % header
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError("方法体没闭合：%s" % header)


# ══════════════════════════════════════════════════════════════════
# 跨语言命名对账（最容易"两边都改了但没对上"的地方）
# ══════════════════════════════════════════════════════════════════

def test_event_rpc_and_field_names_match_between_python_and_csharp():
    """事件名 / RPC 名 / 六个字段名，两端逐字一致。

    任何一处拼错的表现都是**静默**：事件名错 = 帧在传输层被丢；字段名错 = 面板空白或
    永远不可改；RPC 名错 = 发送永远失败（且 Python 侧只会记一条"unknown method"）。
    """
    ws, pres, win, panel = _read(WS), _read(PRES), _read(WIN), _read(PANEL)

    # 事件：Python 发 / C# 收
    assert 'send_event("compact_summary"' in ws
    assert 'case "compact_summary":' in pres
    assert 'ev == "compact_summary"' in _read(WSC), "传输层白名单漏了它 → 帧根本没到 UI 线程"

    # RPC：C# 发 / Python 收
    assert '"edit_compact_summary"' in pres
    assert 'if method == "edit_compact_summary":' in ws

    # 字段：Python 产出（推 event / 回 RPC）↔ C# 读
    for key in ("compaction_id", "text", "chars", "editable", "lock_reason"):
        assert '"%s"' % key in ws, "Python 侧缺少字段 %s" % key
        assert '"%s"' % key in pres or '"%s"' % key in panel, "C# 侧没读字段 %s" % key
    # 历史投影的条目名与 turn 恒 0 / 不给 uid（它是记忆，不是回合内容）
    assert '"kind": "compaction"' in _read(ROOT / "session" / "history.py")


# ══════════════════════════════════════════════════════════════════
# ① + ② 折叠行
# ══════════════════════════════════════════════════════════════════

def test_summary_row_is_collapsed_by_default_and_has_edit_button():
    src = _read(VIEWS)
    i = src.index("public class SummaryGroup")
    block = src[i:src.index("public class ChatDivider")]

    assert 'body.SetActive(false);' in block, "正文必须默认收起（用户：默认不是展开形式）"
    assert '"压缩纪要 · "' in block, "标题要写成「压缩纪要 · N 字」"
    assert '"修改"' in block, "行内必须有「修改」钮"
    assert '" · 已锁定"' in block, "不可改时标题必须显式标出（光置灰不解释原因）"
    assert 'horizontalOverflow' in block and 'VerticalWrapMode.Overflow' in block, \
        "展开后正文要能换行、不被截断"

    # ★ 根节点不许挂 Button：uGUI 点击会冒泡，子按钮点一下会连父按钮的展开一起触发
    assert "go.AddComponent<Button>()" not in block, \
        "折叠行根节点挂了 Button → 点「修改」会顺手把这一行收起来"
    assert "toggle.AddComponent<Button>()" in block, "展开按钮应挂在左侧标题那一条上"
    assert "edit.AddComponent<Button>()" in block, "「修改」钮必须是标题的兄弟节点"

    # 点击「修改」把 (compactionId, 正文) 交给上层，不自己发请求（分层）
    assert "EditRequested" in block and "h(_compactionId, _text)" in block


def test_chat_window_owns_summary_rows():
    src = _read(WIN)
    assert "AppendSummaryRow" in src
    assert "_summaryRows.Clear()" in _method(src, "private void ResetContent()"), \
        "内容整块重建时必须清表（否则锁/刷会作用到已销毁的行上）"
    assert "LockSummaryEdits" in src and "RefreshSummaryRow" in src
    # 新回合号一到就锁定（Python 也会拒，这里让按钮立刻跟上）
    note = _method(src, "public void NoteTurn(int turn)")
    assert "LockSummaryEdits" in note, "回合号变化必须锁定纪要修改"
    # 关窗 / 群聊 / 删除模式：面板必须收起
    assert "CompactEditPanel.Hide();" in _method(src, "public void Close()")
    assert "CompactEditPanel.Hide();" in _method(src, "private void SetRoomButtonsVisible(bool show)")
    assert "CompactEditPanel.Hide();" in _method(src, "public void EnterSelecting()")


# ══════════════════════════════════════════════════════════════════
# ③ 左侧面板
# ══════════════════════════════════════════════════════════════════

def test_edit_panel_layout_is_left_half_screen():
    src = _read(PANEL)
    assert "private const float PanelW = 600f;" in src, "用户定的：占 BG（1200 宽）的一半"
    assert "private const float TopInset = 40f;" in src, "顶边必须停在 760（不能盖住顶栏 ✕）"

    build = _method(src, "public static void Build(RectTransform bg")
    # 左锚点 + 上下拉伸（与 ToolPanel 的右锚点镜像）
    assert "rt.anchorMin = new Vector2(0f, 0f);" in build
    assert "rt.anchorMax = new Vector2(0f, 1f);" in build
    assert "rt.anchoredPosition = new Vector2(0f, -TopInset);" in build
    assert "PanelW" in build

    # 输入框：多行 + 克隆对话窗自己的（同观感）；两个属性缺一个都改不了长纪要
    assert "InputField.LineType.MultiLineNewline" in src
    assert "CloneInput" in src and "Instantiate(template.gameObject)" in src
    assert "inp.characterLimit = MaxChars;" in src
    assert "verticalOverflow = VerticalWrapMode.Overflow" in src

    # 发送钮 + 三态（发送中/失败/成功）
    assert '"发送"' in src and '"发送中…"' in src
    assert "SetSending" in src and "ShowError" in src and "ShowDone" in src
    assert "面板不关" in src or "ShowError" in src   # 失败保留文本（注释与实现都要在）


def test_panel_submit_is_not_a_player_message():
    """★ 用户原话「不是发送消息」：发送只走 RPC，绝不能碰玩家发言那条路。"""
    src = _read(PRES)
    body = _method(src, "private void OnSummaryEditSubmit(string text)")
    assert '"edit_compact_summary"' in body
    assert "SendPlayerMessage" not in body, "改纪要绝不能走玩家发言通道（会凭空多一句话进账本）"
    assert "SendRequest" in body
    # 成功提示：系统提示行 + 刷新折叠行（不是气泡）
    assert 'AppendSystemNotice("压缩纪要已更新' in body
    assert "RefreshSummaryRow" in body


# ══════════════════════════════════════════════════════════════════
# ④ 联动：直播 / 回放 / 群聊与 ESC
# ══════════════════════════════════════════════════════════════════

def test_presenter_shows_summary_and_respects_room_boundary():
    src = _read(PRES)
    body = _method(src, "private void OnCompactSummary(JObject payload)")
    assert "AppendSummaryRow" in body
    assert 'room_id' in body and "IsRoomMode" in body, "群聊回合里压出来的不该进单聊窗"
    assert "_window.CurrentNpcId" in body, "必须按 npc 过滤（否则别人的记忆会画到当前窗）"

    # ESC：编辑面板先关（否则按一次 ESC 会把整个对话窗关掉，而玩家只是想收面板）
    esc = src[src.index("if (CompactEditPanel.IsOpen)"):]
    assert "CompactEditPanel.Hide();" in esc.split("return;")[0]


def test_replay_draws_compaction_rows_from_history():
    src = _read(WIN)
    # ★ 11-20 起断言对象改成 `ApplyHistoryItem` ★：回放拆成分帧构建后，那个 switch
    #   整块搬进了 `ApplyHistoryItem`，`ReplaceHistory` 只剩分流。断言跟着搬，不放松。
    body = _method(src, "private void ApplyHistoryItem(JToken token)")
    assert 'case "compaction":' in body, "重开窗必须能把纪要行画回来（否则只有本次会话看得到）"
    assert 'AppendSummaryRow' in body
    for key in ('"compaction_id"', '"text"', '"editable"', '"lock_reason"'):
        assert key in body, "回放分支没读字段 %s" % key
    _case = body[body.index('case "compaction":'):body.index('case "think":')]
    assert "BindKey(" not in _case, "纪要行不属于任何回合，不该绑删除模式的选中键"


def test_compressor_hands_out_summary_and_seq():
    """压缩器要把正文与新节点 seq 一起交出去 —— 否则推送/定位都得再扫一遍账本。"""
    src = _read(COMPRESS)
    assert '"summary": summary, "summarySeq": int(summary_seq)' in src
    assert "def _replace(self, session, start_seq: int, end_seq: int, summary: str, cid: str) -> int:" in src
