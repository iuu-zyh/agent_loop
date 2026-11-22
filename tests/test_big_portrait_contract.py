"""点击头像放大立绘契约（10-18）：气泡头像点一下 → 屏幕正中大立绘 → ESC / 右键退出。

**用户需求原文**：「我希望可以点击图标的时候可以放大看到立绘（就和正常的立绘一样）……
**不是**在界面上另加一个固定按钮」。

**为什么要有这个文件**：这条路的两半都会**静默失败**——
  · 点击没挂上 ⇒ 完全没反应，零报错（`m_RaycastTarget` 本来就是 1，看着"应该能点"）；
  · 画布取错 ⇒ 浮层挂到别的树下面，位置/层级不对，照样零报错；
  · 向下转型 ⇒ 本工程铁律上是**抛异常**（`(子类)`）或**静默 null**（`as 子类`），
    而它很容易被"顺手写成 cast"改回去 —— 那正是 `PortraitCache` 用整段注释警告过的坑。
  · **拦截层漏撤** ⇒ 对话窗永久点不动，且零报错（比上面三条更毒：玩家只会以为游戏卡了）。
真机上这些只表现为"点了没反应"。所以接线必须在源码层面钉住。

钉十七件事：
  A. **不得向下转型**：原生路只能 `new UIBigPortraitModel(ui.Pointer)`（同指针真类型代理）
  B. 两条路都在，且**原生在前**（用户要的"和正常立绘一样"就是原生那张）
  C. `Bind` 的**空名早退**在加 Button 之前（空名 = 群聊不知道是谁，挂上去就是点了弹别人脸）
  D. `Tick` 关着时**早退在 `Input.GetKeyDown` 之前**（常驻每帧回调的零开销规矩）
  E. 关闭键**只有一处定义**（`CloseKey`；改键位只改这一处）
  F. 槽位必须是**高瘦**（照旧预制件 170×230 的比例）——正方形只是把方头像拉大，不是"正常立绘"
  G. `Close` 两条路都收，且**幂等**
  H. `ChatWindow` 三处建泡**都**绑，且三态判据与 `SpeakerOf` 同构
  I. 宿主挂**根画布**下（照 `HoverTip` 的成熟做法），且**不自建 Canvas/GraphicRaycaster**
     ——模态浮层一旦漏关就是"全游戏点不动"，复用游戏已有拾取才不会出这种事
  J. 常驻 tick 接入（`AbHotkeys.OnFrame`）——对话窗关掉后 `ChatPresenter.Update` 不跑
  K. 换存档复位（`ModMain`）；且**两类节点的清法不同**（见该测试）
  L. `ChatBubble` 暴露头像节点（`ChatWindow` 才够得着）
  M. 不改 AB 预制体、不新增 MonoBehaviour 类型（免 `RegisterTypeInIl2Cpp`）
  N. **ESC 是模态的**：`ChatPresenter` 那份 ESC 必须先问 `BigPortrait.ConsumesEscape()`
     （10-18 实测 bug：不问就"立绘和对话窗一起关"）
  O. **模态拦截层**：挂在你点的那个窗自己的 Canvas 子树里（不是根画布）+ 末位兄弟，
     **全透明只吃射线、不挂 Button**（10-18 拍板「点击的话吞掉就行」）
  P. 拦截层**任何路径都要撤**（Close / 自愈 / 换存档）——漏撤 = 对话窗永久点不动
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"

BP = CS / "portrait" / "BigPortrait.cs"
CHAT_WINDOW = CS / "UI" / "ChatWindow.cs"
CHAT_PRESENTER = CS / "UI" / "ChatPresenter.cs"
ITEM_VIEWS = CS / "UI" / "ChatItemViews.cs"
HOTKEYS = CS / "UI" / "AbHotkeys.cs"
MODMAIN = CS / "app" / "ModMain.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """去掉文档注释与行注释 —— 断言"不许出现某实现"时，注释里提到它不算（本仓注释很长）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*///.*$", "", src, flags=re.M)
    src = re.sub(r"//.*$", "", src, flags=re.M)
    return src


def _body(src: str, decl: str) -> str:
    """取一个成员（方法/属性）的**花括号配平**体；找不到返回 ""。"""
    i = src.find(decl)
    if i < 0:
        return ""
    j = src.find("{", i)
    if j < 0:
        return ""
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1
    return ""


# ---------------------------------------------------------------- A. 铁律

def test_native_path_must_not_downcast():
    """★ 本工程铁律：不得对 Il2Cpp 方法/属性返回值做向下转型 ★

    `g.ui.OpenUI(...)` 的声明返回类型就是 `UIBase`，而我们要的是 `UIBigPortraitModel`：
      · `(UIBigPortraitModel)ui` → 抛 InvalidCastException；
      · `ui as UIBigPortraitModel` → **静默 null**（更坏：看着写对了、点了没反应）。
    唯一正确写法 = 同一个 native 指针另造真类型代理 `new UIBigPortraitModel(ui.Pointer)`。
    这条极易被"顺手简化成 cast"改回去，故钉死。
    """
    code = _code_only(_read(BP))
    assert "(UIBigPortraitModel)" not in code, "原生路出现了向下转型 cast（必抛异常）"
    assert "as UIBigPortraitModel" not in code, "原生路出现了 as 向下转型（静默 null）"
    assert "new UIBigPortraitModel(" in code, "原生路必须用 new UIBigPortraitModel(ptr) 造代理"
    assert "ui.Pointer" in code, "代理必须建在 OpenUI 返回的那个 native 指针上"


def test_native_path_logged_and_falls_back():
    """原生失败必须**能**转自建（而不是抛出去/静默）。"""
    code = _code_only(_read(BP))
    body = _body(code, "private static bool TryNative(")
    assert body, "找不到 TryNative"
    assert "catch" in body, "TryNative 必须自带 catch（原生窗不可用是常态，不是崩溃）"
    assert "return false" in body, "TryNative 失败要返回 false 交给自建那条路"
    assert "return true" in body, "TryNative 成功要返回 true"


