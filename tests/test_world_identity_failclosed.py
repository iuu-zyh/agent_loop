"""存档身份 **fail-closed**：进世界读不到身份 ⇒ 作废（不是沿用）＋ 补发重试 ＋ 回包判废（10-18）

用户现场
------------------------------------------------------------------------------------
「由用户反应新开一个存档，也就是新建一个世界，通讯录里面的继承了？？？
  我记得不会的啊，专门做过处理的，通讯录的应该是和世界挂钩的。」

机制（本文件钉死的就是这条链）
------------------------------------------------------------------------------------
隔离确实是"按存档"做的：目录 = `storage/worlds/<world_id>/`，`world_id` = **玩家 unitID**。
问题不在隔离，在**钥匙的传递**：

  ① `EGameType.IntoWorld` 那一刻 `g.world.playerUnit` 可能还没挂上（世界正在重建）⇒
     `FillWorldIdentity` 读不到身份 ⇒ 帧里没有 `world_id`；
  ② Python 旧语义：**没有 id = 沿用当前命名空间**（这条本来是救 Python 中途重启的命）⇒
     玩家从主菜单新建存档时，它手上正是**上一个存档**的钥匙 ⇒ 新档读到旧档的通讯录 ——
     而且回包 `world_known=true`（Python 确实"定档"了，只是定在别人家）⇒ C# 无从分辨 ⇒
     照单写进镜像。新档加的好友还会写进旧档目录。
  ③ 没有任何"离开世界"的帧（`Destroy()` 只停 WS）：从回主菜单到新身份到达，整段窗口都是旧档。

实机证据（本机日志，非推断）：134 条 `world_hello` 里 **67 条没有 `world_id`**，其中 **17 条
是"人已经在世界里"却读不到玩家单位**（10 次 BOlQu6、7 次 7tlNzH）—— 窗口真实且频繁；
那 17 次后面的 `load_happened` 恰好都带到了 id，所以本机没复现（这就是"有时候"）。

修法（三条防线，缺一条都还能串）
------------------------------------------------------------------------------------
  ① Python：**进世界帧宣告 `identity:unknown` ⇒ 作废身份**（退回未定档 → `list_*` 拒绝回答 →
     C# 一个字都不写 → 面板如实提示），而不是沿用。`world_hello`（连接事件）保持沿用不变。
  ② C#：`IntoWorld` 后**逐帧重试**读玩家单位，读到就补发一帧 `load_happened`（幂等）并重拉名单
     —— 让"作废"这个状态自动收敛（新存档没有存档文件，`save_happened` 可能几分钟后才来）。
  ③ C#：换档**先整表作废镜像**再重拉（否则回包没回来之前面板会把旧档名单渲染出来，
     见 `ContactPresenter.Show()` 第 ③ 步"先把 Store 里已有的镜像并进来"），
     且每个档级回包都按自带 `world_id` 判废（跨档回包一个字节都不许写）。

⚠ 向后兼容：**缺 `identity` 键 / 缺 `world_id` 键 = 老 DLL / 老 Python** ⇒ 一律走旧路
（沿用 / 无从判废即放行），新老组合的行为必须与改动前一致。这条由下面两组用例分别钉。
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from agent_loop.agent_loop import AgentLoop                       # noqa: E402
from agent_loop.llm.stub_client import StubLlmClient              # noqa: E402
from agent_loop.storage.contacts_store import ContactsService     # noqa: E402
from agent_loop.transport.bridge import StubGameBridge            # noqa: E402
from agent_loop.transport.ws_channel import ChatHub, WsServer     # noqa: E402

MODMAIN_SRC = (ROOT / "csharp/app/ModMain.cs").read_text(encoding="utf-8")
DUTY_SRC = (ROOT / "csharp/contacts/ContactDuty.cs").read_text(encoding="utf-8")
STORE_SRC = (ROOT / "csharp/contacts/ContactStore.cs").read_text(encoding="utf-8")

A, B = "BalAaa", "BalBbb"


def _body(src: str, signature: str) -> str:
    """取 C# 方法体（按大括号配平）。"""
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
    raise AssertionError(f"未找到方法体: {signature}")


def _write_contacts(root: Path, world: str, names) -> None:
    d = root / "worlds" / world
    d.mkdir(parents=True, exist_ok=True)
    (d / "contacts.json").write_text(
        json.dumps({"contacts": [{"npc_id": n, "added_at": 1, "source": "manual"} for n in names]},
                   ensure_ascii=False), encoding="utf-8")


