"""通讯录头像"已保存但重进仍显示未加载"的三层根因契约（09-19 实机事故）。

用户现场
------------------------------------------------------------------------------------
「通讯录已经保存了一些用户的头像，但是重新进入的时候还是未加载状态。」
盘上 `portrait_cache/<unitID>.png` **确实存在**（对话时抓的快照），但行上恒是预制件的
剪影占位图。零报错、零日志、同进程内**永不恢复**。

三层独立成因（任一层单独存在都足以让头像不显示，所以四条链都要钉）
------------------------------------------------------------------------------------
① **僵尸条目**（确定性、永久）：`TryGet` 用 `e.Tex != null` 判命中，而 `RequestLoad` /
   `PumpLoad` 只用 `_mem.ContainsKey` 判"已缓存"。Unity 的 `Texture2D` 是**场景级**对象，
   读档/重进世界时被销毁；`_mem` 是托管静态字典，活得比它久 ⇒ 出现"key 在、纹理已死"
   的条目，三个方法对同一状态给出**相反**判断 ⇒ 盘上 PNG 再也读不出来。
   唯一原删除路径 `Evict()` 要求 `_mem.Count > 64`，好友少时**永远等不到**。

② **早退在算 key 之前**（确定性、独立于①）：`ApplyAvatar` 旧写法第一行
   `if (avatarGo == null || e?.Unit == null) return;` —— 任何"此刻不在场"的好友
   （`Unit == null`）**连 `RequestLoad` 都不调**。而 `e.UnitId`（contacts.json 里的精确
   身份）就在手上却没被用。`MergeManualIntoContacts` 对已存在条目 `continue`，不重试
   `ResolveEntry` ⇒ 该行整个会话不自愈。对话窗早有"精确 → 按名"两级（`RowAvatars.Apply`），
   通讯录漏了这一层。

③ **泵挂在关闭即不跑的 `Update()` 上**：待读 id 是**常驻层**塞进来的
   （`ContactDuty` 的 list_contacts 回包 → `OnStoreChanged`），面板关着时照样在塞；
   宿主根 OFF ⇒ `ContactPresenter.Update` 不运行 ⇒ id 躺在 `_loadQueue` 里没人处理。
   （`RowAvatars` 已为对话窗记过同款教训：PumpLoad 只挂在 ContactPresenter.Update 上，
   结果"头像一直不出现，且零报错"。）

④ **世界重载不清 `_mem`**：`OnIntoWorldEvent` 清了 `ClearNameIndex()`（名字备忘录），
   却没清孪生的像素缓存 `_mem` —— 这正是①的产生条件，不修则重进世界必再中毒。

为什么判据必须是"调用点/判据形态"而不是"函数存在"
------------------------------------------------------------------------------------
本事故里所有函数都一直存在、一直编译通过、注释一直说它没问题。删掉那一行
`ResetForWorld()` 注册、或把 `TryGet` 改回裸 `return false`，**不会有任何东西变红**
—— 除非测试盯着这些具体形态。①的判据尤其刻意：`_mem.ContainsKey(id)` 在
`RequestLoad`/`PumpLoad` 里的**存在**才是 bug，所以这里**禁止**它出现在这两处。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "csharp/portrait/PortraitCache.cs"
PRESENTER = ROOT / "csharp/UI/ContactPresenter.cs"
ROOMWARM = ROOT / "csharp/UI/RoomPortraitWarmup.cs"
HOTKEYS = ROOT / "csharp/UI/AbHotkeys.cs"
MODMAIN = ROOT / "csharp/app/ModMain.cs"
ROWAVATARS = ROOT / "csharp/UI/RowAvatars.cs"

CACHE_SRC = CACHE.read_text(encoding="utf-8")
PRESENTER_SRC = PRESENTER.read_text(encoding="utf-8")
HOTKEYS_SRC = HOTKEYS.read_text(encoding="utf-8")
MODMAIN_SRC = MODMAIN.read_text(encoding="utf-8")


def _body(src: str, signature: str) -> str:
    """取某个方法的方法体（按大括号配平；IL2CPP 工程里方法体不长，够用）。"""
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


# ---------------------------------------------------------------------------
# ① 僵尸条目：判据必须是"有活纹理"，且发现死纹理要驱逐
# ---------------------------------------------------------------------------


def test_tryget_evicts_poisoned_entry_instead_of_just_returning_false():
    """`TryGet` 撞上死纹理必须**驱逐该条目**，不能只 `return false`。

    只返回 false = key 留在 `_mem` → `RequestLoad` 见 key 就拒绝入队 →
    盘上 PNG 永久读不出来（这就是用户的"重进还是未加载"）。
    """
    body = _body(CACHE_SRC, "public static bool TryGet(string id, out Texture2D tex)")
    assert "DropEntry(" in body, (
        "TryGet 发现 `e.Tex == null` 后必须驱逐僵尸条目（DropEntry），"
        "否则 key 残留会让 RequestLoad 永久拒绝重载"
    )
    # 驱逐必须发生在 `e.Tex != null` 判失败的那一支
    assert re.search(r"if\s*\(\s*e\.Tex\s*!=\s*null\s*\)\s*\{[^}]*return\s+true\s*;", body), (
        "TryGet 丢了「活纹理才算命中」的判据"
    )


def test_drop_entry_destroys_sprite_and_removes_key():
    """驱逐要连 `Sprite` 一起销毁再移除 key —— 只移除 key 会漏掉一个 Unity 对象。"""
    body = _body(CACHE_SRC, "private static void DropEntry(")
    assert "Destroy(e.Sprite)" in body, "驱逐时 Sprite 没销毁（Unity 对象泄漏）"
    assert "_mem.Remove(id)" in body, "驱逐没有真的把 key 从 _mem 里摘掉"


def test_load_paths_must_not_treat_a_bare_key_as_cached():
    """★核心判据★ `RequestLoad` / `PumpLoad` 不许用裸 `_mem.ContainsKey(id)` 判"已缓存"。

    这正是①的成因：key 在但纹理已死时，`ContainsKey` 返回 true ⇒ 拒绝入队/跳过 ⇒
    盘上的 PNG 再也读不出来。两处都必须是"有活纹理才跳过"。
    """
    for sig in ("public static void RequestLoad(string id)",
                "public static void PumpLoad(int budget = 2)"):
        body = _body(CACHE_SRC, sig)
        assert "_mem.ContainsKey(id)" not in body, (
            f"{sig.split('(')[0]} 仍用裸 `_mem.ContainsKey(id)` 当「已缓存」判据 —— "
            "僵尸条目（key 在、纹理已销毁）会被当成有缓存，导致永久拒绝重载"
        )
        assert "pe.Tex != null" in body or "e.Tex != null" in body, (
            f"{sig.split('(')[0]} 必须按「有没有活纹理」判跳过，而不是按 key 存在与否"
        )


# ---------------------------------------------------------------------------
# ② ApplyAvatar：三级 key 解析，早退移到算完之后
# ---------------------------------------------------------------------------


def test_apply_avatar_uses_exact_unitid_before_live_unit():
    """`ApplyAvatar` 必须先认条目自带的精确 `UnitId`，不能只看活着的 `Unit`。

    `e.UnitId` 来自 contacts.json / 关系数据，**不依赖那个人此刻在不在场上**。
    """
    body = _body(PRESENTER_SRC, "private void ApplyAvatar(GameObject avatarGo, ContactEntry e)")
    assert "e.UnitId" in body, (
        "ApplyAvatar 无视了 e.UnitId —— 不在场的好友（Unit == null）因此永远读不出头像"
    )
    assert "IdByName" in body, (
        "ApplyAvatar 缺按名兜底（RowAvatars.Apply 有这条两级链，通讯录必须对齐）"
    )
    # 早退不许再出现在算 key 之前
    assert not re.search(
        r"if\s*\(\s*avatarGo\s*==\s*null\s*\|\|\s*e\?\.Unit\s*==\s*null\s*\)\s*return\s*;", body
    ), (
        "ApplyAvatar 仍在算 key 之前因 `e.Unit == null` 提前返回 —— "
        "该分支正是「不在场的好友连读盘请求都不发」的成因"
    )


def test_apply_avatar_still_requires_an_avatar_node_and_image():
    """反向钉：放宽早退不等于放弃防御 —— 没有槽位/没有 Image 仍要不炸。"""
    body = _body(PRESENTER_SRC, "private void ApplyAvatar(GameObject avatarGo, ContactEntry e)")
    assert "avatarGo == null" in body, "ApplyAvatar 丢了 avatarGo 空判"
    assert "GetComponent<Image>()" in body, "ApplyAvatar 丢了 Image 取用"
    assert re.search(r"if\s*\(img\s*==\s*null\)\s*return\s*;", body), "ApplyAvatar 丢了 img 空判"


def test_unknown_identity_still_keeps_placeholder_without_guessing():
    """实在算不出 key 时保持占位即可 —— 不许拿名字乱猜一个 id 去贴别人的脸。"""
    body = _body(PRESENTER_SRC, "private void ApplyAvatar(GameObject avatarGo, ContactEntry e)")
    assert re.search(r"if\s*\(string\.IsNullOrEmpty\(id\)\)\s*return\s*;", body), (
        "ApplyAvatar 缺「算不出 id 就保持占位」的收口"
    )


# ---------------------------------------------------------------------------
# ③ 泵必须挂在常驻帧回调上
# ---------------------------------------------------------------------------


def test_pumpload_is_driven_by_the_resident_frame_callback():
    """`AbHotkeys.OnFrame` 必须泵 `PortraitCache.PumpLoad()`。

    待读 id 由**常驻层**塞入，而面板关闭时 `ContactPresenter.Update` 不运行 ⇒
    泵不在常驻回调里跑，id 就躺在队列里没人处理（头像不出现、零报错）。
    """
    body = _body(HOTKEYS_SRC, "public static void OnFrame()")
    assert "PortraitCache.PumpLoad()" in body, (
        "常驻帧回调没有泵立绘读盘 —— 面板关闭期间塞入的 id 永远不会被读出来"
    )


def test_resident_frame_callback_is_actually_registered():
    """光有 `OnFrame` 里的调用不够 —— 它得真的被 `g.timer.Frame` 注册（09-20 事故同款）。"""
    # 11-19 起常驻帧回调统一走 `FrameTicker`（键 `KHotkeys`）—— 判据是"**真的被注册**"，
    # 不是"用哪套 API 注册"。旧写法 `g.timer.Frame(new Action(AbHotkeys.OnFrame))` 已被替换。
    assert re.search(r"FrameTicker\.Ensure\(FrameTicker\.KHotkeys,\s*\"hotkeys\",\s*AbHotkeys\.OnFrame\)", MODMAIN_SRC), (
        "AbHotkeys.OnFrame 没有被 ModMain 注册 ⇒ 整段是死代码"
    )


def test_presenter_update_may_keep_pump_but_is_not_the_only_driver():
    """`ContactPresenter.Update` 保留一份泵是无害冗余（队列空即早退），但**不许**是唯一驱动。"""
    assert "PortraitCache.PumpLoad()" in HOTKEYS_SRC, (
        "泵的唯一驱动不能又回到面板组件上（关闭态不运行）"
    )


# ---------------------------------------------------------------------------
# ④ 世界重载清内存缓存
# ---------------------------------------------------------------------------


def test_reset_for_world_clears_mem_queue_and_dir():
    """`ResetForWorld()` 必须清 `_mem` / `_loadQueue`，否则重进世界必再中毒。"""
    body = _body(CACHE_SRC, "public static void ResetForWorld()")
    assert "_mem.Clear()" in body, "ResetForWorld 没有清 _mem（僵尸条目会留下来）"
    assert "_loadQueue.Clear()" in body, (
        "ResetForWorld 没有清 _loadQueue —— 上一局排队的 id 会串到新世界"
    )
    assert "_dir = null" in body, "ResetForWorld 没有复位 _dir 备忘"


def test_reset_for_world_destroys_owned_unity_objects():
    """清表前要把自己持有的 `Tex`/`Sprite` 销毁 —— 只清字典会泄漏 Unity 对象。"""
    body = _body(CACHE_SRC, "public static void ResetForWorld()")
    assert "Destroy(kv.Value.Sprite)" in body or ".Sprite)" in body, "ResetForWorld 没销毁 Sprite"
    assert ".Tex)" in body, "ResetForWorld 没销毁 Tex"


def test_reset_for_world_is_wired_into_the_world_reload_hook():
    """★核心判据★ 光有 `ResetForWorld` 没有调用点 = 死代码（09-20 事故的教训）。

    它必须与 `ClearNameIndex` / `UnitIdRegistry.ResetForWorld` / `ChatFrameQueue.ClearAll`
    并列，挂在 `ModMain.OnIntoWorldEvent`。
    """
    assert re.search(r"PortraitCache\.ResetForWorld\(\)", MODMAIN_SRC), (
        "PortraitCache.ResetForWorld 没有任何调用点 —— 世界重载时 _mem 不会被清，"
        "僵尸条目照旧产生"
    )
    body = _body(MODMAIN_SRC, "private void OnIntoWorldEvent(ETypeData e)")
    assert "PortraitCache.ResetForWorld()" in body, (
        "ResetForWorld 不在 OnIntoWorldEvent 里 —— 那不是世界重载的那条钩子"
    )
    # 与孪生的名字备忘并列（两者成因同源，漏一个就是本事故）
    assert "PortraitCache.ClearNameIndex()" in body, (
        "OnIntoWorldEvent 丢了 ClearNameIndex（本修复不该把它挤掉）"
    )


def test_all_world_resets_are_deliberately_isolated_and_named_alike():
    """四个世界级缓存重置应同名同钩子，便于以后新增缓存时不会再漏一个。"""
    body = _body(MODMAIN_SRC, "private void OnIntoWorldEvent(ETypeData e)")
    for call in ("PortraitCache.ClearNameIndex()", "PortraitCache.ResetForWorld()",
                 "UnitIdRegistry.ResetForWorld()", "ChatFrameQueue.ClearAll()"):
        assert call in body, f"世界重载钩子缺 {call}（同类缓存重置被漏掉一个）"


# ---------------------------------------------------------------------------
# 反向钉：不许为了让头像出现而退回"直接调渲染 API"
# ---------------------------------------------------------------------------


def test_contacts_path_never_calls_the_game_portrait_renderer():
    """通讯录**绝不许**调 `PortraitService.Fill`（对未进场 NPC 调它直接异常，是历史事故根因）。

    本修复只放宽"读像素快照"的条件，不许顺手接回渲染 API。
    判据只看**代码行**：`ContactPresenter` 的注释里**确实**提到 `CreateTextureInModelData`
    （在解释"为什么不能调它"），所以这里必须剥掉注释再查 —— 否则测试会把说明文字当调用。
    """
    body = _body(PRESENTER_SRC, "private void ApplyAvatar(GameObject avatarGo, ContactEntry e)")
    assert "PortraitService" not in body, (
        "ApplyAvatar 调了游戏立绘渲染 API —— 未进场 NPC 会异常（历史事故根因）"
    )
    # 剥掉行注释/文档注释后再查全文件（注释里提到它是允许的，那是在说明"不能调"）
    code = "\n".join(
        line for line in PRESENTER_SRC.splitlines()
        if not line.lstrip().startswith(("//", "///", "*", "/*"))
    )
    assert "CreateTextureInModelData" not in code, (
        "ContactPresenter 的**代码**里出现了游戏渲染 API 调用"
    )


def test_cache_module_keeps_the_documented_dir_fallback():
    """`Dir()` 的两级兜底（mod 根 → persistentDataPath）不许在改动中被删。"""
    assert "portrait_cache" in CACHE_SRC
    body = _body(CACHE_SRC, "private static string Dir()")
    assert "ResolveRoot()" in body, "Dir() 丢了 mod 根解析"
    assert "persistentDataPath" in body, "Dir() 丢了游戏可写目录兜底"


if __name__ == "__main__":
    import sys

    sys.exit(__import__("pytest").main([__file__, "-v"]))