def test_native_guards_null_uitype_before_openui():
    """★ 拿 null 去调 `OpenUI` 会在**游戏 UIMgr 内部**炸 ★

    `UIType.BigPortraitModel` 是静态属性（由 `UIType..cctor` 从配表灌进来）——
    万一某个游戏版本没注册它，属性就返回 null。而 `OpenUI(null)` 的炸点不在我们这层，
    是在游戏 UI 管理器内部，正是 09-10 那次"从游戏 UI 内部炸出来、打断游戏 UI 流程"
    的事故形状（当时是 `Instantiate(null)`）。
    ⇒ 必须先取 `uiType` 判空，**宁可不调**。
    """
    code = _code_only(_read(BP))
    body = _body(code, "private static bool TryNative(")
    assert body, "找不到 TryNative"
    i_guard = body.find("uiType == null")
    i_open = body.find("g.ui.OpenUI(")
    assert i_guard >= 0, "TryNative 必须先判 UIType.BigPortraitModel 是否为 null"
    assert i_open >= 0, "TryNative 要调 OpenUI"
    assert i_guard < i_open, "判空必须排在 OpenUI 之前（事后 catch 拦不住游戏内部的半破坏）"
    assert "OpenUI(uiType)" in body, "OpenUI 要传判过空的那个局部变量"


def test_show_prefers_native_then_own():
    """两条路都在，且**原生在前** —— 用户要的"和正常的立绘一样"就是游戏原生那张。"""
    code = _code_only(_read(BP))
    body = _body(code, "public static void Show(WorldUnitBase unit")
    assert body, "找不到 Show(WorldUnitBase)"
    i_native = body.find("TryNative(")
    i_own = body.find("TryOwn(")
    assert i_native >= 0, "Show 没走原生路"
    assert i_own >= 0, "Show 没走自建兜底"
    assert i_native < i_own, "必须原生优先、自建兜底（反了就等于永远不用游戏原生立绘）"


# ---------------------------------------------------------------- B. 点击接线

def test_bind_empty_name_returns_before_adding_button():
    """★ 空名早退必须在 `AddComponent<Button>` **之前** ★

    空名的语义是"群聊里不知道这条是谁说的"（`ChatWindow.SpeakerOf` 的三态之一）——
    那时头像本身都按"不画错人"隐藏了，再挂个点了弹别人脸的按钮就是撒谎。
    早退若排到加 Button 之后，等于"按钮装上了、点开是别人"。
    """
    code = _code_only(_read(BP))
    body = _body(code, "public static void Bind(")
    assert body, "找不到 Bind"
    i_guard = body.find("IsNullOrEmpty(name)")
    i_add = body.find("AddComponent<Button>")
    assert i_guard >= 0, "Bind 必须有空名早退判据"
    assert i_add >= 0, "Bind 必须给头像挂 Button"
    assert i_guard < i_add, "空名早退必须排在挂 Button 之前"


def test_bind_uses_clickutils_not_raw_addlistener():
    """必须走 `ClickUtils.Attach` —— IL2CPP 下 UnityAction 要「Action → 隐式转换 → AddListener」
    三步写法，裸 lambda 会 CS1660 / 运行期不触发（那条坑 `ChatUiBuilder.ClickUtils` 记着）。"""
    code = _code_only(_read(BP))
    assert "ClickUtils.Attach(" in code, "必须复用 ClickUtils.Attach 接线"
    assert "onClick.AddListener(" not in code, "不许绕开 ClickUtils 裸写 AddListener"


def test_bind_passes_id_so_no_name_guessing():
    """`unitId` 要一路传到 `Resolve` —— 重名时按名解析会**猜错人**（`RowAvatars.Apply` 的教训）。"""
    code = _code_only(_read(BP))
    body = _body(code, "public static void Bind(")
    assert "unitId" in body, "Bind 必须接收 unitId"
    show = _body(code, "public static void Show(string name")
    assert "Resolve(name, unitId)" in show, "Show 要把 unitId 交给 Resolve（不是只传名）"


# ---------------------------------------------------------------- C. 关闭

def test_close_key_defined_once():
    """关闭键只有一处定义（改键位只改这一处）。

    `KeyCode.Escape` 在 `KeyLabel`（给提示文案取人话键名）里合法地再出现一次，
    故判据不是"字面量只出现一次"，而是**赋值点只有一处 + Tick 用的是那个常量**。
    """
    code = _code_only(_read(BP))
    assert re.search(r"CloseKey\s*=\s*KeyCode\.Escape", code), "关闭键应在 CloseKey 处定义"
    assert len(re.findall(r"CloseKey\s*=", code)) == 1, "CloseKey 只许赋值一处"
    # 判据核用常量、不写字面量 —— 否则改键位时会漏掉接线那一处
    tick = _body(code, "public static void Tick(")
    assert "Input.GetKeyDown(CloseKey)" in tick, "Tick 必须用 CloseKey 常量读键"
    assert "Input.GetKeyDown(KeyCode." not in code, "不许在别处硬编码键位"


def test_tick_early_returns_before_input():
    """★ `Tick` 关着时必须**在 `Input.GetKeyDown` 之前**早退 ★

    它挂在每帧常驻回调（`AbHotkeys.OnFrame`）上，而常态是"浮层没开" ——
    判据排到输入读取之后就等于每帧白问一次 Unity 输入（本仓常驻 tick 的零开销规矩）。
    """
    code = _code_only(_read(BP))
    body = _body(code, "public static void Tick(")
    assert body, "找不到 Tick"
    # 判据要查**完整语句**而不是"IsOpen 三个字母"：`Tick` 里现在还有一处
    # `if (_blocker != null && !IsOpen) ClearBlocker();`（拦截层自愈），
    # 只查 IsOpen 字样的话，把早退改成 `if (false) return;` 照样绿（10-18 二测收紧）。
    i_guard = body.find("if (!IsOpen) return;")
    i_input = body.find("Input.GetKeyDown")
    assert i_guard >= 0, "Tick 缺 `if (!IsOpen) return;` 这条早退（别用别处的 IsOpen 引用糊弄）"
    assert i_input >= 0, "Tick 要读关闭键"
    assert i_guard < i_input, "IsOpen 早退必须排在读输入之前"


def test_close_covers_both_paths_and_is_idempotent():
    """`Close` 两条路都收：原生走 `CloseUI`、自建走 `SetActive(false)`。"""
    code = _code_only(_read(BP))
    body = _body(code, "public static void Close(")
    assert body, "找不到 Close"
    assert "if (!IsOpen) return;" in body, "Close 缺幂等早退（没开时该是空操作，也别刷日志）"
    assert "CloseUI(" in body, "Close 要收掉原生窗"
    assert "_nativeOpen = false" in body, "Close 要复位原生标志位（否则下次白关一次）"
    assert "SetActive(false)" in body, "Close 要收掉自建浮层"


