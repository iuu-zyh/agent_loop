"""群聊「跳过」按钮的跨语言契约：`UIChatAi.prefab` 的 `BG/Input/SkipBtn` ↔ C# ↔ Python 事件。

这个功能的每一处都**天生静默失败**，所以这里钉的不是"有没有按钮"，而是四条语义：

  ① **路径字符串**是 C# `Find` 的契约（写错只静默拿到 null ⇒ 按钮永远不存在，零报错）；
  ② **出厂隐藏**是 fail-safe（预制件不知道当前是不是群聊；出厂可见 = 单聊窗里冒出一个
     点了没反应的按钮），代价是 C# 必须在**两条路**上都显式设置显隐 —— 尤其
     `ExitSelecting` 那句：`SendBtn` 的恢复是**无条件** `SetActive(true)`，
     照抄到跳过键上，就会在**单聊窗退出删除模式时把它点亮**；
  ③ **几何**：发送键正上方 8px、同一列、不压住输入文字带（压住了就是"玩家正在打的字被按钮盖住"，
     不报错，只在实机上看得见）；
  ④ **事件名与路径各只有一处真相**：`room_skip` 在 C# 里只许有一个发送点，
     Python 侧只在 `ws_channel` 的事件分发里接一次（多一个入口 = 第二个真相源）。

预制件真源不在本仓库（在 AB 打包工程里），所以依赖它的用例整段 skipif；
C# 源码与补丁脚本那一半任何机器都能跑。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATCH_SCRIPT = ROOT / "scripts" / "dev" / "prefab_patch_chat_skip_button.py"
PREFAB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/"
              "资源修改教程/ResBuildABProject/Assets/Resources/UI/UIChatAi.prefab")

WIN = ROOT / "csharp" / "UI" / "ChatWindow.cs"
REFS = ROOT / "csharp" / "UI" / "ChatWindowRefs.cs"
PRES = ROOT / "csharp" / "UI" / "ChatPresenter.cs"
AB_PANEL = ROOT / "csharp" / "UI" / "AbChatPanel.cs"
WS = ROOT / "csharp" / "bridge" / "WsClient.cs"
WS_CHANNEL = ROOT / "transport" / "ws_channel.py"
ROOM_FLOW = ROOT / "room" / "room_flow.py"

#: 跨语言路径契约（预制件节点路径 = `CollectRefs` 里 Find 的那个字符串）。
SKIP_PATH = "/UIChatAi/BG/Input/SkipBtn"
SKIP_FIND = "BG/Input/SkipBtn"
#: C# 只接按钮本体，不碰文案子节点。
SKIP_TEXT_PATH = SKIP_PATH + "/Text"


def _mod():
    if str(ROOT / "scripts" / "dev") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    import prefab_patch_chat_skip_button as M
    return M


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _go_at(pf, path: str):
    """按**绝对路径**取节点（不能用 `find_go(leaf)`：预制件里有多个同名 `Text`/`Placeholder`）。"""
    parts = [s for s in path.strip("/").split("/") if s]
    go = pf.find_go(parts[0])
    assert go, "找不到根节点 %s" % parts[0]
    for seg in parts[1:]:
        go = pf.child_go_named(go, seg)
        assert go, "路径 %s 在 %s 处断了" % (path, seg)
    return go


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员正文（含花括号）。签名必须唯一。"""
    src = _strip_comments(src)
    assert src.count(signature) == 1, "签名不唯一（%d 次）：%s" % (src.count(signature), signature)
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + signature)


def _py_func(src: str, name: str) -> str:
    """取一个 Python 函数的**源码正文**（按 AST，不按大括号）。

    ⚠ 这里**不能**复用 `_method`：那个是按 `{}` 配对的 C# 版，而 Python 的
    docstring/注释/f-string 里到处是 `{}` —— 实测它会把 `handle_room_skip` 的正文
    切到 f-string 片段 `'{before}'` 上，于是所有"函数里有没有 X"的断言全部假红/假绿。
    """
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError("找不到函数：" + name)


