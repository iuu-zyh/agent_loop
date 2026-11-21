"""通讯录「已故」层契约（10-27）——死者不该从名单里消失。

## 这一层在修什么

关系层（`RelationNetwork.CollectKnownUnits`）是**每次渲染现取**游戏数据的，而死者
`GetUnit(id)` 恒 null ⇒ 三条采集路全部 `if (u == null) return` ⇒ 人一死就从通讯录里
**静默消失**，连"已不在世间"都看不到，更打不开旧对话。

死后也反推不出"他来过"：`ClearDieUnit` 把关系簿清了，死者快照 `DieData.relationData`
的 `GetRelation(玩家)` 返回 **None**（真机确认）。所以"他是我的道侣"这件事**只有活着时
能抓** —— 只能活着时留一份快照（`known.json`），死后据 unit_id 判已故并把行补回列表。

## 为什么不写成"扫死亡账本"

那是**遍历**（本作一局几十个死者），而且覆盖面也不对：关系层第 ④ 路是"全部有记录的人"
（含零好感），他们死后落在 `normalDieUnitIds` 里，和街上陌生人混在一起分不出来。
故本文件钉的是：**成员资格来自"关系层的历史快照"，死亡只影响能不能说话**。

## 本文件只钉接线

判档/合并的规则本身由 Python 存储测试（`test_contacts_store.py` 的 known_* 一组）钉死；
这里钉的是"C# 有没有真的接上"——单测全绿但一处没接 = 玩家侧一点变化都没有（老教训）。

钉住的六条，每条都对应一种**静默**失败：

1. 死者层**只在 `GetUnit(id)` 解析不到时才补** —— 放宽了，断交/改嫁的活人会借快照复活；
2. 「好友」页**不滤**已故（要看得见），「建群/拉人」**要滤**（死人不能入群）；
3. `OpenChatFor` 解析不到真身时**改走按名开窗**，不许再"已不在世间"直接返回；
4. 按名开窗**不写** `UnitIdRegistry`（那是"自身段"的权威名册，写错就是静默错人）；
5. 已故行**沉底**（混在活人中间会让"还有谁在"看不出来）；
6. 快照在**开面板**与**存档**两处都要推（从没开过面板就存档的人，死了也一样补不回来）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _method_body(src: str, sig: str) -> str:
    """按花括号配平抽出一个方法体（签名必须唯一命中）。"""
    idx = src.index(sig)
    body = src[idx:]
    depth, started = 0, False
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                return body[: i + 1]
    raise AssertionError(f"方法体没闭合：{sig}")


# ---------------------------------------------------------------------------
# 1. 死者层：只在解析不到时补
# ---------------------------------------------------------------------------

def test_dead_layer_only_restores_unresolvable_entries():
    """`alive != null` 必须 `continue` —— 这是"断交的活人不借快照复活"的唯一开关。"""
    src = _read("UI/ContactPresenter.cs")
    body = _method_body(src, "private void MergeDeadIntoContacts()")
    assert "if (alive != null) continue;" in body, (
        "解析得到活体就必须跳过；否则断交/改嫁的人会借快照重新出现在通讯录里（静默）"
    )
    # 没有精确身份就不判：按名查死亡账本是遍历，逐行做会把开面板变成 O(行数×死者数)
    assert "if (string.IsNullOrEmpty(k.UnitId)) continue;" in body


def test_dead_layer_requires_snapshot_loaded():
    """快照还没拉到就不补 —— 否则面板先少几行、回包后再突然冒出来。"""
    src = _read("UI/ContactPresenter.cs")
    body = _method_body(src, "private void MergeDeadIntoContacts()")
    assert "if (!ContactStore.KnownLoaded) return;" in body


def test_dead_layer_is_merged_on_both_entry_points():
    """开面板 与 Store 变更 两条路径都要合并，漏一条就是"名单忽然少人"。"""
    src = _read("UI/ContactPresenter.cs")
    show = _method_body(src, "private void ShowContact()") if "private void ShowContact()" in src \
        else _method_body(src, "public void Show(")
    changed = _method_body(src, "private void OnStoreChanged()")
    assert "MergeDeadIntoContacts();" in show
    assert "MergeDeadIntoContacts();" in changed


def test_manual_dead_entries_are_marked_too():
    """手动好友死了同样要标已故（行不能消失、也不能显示成"人物档案缺失"了事）。"""
    src = _read("UI/ContactPresenter.cs")
    body = _method_body(src, "private void MergeManualIntoContacts()")
    assert "MarkDeadIfAny(e);" in body


# ---------------------------------------------------------------------------
# 2. 好友页显示 / 建群选人排除
# ---------------------------------------------------------------------------

def test_friend_page_shows_dead_but_group_pickers_exclude_them():
    src = _read("UI/ContactPresenter.cs")
    # 好友页的 rows 只滤「临时」，**不许**顺手滤 Dead
    assert "rows = _contacts.FindAll(c => !IsTempOnly(c));" in src
    # 建群/拉人：选人列表必须排除死者
    assert re.search(r"if \(_pick == PickMode\.Create \|\| _pick == PickMode\.Invite\)\s*\n\s*rows = rows\.FindAll\(c => c != null && !c\.Dead\);", src), \
        "建群/拉人选人必须排除已故者（死人不能入群）"


def test_dead_rows_sink_to_bottom():
    src = _read("UI/ContactPresenter.cs")
    body = _method_body(src, "private void SortContacts()")
    assert "a.Dead" in body and "b.Dead" in body, "已故必须沉底，否则一个人死后会像随机跳位"


# ---------------------------------------------------------------------------
# 3. 打开链路：解析不到真身时按名开窗
# ---------------------------------------------------------------------------

def test_open_chat_falls_back_to_name_instead_of_refusing():
    src = _read("UI/ContactPresenter.cs")
    body = _method_body(src, "private void OpenChatFor(ContactEntry e)")
    assert "ChatLauncher.OpenForName(e.Name, e.UnitId)" in body, (
        "死者/失效条目必须能按名开窗 —— Python open_chat 只认 npc_id，账本从 jsonl 恢复"
    )
    assert "已不在世间（找不到对应人物）" not in body, (
        "旧实现挡在这里，于是逝者的旧对话永远打不开"
    )


def test_open_for_name_does_not_touch_identity_registry():
    """没有真身就没有权威身份：写进 `UnitIdRegistry` 就是把猜测洗成权威（静默错人）。"""
    src = _read("npc/ChatLauncher.cs")
    body = _method_body(src, "public static bool OpenForName(")
    assert "UnitIdRegistry.Remember" not in body
    assert "BackfillManualIdentity" not in body
    assert "AbChatPanel.OpenForNameStatic" in body


def test_name_open_path_exists_on_host_panel():
    src = _read("UI/AbChatPanel.cs")
    assert "public static bool OpenForNameStatic(" in src
    body = _method_body(src, "public bool OpenForName(string npcId, string unitId = null)")
    # 立绘只能走缓存（PortraitService.Fill 要活体）
    assert "RowAvatars.Apply(" in body
    assert "PortraitService.Fill(unit" not in body


# ---------------------------------------------------------------------------
# 4. 快照推送：开面板 + 存档两处
# ---------------------------------------------------------------------------

def test_snapshot_pushed_from_panel_and_save():
    presenter = _read("UI/ContactPresenter.cs")
    body = _method_body(presenter, "private void BuildRelations()")
    assert "ContactDuty.SyncKnownContacts(" in body, "开面板时必须把关系层快照推上去"

    main = _read("app/ModMain.cs")
    save = _method_body(main, "private void OnSaveDataEvent(ETypeData e)")
    assert "SyncKnownContacts(" in save, (
        "存档也要推：玩家可能从没打开过通讯录就存档，那个人一旦死了就再也补不回来"
    )


def test_snapshot_is_pulled_on_world_switch_and_reconnect():
    main = _read("app/ModMain.cs")
    assert main.count("RequestKnownContacts()") >= 3, (
        "进世界 / 换档 / 重连 三条路都要重拉快照（与 list_contacts 同刻）"
    )


def test_snapshot_rpc_names_match_python():
    ws = (ROOT / "transport" / "ws_channel.py").read_text(encoding="utf-8")
    store = (ROOT / "storage" / "contacts_store.py").read_text(encoding="utf-8")
    duty = _read("contacts/ContactDuty.cs")
    assert '"list_known_contacts"' in duty and 'method == "list_known_contacts"' in ws
    assert '"sync_known_contacts"' in duty and 'method == "sync_known_contacts"' in ws
    assert 'KNOWN_FILE = "known.json"' in store


def test_snapshot_sync_is_signature_deduped():
    """面板每次打开都会推 100 条；没变就不发（否则日志/抓包看不出哪次真变了）。"""
    src = _read("contacts/ContactDuty.cs")
    body = _method_body(src, "public static void SyncKnownContacts(")
    assert "if (s == _knownSyncSig) return;" in body
    assert "_knownSyncSig = null;" in body, "失败要清签名，否则本次会话再也不重试"


def test_known_mirror_resets_on_world_switch():
    src = _read("contacts/ContactStore.cs")
    body = _method_body(src, "public static void ResetForWorld()")
    assert "_known.Clear();" in body
    assert "_knownLoaded = false;" in body