def test_close_called_by_mask_and_esc():
    """三条关闭路都要接上：ESC → `Tick`；遮罩 → `ClickUtils.Attach(maskBtn, Close)`。"""
    code = _code_only(_read(BP))
    assert "Input.GetKeyDown(CloseKey)" in code, "ESC 那条没接"
    assert "ClickUtils.Attach(maskBtn, Close)" in code, "点空白处那条没接"


# ---------------------------------------------------------------- D. 尺寸（关键事实）

def test_slot_is_tall_not_square():
    """★ 槽位必须是**高瘦**比例 ★

    `ModelOffset=(0,-4.5)` / `ModelScale=1f` 是从神识传音逐字抄的，而它原来用在
    **高瘦立绘**槽位上（旧预制件 `PortraitLeft/Right = 170×230`，比例 ≈0.739）。
    放大时若用正方形，得到的只是"80×80 那张方头像拉大"，不是用户要的"正常立绘"。
    """
    code = _code_only(_read(BP))
    mw = re.search(r"SlotW\s*=\s*([0-9.]+)f", code)
    mh = re.search(r"SlotH\s*=\s*([0-9.]+)f", code)
    assert mw and mh, "找不到 SlotW/SlotH"
    w, h = float(mw.group(1)), float(mh.group(1))
    ratio = w / h
    assert 0.70 <= ratio <= 0.78, (
        f"槽位比例 {ratio:.3f} 偏离旧立绘槽位的 170/230≈0.739 —— 取景常量是按那个比例调的"
    )
    assert h > 400, f"槽位高 {h} 太小，渲不出'正常立绘'的尺寸（分辨率跟槽位走）"


def test_slot_rendered_by_portrait_service():
    """大图必须走**游戏立绘管线**（`PortraitService.Fill`）—— 这正是"不用存图"的那条路。"""
    code = _code_only(_read(BP))
    assert "PortraitService.Fill(" in code, "自建浮层必须用 PortraitService.Fill 现渲"
    assert "PortraitCache" not in code, "放大立绘不该走像素缓存（那是'单位不在场'的降级路）"


# ---------------------------------------------------------------- E. 宿主与接线

def test_host_is_root_canvas_child():
    """宿主挂**根画布**下（照 `HoverTip` 的成熟做法：嵌套 Canvas 的 renderMode 只是残留）。"""
    code = _code_only(_read(BP))
    body = _body(code, "private static Transform ResolveCanvasRoot(")
    assert body, "找不到 ResolveCanvasRoot"
    assert "rootCanvas" in body, "必须取**根** Canvas（子 Canvas 的 renderMode 不生效）"
    assert "SetAsLastSibling" in code, "浮层要 SetAsLastSibling 才盖在最上层"


def test_no_own_canvas_or_raycaster():
    """★ **不许**自建 Canvas / GraphicRaycaster ★

    模态浮层自带 raycaster 一旦漏关就是"全游戏点不动"（`UnreadBanner` 那条路刻意
    **不挂** GraphicRaycaster 就是为了不参与拾取；本浮层要拾取，故必须**复用**游戏已有的，
    而不是自己造一个）。造 Canvas 也会引入嵌套画布的 renderMode/scale 坑。
    """
    code = _code_only(_read(BP))
    assert "AddComponent<Canvas>" not in code, "不许自建 Canvas（挂根画布下即可）"
    assert "AddComponent<CanvasScaler>" not in code, "不许自建 CanvasScaler"
    assert "GraphicRaycaster" not in code, "不许自建 GraphicRaycaster（漏关 = 全游戏点不动）"


def test_no_prefab_change_and_no_new_monobehaviour():
    """不改 AB 预制体、不新增 MonoBehaviour 类型（免 `RegisterTypeInIl2Cpp`）。"""
    code = _code_only(_read(BP))
    assert "ModAbRes" not in code and "PrefabKey" not in code, "不该碰 AB 预制体"
    assert ": MonoBehaviour" not in code, "不该新增 MonoBehaviour 类型（须注册才能 AddComponent）"
    assert "static class BigPortrait" in code, "BigPortrait 应是静态工具类"


def test_host_rebuilt_when_destroyed():
    """宿主随场景销毁后要能重建（Unity fake-null 判据），否则"看着还开着、实际已死"。"""
    code = _code_only(_read(BP))
    body = _body(code, "private static bool EnsureBuilt(")
    assert body, "找不到 EnsureBuilt"
    assert "if (_host != null) return true;" in body, "EnsureBuilt 要先判缓存（含 fake-null）"
    assert "_host = null" in body, "销毁后要清引用以便重建"


def test_chat_window_binds_all_three_bubble_sites():
    """★ `ChatWindow` 三处建泡**都**要绑 ★

    漏一处的表现是"某些气泡点了没反应"（比如只有历史回放能点、新消息不能），零报错。
    三处 = `AppendUserMessage` / `AppendNpcStatic` / `BeginNpcBubble`。
    """
    code = _code_only(_read(CHAT_WINDOW))
    assert code.count("new ChatBubble(") == 3, "ChatBubble 建泡点数量变了，请同步检查绑定"
    assert code.count("BindAvatarClick(") >= 4, (
        "BindAvatarClick 应出现 1 次定义 + 3 次调用"
    )
    for site in ("public RowMeta AppendUserMessage(",
                 "public RowMeta AppendNpcStatic(",
                 "public void BeginNpcBubble("):
        body = _body(code, site)
        assert body, f"找不到 {site}"
        assert "BindAvatarClick(" in body, f"{site} 建的行没挂「点开大立绘」"


def test_bind_avatar_click_three_states_match_speakerof():
    """★ 三态判据必须与 `SpeakerOf` 同构 ★

    "画的是谁"与"点开显示谁"必须是同一个人。判据分叉就会出现
    "看着是甲、点开是乙" —— 比不能点更坏（`RowAvatars.Apply` 拒绝"模板那张别人的脸"同理由）。
    """
    code = _code_only(_read(CHAT_WINDOW))
    body = _body(code, "private void BindAvatarClick(")
    assert body, "找不到 BindAvatarClick"
    assert "isUser" in body, "玩家行要单独判（玩家行画的恒是玩家）"
    assert "who == null" in body, "单聊分支（who==null）缺失"
    assert "CurrentNpcId" in body, "单聊要点开**当前对话对象**"
    assert "who.Length == 0" in body, "群聊未知（空串）必须早退、不挂"
    # 单聊判据必须是 `who == null`（而非 IsNullOrEmpty）—— 空串是"群聊不知道是谁"，语义不同
    assert "IsNullOrEmpty(who)" not in body, "不能用 IsNullOrEmpty 合并两态（空串≠单聊）"


