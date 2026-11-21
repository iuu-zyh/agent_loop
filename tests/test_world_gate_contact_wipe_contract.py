"""`world_id` 未定档 → 名单被清空 的事故契约（09-20 实机事故）。

用户现场
------------------------------------------------------------------------------------
「有时候我加载进去只显示一个用户（姜萌），有时候又是都显示。」
用户提供的 `contacts.json`（存档 BOlQu6）里**四个人好好的**：
    濮阳彤萱（无 unit_id）、姜萌(yqVSYV)、江宇文(rIsCQs)、公冶抚(1QdhbY)
但通讯录里有时只剩一个 —— 而盘上数据一直是完整的。

根因链
------------------------------------------------------------------------------------
`world_id`（= 玩家 unitID）**只活在 Python 进程内存里**，靠 C# 的三个帧送达
（`world_hello` / `load_happened` / `save_happened`）。它未就绪时 `_blocked()` 为真，
`list_contacts()` 返回 **`[]`** —— 那是**拒绝回答**（不知道是哪个存档，不敢读扁平目录），
与"这个存档确实没有好友"**同值不同义**。而 C# `ContactStore.SetManualNames` 是**整体替换**
（先 `Clear()` 再填）⇒ 空表被当成真相 ⇒ **把手上正确的镜像清空**。

于是通讯录只剩「关系层」（`BuildRelations` 本地读活着的世界，**不受闸门管**）——
姜萌恰好在关系层里，所以"只显示一个姜萌"。

为什么"有时候"
------------------------------------------------------------------------------------
`ModMain` 两条路径**不对称**（本事故的放大器）：
    · `OnIntoWorldEvent`（进世界/读档）→ **有** `RequestManualContacts()` → 自愈
    · `OnWsReconnected`（Python 重启后重连）→ **没有** → 整局残缺，必须手动关开面板
实机日志量得身份真空期：23 次连接，中位 67s，最长 294s，21 次 > 10s。

本文件钉死的四件事
------------------------------------------------------------------------------------
  ① Python 的 list RPC 必须回传 `world_known`（否则客户端无从分辨"空"与"未知"）；
  ② C# 在 `world_known=false` 时**一个字都不许写**（不许清镜像、不许置 `_manualLoaded`）；
  ③ `OnWsReconnected` 必须重拉通讯录（自愈），且顺序在 `SendWorldHello()` **之后**；
  ④ 面板必须如实提示"未取得存档身份"，而不是把残缺名单伪装成完整名单。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from agent_loop.storage.contacts_store import ContactsService  # noqa: E402

WS = ROOT / "transport" / "ws_channel.py"
DUTY = ROOT / "csharp/contacts/ContactDuty.cs"
STORE = ROOT / "csharp/contacts/ContactStore.cs"
MODMAIN = ROOT / "csharp/app/ModMain.cs"
PRESENTER = ROOT / "csharp/UI/ContactPresenter.cs"

WS_SRC = WS.read_text(encoding="utf-8")
DUTY_SRC = DUTY.read_text(encoding="utf-8")
STORE_SRC = STORE.read_text(encoding="utf-8")
MODMAIN_SRC = MODMAIN.read_text(encoding="utf-8")
PRESENTER_SRC = PRESENTER.read_text(encoding="utf-8")

# 用户提供的真实 contacts.json（存档 BOlQu6）——用真数据而不是造数据，
# 这样测试同时钉住"老记录没有 unit_id"这一形态。
REAL_CONTACTS = {
    "contacts": [
        {"npc_id": "濮阳彤萱", "added_at": 1789375666, "source": "manual"},
        {"npc_id": "姜萌", "added_at": 1789409220, "source": "manual", "unit_id": "yqVSYV"},
        {"npc_id": "江宇文", "added_at": 1789614490, "source": "manual", "unit_id": "rIsCQs"},
        {"npc_id": "公冶抚", "added_at": 1789751380, "source": "manual", "unit_id": "1QdhbY"},
    ]
}


def _body(src: str, signature: str) -> str:
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


def _service(tmp_path: Path, world_id=None) -> ContactsService:
    """生产形态（require_world=True）的服务；world_id 给了就定档。"""
    w = tmp_path / "worlds" / "BOlQu6"
    w.mkdir(parents=True, exist_ok=True)
    (w / "contacts.json").write_text(
        json.dumps(REAL_CONTACTS, ensure_ascii=False), encoding="utf-8"
    )
    c = ContactsService(str(tmp_path), require_world=True)
    if world_id:
        c.set_world(world_id, "唐炎")
    return c


# ---------------------------------------------------------------------------
# ① Python：未定档必须可分辨（blocked 属性 + RPC 回传 world_known）
# ---------------------------------------------------------------------------


def test_service_exposes_blocked_so_callers_can_tell_apart(tmp_path):
    """`blocked` 必须存在且如实反映闸门 —— 它是"空表是拒绝回答"的唯一对外信号。"""
    unknown = _service(tmp_path / "a")
    assert unknown.blocked is True, "未定档时 blocked 应为 True"
    known = _service(tmp_path / "b", world_id="BOlQu6")
    assert known.blocked is False, "已定档时 blocked 应为 False"


def test_empty_reply_means_refusal_when_undated(tmp_path):
    """未定档时列表为空 —— 但 `blocked` 同时为真，两者合起来才表达"拒绝回答"。"""
    c = _service(tmp_path)
    assert c.list_contacts() == []
    assert c.blocked is True, "空表必须伴随 blocked=True，否则调用方会当成「没有好友」"


def test_dated_service_returns_the_real_four_contacts(tmp_path):
    """定档后必须读到用户真实的四个人（含"老记录无 unit_id"那一条）。"""
    c = _service(tmp_path, world_id="BOlQu6")
    got = c.list_contacts()
    assert [x["npc_id"] for x in got] == ["濮阳彤萱", "姜萌", "江宇文", "公冶抚"]
    assert c.blocked is False


def test_list_rpcs_return_world_known_flag():
    """★核心★ 三个 list RPC 都必须回传 `world_known`。

    不回传，C# 就无从分辨"空"与"未知"，本事故必然重演（老 DLL 忽略未知键，向后兼容）。

    （10-18 重构口径）三个分支改为统一并入 `_world_fields()` —— 身份字段只有一个源，
    漏一个就是一条新的串档入口。判据随之从"分支里出现 `world_known` 字面量"升级为
    "分支必须并入那一个源，且那个源里 `world_known` 真的来自闸门、并带 `world_id`/`world_epoch`"。
    """
    for method in ("list_contacts", "list_sessions", "list_hidden"):
        m = re.search(
            r'if method == "%s":(.*?)(?=\n        if method|\n        # ----)' % method,
            WS_SRC, re.S,
        )
        assert m, f"没找到 {method} 的路由分支"
        branch = m.group(1)
        assert "_world_fields()" in branch, (
            f"{method} 的回包没有并入 _world_fields() —— 客户端无法区分「未定档拒绝回答」"
            "与「确实为空」，也无从判废跨档回包"
        )
    m = re.search(r"def _world_fields\(self\)(.*?)\n    def ", WS_SRC, re.S)
    assert m, "找不到 _world_fields 实现"
    helper = m.group(1)
    assert "world_known" in helper, "_world_fields 没有回传 world_known"
    assert "world_id" in helper and "world_epoch" in helper, (
        "_world_fields 缺 world_id / world_epoch —— 回包不带身份，跨档回包就判不了废"
        "（10-18「新开存档继承了通讯录」的第二条入口）"
    )


def test_world_known_is_derived_from_the_service_gate_not_hardcoded():
    """标志必须来自服务端真实闸门状态，不许写死 True（写死 = 等于没有这个标志）。"""
    assert "not getattr(self._contact_service, \"blocked\", False)" in WS_SRC, (
        "world_known 不是从 contact_service.blocked 推出来的"
    )


# ---------------------------------------------------------------------------
# ② C#：world_known=false 时一个字都不许写
# ---------------------------------------------------------------------------


def test_csharp_checks_world_known_before_writing_the_mirror():
    """两个回调都要先过 `WorldKnown(resp)` 再写 —— 顺序错了就等于没检查。"""
    for sig in ("public static void RequestManualContacts()",
                "public static void RequestSessions()"):
        body = _body(DUTY_SRC, sig)
        assert "WorldKnown(resp)" in body, f"{sig} 没有校验 world_known"
        # 检查必须早于写操作
        i_check = body.index("WorldKnown(resp)")
        i_write = min(
            # 10-21：会话索引的写入入口由 `SetSessionMtimes` 换成双列版 `SetSessionIndex`
            # （`mtime` = 上次存档 / `last` = 最后一次真说话，两者语义不同不能合并）。
            # 这里要钉的是**顺序**（先判身份、后写），不是函数名 —— 换名时同步这一处即可。
            [body.index(w) for w in ("SetManualNames(", "SetSessionIndex(") if w in body]
            or [len(body)]
        )
        assert i_check < i_write, f"{sig} 里 world_known 校验晚于写操作（等于没校验）"


def test_missing_flag_from_old_python_is_treated_as_known():
    """缺键 = 老 Python（没这道闸）→ 必须当作"已定档"，否则老服务端下通讯录永远不显示。

    这是向后兼容的关键：新 DLL + 老 Python 的组合必须与改动前行为一致。
    """
    body = _body(DUTY_SRC, "private static bool WorldKnown(JObject resp)")
    assert "JTokenType.Null" in body, "WorldKnown 没有处理「键不存在」的情形"
    assert re.search(r"if\s*\(tok\s*==\s*null\s*\|\|\s*tok\.Type\s*==\s*JTokenType\.Null\)\s*return\s+true", body), (
        "缺键/Null 必须返回 true（老服务端无此闸）"
    )


def test_unknown_world_marks_but_never_writes_data():
    """未定档只许"标注镜像不可信"，绝不许碰数据（Store 侧入口必须是独立的）。"""
    assert "MarkWorldUnknown()" in DUTY_SRC, "未定档时没有标注镜像不可信"
    body = _body(STORE_SRC, "public static void MarkWorldUnknown()")
    for forbidden in ("_manualNames.Clear()", "_manualUnitIds.Clear()", "_sessionsLast.Clear()"):
        assert forbidden not in body, f"MarkWorldUnknown 里出现了 {forbidden} —— 又在清数据"
    assert "_manualLoaded = true" not in body, (
        "MarkWorldUnknown 把 _manualLoaded 置真了 —— 面板会一边显示残缺名单一边说"
        "「已加载完」（正是本事故的用户可见症状）"
    )


def test_only_a_valid_reply_sets_manual_loaded():
    """`_manualLoaded` 只许在"有效回包"里置真；未定档那条路不许经过它。"""
    body = _body(STORE_SRC, "public static void SetManualNames(")
    assert "_manualLoaded = true" in body, "SetManualNames 丢了 _manualLoaded 置位"
    assert "_worldUnknown = false" in body, (
        "SetManualNames 收到有效回包后没有清掉 WorldUnknown 标注"
    )


# ---------------------------------------------------------------------------
# ③ 重连必须重拉（自愈），且在 hello 之后
# ---------------------------------------------------------------------------


def test_reconnect_repulls_contacts():
    """★核心★ `OnWsReconnected` 必须重拉通讯录 —— 这是"不自愈 → 自愈"的那一刀。"""
    body = _body(MODMAIN_SRC, "private void OnWsReconnected()")
    assert "RequestManualContacts()" in body, (
        "重连没有重拉好友名单 —— Python 补上身份后没有任何代码再拉一次，"
        "手动好友整局残缺（用户：「有时候只显示一个」）"
    )
    assert "RequestSessions()" in body, "重连没有重拉会话索引（「最近」页同理残缺）"


def test_reconnect_repull_happens_after_world_hello():
    """顺序：必须 `SendWorldHello()` 在前 —— 同一连接按序处理，身份先到位才拉得到真名单。"""
    body = _body(MODMAIN_SRC, "private void OnWsReconnected()")
    i_hello = body.index("SendWorldHello()")
    i_pull = body.index("RequestManualContacts()")
    assert i_hello < i_pull, (
        "重拉排在 SendWorldHello() 之前 —— 身份还没送到就拉，拿回的还是空表"
    )


def test_both_world_entry_paths_repull_symmetrically():
    """两条世界级路径（进世界 / 重连）必须**对称**——本事故正是它们不对称造成的。"""
    into = _body(MODMAIN_SRC, "private void OnIntoWorldEvent(ETypeData e)")
    reconn = _body(MODMAIN_SRC, "private void OnWsReconnected()")
    for name, body in (("OnIntoWorldEvent", into), ("OnWsReconnected", reconn)):
        assert "RequestManualContacts()" in body, f"{name} 缺重拉好友名单"
        assert "RequestSessions()" in body, f"{name} 缺重拉会话索引"


# ---------------------------------------------------------------------------
# ④ 面板如实提示
# ---------------------------------------------------------------------------


def test_panel_surfaces_the_unknown_world_state():
    """面板必须在未定档时提示"未取得存档身份"，不许把残缺名单伪装成完整名单。"""
    assert "ContactStore.WorldUnknown" in PRESENTER_SRC, (
        "面板没有读取 WorldUnknown —— 玩家只会看到一份残缺名单配「按字母排序」的文案，"
        "从而以为好友真的丢了"
    )
    assert "尚未取得存档身份" in PRESENTER_SRC, "面板缺「未取得存档身份」的可见提示"


def _strip_comments(src: str) -> str:
    """剥掉行注释/文档注释 —— 否则断言会命中**注释里复述的文案**而不是真实分支。

    （本文件第一版就踩了这个坑：`ContactPresenter` 有一行注释在解释"不这样会显示
    好友按字母排序 · 点行或 ✓ 传音"，`index()` 先命中它，于是断言对着注释比较顺序。）
    """
    return "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("//", "///", "*", "/*"))
    )


def test_unknown_world_hint_outranks_informational_status():
    """该提示必须排在信息性分支（加载中…／按字母排序）**之前**，否则永远显示不出来。

    判据只看**同一个 if/else 链**且**剥掉注释**：`RenderCurrent` 里有两段状态条
    （好友页一段、群聊页一段，后者由 `RenderRoomList` 负责），拿整段 body 找会撞上
    不相干的那段；而注释里复述的文案更会抢在真实分支前面被命中。
    """
    body = _strip_comments(_body(PRESENTER_SRC, "private void RenderCurrent()"))
    i_warn = body.index("ContactStore.WorldUnknown")
    # 链尾 = 最后一个 SetStatus **整句**的结束（rindex 停在调用起点，会把末分支切掉）
    last = body.rindex("SetStatus(")
    chain_end = body.index(";", body.index(")", last)) + 1
    chain = body[i_warn:chain_end]
    for info in ("通讯录名单加载中…", "好友按字母排序 · 点行或 ✓ 传音"):
        assert info in chain, f"测试前提失效：链内应仍保留「{info}」这条分支"
        assert i_warn < body.index(info), (
            f"未定档提示排在「{info}」之后 —— 那条分支会先命中，提示形同不存在"
        )


def test_unknown_world_hint_does_not_hijack_active_operations():
    """反向钉：搜索/选人时玩家正在操作，反馈优先 —— 警示不许盖掉操作反馈。

    守卫写在 `WorldUnknown` **之前**（`if (守卫 && WorldUnknown)` 是同一条件的两半），
    故取窗口要往前多看一点，否则从令牌处切片会看不到守卫。
    """
    body = _body(PRESENTER_SRC, "private void RenderCurrent()")
    i_warn = body.index("ContactStore.WorldUnknown")
    seg = body[max(0, i_warn - 120) : i_warn + 160]
    assert "_pick == PickMode.None" in seg, "警示没有避开选择模式"
    assert "_search.Length == 0" in seg, "警示没有避开搜索态"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# ⑤ 收尾改进（09-20）：身份取不到时别发注定为空的请求 + 文案说准成因
# ---------------------------------------------------------------------------


def test_world_identity_availability_is_derived_from_the_player_unit():
    """`WorldIdentityAvailable` 必须真的去问玩家单位，不许写死 true（写死 = 等于没挡）。"""
    body = _body(MODMAIN_SRC, "public static bool WorldIdentityAvailable")
    assert "PlayerUnitId()" in body, (
        "WorldIdentityAvailable 没去问 UnitSnapshot.PlayerUnitId —— 它必须反映"
        "「世界是否已加载」，而不是一个常量"
    )
    assert "IsNullOrEmpty" in body, "判据必须是非空，不能只看 null"


def test_show_skips_doomed_pull_when_identity_is_unavailable():
    """★核心★ 身份取不到时不许发 list RPC —— 那一次注定拿到"拒绝回答"空表。

    只是白等 8s 超时，还让日志看起来像"拉过一次但没朋友"（掩盖真实原因）。
    """
    body = _body(PRESENTER_SRC, "public void Show()")
    assert "ModMain.WorldIdentityAvailable" in body, (
        "Show() 没有按「身份是否可取」决定要不要拉名单 —— 会发注定为空的请求"
    )
    i_guard = body.index("ModMain.WorldIdentityAvailable")
    i_pull = body.index("RequestManualContacts()")
    assert i_guard < i_pull, "守卫晚于拉取调用（等于没守卫）"


def test_skipped_pull_is_not_silent():
    """跳过拉取必须留一行日志 —— 否则排障时只看到"名单没更新"，不知为何。"""
    body = _body(PRESENTER_SRC, "public void Show()")
    tail = body[body.index("ModMain.WorldIdentityAvailable"):]
    assert "ModMain.P(" in tail, (
        "跳过拉取没有日志（本工程反复栽在「静默跳过」上：头像泵、面板闸门都是这个形态）"
    )


def test_unknown_world_message_names_the_actual_cause():
    """⚠ 文案必须区分两种成因 —— 混成一句会让玩家照着一个做不到的建议干等。

    身份取不到 = 世界还没加载 → 建议"进入世界"；
    身份取得到但没送达 = 帧在路上 → 建议"稍候"。
    """
    body = _strip_comments(_body(PRESENTER_SRC, "private void RenderCurrent()"))
    seg = body[body.index("ContactStore.WorldUnknown") : body.index("ContactStore.WorldUnknown") + 400]
    assert "ModMain.WorldIdentityAvailable" in seg, (
        "⚠ 文案没有按成因分叉 —— 两种情形成因不同、建议也该不同"
    )
    assert "请稍候" in seg and "进入世界" in seg, (
        "⚠ 文案缺少两种情形各自的措辞（稍候 / 进入世界）"
    )


def test_identity_availability_does_not_replace_the_world_known_check():
    """反向钉：`WorldIdentityAvailable` 是**前置**优化，不许取代 `world_known` 校验。

    两者针对不同情形：前者挡"压根无从可发"，后者挡"发了但 Python 未定档"。
    只留前者会在"身份已取到、帧还在路上"时重新把空表当真相 —— 事故重演。
    """
    assert "WorldKnown(resp)" in DUTY_SRC, (
        "world_known 校验被移除了 —— 身份可取 ≠ Python 已收到，这条不能少"
    )