# ----------------------------------------------------------------------
# ① 补丁脚本自身（不需要真预制件）
# ----------------------------------------------------------------------

def test_clone_source_is_the_send_button_not_the_top_bar():
    """★ 克隆源必须是 `SendBtn`。

    顶栏那批按钮克隆 `CompactBtn` 是对的（它们与它同排）；这一颗在**输入区**、
    与正下方那颗同属"发出去"这个动作 —— 克隆源选错的表现是"这颗按钮的字比下面那颗
    大一号/颜色不同"，**不报错**，只在实机上看得出来。
    """
    M = _mod()
    assert M.CLONE_SRC == "SendBtn"
    assert M.PARENT == "Input", "父节点必须是 BG/Input（不是 BG 直下 —— 那样它不跟着输入框走）"


def test_geometry_is_derived_not_hardcoded():
    """★ "发送键正上方 8px"必须由发送键的实测几何**算出来**，且算出来就是 53。

    写死 53 的坏处不是错，而是**将来改发送键位置时它不会跟着动** —— 两个按钮叠在一起，
    下面那个永远点不到，而预制件里看不出任何异常。
    """
    M = _mod()
    assert M.SKIP_POS_X == M.SEND_POS_X, "跳过键与发送键必须同 x（错开 1px 肉眼就是歪的）"
    assert M.SKIP_POS_Y == M.SEND_POS_Y + M.SEND_H / 2.0 + M.GAP + M.SEND_H / 2.0
    assert M.SKIP_POS_Y == 53.0
    assert M.SKIP_POS == "{x: -20, y: 53}"
    assert M.BACKUP_SUFFIX != ".bak_chatgroupbtns", \
        "备份后缀不能与顶栏补丁共用（两次手术会互相覆盖原始件）"


def test_the_two_patches_use_disjoint_fileid_segments():
    """★ 号段不许复用：顶栏补丁占 887，本条占 888。

    共用基址正是 `prefab_patch_contact_groups` 踩过的撞号事故（`AssertionError: 新 fileID 撞号`）。
    """
    M = _mod()
    sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    import prefab_patch_chat_group_buttons as G
    assert M.NEW_ID_BASE == 8880000000000000000
    assert G.NEW_ID_BASE == 8870000000000000000
    assert M.NEW_ID_BASE != G.NEW_ID_BASE


def test_text_band_overlap_predicate_has_teeth():
    """★ "不压文字带"那条判据的**判据本身**也要被测：内联布尔值写反了照样恒真/恒假。

    这里用三段几何把 `rects_overlap` 钉住：真按钮不与文字带相交；把它挪进文字带必然相交；
    把它撑宽到跨过文字带右边缘也必然相交（后者正是"把按钮改宽"这条改动的形态）。
    """
    M = _mod()
    band = ((M.TEXT_BAND[0], M.TEXT_BAND[1]), (M.TEXT_BAND[2], M.TEXT_BAND[3]))
    real = ((-20.0, 0.0), (0.0, 53.0))          # 右锚定、相对 Input 右边缘的近似
    assert M.rects_overlap(real, band) is False
    assert M.rects_overlap(((500.0, 900.0), (32.0, 102.0)), band) is True, "判据漏掉了相交"
    assert M.rects_overlap(((8.0, 788.1), (0.0, 30.0)), band) is False, \
        "边界相接被判成相交了（x 恰好等于文字带右边缘）"
    # 真实产物的矩形（相对于输入面板）用常量独立复算一遍
    kx0 = M.INPUT_W + M.SKIP_POS_X - M.SEND_W      # 右锚点 + pos.x - 半宽
    kx1 = M.INPUT_W + M.SKIP_POS_X
    ky0 = M.SKIP_POS_Y - M.SEND_H / 2.0
    ky1 = M.SKIP_POS_Y + M.SEND_H / 2.0
    assert M.rects_overlap(((kx0, kx1), (ky0, ky1)), band) is False, \
        "按常量实算的跳过键压住了文字带"