def test_chatbubble_exposes_avatar():
    """`ChatBubble` 要暴露头像节点，否则 `ChatWindow` 够不着（它只知道说话人三态）。"""
    code = _code_only(_read(ITEM_VIEWS))
    assert "public readonly RawImage Avatar" in code, "ChatBubble 没暴露 Avatar"
    body = _body(code, "public ChatBubble(ChatWindowRefs refs, Transform contentParent, bool isUser, string text,")
    assert body, "找不到 ChatBubble 主构造函数"
    assert "Avatar =" in body, "构造函数必须给 Avatar 赋值（readonly 只能在构造里定）"
    # 三条提前 return 的分支都要给它一个值，否则编译不过 —— 这里钉住"确实给了"
    assert body.count("Avatar = ") >= 3, "提前 return 的分支也要给 Avatar 赋值"


# ---------------------------------------------------------------- F. 常驻接线

def test_tick_wired_into_always_on_callback():
    """★ 必须挂**常驻**回调 ★

    `AbHotkeys.OnFrame` 类头记过两次同款教训：浮层挂在游戏根画布下，
    **对话窗关掉后 `ChatPresenter.Update` 那条路根本不跑**（宿主根 OFF）——
    挂到那边就成了"窗口关了、ESC 关不掉浮层"。
    """
    code = _code_only(_read(HOTKEYS))
    body = _body(code, "public static void OnFrame(")
    assert body, "找不到 OnFrame"
    assert "BigPortrait.Tick()" in body, "OnFrame 没接 BigPortrait.Tick"


def test_reset_on_world_reload():
    """换存档要复位 —— 但**两类节点的清法相反**，这里把区别钉死（10-18 二测加）。

    · `_host`/`_slot` 挂在**游戏根 Canvas** 下 ⇒ 随场景一起销毁 ⇒ **只清引用**，
      去 Destroy 反而是拿已销毁对象操作（该文件里"只清引用"的原始理由，仍然成立）；
    · `_blocker` 挂在**你点的那个窗**（对话窗，常驻 mod UI）下 ⇒ **可能活过换存档** ⇒
      必须**真 Destroy**。只清引用就会剩下一层"没人认领、但仍盖在对话窗上"的透明拦截层，
      表现为**对话窗永久点不动、且零报错** —— 这条正是 `_blocker` 字段注释里警告的形状。
    """
    code = _code_only(_read(MODMAIN))
    assert "BigPortrait.ResetForWorld()" in code, "ModMain 没接整理"
    bp = _code_only(_read(BP))
    body = _body(bp, "public static void ResetForWorld(")
    assert body, "找不到 ResetForWorld"
    assert "_nativeOpen = false" in body, "复位要清原生标志位（否则新档 IsOpen 恒真）"
    # 宿主那两类：只清引用
    assert "_host = null" in body and "_slot = null" in body, "宿主引用没清"
    # 拦截层：必须真撤
    assert "_blocker" in body, "换存档没管拦截层 —— 它会活过换存档，把对话窗永久盖住"
    assert "Destroy" in body, "拦截层必须真 Destroy（只清引用 = 留下一层没人认领的拦截层）"
    assert "_blocker = null" in body, "拦截层引用没清"


def test_esc_is_modal_and_chat_presenter_yields():
    """★ ESC 必须**只关最上面那层** ★（10-18 用户实测 bug 的反向锁）

    现场：点头像开立绘 → 按 ESC → **立绘和对话窗一起关了**。
    根因：两个互不知情的 ESC 处理器同帧各关各的——
      · `BigPortrait.Tick()`（我们，10-18 加的）；
      · `ChatPresenter.Update()` 里那份（09-xx 为"按 Esc 关对话窗"加的，比本功能早得多）。

    判据必须**与两者执行顺序无关**，所以 `ConsumesEscape()` 里是**两条**：
    `IsOpen`（对话窗先跑时立绘还开着）**或** `_escFrame == Time.frameCount`
    （我们先把窗关了、`IsOpen` 已 false）。只留一条就会有一半的帧序漏掉。
    """
    bp = _code_only(_read(BP))
    body = _body(bp, "public static bool ConsumesEscape(")
    assert body, "找不到 ConsumesEscape —— 收口点被删了"
    assert "IsOpen" in body, "ConsumesEscape 少了 IsOpen 判据（对话窗先跑时会漏）"
    assert "_escFrame" in body and "Time.frameCount" in body, \
        "ConsumesEscape 少了帧号判据（我们先把窗关了时会漏 ⇒ 对话窗仍被一起关）"
    # 标记点：关窗前记下帧号
    tick = _body(bp, "public static void Tick(")
    assert "_escFrame = Time.frameCount" in tick, "Tick 关窗前没标记帧号 ⇒ 帧号判据恒不成立"
    # 对话窗那条路必须先问
    cp = _code_only(_read(CHAT_PRESENTER))
    esc = _body(cp, "if (Input.GetKeyDown(KeyCode.Escape))")
    assert esc, "ChatPresenter 的 ESC 分支不见了（被重构过？）"
    assert "BigPortrait.ConsumesEscape()" in esc, \
        "ChatPresenter 的 ESC 没让路 ⇒ 同一次按键又会把立绘和对话窗一起关掉"


def test_doc_records_the_resolution_fact():
    """文件头必须记着"渲染分辨率跟槽位走"这条**关键事实** —— 它是整个方案的地基，
    而它极容易被后人用"80×Canvas缩放=106.67≠120"那种算术重新推翻（本仓真发生过一次）。"""
    src = _read(BP)
    assert "size" in src and "模型缩放" in src, "没写清第 4 个参数 size 是模型缩放"
    assert "跟" in src and "槽位" in src, "没写明分辨率跟着槽位走"
    assert "170×230" in src or "170" in src, "没留旧大立绘槽位的比例依据"


# ---------------------------------------------------------------- G. 诊断探针