class _Rig:
    """生产形态（`require_world=True`）的 hub + 两个存档目录 + 一份**扁平目录残留**。

    扁平根故意放一个"谁都不属于"的群（`rooms/index.json`）：作废路径若去载群名录，读到的
    就是这个 —— 于是"作废时不读扁平目录"这条从"数目恰好为 0"变成**可判据**。

    临时目录自建自清（不用 `tmp_path` fixture）：同族 `test_world_scope.py` 也是这个写法，
    而且这样本文件对 `_mutate_lib` 的"无 fixture 直调"快速路径是**可用**的（变异跑得更快）。
    """

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="agent_loop_world_failclosed_"))
        root = self.root
        _write_contacts(root, A, ["姜萌", "江宇文"])
        _write_contacts(root, B, ["云含"])
        flat_rooms = root / "rooms"
        flat_rooms.mkdir(parents=True, exist_ok=True)
        (flat_rooms / "index.json").write_text(
            json.dumps({"rooms": [{"room_id": "g9", "title": "谁都不属于的群", "members": []}]},
                       ensure_ascii=False), encoding="utf-8")
        AgentLoop.reset_for_test(storage_root=str(root), bridge=StubGameBridge(), llm=StubLlmClient())
        self.loop = AgentLoop()
        self.svc = ContactsService(str(root), require_world=True)
        self.hub = ChatHub(self.loop, WsServer(port=0), contact_service=self.svc)

    def call(self, method: str, params=None):
        return asyncio.run(self.hub.handle_request(method, params or {}))

    def close(self):
        AgentLoop.reset_for_test(storage_root=str(self.root))
        shutil.rmtree(self.root, ignore_errors=True)


def _rig() -> _Rig:
    return _Rig()


# =====================================================================================
# ① Python：进世界没身份 = 作废；world_hello 没身份 = 沿用（两条语义不得混）
# =====================================================================================


def test_load_happened_without_identity_invalidates_instead_of_keeping():
    """★核心★ 进世界帧宣告 identity=unknown ⇒ **作废**身份，不许沿用上一个存档。

    沿用就是"新开存档继承了通讯录"：那一刻 Python 手上正是上一个存档的钥匙，而世界已经换了。
    """
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A, "player_name": "甲"})
        assert r.loop.world_id == A and r.svc.world_id == A
        assert [c["npc_id"] for c in r.svc.list_contacts()] == ["姜萌", "江宇文"]

        out = r.call("load_happened", {"identity": "unknown"})
        assert out["world_unknown"] is True, "进世界帧没回 world_unknown，调用方无从知道身份已作废"
        assert r.loop.world_id is None, "进世界读不到身份却仍沿用上一个存档 —— 新档会读到旧档的通讯录"
        assert r.svc.world_id is None and r.svc.blocked is True, "通讯录命名空间没跟着作废"
        assert out["rooms"] == 0, (
            "作废时去载群名录了 —— 未定档时 `rooms_root` 指向**扁平目录**，读进来就是'别人家的群'"
            "（夹具在扁平根放了一个 g9，正是为了让它可判）"
        )
    finally:
        r.close()


def test_invalidated_world_refuses_contact_reads():
    """作废之后 list_* 必须是"拒绝回答"（空表 + world_known=false），而不是"没有好友"。"""
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A})
        r.call("load_happened", {"identity": "unknown"})
        out = r.call("list_contacts", {})
        assert out["contacts"] == []
        assert out["world_known"] is False, "空表没配 world_known=false ⇒ C# 会把'拒绝回答'当成真相写进镜像"
        assert out["world_id"] == "", "作废后回包不该还带着旧档 id"
    finally:
        r.close()


def test_hello_without_identity_still_keeps_the_world():
    """反向钉（向后兼容 + 原设计）：`world_hello` 没带身份 = 连接事件 ⇒ **沿用**，不许作废。

    它是"Python 中途重启不许丢档"的救命绳（09-18 修的那次静默丢记忆），主菜单里本来就读不到
    身份（实机 67 次空帧里 50 次是这种）—— 一起作废就是把正常局面毁掉。
    """
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A})
        r.call("world_hello", {})                      # 旧 DLL / 主菜单形态
        assert r.loop.world_id == A, "world_hello 缺身份不该作废（那是沿用语义）"
        assert r.svc.blocked is False
        assert [c["npc_id"] for c in r.svc.list_contacts()] == ["姜萌", "江宇文"]
    finally:
        r.close()