# ----------------------------------------------------------------------
# ② 真预制件（AB 打包工程不在本机时整段跳过）
# ----------------------------------------------------------------------

@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_passes_its_own_verify():
    M = _mod()
    assert M.verify(_read(PREFAB)) is True


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_has_the_path_contract():
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    go = _go_at(pf, SKIP_PATH)
    assert pf.path_of(go) == SKIP_PATH, "节点路径不符：%s" % pf.path_of(go)
    # 只接按钮本体；文案子节点是预制件自己的事
    assert _go_at(pf, SKIP_TEXT_PATH), "缺少文案子节点"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_is_idempotent():
    M = _mod()
    text = _read(PREFAB)
    assert M.patch(text, do_log=False) == text, "补丁不幂等 —— 重跑会改坏预制件"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_factory_state_is_hidden_and_the_send_button_is_untouched():
    """★ 出厂必须**隐藏**（fail-safe：C# 显隐逻辑一次都没跑，单聊窗也要和今天一模一样），
    且本补丁**只追加**节点 —— 发送键/输入框一个字节都不许动。"""
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    assert pf.field(_go_at(pf, SKIP_PATH), "m_IsActive") == "0", "跳过键出厂应隐藏（仅群聊点亮）"
    send = pf.rt_of_go(_go_at(pf, "/UIChatAi/BG/Input/SendBtn"))
    inp = pf.rt_of_go(_go_at(pf, "/UIChatAi/BG/Input/ChatInput"))
    assert pf.field(send, "m_AnchoredPosition") == "{x: -20, y: 15}", "发送键的位置被改了"
    assert pf.field(send, "m_SizeDelta") == "{x: 40, y: 30}", "发送键的尺寸被改了"
    assert pf.field(inp, "m_SizeDelta") == "{x: 0, y: 110}", "输入框被改了"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_sits_exactly_above_the_send_button_and_clears_the_text_band():
    """★ 几何：同列、上方 8px、不压输入文字带。三条都是"只在实机上看得见"的那类错。"""
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    send = pf.rt_of_go(_go_at(pf, "/UIChatAi/BG/Input/SendBtn"))
    skip_rt = pf.rt_of_go(_go_at(pf, SKIP_PATH))
    (sx0, sx1), (sy0, sy1) = M.rect_in_parent(pf, send, M.INPUT_W, M.INPUT_H)
    (kx0, kx1), (ky0, ky1) = M.rect_in_parent(pf, skip_rt, M.INPUT_W, M.INPUT_H)
    assert abs(kx0 - sx0) < 1e-3 and abs(kx1 - sx1) < 1e-3, "与发送键不在同一列"
    assert abs((ky0 - sy1) - M.GAP) < 1e-3, "与发送键的间距不是 %.0fpx（实际 %.2f）" % (M.GAP, ky0 - sy1)
    assert ky1 <= M.INPUT_H, "跳过键超出了输入面板上沿（%.1f > %.1f）" % (ky1, M.INPUT_H)
    bx0, bx1, by0, by1 = M.TEXT_BAND
    overlap = (kx0 < bx1) and (kx1 > bx0) and (ky0 < by1) and (ky1 > by0)
    assert not overlap, "跳过键压在输入文字带上了（玩家正在打的字会被按钮盖住）"
    # 文案
    assert pf.field(M.find_text_comp(pf, _go_at(pf, SKIP_TEXT_PATH)), "m_Text") == M.yaml_str(M.SKIP_TEXT)


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_new_nodes_all_come_from_the_888_segment():
    """★ 新增节点的 fileID 必须全部落在 888 段 —— "看到号段就知道是哪个补丁"这条约定。"""
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    span = [int(x) for x in pf.all_ids()
            if str(x).isdigit() and M.NEW_ID_BASE <= int(x) < M.NEW_ID_BASE + 1000]
    assert span, "预制件里没有任何 888 段的号 —— 本补丁没打上？"
    # 一条按钮 = 9 个对象（GO + RT + CR + Image + Button + 子 GO + 子 RT + 子 CR + 子 Text）
    assert len(span) == 9, "新增对象数应为 9，实际 %d：%s" % (len(span), sorted(span))


