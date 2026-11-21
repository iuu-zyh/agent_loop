"""对话窗（UIChatAi.prefab + ChatWindow/ChatPresenter）的「群聊逐行立绘 + 拉人/踢人入口」契约。

P3 第 4 步改了对话窗，而两处都**天生静默失败**：

  ① **逐行立绘**：单聊的立绘是"渲一次进**行模板**、克隆行共享那一张"。群聊里这就等于
     "所有人共用一张脸"，而且那张脸是上一个单聊 NPC 的 —— 看得见、不报错、只是画错了人。
     所以这里钉的不是"有没有贴图"，而是**每个说话人各贴各的**这条语义：
     说话人必须一路传到 `ChatBubble`，缺省值必须存在（`step(text)` 那条正文路径不带说话人），
     且"模板那张陈旧的脸"不许被沿用。
  ② **拉人 / 踢人按钮**：预制件节点名是 C# `Find` 的**字符串契约**（写错只静默拿到 null），
     出厂态是**隐藏**的（漏设显隐 = "按钮该出现时不出现"，零报错）。

预制件真源不在本仓库（在 AB 打包工程里），所以依赖它的用例整文件 skipif；
C# 源码那一半任何机器都能跑。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATCH_SCRIPT = ROOT / "scripts" / "dev" / "prefab_patch_chat_group_buttons.py"
PREFAB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/"
              "资源修改教程/ResBuildABProject/Assets/Resources/UI/UIChatAi.prefab")

#: 第 4 步 `AbChatPanel.CollectRefs` 必须 Find 的路径（**跨语言契约**，与预制件同名）。
EXPECTED_PATHS = [
    "/UIChatAi/BG/InviteBtn",
    "/UIChatAi/BG/InviteBtn/Text",
    "/UIChatAi/BG/KickBtn",
    "/UIChatAi/BG/KickBtn/Text",
]
#: C# 只接按钮本体，不碰文案子节点（文案是预制件自己的事，由下面的
#: `test_labels_and_geometry_match_patch_constants` 负责）。
_NOT_FOUND_BY_CSHARP = {"/UIChatAi/BG/InviteBtn/Text", "/UIChatAi/BG/KickBtn/Text"}
MUST_FIND = [p for p in EXPECTED_PATHS if p not in _NOT_FOUND_BY_CSHARP]
assert set(_NOT_FOUND_BY_CSHARP) <= set(EXPECTED_PATHS), "豁免名单里有契约中不存在的路径"

WIN = ROOT / "csharp" / "UI" / "ChatWindow.cs"
PRES = ROOT / "csharp" / "UI" / "ChatPresenter.cs"
ITEMS = ROOT / "csharp" / "UI" / "ChatItemViews.cs"
AB_PANEL = ROOT / "csharp" / "UI" / "AbChatPanel.cs"
ROWS = ROOT / "csharp" / "UI" / "RowAvatars.cs"
WARM = ROOT / "csharp" / "UI" / "RoomPortraitWarmup.cs"
CACHE = ROOT / "csharp" / "portrait" / "PortraitCache.cs"


def _mod():
    if str(ROOT / "scripts" / "dev") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    import prefab_patch_chat_group_buttons as M
    return M


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _go_at(pf, path: str):
    """按**绝对路径**取节点（不能用 `find_go(leaf)`：预制件里有多个同名 `Text`）。"""
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


# ----------------------------------------------------------------------
# ① 不依赖资产的静态检查
# ----------------------------------------------------------------------

def test_patch_script_exists_and_is_importable():
    assert PATCH_SCRIPT.is_file(), "补丁脚本不见了"
    M = _mod()
    assert M.INVITE_BTN == "InviteBtn"
    assert M.KICK_BTN == "KickBtn"
    assert M.CLONE_SRC == "CompactBtn", "克隆源必须是顶栏既有的压缩键（组件集合/字号靠它保真）"
    # 号段约定：887 是本补丁的专属段，将来加阶段要换段（见 _alloc_for 的注释）
    assert str(M.NEW_ID_BASE).startswith("887"), M.NEW_ID_BASE


def test_top_bar_button_spacing_is_exact():
    """三个按钮（压缩 / 拉人 / 踢人）必须**等距 8px、互不重叠**（纯常量，不需要资产）。

    ★ 重叠的后果不是"难看"：uGUI 拾取只给**最上层**的 raycast target ⇒ 下面那个按钮
    **永远点不到**，而且零报错。这与通讯录底栏那次（NewGroupBtn/DelGroupBtn 完全重叠）
    是同一类坑。
    """
    M = _mod()
    def nums(s):
        return [float(x) for x in re.findall(r"[-+]?[\d.]+", s)]

    inv = nums(M.INVITE_POS)[0]
    kick = nums(M.KICK_POS)[0]
    w = nums(M.BTN_SIZE)[0]
    gap = M.GAP
    # 右锚点 + piv(1,0.5)：x 就是**右边缘**相对 BG 右边缘的偏移
    assert abs((M.COMPACT_X - inv) - (w + gap)) < 1e-6, \
        "拉人与压缩键的间距不等于 %.0fpx（压缩键右边缘 %.1f）" % (gap, M.COMPACT_X)
    assert abs((inv - kick) - (w + gap)) < 1e-6, \
        "踢人与拉人的间距不等于 %.0fpx" % gap
    # 踢人的左边缘（相对 BG 右边缘）= kick - w；BG 半宽 600 ⇒ 本地坐标 = 600 + kick - w
    local_left = M.BG_W / 2.0 + kick - w
    assert local_left > M.NPC_INPUT_W, \
        "踢人会撞上左侧 NpcInput 名牌（左边缘 %.1f ≤ 名牌右边缘 %.0f）" % (local_left, M.NPC_INPUT_W)


# ----------------------------------------------------------------------
# ② 真预制件（AB 打包工程不在本机时整段跳过）
# ----------------------------------------------------------------------

@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_passes_its_own_verify():
    M = _mod()
    assert M.verify(_read(PREFAB)) is True


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_has_every_expected_path():
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    for p in EXPECTED_PATHS:
        go = _go_at(pf, p)
        assert pf.path_of(go) == p, "节点路径不符：%s != %s" % (pf.path_of(go), p)


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_is_idempotent():
    M = _mod()
    text = _read(PREFAB)
    assert M.patch(text, do_log=False) == text, "补丁不幂等 —— 重跑会改坏预制件"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_default_state_keeps_single_chat_unchanged():
    """★ 默认状态必须 = **改造前的单聊观感**：两个新按钮出厂隐藏。

    预制件不知道当前是不是群聊，而这两个动作只在群聊里有意义。默认隐藏 ⇒
    即使 C# 的显隐逻辑一次都没跑（节点缺失 / 装配失败），单聊窗也和今天一模一样。
    代价是 C# 必须在**每次开窗**时显式设置显隐（见 `test_visibility_is_set_on_both_paths`）。
    """
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    for name in (M.INVITE_BTN, M.KICK_BTN):
        assert pf.field(_go_at(pf, "/UIChatAi/BG/" + name), "m_IsActive") == "0", \
            "%s 出厂应隐藏（仅群聊模式由 C# 点亮）" % name
    # 压缩键必须一个字节没动（补丁只**追加**节点；动了它就说明克隆源被改写）
    compact = pf.rt_of_go(_go_at(pf, "/UIChatAi/BG/CompactBtn"))
    assert pf.field(compact, "m_AnchoredPosition") == "{x: -177.8, y: -20}", \
        "压缩键的位置被改了 —— 本补丁不该动既有节点"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_new_buttons_share_the_top_bar_row():
    """两个新按钮与压缩键**同一行、同尺寸、同 pivot**（克隆保真的可观测部分）。"""
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    compact = pf.rt_of_go(_go_at(pf, "/UIChatAi/BG/CompactBtn"))
    for name in (M.INVITE_BTN, M.KICK_BTN):
        rt = pf.rt_of_go(_go_at(pf, "/UIChatAi/BG/" + name))
        for f in ("m_AnchorMin", "m_AnchorMax", "m_Pivot", "m_SizeDelta"):
            assert pf.field(rt, f) == pf.field(compact, f), \
                "%s 的 %s 与压缩键不一致（%s vs %s）" % (name, f, pf.field(rt, f), pf.field(compact, f))


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_labels_and_geometry_match_patch_constants():
    M = _mod()
    pf = M.Prefab(_read(PREFAB))

    def label_text(path):
        cg = _go_at(pf, path)
        return pf.field(M.find_text_comp(pf, cg), "m_Text")

    assert label_text("/UIChatAi/BG/InviteBtn/Text") == M.yaml_str(M.INVITE_TEXT)
    assert label_text("/UIChatAi/BG/KickBtn/Text") == M.yaml_str(M.KICK_TEXT)
    for name, pos in ((M.INVITE_BTN, M.INVITE_POS), (M.KICK_BTN, M.KICK_POS)):
        rt = pf.rt_of_go(_go_at(pf, "/UIChatAi/BG/" + name))
        assert pf.field(rt, "m_AnchoredPosition") == pos, name


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_backup_is_written_once_and_never_clobbered(tmp_path):
    """备份必须留住**原始件**：幂等复跑不能把已打补丁的版本盖上去。"""
    M = _mod()
    src = tmp_path / "UIChatAi.prefab"
    src.write_text("原始件", encoding="utf-8")
    bak = str(src) + M.BACKUP_SUFFIX
    assert M.ensure_backup(str(src), bak) is True
    assert Path(bak).read_text(encoding="utf-8") == "原始件"
    src.write_text("已打补丁件", encoding="utf-8")
    assert M.ensure_backup(str(src), bak) is False
    assert Path(bak).read_text(encoding="utf-8") == "原始件", "备份被覆盖了"


# ----------------------------------------------------------------------
# ③ C# 源码半段（不需要预制件）
# ----------------------------------------------------------------------

def test_collectrefs_paths_match_the_prefab_contract():
    """跨语言路径契约的另一边：`AbChatPanel.CollectRefs` 必须真的 Find 这两个节点。"""
    src = _read(AB_PANEL)
    body = _method(src, "private static ChatWindowRefs CollectRefs(Transform root)")
    found = set(re.findall(r'Find(?:Go|Rt|Comp<[^>]+>)\(\s*root\s*,\s*"([^"]+)"\s*\)', body))
    assert found, "CollectRefs 里一个路径都没抽到 —— 解析正则该更新了"
    for p in MUST_FIND:
        rel = p.split("/UIChatAi/", 1)[1]
        assert rel in found, "CollectRefs 没有 Find(%r) —— 预制件里有这个节点也白搭" % rel
    extra = {p for p in found if "InviteBtn" in p or "KickBtn" in p} \
        - {p.split("/UIChatAi/", 1)[1] for p in MUST_FIND}
    assert not extra, "CollectRefs 里有多出来的路径（契约没跟上）：%s" % sorted(extra)


def test_visibility_is_set_on_both_paths():
    """★ 四个顶栏按钮的显隐必须在**单聊与群聊两条路上都显式设置**。

    预制件出厂：拉人/踢人**隐藏**、压缩/删除**可见**。任何一条路漏设，表现就是
    "该出现时不出现 / 该消失时还在"，**零报错**（与通讯录底栏那条教训完全同源）。
    群聊里压缩/删除还必须**收起来**（§9.2.1）：它们点下去只会静默 return
    （`CurrentNpcId` 在群聊里刻意留空）—— 那就是两个"看起来能点、点了没反应"的死按钮。
    """
    src = _read(WIN)
    single = _method(src, "public void OpenFor(string npcId)")
    room = _method(src, "public void OpenForRoom(string roomId, string title, List<string> members)")
    assert "SetRoomButtonsVisible(false)" in single, "单聊开窗没恢复压缩/删除、没关掉群聊按钮"
    assert "SetRoomButtonsVisible(true)" in room, "群聊开窗没点亮群聊按钮"
    body = _method(src, "private void SetRoomButtonsVisible(bool show)")
    assert "_inviteBtn" in body and "_kickBtn" in body, "显隐没有覆盖两个群聊按钮"
    assert "_r.CompactButton" in body, "群聊里没收起压缩（§9.2.1 的死按钮）"
    assert "_r.DeleteButton" not in body, \
        "删除键不该再随群聊隐藏 —— v2 已落地（群窗走 uid + room_delete），它在两个模式都可用"
    # 09-17：第四个按钮「跳过」进来了（BG/Input/SkipBtn，出厂隐藏、只在群聊亮）。
    # 计数从 3 改到 4 是**有意的**：这条断言的全部价值就是"新增按钮时被迫回来改一次"，
    # 顺手逼作者回答"它随 show 还是随 !show"。
    # 10-19：第五个按钮「工具侧边栏」（`ToolPanel.ButtonGo`）也进了这个方法。
    # 计数的全部价值就是"新增按钮时被迫回来回答一次「它随 show 还是随 !show」"，
    # 所以照着改数字是对的；改的时候确认过：工具侧边栏 = 群聊里收起（!show），
    # 因为面板里每条都指向"某个 NPC"，群聊里点它没有意义。
    assert body.count("SetActive(") == 5, "五个按钮都要显式设置（漏一个 = 它停在出厂态）"
    # 方向必须**互补**：群聊按钮随 show、压缩反过来。
    # 写反的表现是"单聊里没有压缩键、群聊里多一个死按钮"，而两处都不报错。
    assert "_inviteBtn.SetActive(show)" in body and "_kickBtn.SetActive(show)" in body
    assert "_skipBtn.SetActive(show)" in body, "跳过键也只在群聊可见（单聊没有'让 NPC 自己接'这回事）"
    assert "_r.CompactButton.SetActive(!show)" in body


def test_room_delete_reuses_delete_bar_with_uid_payload():
    """★ 群窗删除：**复用同一套选择模式/操作条**，只把"选中键"从回合号换成发言 uid。

    三个点必须同时成立（少一个就是"静默删错"或"死按钮"）：
      ① `RowMeta` 两条键：`Turn`（单聊）/ `Uid`（群聊），`BindKey` 一条判据覆盖
         （群窗投影刻意不给 turn ⇒ 只认 turn 的行在群窗里永远不可选）；
      ② 载荷分叉：`SelectedUids()` + 两个新事件 → Presenter 发 `room_delete` /
         `preview_room_delete` 并带 `room_id`（跨群错删的最后一道闸）；
      ③ 入口分叉：`OnDeleteBtnClicked` 的群聊分支必须排在 `CurrentNpcId` 早退**之前**
         —— 群模式刻意清空 `CurrentNpcId`，排在后面就是"群窗里点了没反应"。
    """
    win = _read(WIN)
    pres = _read(PRES)

    meta = win[win.index("public sealed class RowMeta"):]
    assert "public int Turn" in meta and "public string Uid" in meta, "RowMeta 少了一条选中键"
    bind = _method(win, "private static void BindKey(RowMeta m, JObject o)")
    assert 'o?["uid"]' in bind and 'o?["turn"]' in bind, "BindKey 没同时认两种键"
    assert "public List<string> SelectedUids()" in win, "缺 uid 载荷出口"
    assert "RoomDeletePreviewRequested" in win and "RoomDeleteConfirmed" in win

    for name in ("OnRoomDeletePreviewRequested", "OnRoomDeleteConfirmed"):
        body = _method(pres, "private void " + name + "(List<string> uids)")
        # 断"**载荷**里带了什么"，不是"这个函数里出现过这个词" —— 回执字段 `data["room_id"]`
        # 也会命中裸串匹配（变异 C3 实测漏网），所以必须钉到赋值那一行。
        assert 'p["room_id"] = ' in body, name + " 载荷没带 room_id（会跨群错删）"
        assert 'p["uids"] = ' in body, name + " 载荷没带 uids"
        assert "CurrentRoomId" in body, name + " 没用当前房间号"
    assert '"preview_room_delete"' in pres and '"room_delete"' in pres, "没发群删的两个 RPC"
    # 两个新事件要**接上也拆掉**（只接不拆 → 面板重建后旧实例仍被回调，同 Invite/Kick 的教训）
    init = _method(pres, "public void Init(ChatWindow window, WsClient ws)")
    destroy = _method(pres, "private void OnDestroy()")
    for ev in ("RoomDeletePreviewRequested", "RoomDeleteConfirmed"):
        assert ev in init and ev in destroy, ev + " 没有成对订阅/退订"

    btn = _method(pres, "private void OnDeleteBtnClicked()")
    assert "IsRoomMode" in btn and "CurrentNpcId" in btn
    assert btn.index("IsRoomMode") < btn.index("CurrentNpcId"), \
        "群聊分支排在 CurrentNpcId 早退之后 ⇒ 群窗里点删除**毫无反应**（死按钮）"
    assert "CurrentRoomId" in btn, "群聊入口没用当前房间号"
    assert "_roomSpeaking" in btn, "群聊入口没挡'这一轮正在说话'（Python 会整体拒绝，白点一下）"
    # 群删的确认摘要必须写明"成员记忆一并移除"（用户拍板；单聊删只动一个 NPC 的记忆）
    assert "在场成员的记忆一并删除" in pres, "确认文案没交代'所有成员记忆一并移除'"


def test_room_mode_refuses_compact_before_it_becomes_a_group_message():
    """★ 群聊**不提供**手动压缩（§9.2.1 定稿）：`/compact` 必须在**群发言分支里就拦下**。

    不拦的后果不是"压缩没生效"，而是这四个字被当成**一条群消息**发出去 —— 写进
    `Room.log`、投递给所有 NPC（他们读到的是 "/compact" 这串命令文本），而压缩一次
    都没发生（§16.11 记的既有行为）。压缩的真正归属地是某个成员的**单聊窗**。

    这条契约的命门是**顺序**：判据必须排在 `SendRoomMessage` 之前 —— 写反了照样编译、
    照样"看起来有提示"，但命令已经发出去了，还多一个群里根本没有的本地幽灵气泡。
    """
    src = _read(PRES)
    body = _method(src, "private void OnSubmitRequested()")
    room_at = body.index("IsRoomMode")
    compact_at = body.index('"/compact"')
    bubble_at = body.index("AppendUserMessage")
    send_at = body.index("SendRoomMessage")
    assert room_at < compact_at < bubble_at < send_at, \
        "群发言分支里必须先拦 /compact 再落气泡/发群消息（顺序反了：命令照样被发出去）"
    guard = body[compact_at:bubble_at]
    assert "AppendSystemNotice" in guard, "拦下之后必须给一句说明（静默 return = 按了没反应）"
    assert "单聊窗口" in guard, "提示要指路（§9.2.1：到他的单聊窗操作）"
    assert "return" in guard, "拦下后必须 return（否则继续往下落气泡 + 发送）"
    assert "SendRoomMessage" not in guard, "拦下分支里不许出现发送"

    # 兜底那一侧（Python 拦下时回的 `room_state` 终帧）也必须**把说明显示出来** ——
    # 终帧的 `text` 此前被静默丢掉，玩家只看到"忙碌复位、屏幕上什么都没有"。
    # ★ 钉**字段版** `ApplyRoomState`：关窗重放的终帧也走它（`DrainRoomFrames`）——
    #   只钉直播入口的话，重放路径上这句说明照样会被静默丢掉。
    st = _method(src, "private void ApplyRoomState(string phase, string who, string note, string whoId = null)")
    assert "AppendSystemNotice(note)" in st, \
        "room_state 终帧的 text 没被显示（Python 回的说明又静默丢了）"
    entry = _method(src, "private void OnRoomState(JObject payload)")
    assert 'payload["text"]' in entry and "ApplyRoomState(" in entry, \
        "直播入口没把终帧文案解构出来 / 没转调字段版"


def test_both_buttons_are_attached_and_detached():
    """两个点击事件必须**接上也拆掉**（只接不拆 → 面板重建后旧实例仍被回调）。"""
    src = _read(PRES)
    init = _method(src, "public void Init(ChatWindow window, WsClient ws)")
    destroy = _method(src, "private void OnDestroy()")
    for ev, name in (("InviteClicked", "OnInviteClicked"), ("KickClicked", "OnKickClicked")):
        assert "_window.%s += %s" % (ev, name) in init, "%s 没接上" % ev
        assert "_window.%s -= %s" % (ev, name) in destroy, "%s 没拆掉" % ev
    # 拆装必须成对（漏一个方向就是内存/回调泄漏）
    assert init.count("_window.") == destroy.count("_window."), \
        "Init 与 OnDestroy 的事件数量不一致（有一边漏了）"


def test_room_actions_are_refused_while_a_turn_is_running():
    """★ 回合进行中不许改成员：正在跑的一轮已按当时的 members 定了 roster 与轮转，
    中途增删会让"谁在说话"与"名单有谁"对不上。两个入口都必须走同一个守卫。"""
    src = _read(PRES)
    for sig in ("private void OnInviteClicked()", "private void OnKickClicked()"):
        assert "RoomActionAllowed(" in _method(src, sig), "%s 没有走共用的守卫" % sig
    guard = _method(src, "private bool RoomActionAllowed(string what)")
    # ★ 判据必须是**条件本身**，不能是"这两个名字在方法里出现过"（变异检查当场抓出来的）：
    #   把条件改成 `if (false)` 之后，下面那行**日志**里仍然写着 `_busy` 与 `HasActiveBubble`
    #   ⇒ 子串断言照样绿，而回合进行中可以直接改成员了。
    #   允许两个操作数换顺序，但不接受任何一个缺席。
    cond = (re.search(r"if\s*\(([^)]*_busy[^)]*HasActiveBubble[^)]*)\)", guard)
            or re.search(r"if\s*\(([^)]*HasActiveBubble[^)]*_busy[^)]*)\)", guard))
    assert cond, "守卫没有把「忙标」与「活跃流式泡」放在同一个 if 条件里（回合中能改成员了）"
    assert "IsConnected" in guard, "断线时必须明确告诉玩家（不许静默什么都不发生）"


def test_invite_and_kick_have_exactly_one_send_site_each():
    """单一发送点：`add_room_members` 与 `kick_room_member` 各只能有一处发。

    多一处就是两份处理迟早走偏（同 `open_room` / `dissolve_room` 的既有约定）。
    """
    code = _strip_comments(_read(ROOT / "csharp" / "UI" / "ContactPresenter.cs"))
    assert code.count('SendRequest("add_room_members"') == 1, "拉人有不止一处发送点"
    assert code.count('SendRequest("kick_room_member"') == 1, "踢人有不止一处发送点"


def test_kick_is_two_step_and_invite_warns_about_history():
    """踢人必须**两段确认**；拉人的提示必须写明"新人看不到入群前的对话"。

    前者：误点一下就把人移出群聊，他之后的群消息全部收不到（而玩家不一定当场发现）。
    后者：Python 的 `add_member` 把入群位置钉在 `room.last_seq`（只能看到入群后的话），
          不说明的话这一定会被当成 bug 报上来。
    """
    cp = _read(ROOT / "csharp" / "UI" / "ContactPresenter.cs")
    body = _method(cp, "private void ConfirmKick()")
    assert "_kickConfirm.Click(" in body, "踢人没有走两段确认"
    assert "Step.First" in body and "Step.Expired" in body, "两段确认的两个分支没写全"
    assert "ExecuteKick()" in body, "第二击没有落到真正的执行"
    # 执行体：逐个发 RPC，并**只回报一次**（每个回执都回一次会刷 N 行系统提示）
    ex = _method(cp, "private void ExecuteKick()")
    assert "NotifyMembersChangedStatic" in ex, "踢完没有通知对话窗（名单会停在旧值）"
    assert "RequestRooms()" in ex, "踢完没有重拉名录校准"

    inv = _method(cp, "private void ConfirmInvite()")
    assert "NotifyMembersChangedStatic" in inv, "拉完没有通知对话窗"
    # 警告文案在状态条上（RenderCurrent 的 Invite 分支）
    assert "看不到入群前的对话" in cp, "拉人的界面没有写明新人看不到入群前的对话"
    pres = _read(PRES)
    assert "看不到入群前的对话" in pres, "拉完后的系统提示没有写明这件事"


def test_cancel_from_room_returns_to_the_chat_window():
    """拉人/踢人是**从对话窗跳过来**的：取消 = 回群里去，所以要把面板也关掉。

    建群/解散是 F10 自己打开的，取消只退出选择模式、面板留着 —— 两者语义不同。
    """
    cp = _read(ROOT / "csharp" / "UI" / "ContactPresenter.cs")
    body = _method(cp, "private void ExitSelect()")
    assert "PickMode.Invite" in body and "PickMode.Kick" in body, \
        "取消没有区分「从对话窗来的」两种模式（会把 F10 打开的建群/解散也一起关掉）"
    # ★ 只查 `"Hide()" in body` 是**不够的**（变异检查当场抓出来的）：把条件改成
    #   `if (false) { Hide(); return; }` 之后那个字符串还在，用例照样绿，而取消就再也
    #   关不掉面板了。所以连同**条件**一起钉住。
    assert "if (fromRoom && _isOpen) { Hide(); return; }" in body, \
        "从对话窗来的取消没有真的关面板（条件被短路 / 返回值丢了）"


# ----------------------------------------------------------------------
# ④ 逐行立绘（群聊里"每个人各贴各的脸"）
# ----------------------------------------------------------------------

def test_every_room_bubble_path_carries_a_speaker():
    """★ 群聊每一条"往屏幕上放正文"的路都必须带上说话人。

    一条都不许漏：漏掉的那条会用**模板预填的立绘**（= 上一个单聊 NPC 的脸），
    而这是静默的（头像看得见、只是画错了人）。
      · 流式起泡（`ApplyDelta`，带名字 + 精确 id）—— 09-17 起气泡由它**惰性创建**
      · `room_say` 定稿（带名字）
      · `ReplaceHistory` 的 peer 分支（回放，带名字）
      · `step(text)` 整段正文（09-17 起**也补**名字前缀与说话人，见下）
    ★ 09-17（C2）：`room_state(phase=speak)` **不再预开气泡** —— 点名就开泡会让
      "模型思考几十秒"期间挂一条空气泡，而弃权/出错时它还要等下一个人的 speak 才消失
      （实机："她的气泡过了一会自己消失了"）。所以这里不再钉"speak 起泡带名字"，
      改钉**真正起泡的那两条路**，以及"speak 不许再起泡"。
    """
    win = _read(WIN)
    pres = _read(PRES)

    room = _method(win, "private string SpeakerOf(string speaker)")
    assert "IsRoomMode" in room and "_roomSpeaker" in room, \
        "缺省说话人必须只在群聊里生效（单聊要沿用模板立绘，观感零变化）"

    # ★ 11-20 起断言对象改成 `ApplyHistoryItem` ★：回放拆成分帧构建后，那个 switch
    #   整块搬进了 `ApplyHistoryItem`，`ReplaceHistory` 只剩分流。断言跟着搬，不放松。
    hist = _method(win, "private void ApplyHistoryItem(JToken token)")
    assert "AppendNpcStatic(shown, who, whoId)" in hist, \
        "回放的 peer 分支没把说话人（名字 + 精确 id）传下去"
    # ★ 回放必须**读 `speaker_id`**：重开窗那一刻 C# 手上未必还有这个群的身份，
    #   而按名解析在重名时会画上别人的脸，且**不报错、不缺字段**。
    assert re.search(r'string\s+whoId\s*=\s*\(string\)o\["speaker_id"\]', hist), \
        "回放没有读 speaker_id（群窗回放的每一行都会退回按名解析）"
    # 而且这条逐条应用必须是**唯一**的构建入口（两份 switch = 群窗回放可能走不到带 id 的那份）
    assert "ApplyHistoryItem(" in _method(win, "public void ReplaceHistory(JArray items)"), \
        "ReplaceHistory 没有走统一的那条逐条应用"

    # ★ 钉**字段版**：直播与关窗重放（`DrainRoomFrames`）共用同一条执行路径 ——
    #   只钉直播入口的话，重放路径少传说话人照样会被静默放过（同一个坑的第二个入口）。
    say = _method(pres, "private void ApplyRoomSay(string uid, string speaker, string text, string speakerId = null,")
    assert 'FinishNpcBubble(speaker.Length > 0 ? speaker + "：" + text : text, false,\n                                    speaker, speakerId)' in say, \
        "room_say 定稿没带说话人/精确 id（新建那一档会退回模板那张陈旧的脸）"
    # ★ 旁白（`narration`）**不许走到气泡那一步**：它改画「行动记录」行 —— 那类行不是谁说的
    #   话、里面还夹着玩家的原话，画成气泡就是把它安到某个人头上（09-22 用户报）。
    #   重开窗那条路由 `room_history` 发成 `tool_result` 条目，走 `ReplaceHistory` 的既有分支。
    assert "AppendActionRow(text)" in say, "ApplyRoomSay 没有旁白分支 → 直播仍会画气泡"
    assert say.index("AppendActionRow(text)") < say.index("FinishNpcBubble("), \
        "旁白分支必须**早于**气泡那一步（晚于它 = 气泡已经画出来了）"
    assert "ApplyRoomSay(" in _method(pres, "private void OnRoomSay(JObject payload)"), \
        "直播入口必须转调字段版（两种来路同一条执行路径）"

    # ① 流式起泡那条路（`ApplyDelta`）：名字前缀 + 名字 + 精确 id 三样都要给
    delta = _method(pres, "private void ApplyDelta(string tok, string part)")
    i_begin = delta.index("BeginNpcBubble(")
    begin_call = delta[i_begin: delta.index(";", i_begin)]
    for needle in ('_roomSpeaking + "："', "_roomSpeaking : null", "_roomSpeakingId : null"):
        assert needle in begin_call, f"流式起泡没带说话人（缺 {needle}）"
    # ② 整段正文那条路（`step(text)`）：09-17 起同样补名字前缀 + 精确 id
    step = _method(pres, "private void ApplyStep(int turn, string kind, string name, string args, string text, bool isError)")
    txt_seg = step[step.index('case "text":'):]
    assert '_roomSpeaking + "：" + text' in txt_seg, \
        "step(text) 在群聊里没补说话人前缀（现建的气泡没有归属）"
    assert "_roomSpeaking : null" in txt_seg and "_roomSpeakingId : null" in txt_seg, \
        "step(text) 没把说话人/精确 id 传给气泡（头像会退回模板）"

    st = _method(pres, "private void ApplyRoomState(string phase, string who, string note, string whoId = null)")
    # ★ 名字与 id **必须同一次写**（`SetRoomSpeaker(name, id)` 单入口）：分开写会出现
    #   "名字是新的、id 还是上一轮的" —— 那一行的头像会是**别人**，而名字看着完全正常。
    assert "SetRoomSpeaker(who, whoId)" in st, "room_state 没把说话人（名字 + 精确 id）同一次记下"
    # ★ 09-17（C2）：speak **不许**再预开气泡 —— 起泡只发生在"真有正文"的那两条路上
    speak_seg = st[st.index('phase == "speak"'): st.index('phase == "silent"')]
    assert "BeginNpcBubble" not in speak_seg, \
        "speak 又预开气泡了：弃权/出错时会留下一条要等别人说话才消失的幽灵泡"
    assert 'SetRoomSpeaker("")' in st, "phase=end 没清说话人（下一轮首条正文会挂到上一个人身上）"
    assert "ApplyRoomState(" in _method(pres, "private void OnRoomState(JObject payload)"), \
        "直播入口必须转调字段版"

    # 用户行：群聊里也要逐行取图（玩家可能这一局还没单聊过）
    um = _method(win, "public RowMeta AppendUserMessage(string text)")
    assert "IsRoomMode ? _playerName : null" in um, "群聊里玩家行没有逐行取图"


def test_chatbubble_does_not_inherit_the_template_face_when_a_speaker_is_given():
    """★ 给了说话人时，这一行的头像必须**完全由逐行取图决定**（含隐藏）。

    沿用模板那张 = 群聊里所有人共用一张脸（改造前的 bug 形态），而且零报错。
    """
    src = _read(ITEMS)
    body = _method(src, "public ChatBubble(ChatWindowRefs refs, Transform contentParent, bool isUser, string text,\n                          string speaker, string speakerId = null)")
    assert "RowAvatars.Apply(av, speaker, speakerId)" in body, \
        "给了说话人却没走逐行取图（且要带上精确 id：有 id 就不该再按名解析一次）"
    # ★ 只查"调用了 Apply"不够：把条件改成 `if (false)` 时那行**仍在**，用例照样绿，
    #   而群聊会退回"所有人共用模板那张脸"。所以钉住分支条件本身。
    # 判据是 **null**（单聊）而不是"空串"：群聊里"不知道说话人是谁"必须走逐行那条路
    # 并且**隐藏**，不能退回模板那张上一个单聊 NPC 的脸。
    assert "if (speaker != null)" in body, \
        "逐行取图那条分支的条件变了（null = 单聊才该用模板立绘，群聊一律逐行）"
    # 两条分支必须互斥：有说话人 → 逐行；没有 → 才看模板纹理
    i_apply = body.index("RowAvatars.Apply(av, speaker, speakerId)")
    i_tpl = body.index("av.texture != null")
    assert i_apply < i_tpl, "模板纹理那条分支必须在 else 里（有说话人时不许落到它）"
    assert "else" in body[i_apply:i_tpl], "有说话人那条路没有把模板分支挡在 else 外"


def test_cache_load_is_pumped_by_the_chat_window():
    """★ `PortraitCache.PumpLoad` 必须由**对话窗**也泵一份。

    它原先只挂在 `ContactPresenter.Update` 上，而通讯录关闭时它所在根节点是 OFF 的
    （`Update` 根本不跑）⇒ 只看对话窗时，磁盘上的快照**永远不会被读进内存**，
    表现是"头像一直不出现"，零报错。
    """
    win = _read(WIN)
    upd = _method(win, "private void Update()")
    assert "RowAvatars.Pump()" in upd, "对话窗 Update 没泵逐行贴图（PumpLoad 在里面）"
    rows = _read(ROWS)
    pump = _method(rows, "public static void Pump()")
    assert "PortraitCache.PumpLoad()" in pump, "逐行贴图的泵里没有读盘限流"


def test_warmup_is_strictly_serialized():
    """★★ 立绘预热必须**串行**：渲 A → 等 A 的捕获任务结束 → 才渲 B。

    不等的后果是**静默错脸**：A 的捕获任务到期时读到的是 B 的渲染结果（两者共用同一个
    模板槽位），然后把它存成 A 的名字。PNG 打开看着完全正常，只会表现为"这个人怎么长着
    别人的脸"，而且从此固化在盘上。
    """
    src = _read(WARM)
    pump = _method(src, "public static void Pump()")
    assert "HasPendingCapture(_curId)" in pump, "没等前一个的捕获任务结束就往下走"
    assert "CancelCapture(_curId)" in pump, "超时没有撤销（一个不落盘的成员会把队伍卡死）"
    i_gate = pump.index("if (!_waiting)")
    i_step = pump.index("Step()")
    assert i_gate < i_step, "`Step()`（会调渲染 API）必须在 `!_waiting` 门后面"

    step = _method(src, "private static void Step()")
    assert step.count("PortraitService.Fill(") == 1, "渲染调用应当只有一处"
    assert step.count("PortraitCache.RequestCapture(") == 1, "渲染后必须立刻排队捕获"
    # 已有快照就不许再渲一次（这是"每个说话人渲一次"的复用点）
    assert "TryGet(id, out have)" in step, "没检查内存是否已有"
    assert "HasOnDisk(id)" in step, "没检查盘上是否已有（盘上有就只需读回来）"


def test_solo_open_cancels_warmup_and_pending_captures():
    """★ 单聊开窗前必须**停预热 + 撤掉在飞的捕获任务**。

    单聊与预热共用同一个模板槽位（`BG/Templates/NpcRow/Npcavatar`），而捕获是"排队 → 延迟
    0.15s 抓图"。不撤的话，旧任务到期时读到的是刚渲进去的另一张脸 ⇒ 存错 id。
    这条同时修掉单聊里一个既有的同类隐患（快速连开两个 NPC）。
    """
    src = _read(AB_PANEL)
    body = _method(src, "private void FillPortraits(WorldUnitBase unit)")
    i_stop = body.index("RoomPortraitWarmup.Stop()")
    i_cancel = body.index("PortraitCache.CancelAllCaptures()")
    i_fill = body.index("PortraitService.Fill(")
    assert i_stop < i_fill and i_cancel < i_fill, \
        "停预热 / 撤任务必须在**重新渲染槽位之前**（之后就没意义了）"
    # 离开群聊也要停（否则它继续渲着那个槽位）
    assert "RoomPortraitWarmup.Stop()" in _method(_read(WIN), "public void OpenFor(string npcId)")


def test_name_to_unitid_memo_is_cleared_on_world_reload():
    """★ 名字→unitID 的备忘录必须**换存档时清空**。

    同一个中文名在新存档里是另一个 unitID（这游戏全图上千单位、名字用字池有限，重名是常态）。
    留着旧映射会把上一存档的脸贴到这一存档的人身上，而且不报错。
    """
    cache = _read(CACHE)
    assert "public static void ClearNameIndex()" in _strip_comments(cache)
    main = _read(ROOT / "csharp" / "app" / "ModMain.cs")
    body = _method(main, "private void OnIntoWorldEvent(ETypeData e)")
    assert "PortraitCache.ClearNameIndex()" in body, "换存档没有清空名字映射"
    # 失败必须限流（否则群窗回放 100 行 = 100 次全图扫描）
    idb = _method(cache, "public static string IdByName(string name)")
    assert "NameMissRetry" in idb, "解析失败没有限流 —— 回放会退化成每行一次全图扫描"


def test_warmup_is_started_on_room_open_and_includes_the_player():
    """★ 开群必须真的**启动**预热，而且名单里要有玩家。

    预热是"每个 NPC 都有立绘"的唯一实现（只读快照的话，本机实测盘上只有 2 张 PNG，
    群里大概率一个头像都不显示）。漏掉 `StartRoomPortraitWarmup()` 的后果是
    **功能整个不发生**，而且零报错 —— 这正是最需要用测试钉住的一类。
    玩家也要进去：群聊里玩家行同样逐行取图（他也可能这一局还没单聊过）。
    """
    win = _read(WIN)
    room = _method(win, "public void OpenForRoom(string roomId, string title, List<string> members)")
    assert "StartRoomPortraitWarmup()" in room, "开群没有启动立绘预热"
    assert room.index("Open()") < room.index("StartRoomPortraitWarmup()"), \
        "预热必须在 Open()（整块重建内容）之后启动"
    starter = _method(win, "private void StartRoomPortraitWarmup()")
    assert "_playerName" in starter, "预热名单里没有玩家"
    assert "CurrentRoomMembers" in starter, "预热名单里没有群成员"
    assert "RoomPortraitWarmup.Start(" in starter, "没有真的排队"


def test_adding_a_member_does_not_wipe_the_pending_warmup_queue():
    """★ 拉人后追加预热必须用 `Enqueue`（追加），**不能**用 `Start`（重置整队）。

    用 `Start` 会把开群时排下的其他成员整队撤掉 —— 他们的脸从此不会再补上
    （直到下次开群），而界面上只是"某几个人没头像"，没有任何报错。
    """
    win = _read(WIN)
    body = _method(win, "public void SetRoomMembers(List<string> members, List<string> newlyAdded)")
    assert "RoomPortraitWarmup.Enqueue(" in body, "拉人后给新人预热走的是重置而不是追加"
    assert "RoomPortraitWarmup.Start(" not in body, "拉人后不该重置整队（会丢掉其他成员的预热）"
    warm = _read(WARM)
    assert "public static void Enqueue(List<string> names)" in _strip_comments(warm)
    enq = _method(warm, "public static void Enqueue(List<string> names)")
    assert "Stop(" not in enq, "Enqueue 不许停掉在飞的那个（那正是它与 Start 的区别）"

def test_unknown_speaker_in_a_room_hides_the_avatar_instead_of_lying():
    """★★ 群聊里"说话人未知"必须**隐藏头像**，绝不能退回模板那张脸。

    `room_state(phase=speak)` 会在四种情形下没能落到这个窗口上：窗口**关着**时那条帧被
    `!IsOpen` 守卫丢掉后又重开窗 / 中途切去单聊再切回（`ClearRoom` 清空）/
    `ApplyOpenRoom` 在回执到达时又清一次 / `phase="end"` 之后还有迟到 delta。
    （**不是**帧乱序：Python 侧 speak 是 `await` 在回合之前发的，C# 侧所有 event 走同一条
    FIFO 队列。）那一刻不知道是谁在说，而模板里预填的是**上一次单聊那个 NPC** 的立绘 ——
    沿用它就是画错人，且完全静默。
    所以约定是：`SpeakerOf` 在群聊里返回**空串**（不是 null），`RowAvatars.Apply`
    见到空串 → 隐藏。单聊返回 null → 才走模板立绘那条老路。
    """
    win = _read(WIN)
    sp = _method(win, "private string SpeakerOf(string speaker)")
    assert 'IsRoomMode ? (_roomSpeaker ?? "") : null' in sp, \
        "群聊里说话人未知时必须返回空串（非 null）—— 返回 null 会退回模板那张别人的脸"
    # 早到 delta 那条路也要传说话人（漏传就是同一个坑的另一个入口）。
    # ★ 钉**字段版** `ApplyDelta`：关窗重放的 delta 也走它。
    pres = _read(PRES)
    de = _method(pres, "private void ApplyDelta(string tok, string part)")
    assert "_window.IsRoomMode ? _roomSpeaking : null" in de, \
        "说话人未知时没把它传下去（会退回模板那张上一个单聊 NPC 的脸）"
    assert "_window.IsRoomMode ? _roomSpeakingId : null" in de, \
        "说话人的**精确 id** 没跟着传下去（这一行的头像会退回按名解析 = 重名时画错人）"
    assert "ApplyDelta(" in _method(pres, "private void OnDeltaEvent(JObject payload)"), \
        "直播入口必须转调字段版"
    # Apply 的空串分支必须**隐藏**
    rows = _read(ROWS)
    ap = _method(rows, "public static void Apply(RawImage img, string speakerName, string speakerId = null)")
    assert "if (img == null || speakerName == null) return;" in ap, \
        "null（单聊）必须原样返回、不碰模板立绘"
    i_empty = ap.index("speakerName.Length == 0")
    assert "Hide(img)" in ap[i_empty:i_empty + 120], \
        "空串（群聊但不知道是谁）必须隐藏头像，而不是留占位/沿用模板"
    # ★ 取图必须**优先用精确 id**，没有才按名解析 —— 有 id 还去按名查一遍，
    #   等于白白保留一次"重名画错人"的机会。
    assert re.search(r'!string\.IsNullOrEmpty\(speakerId\)\s*\?\s*speakerId\s*:\s*PortraitCache\.IdByName\(speakerName\)', ap), \
        "取图没有优先用精确 id（给了 id 却仍按名解析）"
    # 反过来：单聊必须仍然看得见模板立绘（不许把单聊也一起隐藏了）
    assert "bool show = av != null && av.texture != null;" in _read(ITEMS), \
        "单聊那条「看模板纹理」的分支被动了（单聊头像会整个消失）"

# ══════════════════════════════════════════════════════════════════
# 底部统计条（BG/StatsBar）：上下文那一项**不许整段消失**（09-18 用户反馈）
# ══════════════════════════════════════════════════════════════════

def test_stats_line_always_shows_the_context_window():
    """★ 09-18 用户反馈："当前显示缓存率之类的，没有展示当前上下文和总上下文"。

    三层原因，这里钉的是最外面那层（渲染）：旧写法把 `contextWindow`（**总**上下文，
    配置事实）也塞进 `if (pressureTokens > 0)` 的分支里 —— 于是"还没实测过"的窗口
    （刚开窗 / agent 刚 resume / 刚压缩或删除过，实测值被 Python 侧故意作废）
    连总量一起消失，看起来就像"这一项被去掉了"。

    要求：总量与当前值**分开判**；当前值实测优先、退到估算（打 ≈）、最后才是 —；
    估算口径必须读 `estimatedOccupancyPercent`（不许拿实测的 `occupancyPercent` 顶替）。
    """
    win = _read(WIN)
    i = win.index("private static string BuildStatsLine(")
    seg = win[i: win.index(" long LongOf(", i)]
    # 判据必须落在**代码**上：`estimatedTokens` 这个词在紧邻的注释里也有，
    # 只查子串会被注释满足（变异 ③ 第一次跑就是这么漏的）。
    assert 'ctx = LongOf(cp["estimatedTokens"]);' in seg, \
        "统计行没读估算字段（没实测时只能整段消失）"
    assert "estimatedOccupancyPercent" in seg, "占用率的估算口径没接上"
    # 总量不许被"当前值 > 0"锁住：窗口判断必须与 ctx 判断并列出现
    assert "long win = LongOf(cp[\"contextWindow\"])" in seg
    assert "if (ctx > 0 || win > 0)" in seg, "总量仍被嵌在「当前值 > 0」里 ⇒ 会整段消失"
    assert "sb.Append(est ? \"≈\" : \"\")" in seg, "估算值没有标记（会被读成实测值）"
    assert "sb.Append(\"—\")" in seg, "连估算都没有时没有占位（那一格会空着）"
    print("✓ 统计条：总量任何时候都显示；当前值实测→≈估算→—")


def test_stats_line_is_cleared_when_a_window_is_rebuilt():
    """统计行是"上一个窗口的账"：整块重建（开窗/换对象/换群）必须清掉。

    单聊马上会被 open_chat 基线刷掉，**群聊**要等第一个成员发言才来基线 —— 不清就会把
    上一位单聊对象的 token 数挂在一个不相干的群窗上（实测现象：缓存率那行还在、
    上下文那项是空的/旧的）。
    """
    win = _read(WIN)
    i = win.index("private void ResetContent()")
    seg = win[i: len(win)]
    seg = seg[: seg.index("\n        }\n")]
    assert "_tokensBar" in seg and "= \"\"" in seg, "ResetContent 没有清统计行（会留下上一个窗口的残影）"
    print("✓ 开窗/换窗口清统计行（不再留上一个窗口的残影）")


def test_room_stats_line_is_labeled_with_the_speaker():
    """群聊那行是**某个成员**的账（stats_update 一次只带一个 npc_id）⇒ 必须标名字。"""
    win = _read(WIN)
    i = win.index("public void SetStats(JObject stats)")
    seg = win[i: win.index("private static string BuildStatsLine(", i)]
    assert "IsRoomMode" in seg and 'stats["npc_id"]' in seg, \
        "群聊统计行没标说话人（会被读成「这个群的统计」）"
    print("✓ 群聊统计行带说话人前缀（单聊不加）")

def test_room_stats_line_reports_group_scope_only():
    """★ 群聊底部那一行 = **本群口径**（09-18 用户拍板）：只报花费，不报上下文/占用率。

    为什么群里不报上下文：每个成员各有各的账本与上下文，占用率没有可加的语义 ——
    报出来就是把某一个人的数当全群的。能诚实相加的只有花费（总/输入/输出/缓存率）。
    并且这一行**必须自报口径**（"本群期间"），否则玩家会拿它跟单聊窗那行对比
    （那是"我在这个 NPC 身上花的"，含单聊）。
    """
    win = _read(WIN)
    i = win.index("private static string BuildStatsLine(")
    seg = win[i: win.index(" long LongOf(", i)]
    assert 'stats["roomUsage"] as JObject' in seg, "BuildStatsLine 没接本群口径（群窗只能显示某个成员的账）"
    assert "BuildRoomStatsLine" in seg, "本群口径没有独立的渲染函数"
    j = seg.index("private static string BuildRoomStatsLine(")
    room_seg = seg[j:]
    assert "本群期间" in room_seg, "本群那一行没有自报口径"
    assert "不含各自单聊" in room_seg, "本群那一行没写明「不含单聊」（会与单聊窗那行混淆）"
    assert "上下文" not in room_seg, "本群那一行不许报上下文（每人一份账本，不可加）"
    assert "occupancyPercent" not in room_seg and "contextWindow" not in room_seg, \
        "本群那一行不许报占用率/窗口"
    assert "还没有发言" in room_seg, "空群（还没人说话）没有兜底文案（会显示一行 0）"
    print("✓ 群聊统计行走本群口径：只报花费 + 自报「本群期间」 + 不报上下文")


def test_room_open_seeds_the_group_stats_line():
    """开群回执就要刷那一行 —— 否则群窗底部停在预制体默认文本（"None"）直到第一个成员说完。

    用户 09-18 的截图（群窗底部一个 "None"）就是这个现象：既没基线、群帧又被准入判据丢掉。
    """
    pres = _read(PRES)
    k = pres.index("public bool ApplyOpenRoom(JObject resp)")
    seg = pres[k:]
    seg = seg[: seg.index("\n        /// <summary>")]      # 到下一个成员（带文档注释的那个）为止
    assert 'data["roomUsage"]' in seg and "SetStats(" in seg, \
        "开群回执没有把本群口径喂给统计行（会停在预制体的 None）"
    print("✓ 开群回执刷本群口径统计行")


def test_room_speaker_prefix_only_applies_to_member_scope_frames():
    """人名前缀只加在**成员口径**的帧上；本群口径自带「本群期间」，加人名会说反。"""
    win = _read(WIN)
    i = win.index("public void SetStats(JObject stats)")
    seg = win[i: win.index("private static string BuildStatsLine(", i)]
    assert 'stats["roomUsage"] == null' in seg, "没有按口径分流：本群口径那一行会被加上某个成员的名字"
    print("✓ 人名前缀只用于成员口径帧；本群口径行不加名字")


# ══════════════════════════════════════════════════════════════════
# 传输层事件白名单（WsClient.HandleMessage）：漏一个 = 该帧在传输层静默蒸发
# ══════════════════════════════════════════════════════════════════

def test_transport_whitelist_forwards_stats_update():
    """★ 09-21 用户反馈："直播对话中底部统计条不刷新，关窗重开才有"。

    根因在传输层：`WsClient.HandleMessage` 的白名单没有 `stats_update`，Python 每回合
    收口发的统计帧在这里就被静默丢弃，ChatPresenter 的 `case "stats_update"` 成了
    收不到帧的死代码 —— 而 open_chat 响应自带 stats 基线，所以"关窗重开才有数"。
    下游（presenter / 帧队列 / tee）全有测试，唯独这一层没有 ⇒ 钉在这里。

    在 `_strip_comments` 之后的代码上断言（注释写了不算，得真的转发）。
    """
    ws_src = ROOT / "csharp" / "bridge" / "WsClient.cs"
    code = _strip_comments(_read(ws_src))
    m = re.search(r'else if \(type == "event"\)(.*?)else if \(type == "response"\)', code, re.S)
    assert m, "WsClient 的 event 分支结构变了，测试要跟着改"
    seg = m.group(1)
    for ev in ("step", "text_delta", "npc_reply", "compact_result",
               "room_say", "room_state", "stats_update"):
        assert '"%s"' % ev in seg, \
            "传输层白名单缺 %r ⇒ 该事件在 WsClient 被静默丢弃（UI 永远收不到）" % ev
    print("✓ 传输层白名单转发全部 UI 事件（含 stats_update）")