def test_hello_cannot_resurrect_an_invalidated_identity():
    """作废之后，紧接着的"没身份的 world_hello"也不许把身份捡回来。

    （它是"沿用**当前**命名空间"——作废后当前就是"未定档"，沿用等于继续未定档。）
    """
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A})
        r.call("load_happened", {"identity": "unknown"})
        r.call("world_hello", {})
        assert r.loop.world_id is None and r.svc.blocked is True
    finally:
        r.close()


def test_late_identity_restores_the_right_namespace():
    """身份迟到（C# 逐帧重试补发的那一帧）必须把命名空间切回**新档**。"""
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A})
        r.call("load_happened", {"identity": "unknown"})          # 作废
        out = r.call("load_happened", {"world_id": B, "player_name": "乙"})   # 补发
        assert r.loop.world_id == B and r.svc.world_id == B
        assert r.svc.blocked is False
        assert [c["npc_id"] for c in r.svc.list_contacts()] == ["云含"], "补发后读到的必须是新档名单"
        assert out.get("world_unknown") is None, "补发这一帧是正常路径，不该回 world_unknown"
    finally:
        r.close()


def test_save_happened_without_identity_does_not_invalidate():
    """刻意的不对称：`save_happened` 宣告 unknown **不作废**。

    存档不是"世界变了"的证据（你能存的档就是你在的那个世界）；作废只会让 `flush_all` 整体
    跳过 ⇒ 玩家存了档、对话却没落盘。世界真变了的话，那条 `load_happened` 已经负责作废了。
    """
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A})
        r.call("save_happened", {"identity": "unknown"})
        assert r.loop.world_id == A, "存档帧把身份作废了 —— flush_all 会被跳过，玩家存了档却不落盘"
        assert r.svc.blocked is False
    finally:
        r.close()


def test_list_replies_carry_world_identity_and_epoch():
    """★核心★ 三个档级回包都要带 `world_id` + `world_epoch`，且世代随真实换档 +1。

    没有它们，"旧档的名单"和"新档的名单"是两个**无法区分**的合法答案 —— 一次跨档回包就能把
    旧档名单写进新档镜像（09-20 的 `world_known` 只挡了"Python 不知道"，没挡"知道的是别人"）。
    """
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A})
        e1 = r.hub._world_epoch
        out = r.call("list_contacts", {})
        assert out["world_id"] == A and out["world_epoch"] == e1
        for method, key in (("list_sessions", "sessions"), ("list_hidden", "hidden")):
            o = r.call(method, {})
            assert o["world_id"] == A and o["world_epoch"] == e1, f"{method} 没带身份三元组"
            assert key in o

        r.call("load_happened", {"world_id": B})
        e2 = r.hub._world_epoch
        assert e2 > e1, "换档没有推进世代号 —— 跨档回包判不了废"
        assert r.call("list_contacts", {})["world_id"] == B

        r.call("load_happened", {"identity": "unknown"})
        e3 = r.hub._world_epoch
        assert e3 > e2, "作废（退回未定档）同样是一次真实切换，世代必须 +1"
        assert r.call("list_contacts", {})["world_epoch"] == e3
    finally:
        r.close()


def test_identity_flag_is_case_and_space_tolerant():
    """判据宽容解析：`"Unknown"` / `" unknown "` 同样算宣告（宁可不触发也别误触发）。"""
    r = _rig()
    try:
        r.call("load_happened", {"world_id": A})
        r.call("load_happened", {"identity": " Unknown "})
        assert r.loop.world_id is None
    finally:
        r.close()


# =====================================================================================
# ② C#：三态宣告 / 补发重试 / 换档清理（源码级契约 —— 这些接线删掉行为测试不会变红）
# =====================================================================================