# ----------------------------------------------------------------------
# ③ C# 源码（不需要预制件）
# ----------------------------------------------------------------------

def test_collectrefs_finds_the_skip_node():
    """跨语言路径契约的另一边：`CollectRefs` 必须真的 Find 那个字符串。"""
    body = _method(_read(AB_PANEL), "private static ChatWindowRefs CollectRefs(Transform root)")
    found = set(re.findall(r'Find(?:Go|Rt|Comp<[^>]+>)\(\s*root\s*,\s*"([^"]+)"\s*\)', body))
    assert SKIP_FIND in found, "CollectRefs 没有 Find(%r) —— 预制件里有这个节点也白搭" % SKIP_FIND
    assert "r.SkipButton = FindGo(root, \"%s\")" % SKIP_FIND in body, \
        "找到了但没赋给 SkipButton（接了别的字段同样静默）"


def test_skip_button_is_optional_not_critical():
    """★ 缺失 = **没有这个入口**，不是"拒绝装配"。

    进了 `MissingCritical` 的后果是整个对话窗装不起来（玩家连单聊都打不开）——
    为了让一个群聊按钮出现，把整个窗口赔进去，方向反了。
    """
    src = _read(AB_PANEL)
    body = _method(src, "private static List<string> MissingCritical(ChatWindowRefs r)")
    assert "SkipButton" not in body, "跳过键进了 MissingCritical —— 缺它会让整个对话窗装不起来"
    assert "SendButton" in body, "发送键本该是 critical（这条断言用来证明上面那句不是空过）"


def test_refs_field_exists():
    assert "public GameObject SkipButton;" in _read(REFS)


def test_visibility_restore_in_delete_mode_is_room_conditioned():
    """★★ 本功能最容易踩的那个坑：`ExitSelecting` 里**不能**无条件 `SetActive(true)`。

    `SendBtn` 那句无条件恢复是对的（它在两个模式都该可见）；跳过键出厂隐藏、**只在群聊可见**，
    照抄的后果是"在单聊窗里退出删除模式 ⇒ 跳过键亮起来"，而它在单聊下点了只会提示
    「当前不在群聊中」—— 正是本仓库反复写死的"看得见却没用"。
    """
    src = _read(WIN)
    enter = _method(src, "public void EnterSelecting()")
    exit_ = _method(src, "public void ExitSelecting()")
    assert "_skipBtn.SetActive(false)" in enter, "选择模式下跳过键没收起来（点下去会在选删除时另起一轮）"
    line = [ln for ln in exit_.splitlines() if "_skipBtn" in ln]
    assert line, "ExitSelecting 没恢复跳过键（退出删除模式后它永远不见了）"
    assert "IsRoomMode" in line[0], "★ 恢复条件缺 IsRoomMode：单聊窗退出删除模式会把跳过键点亮"
    assert "_skipBtn.SetActive(IsRoomMode" in exit_.replace(" ", ""), \
        "恢复式必须是 _skipBtn.SetActive(IsRoomMode && !Selecting)：%r" % line[0].strip()


def test_window_exposes_and_wires_the_event():
    src = _read(WIN)
    assert "public event Action SkipRequested;" in src
    init = _method(src, "public void Init(ChatWindowRefs refs)")
    assert "SkipRequested" in init and "ClickUtils.Attach" in init, "按钮没接线（点了不会有反应）"