def test_probe_captures_all_decisive_numbers():
    """★ 探针必须一次把**决定性的那几个量**都打出来 ★

    用户原话：「免得我要多次测试，你最好一次就拿到所有想要的数据」。
    这条路上有两个只能真机量的未知数（渲染分辨率跟什么走 / 游戏原生窗的槽位多大），
    而**每量一次要开一次游戏**，所以漏一个数就等于要多开一次机。
    判据 = 五个量一个都不能少：
        Screen 尺寸 · 画布 scaleFactor · 槽位 rect(canvas 单位) · 槽位**屏幕像素** · texture 尺寸
    """
    code = _code_only(_read(BP))
    env = _body(code, "private static void LogEnv(")
    assert env, "找不到 LogEnv"
    assert "Screen.width" in env and "Screen.height" in env, "环境段没打 Screen 尺寸"
    assert "scaleFactor" in env, "环境段没打画布 scaleFactor（反解公式必需）"

    node = _body(code, "private static void LogNode(")
    assert node, "找不到 LogNode"
    assert "rt.rect.width" in node and "rt.rect.height" in node, "没打槽位 rect（canvas 单位）"
    # 必须查**调用**而不是字样：日志文案里也写着 "lossyScale=" 五个字，
    # 只查字样的话把它换成 Vector3.one 照样绿（变异 ⑭ 实测漏过一次）
    assert "rt.lossyScale" in node, "没真取 lossyScale —— 屏幕像素=rect×lossyScale 靠它"
    assert "t.width" in node and "t.height" in node, "没打渲染出来的 texture 尺寸（决定性数据）"


def test_probe_reads_native_slot():
    """原生路必须探到游戏那个大立绘槽位（`rimgUnit`）—— 那是"原生窗到底多大"的唯一来源。"""
    code = _code_only(_read(BP))
    body = _body(code, "private static void LogNative(")
    assert body, "找不到 LogNative"
    # 同样查**读取**而非字样：下面那条 null 提示文案里就写着 "原生 rimgUnit=null"
    assert "big.rimgUnit" in body, "原生探针没真读 rimgUnit"
    assert "LogNode(" in body, "原生探针要把槽位交给 LogNode 打全套"


def test_probe_is_deferred_too():
    """★ 立刻打一次**不够** ★

    立绘是**异步**渲的（`PortraitCache` 用 0.15s 等"渲完当帧"）——
    立刻量很可能拿到"还没渲"的空纹理，那就白开一次游戏。所以必须延迟再量一次。
    """
    code = _code_only(_read(BP))
    assert "ProbeDelay" in code, "没有延迟常量"
    assert "_probeAt = Now() + ProbeDelay" in code, "Show 没有排延迟探针"
    tick = _body(code, "public static void Tick(")
    probe = tick.find("TickProbe()")
    guard = tick.find("if (!IsOpen) return;")
    assert probe >= 0, "Tick 没跑探针"
    assert guard >= 0, "Tick 的 IsOpen 早退丢了"
    assert probe < guard, (
        "探针必须在 IsOpen 早退**之前** —— 否则用户手快先关窗，延迟那次就永远量不到"
    )


def _strip_comments_and_strings(src: str) -> str:
    """剥掉 `//`、`/* */` 与字符串/字符字面量（保留换行数，便于报行号）。

    与 `_code_only` 的区别：那个**保留字符串**（多数契约要查文案），
    这个连字符串一起去掉 —— 用于"查某种写法在**代码**里出现没有"，
    免得注释/日志文案里提到它就被判红（本仓注释特别喜欢复盘坑）。
    """
    out = []
    i, n = 0, len(src)
    state = None          # None | 'line' | 'block' | 'str' | 'char' | 'verbatim'
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state is None:
            if c == "/" and nxt == "/":
                state = "line"; i += 2; continue
            if c == "/" and nxt == "*":
                state = "block"; i += 2; continue
            if c == '@' and nxt == '"':
                state = "verbatim"; i += 2; continue
            if c == '"':
                state = "str"; i += 1; continue
            if c == "'":
                state = "char"; i += 1; continue
            out.append(c); i += 1; continue
        if state == "line":
            if c == "\n":
                state = None; out.append(c)
            i += 1; continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = None; i += 2; continue
            if c == "\n":
                out.append(c)
            i += 1; continue
        if state == "str":
            if c == "\\":
                i += 2; continue
            if c == '"':
                state = None
            i += 1; continue
        if state == "char":
            if c == "\\":
                i += 2; continue
            if c == "'":
                state = None
            i += 1; continue
        if state == "verbatim":
            if c == '"' and nxt == '"':
                i += 2; continue
            if c == '"':
                state = None
            if c == "\n":
                out.append(c)
            i += 1; continue
    return "".join(out)


def _all_cs_files():
    """全仓 C# 源（跳过 obj/bin，那是生成物）。"""
    base = ROOT / "csharp"
    out = []
    for p in sorted(base.rglob("*.cs")):
        sp = str(p).replace("\\", "/")
        if "/obj/" in sp or "/bin/" in sp:
            continue
        out.append(p)
    return out


def test_no_as_recttransform_anywhere():
    """★ `transform as RectTransform` 是本工程的静默坑 ★（**全仓扫**，不只 BigPortrait）

    本环境下 `GameObject.transform` 返回的包装器静态类型就是 `Transform`，
    向下转型 `as RectTransform` **静默返回 null、不抛异常**。
    历史事故（每一行都是同一句）：
      · 09-13 HUD「传」钮未读红点建不出来；
      · 09-13 配置面板悬停气泡一个都不出；
      · 10-23 「须知」面板点开**毫无反应** —— 节点与 RectTransform 都在
        （诊断日志已打出 `[Window active=False rt=True]`），只是 `as` 转型给了 null，
        于是被判成"实例节点缺失"、建好又立刻关掉。
    唯一正确取法 = `GetComponent<RectTransform>()`，收口点 `UiRects.Of`。

    ⚠ 这条**曾经只查 BigPortrait.cs 一个文件**，所以新写的 NoticePanel 完全没被覆盖、
    原样踩了第三次。现在改成全仓扫：新文件天然进网。
    """
    # 唯一收口点自己必须有兜底转型（`GetComponent` 拿到就返回，拿不到才试 `as`）——
    # 那几行是**正确**的，豁免；其余任何地方出现都是一颗定时炸弹。
    EXEMPT = {
        "csharp/UI/ChatUiBuilder.cs",          # UiRects.Of / ParentOf 本体
        "csharp/UI/MapMainContactButton.cs",   # 已 GetComponent 成功后再兜底（有注释+判空）
        "csharp/UI/ChatWindow.cs",             # s.transform 已知是 RectTransform 的强转兜底
    }
    bad = []
    for p in _all_cs_files():
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if rel in EXEMPT:
            continue
        # `_code_only` 只剥注释、**保留字符串**（它对别的契约是必须的），
        # 而本仓有大量注释在*讲述*这个坑（"禁止 `as RectTransform`"），
        # 直接扫原文会把这些说明文字当成违规。所以这里额外剥掉字符串字面量。
        code = _strip_comments_and_strings(p.read_text(encoding="utf-8", errors="ignore"))
        for pat in ("as RectTransform", "(RectTransform)"):
            if pat in code:
                line = code[:code.index(pat)].count("\n") + 1
                bad.append(f"{rel}:{line}  {pat}")
    assert not bad, ("出现了静默 null 的转型（见 docstring，已翻车三次）。"
                     "取 RectTransform 一律用 UiRects.Of / GetComponent<RectTransform>()：\n  "
                     + "\n  ".join(bad))