def test_fill_identity_declares_unknown_only_on_world_events():
    """三态：读不到时**只有世界事件**才写 `identity:"unknown"`；`world_hello` 什么都不写。"""
    body = _body(MODMAIN_SRC, "private static void FillWorldIdentity(JObject p")
    assert 'p["identity"] = "unknown"' in body, "读不到身份时不再宣告 —— Python 又会沿用旧档"
    i_flag = body.index('else if (worldEvent)')
    i_write = body.index('p["identity"] = "unknown"')
    assert i_flag < i_write, "宣告没挂在 worldEvent 上 —— world_hello 也会作废，正常局面被毁"

    into = _body(MODMAIN_SRC, "private void OnIntoWorldEvent(ETypeData e)")
    save = _body(MODMAIN_SRC, "private void OnSaveDataEvent(ETypeData e)")
    hello = _body(MODMAIN_SRC, "private void SendWorldHello()")
    assert "FillWorldIdentity(p, true, true)" in into, "进世界帧没宣告三态 / 没清回包基准"
    assert "FillWorldIdentity(p, true)" in save and "FillWorldIdentity(p, true, true)" not in save, (
        "存档帧也清了回包基准 —— 存档不是「世界换了」的证据；一次缺身份的存档会让此后"
        "所有回包被判废（面板一直显示「尚未取得存档身份」、list_sessions 也刷不动）"
    )
    assert "FillWorldIdentity(p, true)" not in hello, "world_hello 也宣告了 —— 主菜单/重启时会把身份作废"
    assert "FillWorldIdentity(p)" in hello


def test_only_world_entry_clears_the_reply_baseline():
    """★ 基准（`ExpectWorld(null)`）只许在**进世界**清 —— 与 Python 侧的不对称逐字对齐。

    Python：`load_happened` 作废 / `save_happened` 不动身份；C# 这边必须同款，
    否则"存档那一刻读不到身份"会让本机进入 fail-closed 的判废状态（回包全丢），
    而 Python 那边一切正常 —— 表现为面板无缘无故说"尚未取得存档身份"。
    """
    body = _body(MODMAIN_SRC, "private static void FillWorldIdentity(JObject p")
    assert "ContactDuty.ExpectWorld(null)" in body
    i_entry_guard = body.index("if (worldEntry) ContactDuty.ExpectWorld(null)")
    i_flag_guard = body.index("else if (worldEvent)")
    assert i_flag_guard < i_entry_guard, "清基准没挂在 worldEntry 上（worldEvent 就清了）"
    retry = _body(MODMAIN_SRC, "private void StartIdentityRetry(int entrySeq)")
    assert "FillWorldIdentity(q, true, true)" in retry, (
        "补发那一帧没按「进世界」处理 —— 补发的语义就是补完那次进世界"
    )


def test_into_world_starts_the_identity_retry():
    """进世界读不到身份时**必须起重试**（否则 Python 停在作废态，玩家整局看不到名单）。"""
    body = _body(MODMAIN_SRC, "private void OnIntoWorldEvent(ETypeData e)")
    assert "_worldEntrySeq++" in body, "没有推进世界入口序号 —— 上一轮重试无法自我作废"
    assert "StartIdentityRetry(entrySeq)" in body, "读不到身份时没有起重试"
    i_guard = body.index("if (!WorldIdentityAvailable) StartIdentityRetry(entrySeq)")
    assert i_guard > body.index("SendWorldFrame(\"load_happened\""), "重试要排在宣告之后"


def test_retry_resends_load_happened_with_a_seq_guard_and_a_deadline():
    """补发的是 `load_happened`（幂等语义），有换档守卫、有上限、并重拉名单。"""
    body = _body(MODMAIN_SRC, "private void StartIdentityRetry(int entrySeq)")
    assert 'SendWorldFrame("load_happened"' in body, "补发帧不对（应为 load_happened：世界刚载入，幂等）"
    assert 'SendWorldFrame("world_hello"' not in body, (
        "用 world_hello 补发 —— 它是连接事件语义（只切不丢），且必须保留'读不到就沿用'的老行为"
    )
    assert "entrySeq != _worldEntrySeq" in body, "缺换档守卫 —— 会拿旧世界读到的人去报新世界"
    assert "AddSeconds(60)" in body, "缺上限（狂读玩家单位不设停）"
    assert "_identityRetryActive = false" in body, "重试没有自我停止的出口"
    for call in ("ContactDuty.RequestManualContacts()", "ContactDuty.RequestSessions()", "ContactDuty.RequestHidden()"):
        assert call in body, f"补发后没有重拉 {call} —— 作废期间那几次拉取全被拒答，镜像还是空的"