def test_presenter_guards_busy_and_writes_a_local_line_first():
    """★ 点下去的**本地系统提示**不能省：跳过这一轮玩家没有气泡上屏，
    若所有人又都弃权，屏幕上就是"忙碌标记转一圈、什么都没变"——与按钮坏了长得一样。
    这行提示是"我确实按到了"的即时凭据（Python 那句终帧文案是另一条腿）。"""
    src = _read(PRES)
    body = _method(src, "private void OnSkipClicked()")
    assert "IsRoomMode" in body, "没判群聊模式（单聊窗口没有'让 NPC 自己接'这回事）"
    assert "RoomActionAllowed" in body, "忙态没挡：连点两下 = 排队两整轮 LLM 回合，而玩家看不到区别"
    assert "AppendSystemNotice" in body, "点下去没有即时反馈（静默失败）"
    assert body.index("AppendSystemNotice") < body.index("SendRoomSkip"), \
        "系统提示必须在发请求**之前**落（先发后提示，提示会排在后面那段等待里）"
    assert "SetBusy(true)" in body, "没置忙 —— 这一轮跑起来时窗口看起来是空闲的"
    assert "CurrentRoomMembers" in body, "没判空群（plan_round([]) 必然什么都不发生，点了等于白点）"
    # 订阅与退订都在（漏退订 = 换窗口/重开后一次点击触发两次请求）
    assert "_window.SkipRequested += OnSkipClicked;" in src
    assert "_window.SkipRequested -= OnSkipClicked;" in src


def test_room_skip_has_exactly_one_send_site():
    """★ `room_skip` 在 C# 里只许有**一个**发送点（多一个入口 = 第二个真相源）。

    与 `open_room` 那条单入口守卫同一条纪律：两个地方各拼一次 JSON，第二个必然会漂移
    （少 `room_id`、多一个没用的 `text`），而两处都不报错。
    """
    src = _strip_comments(_read(WS))
    body = _method(_read(WS), "public virtual void SendRoomSkip(string roomId)")
    assert '["event"] = "room_skip"' in body, "事件名不对（Python 侧收不到任何东西）"
    assert '["room_id"] = roomId' in body
    assert '["text"]' not in body, "跳过没有正文载荷（发了 text 就说明它在冒充 room_message）"
    assert src.count('"room_skip"') == 1, 'room_skip 出现了 %d 次，应恰好一处' % src.count('"room_skip"')
    # C# 侧唯一的发送入口必须是 SendRoomSkip
    assert src.count("SendRoomSkip") == 1


def test_python_receives_the_event_and_never_reuses_room_message():
    """★ Python 侧：`room_skip` 有分发、有 handler，且 handler **不碰** `run_message`。"""
    src = _read(WS_CHANNEL)
    assert 'msg.get("event") == "room_skip"' in src, "事件分发里没有 room_skip（帧被静默丢弃）"
    assert "def register_room_skip_handler" in src
    assert "def handle_room_skip" in src
    body = _py_func(_read(WS_CHANNEL), "handle_room_skip")
    assert "run_round" in body, "跳过没走 run_round（那是跳过唯一该走的入口）"
    assert "run_message" not in body, "跳过走了 run_message —— 会往流水里塞一条玩家发言"
    assert "_fail_room_round" in body, "失败路径没有回终帧（群窗会一直显示'正在输入'）"


def test_skip_round_differs_from_a_player_round_in_exactly_two_places():
    """★ 两条入口的差异**只有两处**（催话文案 + 全员沉默时终帧写不写），且都必须非空。

    写成"跳过也复用常规催话"的后果不是报错，而是**模型会继续沉默**：它上一条回复往往
    就是【沉默】（上一轮弃权才轮得到跳过），而常规催话没说"玩家这一轮不会开口"，
    于是刚答完玩家的 NPC 有理由认为该等玩家接话 —— 等的人正是按下跳过的人。
    """
    src = _read(ROOM_FLOW)
    assert "NUDGE_TEXT_SKIP = (" in src and "SILENT_ROUND_NOTE = " in src
    body = _py_func(src, "run_round")
    assert "nudge=NUDGE_TEXT_SKIP" in body, "跳过那一轮没用专用催话"
    assert "note_on_silence=SILENT_ROUND_NOTE" in body, "全员沉默时终帧没有文案"
    normal = _py_func(src, "run_message")
    assert "nudge=NUDGE_TEXT" in normal and 'note_on_silence=""' in normal, \
        "玩家发言轮的行为被改了（它必须保持「全员沉默不写文案」）"