def test_probe_uses_uirects_for_recttransform():
    """原生窗的 RectTransform 必须走 `UiRects.Of`（铁律的唯一收口点）。"""
    code = _code_only(_read(BP))
    body = _body(code, "private static void LogNative(")
    assert "UiRects.Of(" in body, "取 RectTransform 没走 UiRects.Of"


def test_probe_logs_unit_id():
    """目标单位的 **unitID** 也要打：按名解析会**猜错人**，而日志里两个同名者看着一模一样。"""
    code = _code_only(_read(BP))
    assert "SafeId(" in code, "没打 unitID，重名时无法判断解析到了谁"
    show = _body(code, "public static void Show(WorldUnitBase unit")
    assert "SafeId(unit)" in show, "Show 没把目标 unitID 打进日志"


def test_bind_failure_and_click_are_logged():
    """点击链路的两个断点都要有日志：Bind 失败（挂不上）与点击落地（点到了谁）。"""
    code = _code_only(_read(BP))
    bind = _body(code, "public static void Bind(")
    assert "_bindFail++" in bind, "Bind 失败没有计数"
    assert "_bindSkipEmpty++" in bind, "空名跳过没有计数（否则「点了没反应」查不出是没挂还是没点到）"
    show = _body(code, "public static void Show(string name")
    assert "点击头像" in show, "点击入口没有落日志"


# ---------------------------------------------------------------- H. 退出（刻意无可见控件）

def test_no_visible_close_button():
    """★ 刻意**不挂**可见的「关闭」按钮 ★（用户 10-18 拍板撤掉）

    经过：先嫌没有可见抓手 → 挂了个右上角「关闭」按钮（位置还按实机坐标算过、不压立绘）
    → 用户看到实机效果后改口「**去掉按钮，只留快捷键**」。
    撤掉的真正理由：那按钮是画在**游戏原生立绘窗**上的**外来控件**，与那扇窗的原生样式
    不一路；而退出本来就有两条**零控件**的路（ESC / 点空白处）。
    ⚠ 这是**反向锁**：谁再往浮层上加可见按钮，它就会红。
    """
    code = _code_only(_read(BP))
    assert "_closeBtn" not in code, "又出现了关闭按钮的字段"
    assert "AgentLoopBigPortraitCloseBtn" not in code, "又建了关闭按钮节点"
    assert "EnsureCloseButton" not in code, "又出现了挂按钮的方法"
    assert "BtnW" not in code and "BtnMargin" not in code, "又出现了按钮尺寸常量"
    # 浮层里没理由出现圆角底 —— 那是按钮的典型形状（遮罩是纯色块、槽位是 RawImage）
    assert "MakeSlicedSprite" not in code, "浮层里又出现了圆角底（按钮的形状）"


def test_exit_paths_survive_without_visible_control():
    """★ 撤掉按钮后，出口必须**仍然存在** ★

    没有可见控件兜底 ⇒ 出口任何一条断了，玩家就**退不出去**（只能重启游戏）。
    10-18 二测实机定案的出口清单（**这条测试原来写错了，此处更正**）：
      · **ESC** —— 我们自己的（`Tick` 判 `CloseKey`），日志实证可用；
      · **右键** —— **游戏原生窗自己的**退出键。我们没接它、日志里也没有任何对应记录
        ⇒ 在游戏内部处理。这条不归我们管，但**必须知道它是出口之一**；
      · ~~点空白处~~ —— **原生路没有这条**。实机量到 `btnMask=显示` 但**不吃射线**
        （点它无反应、点击穿透到对话窗）。只有**自建路**那层遮罩是我们自己挂的 Button，
        「点空白处」才算数。旧文档把这条当成原生路的出口，是错的。
    """
    code = _code_only(_read(BP))
    tick = _body(code, "public static void Tick(")
    assert tick, "找不到 Tick"
    assert "Input.GetKeyDown(CloseKey)" in tick, "ESC 那条断了 —— 撤掉按钮后就没有任何出口"
    assert "CloseKey = KeyCode.Escape" in code, "关闭键不是 ESC 了"
    # 自建路那层遮罩仍要是可点的 Button：那条路没有游戏原生窗，不点它就没出口
    assert "ClickUtils.Attach(maskBtn, Close)" in code, "自建路的「点空白处」断了（那扇窗会关不掉）"


def test_probe_logs_native_controls_state():
    """探针要顺带打**游戏自带**那两个关闭控件的状态 —— 回答「游戏侧本来有没有可见入口」。"""
    code = _code_only(_read(BP))
    body = _body(code, "private static void LogNative(")
    assert "big.btnClose" in body, "没探游戏自带 btnClose"
    assert "big.btnMask" in body, "没探游戏自带 btnMask"
    assert "activeInHierarchy" in body, "要判的是整条父链活着（activeSelf 会把「父级关着」误报成显示）"


# ---------------------------------------------------------------- I. 模态拦截层（10-18 二测）