def test_world_change_resets_the_mirror_before_repulling():
    """★顺序即正确性★ 换档必须**先整表作废镜像**再重拉。

    反了的话：重拉的回包没回来之前，面板会先把旧档名单渲染出来（`Show()` 第 ③ 步）。
    """
    body = _body(MODMAIN_SRC, "private void OnIntoWorldEvent(ETypeData e)")
    i_reset = body.index("ContactDuty.ResetForWorld()")
    i_pull = body.index("ContactDuty.RequestManualContacts()")
    assert i_reset < i_pull, "先重拉后作废 —— 作废会把刚拉回来的名单又清掉/或旧名单先渲染一帧"
    assert "ContactStore.ResetForWorld()" in _body(DUTY_SRC, "public static void ResetForWorld()"), (
        "ContactDuty.ResetForWorld 没有把镜像整表作废（只清了它自己那两个基准）"
    )


def test_world_change_and_reconnect_both_repull_the_hidden_list():
    """两条世界级路径都要重拉隐藏名单（`list_hidden` 原先只在面板打开时拉 ⇒ 换档残留）。"""
    for sig in ("private void OnIntoWorldEvent(ETypeData e)", "private void OnWsReconnected()"):
        body = _body(MODMAIN_SRC, sig)
        assert "RequestHidden()" in body, f"{sig} 没重拉隐藏名单"


def test_store_reset_clears_every_cross_world_mirror():
    """`ContactStore.ResetForWorld` 必须把**所有**跨档残留清掉（漏一个是新的串档入口）。"""
    body = _body(STORE_SRC, "public static void ResetForWorld()")
    for x in ("_manualNames.Clear()", "_manualUnitIds.Clear()", "_sessionsLast.Clear()",
              "_localLast.Clear()", "_hiddenNames.Clear()"):
        assert x in body, f"ResetForWorld 没清 {x}"
    assert "_manualLoaded = false" in body, "没复位 _manualLoaded（面板会拿旧档名单当'已加载完'）"
    assert "_worldUnknown = false" in body, (
        "复位成 true 是在替 Python 说话（它没说过不知道）—— 换档只是一次普通加载"
    )


def test_duty_never_writes_without_a_world_match():
    """★核心★ 三个回包处理器都必须**先判身份匹配再写**，否则跨档回包照样污染镜像。"""
    for sig, writers in (
        ("public static void RequestManualContacts()", ("SetManualNames(",)),
        # 10-21：会话索引写入入口换成双列版 `SetSessionIndex`（mtime / last 两列语义不同），
        # 这里钉的是**顺序**（先判废、后写），不是函数名。
        ("public static void RequestSessions()", ("SetSessionIndex(",)),
        ("public static void RequestHidden()", ("SetHiddenNames(",)),
    ):
        body = _body(DUTY_SRC, sig)
        assert "WorldMatches(resp" in body, f"{sig} 没有按回包自带的 world_id 判废"
        assert "NoteStaleReply()" in body, f"{sig} 判出不匹配后没有处理分支"
        i_match = body.index("WorldMatches(resp")
        i_write = min(body.index(w) for w in writers if w in body)
        assert i_match < i_write, f"{sig} 里身份校验晚于写操作（等于没校验）"


def test_world_matches_is_fail_closed_and_backward_compatible():
    """判据本身：缺键（老 Python）= 放行；Python 未定档 = 拒；本机未确认 = 拒；不等 = 拒。"""
    body = _body(DUTY_SRC, "private static bool WorldMatches(JObject resp, out string replyWorld)")
    assert "JTokenType.Null" in body, "没处理'键不存在'——老服务端会被判成跨档，通讯录永远空"
    assert "if (got.Length == 0) return false;" in body, "Python 未定档（空 id）时仍放行"
    assert "if (_expectedWorld.Length == 0) return false;" in body, (
        "本机尚未确认世界时仍放行 —— 而'已进世界但读不到身份'正是最危险的那一瞬（fail-open）"
    )
    assert "return got == _expectedWorld;" in body, "判据不是'回包身份 == 本机宣告身份'"


def test_expect_world_is_wired_from_the_identity_injection():
    """基准必须由**唯一**宣告身份的地方写（`FillWorldIdentity`），否则它会与宣告漂移。"""
    assert "ContactDuty.ExpectWorld(wid)" in MODMAIN_SRC, "读到身份时没更新基准"
    assert "ContactDuty.ExpectWorld(null)" in MODMAIN_SRC, "读不到身份（进世界）时没把基准置空"
    body = _body(MODMAIN_SRC, "private static void FillWorldIdentity(JObject p")
    assert body.index("ContactDuty.ExpectWorld(wid)") < body.index("else if (worldEvent)"), (
        "基准只在 worldEvent 分支里更新 —— world_hello 补发的身份不生效"
    )