def test_blocker_attached_inside_the_clicked_window():
    """★ 拦截层必须挂进**你点的那个窗自己的 Canvas 子树**，不是根画布 ★

    为什么不能挂根画布：Unity 拾取 = 各自 Canvas 排序 + 同 Canvas 内兄弟序。
    对话窗实例根上的 Canvas 是**游戏 UIMgr 挂的**（`AbChatPanel` 类头记着这条），
    挂到根画布会掉到那个 Canvas **之下** ⇒ 白挂（点击照样被对话窗吃掉）。
    挂进它内部 + `SetAsLastSibling()` ⇒ 同 Canvas 内后兄弟在上 ⇒ **不依赖任何层号**，
    所以游戏哪天改了 UI 层排序也不会失效。
    """
    code = _code_only(_read(BP))
    root = _body(code, "private static Transform ResolvePickRoot(")
    assert root, "找不到 ResolvePickRoot"
    assert "GetComponent<Canvas>()" in root, "没按「向上找最近带 Canvas 的节点」定宿主"
    assert "t.parent" in root, "没向上走（那是就地取，不是找最近的 Canvas 祖先）"
    setb = _body(code, "private static void SetBlocker(")
    assert setb, "找不到 SetBlocker"
    assert "ResolvePickRoot(" in setb, "SetBlocker 没用 ResolvePickRoot 定宿主"
    assert "SetAsLastSibling()" in setb, "没提到末位兄弟 ⇒ 压不住本窗内容"
    assert "ResolveCanvasRoot" not in setb, "拦截层挂根画布去了 ⇒ 掉到该窗 Canvas 之下 = 白挂"


def test_blocker_swallows_clicks_without_closing():
    """★ 拦截层**只吞点击、不关窗、不加暗色**，且**不透明度刻意不为 0** ★（用户 10-18：「点击的话吞掉就行」）

    · `raycastTarget=true` + `PickTint`（1/255）：只吃射线。压暗已由**游戏蒙版**负责
      （实机量到 `btnMask raycastTarget=False`：它只压暗、不吃射线），再加一层暗色就是双重压暗；
    · **alpha 刻意不为 0**：`alpha=0` 的 Graphic 有被 canvas 判剔除（`CanvasRenderer.cull`）的
      风险，而 `GraphicRaycaster` 会**跳过被剔除的图形** ⇒ 那层就成了"看不见、也拦不住"。
      10-18 二测的拾取 A/B **逐字相同**，这条是当时的两个候选原因之一（另一个是量的时机太早）。
      这是**反向锁**：谁把 alpha 改回 0，它就会红；
    · **不挂 Button、不接 onClick**：挂上就把「点对话窗」变成「关立绘」，与拍板不符；
    · `ignoreLayout`：宿主要是挂了 LayoutGroup，别让一个全屏子节点去挤动它的布局。
    """
    code = _code_only(_read(BP))
    setb = _body(code, "private static void SetBlocker(")
    assert setb, "找不到 SetBlocker"
    assert "raycastTarget = true" in setb, "拦截层不吃射线 ⇒ 等于没挂"
    assert "img.color = PickTint" in setb, "拦截层没用 PickTint"
    assert "new Color(0f, 0f, 0f, 0f)" not in setb, \
        "拦截层又变成 alpha=0 了 ⇒ 有被 canvas 剔除（cull）的风险，那样它拦不住任何点击"
    assert "1f / 255f" in code, "PickTint 的极低不透明度被动过（不能是 0，也不能大到看得出来）"
    assert "ignoreLayout = true" in setb, "没躲开宿主布局（全屏子节点会挤动 LayoutGroup）"
    assert "Button" not in setb, "拦截层挂了 Button ⇒ 点对话窗会变成关立绘（与拍板不符）"
    assert "onClick" not in setb and "ClickUtils" not in setb, "拦截层接了点击回调 ⇒ 同上"


def test_bind_logs_group_rows():
    """★ 群聊行的绑定必须**留痕** ★（10-18 二测加）

    为什么要单独一条：群聊"点不开"时，第一个要回答的问题是**到底挂上没有** ——
    而那条 `已挂点击 #N` 的明细在长会话里早被"每 50 个一条"取模跳过了（单聊实测已到 70+）。
    **带 `unitId` 的绑定就是群聊行的指纹**：单聊与玩家行走按名解析、`unitId` 传 null。
    """
    code = _code_only(_read(BP))
    bind = _body(code, "public static void Bind(")
    assert bind, "找不到 Bind"
    assert "_bindWithId" in bind, "群聊行的绑定没单独留痕 ⇒ 群里点不开时无从判断挂没挂上"
    assert "IsNullOrEmpty(unitId)" in bind, "留痕的判据不是「带精确 id」（那正是群聊行的指纹）"
    assert "_bindWithId < 3" in bind or "_bindWithId <= 3" in bind, "留痕没有上限，长会话会刷屏"


def test_blocker_is_always_cleared():
    """★ 拦截层**任何路径都要撤** ★ —— 漏撤 = 对话窗永久点不动，且**零报错**

    三条撤除路径缺一不可：
      · `Close()` —— 正常退出；
      · `Tick()` 自愈 —— 浮层没了但层还在（宿主窗被销毁 / 状态被外部改掉）；
      · `ResetForWorld()` —— 换存档（对话窗是常驻 mod UI，可能活过换存档）。
    """
    code = _code_only(_read(BP))
    close = _body(code, "public static void Close(")
    assert close, "找不到 Close"
    assert "ClearBlocker()" in close, "Close 没撤拦截层 ⇒ 关掉立绘后对话窗永久点不动"
    tick = _body(code, "public static void Tick(")
    assert "ClearBlocker()" in tick, "Tick 没有自愈 ⇒ 宿主窗被销毁后拦截层永远留着"
    reset = _body(code, "public static void ResetForWorld(")
    assert "_blocker" in reset, "换存档没管拦截层"
    clear = _body(code, "private static void ClearBlocker(")
    assert clear, "找不到 ClearBlocker"
    assert "_blocker == null" in clear, "ClearBlocker 不幂等（fake-null 时会对已销毁对象 Destroy）"
    assert "_blocker = null" in clear, "撤了没清引用"


def test_external_close_is_watched():
    """★ 游戏**自己**把窗关掉（右键）时，我方状态必须跟着收 ★

    不收的后果：`_nativeOpen` 卡在 true ⇒ `IsOpen` 恒真 ⇒ **拦截层撤不掉**
    ⇒ 对话窗被永久吞掉点击，而且**一句报错都没有**（10-18 二测复盘时发现的隐患）。
    判活要用 `activeInHierarchy`（游戏的关窗可能是销毁、也可能是置灰，两种都要覆盖）；
    且**不能再调 `g.ui.CloseUI`** —— 窗已经没了，再走一次关闭流程是拿已关的 UI 操作。
    """
    code = _code_only(_read(BP))
    body = _body(code, "private static void WatchExternalClose(")
    assert body, "找不到 WatchExternalClose"
    assert "activeInHierarchy" in body, "判活没覆盖「被置灰」那种关法"
    # 判据要**顺序感知**：`_native != null && _native.gameObject...` 这种短路才算先判空，
    # 只查"有没有出现过 null 二字"会被 `X != null` / `X == null` 两种写法糊弄过去。
    i_null = body.find("_native != null")
    i_use = body.find("_native.gameObject")
    assert i_null >= 0 and 0 <= i_null < i_use, \
        "没先判 fake-null 就取 .gameObject ⇒ 窗已被销毁时会在探针里抛"
    assert "CloseUI" not in body, "外部已关掉的窗不该再走一次 CloseUI"
    tick = _body(code, "public static void Tick(")
    assert "WatchExternalClose()" in tick, "Tick 没接外部关窗监视 ⇒ 右键关掉后状态卡死"


def test_probe_logs_mask_raycast_target():
    """蒙版吃不吃射线**必须量**，不能靠「显示着」就以为它能拦。

    10-18 事故现场就是这一条：`btnMask=显示` 却在点击上**完全不存在**
    （用户实测「确实有一层蒙版，但是我依旧可以点击对话 ui 的一些东西」）。
    `raycastTarget` 才是决定吃不吃射线的那个字段。
    """
    code = _code_only(_read(BP))
    body = _body(code, "private static void LogNative(")
    assert body, "找不到 LogNative"
    # ★ 必须查**真取字段的调用**，不能查字样 ★ —— 日志文案里本来就写着
    # `" raycastTarget=" + rct`、`" interactable="`，只查那几个字的话，
    # 把取值换成常量 `"?"` 照样绿（变异 ㊱ 实测漏过一次，与 ⑭ 的 lossyScale 同款坑）。
    assert "gi.raycastTarget" in body, "没真取蒙版的 raycastTarget ⇒ 又只能靠「显示着」推"
    assert "bm.interactable" in body, "没真取蒙版的 interactable"
    # 判「谁盖着谁」要靠排序与兄弟序，不是靠节点名字
    assert "cv.sortingOrder" in body, "没真取原生窗画布排序"
    assert "GetSiblingIndex()" in body, "没真取兄弟序（同 Canvas 内谁在上靠它）"


def test_probe_picks_before_and_after():
    """★ 拾取探针必须 **A/B 各一次**，且**复测必须在延迟那次** ★

    推画布层号不可靠（层号由游戏 UIMgr 排，会随版本变）；直接问引擎
    「这一点的命中链是谁」才是硬证据。修前一次证明**问题存在**。

    ⚠ 但"修后"不能量在**同一帧**：新建的 Graphic 此刻 `absoluteDepth` 多半还是 -1，
    而 `GraphicRaycaster` 跳过 depth=-1 的图形 ⇒ 会量到"没生效"的**假象**。
    10-18 二测就是这么被误导的（`·修前` 与 `·修后` 逐字相同）。
    所以真正作数的那次在 `TickProbe`（+400ms，canvas 已重建）。
    """
    code = _code_only(_read(BP))
    setb = _body(code, "private static void SetBlocker(")
    assert 'PickLog("·修前"' in setb, "挂之前没量 ⇒ 无法证明问题存在"
    assert 'PickLog("·修后' in setb, "同帧那次对照也没了"
    pick = _body(code, "private static void PickLog(")
    assert pick, "找不到 PickLog"
    assert "WorldToScreenPoint" in pick, "没做世界→屏幕换算（ScreenSpaceCamera 下坐标不是屏幕像素）"
    # 延迟那次必须在 TickProbe 里（关窗后也要量得到 ⇒ 早退之前）
    tickp = _body(code, "private static void TickProbe(")
    assert tickp, "找不到 TickProbe"
    assert "LogBlockerProbe(" in tickp, \
        "延迟探针没复测拦截层 ⇒ 只剩同一帧那次假象（二测就是这么被误导的）"


def test_blocker_probe_reads_depth_and_cull():
    """★ 延迟复测要打 `depth` 与 `cull` —— 这两个数才**直接**回答"它到底能不能吃射线" ★

    只打"命中链还是对话窗"只能说明"没生效"，说不出**为什么**。这两个数把两个候选原因分开：
      · `depth >= 0`  ⇒ 已被 canvas 登记（排除"同帧量太早"的假象）；
      · `cull = False` ⇒ 没被剔除（排除 `alpha=0` 那条路径）。
    两个都正常却仍不在命中链里，才是真正没覆盖到（例如被别的排序压住）。
    """
    code = _code_only(_read(BP))
    body = _body(code, "private static void LogBlockerProbe(")
    assert body, "找不到 LogBlockerProbe"
    assert "img.depth" in body, "没量拦截层的 depth（分不清「量太早」与「真没生效」）"
    assert "cr.cull" in body, "没量 CanvasRenderer.cull（分不清「alpha=0 被剔除」与「真没生效」）"
    assert "PickLog(" in body, "延迟探针没在同一时刻再拾取一次"
    # 拦截层已撤/没挂时要如实说，不能静默跳过（否则日志里会"少一行"而不是"说一句"）
    assert "已不在" in body, "拦截层不在时没留话 ⇒ 日志缺行会被误读成探针没跑"


def test_probe_uses_il2cpp_list_for_raycast():
    """★ `RaycastAll` 的列表必须是 **Il2Cpp 的** List ★（编译期硬坑，10-18 踩过一次）

    本环境里 `EventSystem.RaycastAll` 的签名要
    `Il2CppSystem.Collections.Generic.List<RaycastResult>`；喂
    `System.Collections.Generic.List` **编译期就过不去**（CS1503）。
    把它「顺手换回标准库」就会构建失败 —— 这条把这个坑钉在源码层面。
    """
    code = _code_only(_read(BP))
    body = _body(code, "private static string TopHits(")
    assert body, "找不到 TopHits"
    assert "Il2CppSystem.Collections.Generic.List<RaycastResult>" in body, \
        "拾取列表不是 Il2Cpp 的 List ⇒ 编译期就过不去（CS1503，10-18 踩过）"
    assert "RaycastAll" in body, "没真做拾取"
