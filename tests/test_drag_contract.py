"""对话窗拖动契约（10-22 UI 批次；11-21 修「拖不动」+ 位置跨局记住）。

算术（缩放换算/两种钳制域）由 C# 单测工程 `csharp/tests/DragCoreTests/` 钉；
版式判定核（校验/夹视口/去抖）由 `csharp/tests/WindowLayoutCoreTests/` 钉；
本文件钉**接线的纪律**（DragCore.cs 文件头有事故背景）：

1. **不新建 MonoBehaviour**：IL2CPP 漏 RegisterTypeInIl2Cpp = 启动期 TypeInitializationException。
   拖动必须挂在已注册的 ChatWindow 的 Update 里；纯件 DragCore 不许碰 Unity。
2. **控件避让交给 EventSystem 射线**，不许手写控件矩形名单（名单必漏，
   漏一个就是"点 ✕ 却把窗拖走"）。
3. **起点判据按几何**（窗口顶 48px 带）：AB 预制件 Title 节点已废、按钮挂 BG 直下，
   按节点找会两版分叉。
4. **屏幕坐标换算必须带画布相机**（11-21 修的实机 bug）：AB 面板根 Canvas 是
   ScreenSpaceCamera，`RectTransformUtility` 传 null 只对 Overlay 成立，传错的后果
   不是报错而是**恒不命中** ⇒ 一次都拖不起来。同一个坑 HoverTip / DramaAiOption 各踩过一次。
5. **钳制按锚点分派**（11-21 修的第二个 bug）：`ClampAnchored` 是给右中锚的代码版窗口写的，
   AB 版的 BG 是居中点锚；用错公式 ⇒ 只能往一个方向拖。
6. **位置跨局记住**（11-21 用户定案「这就和配置里面的设置一样的啊」）：
   落盘在 `<数据根>\\window_layout.json`，**不写 config.json**（那个归 Python 独占解释），
   也**不用 PlayerPrefs**（IL2CPP 下注册表路径不可控，且本仓一贯用数据根下的 json）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _method_body(src: str, sig: str) -> str:
    body = src[src.index(sig):]
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


def test_drag_lives_on_registered_window_no_new_monobehaviour():
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "private void Update()")
    assert "TickDrag();" in body, "拖动没挂在已注册 MonoBehaviour 的 Update 上"
    drag = _method_body(win, "private void TickDrag()")
    assert "AddComponent" not in drag, "拖动不许运行时挂新组件（IL2CPP 注册雷区）"
    core = _read("UI/DragCore.cs")
    assert "MonoBehaviour" not in core and "using UnityEngine" not in core, \
        "DragCore 必须保持纯算术（net8.0 可单测的前提）"


def test_start_gate_is_geometry_plus_eventsystem():
    win = _read("UI/ChatWindow.cs")
    assert "_dragging = InTitleBand(mp) && !PressOnSelectable(mp);" in win, \
        "起点判据 = 标题带几何 + EventSystem 控件避让（二者缺一不可）"
    assert "Il2CppSystem.Collections.Generic.List<UnityEngine.EventSystems.RaycastResult>" in win, \
        ("RaycastAll 出参必须是 **Il2CppSystem** 的 List（Unhollower 签名如此，托管 List 直接 "
         "CS1503——编译期就能抓到，钉在这里防「顺手改回托管」）")
    band = _method_body(win, "private bool InTitleBand(Vector2 mousePos)")
    assert "RectangleContainsScreenPoint" in band and "yMax - 48f" in band, \
        "标题带按窗口顶 48px 几何算（不许改回按 Title 节点找——AB 版没有那个节点）"
    press = _method_body(win, "private bool PressOnSelectable(Vector2 mousePos)")
    assert "RaycastAll" in press and "GetComponentInParent<UnityEngine.UI.Selectable>" in press, \
        "控件避让必须问 EventSystem（手写矩形名单必漏）"
    # 名单化退化的形状特征：PressOnSelectable 里不许出现具体控件引用
    for banned in ("CloseButton", "ConfigButton", "NpcLabel", "DeleteBtn"):
        assert banned not in press, f"控件避让里出现了 {banned}（回到手写名单了）"


def test_screen_rect_utility_always_gets_canvas_camera():
    """★ 11-21 修的核心 ★ `RectTransformUtility` 传 null 只对 Overlay 成立。

    AB 面板根 Canvas 是 ScreenSpaceCamera（Player.log 实证），传 null 恒不命中
    ⇒ `InTitleBand` 永远 false ⇒ 一次都拖不起来。
    """
    win = _read("UI/ChatWindow.cs")
    band = _method_body(win, "private bool InTitleBand(Vector2 mousePos)")
    assert "UiKit.CanvasCamera(" in band, \
        "标题带判据必须解析画布相机（传 null 在 ScreenSpaceCamera 画布下恒不命中）"
    assert "RectangleContainsScreenPoint(w, mousePos, cam)" in band, \
        "RectangleContainsScreenPoint 必须收到 cam，不许再传 null"
    assert "ScreenPointToLocalPointInRectangle(w, mousePos, cam," in band, \
        "ScreenPointToLocalPointInRectangle 同样必须收到 cam"
    assert "mousePos, null" not in band, "标题带里不许再出现 null 相机"

    # 相机的解析必须看**根 Canvas**（嵌套 Canvas 的 renderMode 不生效）
    uikit = _read("UI/UiKit.cs")
    cam_body = _method_body(uikit, "public static Camera CanvasCamera(Transform t)")
    assert "rootCanvas" in cam_body, "必须取根 Canvas（嵌套 Canvas 的 renderMode 只是残留）"
    assert "ScreenSpaceOverlay" in cam_body and "return null;" in cam_body, \
        "真 Overlay 才返回 null（其余必须给画布相机）"
    assert "Camera.main" in cam_body, "WorldSpace 画布未设 event camera 时要有兜底"


def test_press_selectable_does_not_fake_a_camera():
    """`PointerEventData.pressEventCamera` 是**只读属性**（本工程引用的 uGUI 版本），
    给它赋值直接 CS0200 编译不过。控件避让因此沿用"让 GraphicRaycaster 自己解析相机"
    —— 它内部就是 `pressEventCamera ?? canvas.worldCamera`，与点击判定同源。

    钉这条是为了防止有人"照着 InTitleBand 的样子"给它也补相机（编译就炸）。
    """
    win = _read("UI/ChatWindow.cs")
    press = _method_body(win, "private bool PressOnSelectable(Vector2 mousePos)")
    assert "pressEventCamera" not in press.split("\n")[0], "pressEventCamera 只许出现在注释里"
    # 代码行里不许赋值（注释里可以提）
    for line in press.splitlines():
        code = line.split("//")[0]
        assert "pressEventCamera =" not in code, \
            "pressEventCamera 是只读属性，赋值 CS0200（注释里说明可以，别真写）"


def test_motion_goes_through_drag_core():
    win = _read("UI/ChatWindow.cs")
    drag = _method_body(win, "private void TickDrag()")
    assert "DragCore.ScreenToCanvas(dx, sf)" in drag and "DragCore.ScreenToCanvas(dy, sf)" in drag, \
        "位移必须过缩放换算（不除 scaleFactor = 非 1080p 窗口游得比手快）"
    assert "ClampWindowToViewport(" in drag, "必须钳制（不钳一拽飞出屏）"
    assert "canvas.scaleFactor" in drag


def test_clamp_dispatches_on_anchor_type():
    """★ 11-21 修的第二个 bug ★ 右中锚公式套到居中点锚上 ⇒ 只能往一个方向拖。

    AB 版 BG 是 `anchor=pivot=(0.5,0.5)`；`ClampAnchored` 假设 `anchor=pivot=(1,0.5)`。
    1920 视口下前者算出 x∈[-720,0]、正确是 ±360 —— 往右拖被钳回 0。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "private void ClampWindowToViewport(RectTransform rt, float sf, ref float x, ref float y,")
    assert "pointAnchor" in body, "必须判锚点类型（点锚 vs 拉伸/右中锚）"
    assert "DragCore.ClampToViewport(" in body, "点锚必须走通用公式（AB 预制体版 BG）"
    assert "DragCore.ClampAnchored(" in body, "右中锚保留历史公式（代码版构建器）"
    # 分派必须是 if/else，不许两条都跑（后者会覆盖前者，症状更难查）
    assert "else" in body, "两种钳制必须互斥（if/else），不许串着跑"

    core = _read("UI/DragCore.cs")
    assert "public static void ClampToViewport(" in core, "DragCore 必须提供通用钳制"
    cbody = _method_body(core, "public static void ClampToViewport(float x, float y, float winW, float winH,")
    assert "pivotX" in cbody and "anchorX" in cbody, "通用公式必须吃锚点与 pivot（否则又只能算一种锚）"


def test_drag_position_persists_across_sessions():
    """★ 11-21 用户定案 ★ 位置跨局记住。

    但**不许**写 config.json（那个归 Python 的 config_loader 独占解释，
    C# 侧 ModConfigFile 文件头写着"只读，绝不写回"；两边同写必然互相覆盖）。
    """
    win = _read("UI/ChatWindow.cs")
    drag = _method_body(win, "private void TickDrag()")
    # 松手补最后一笔位置（不是落盘 —— 落盘改到关窗，见
    # `test_layout_saved_on_close_not_on_release`）
    assert "if (_dragMoved) FlushDragPosition();" in drag, \
        "松手必须补最后一笔位置（降频会漏掉尾巴，否则窗会弹回去一点点）"
    assert "_dragMoved = false;" in drag, "按下时要清「动过」标记（否则上一次拖动会污染这次）"

    save = _method_body(win, "private void SaveWindowPos()")
    assert "WindowLayoutStore.Save(" in save, "落盘必须走 WindowLayoutStore（单一写入口）"

    # 恢复：开窗两帧后写回，且每次开窗都重来
    upd = _method_body(win, "private void Update()")
    assert "TickRestoreLayout();" in upd, "版式恢复必须挂在 Update 上"
    restore = _method_body(win, "private void TickRestoreLayout()")
    assert "_layoutRestoreWait < 2" in restore, \
        "必须等两帧（Open 当帧父级 rect 还是 0，这时候钳制域会算出 0×0）"
    assert "ClampWindowToViewport(" in restore, \
        "恢复也要过钳制（换了分辨率后旧位置可能在屏外 ⇒ 窗口看不见）"
    assert "WindowLayoutStore.Current" in restore, "恢复必须读 WindowLayoutStore"

    op = _method_body(win, "public void Open()")
    assert "_layoutRestored = false;" in op and "_layoutRestoreWait = 0;" in op, \
        "每次开窗都要重走恢复（游戏 CloseUI 会销毁宿主，新实例位置是预制体的 (0,0)）"

    # 存储层：写独立文件，不碰 config.json / PlayerPrefs
    store = _read("UI/WindowLayoutStore.cs")
    assert "WindowLayoutCore.FileName" in store, "文件名必须来自判定核（单一真相）"
    assert "config.json" not in store.split('"""')[0] or True  # 注释里提到 config.json 是允许的
    for line in store.splitlines():
        code = line.split("//")[0]
        assert "PlayerPrefs" not in code, "不许用 PlayerPrefs（IL2CPP 下注册表路径不可控）"
        assert '"config.json"' not in code, "不许写 config.json（归 Python 独占解释）"
    assert "File.Replace" in store or "File.Move" in store, "落盘要走 .tmp + 替换（写坏不留半截文件）"


def test_layout_store_never_breaks_the_window():
    """落盘失败绝不影响开窗（记不住位置是小事，打不开窗是大事）。"""
    store = _read("UI/WindowLayoutStore.cs")
    # 每个 IO 入口都必须有 try/catch，且 catch 里不许 rethrow
    assert store.count("catch") >= 4, "IO 入口都要兜住异常"
    assert "throw" not in store.replace("throw new", "@@"), \
        "存储层不许向上抛（窗口位置记不住不能拖垮开窗）"
    for fn in ("internal static bool Save(", "private static bool Write(", "private static void EnsureLoaded()"):
        body = _method_body(store, fn)
        assert "catch" in body, f"{fn} 必须兜异常"


def test_clamp_refuses_to_use_a_zero_viewport():
    """★ 复核加固 ★ 视口两个来源都读不到时**不钳制**，而不是照钳。

    照钳的后果：0×0 视口会让 `ClampToViewport` 的合法域反转，取中点正好是 0
    ⇒ 窗被钉到 (0,0)，玩家视角="拖一下窗就跳回正中"，且没有任何报错。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "private void ClampWindowToViewport(RectTransform rt, float sf, ref float x, ref float y,")
    assert "readOk" in body, "必须记录「视口读到了没有」"
    assert "if (!readOk) return;" in body, \
        "读不到视口时必须原样返回（照钳会把窗钉到 0,0）"
    # 不许留下"无条件用 Screen 兜底"的旧形状：兜底之后还要再判一次
    assert body.index("if (!readOk) return;") < body.index("ClampToViewport("), \
        "早退必须在钳制之前"


def test_restore_retries_boundedly_when_viewport_not_ready():
    """★ 复核加固 ★ 恢复时视口没就绪要**有界重试**，不许直接把 `_layoutRestored` 置位。

    置位的后果：拿"按旧视口算的原始位置"直接写下去（换分辨率就在屏外），
    且从此不再重试 ⇒ 每次开窗都在屏外。
    """
    win = _read("UI/ChatWindow.cs")
    assert "LayoutRestoreMaxWait" in win, "必须有重试上限常量"
    body = _method_body(win, "private void TickRestoreLayout()")
    assert "viewReady" in body, "必须先判视口就绪"
    assert "if (!viewReady)" in body, "视口没就绪要有单独分支"

    # 关键：`!viewReady` 分支里 `_layoutRestored = true` **只能**出现在超时放弃那一处。
    # 若一进分支就置位，则"某帧读不到视口"= 永远不再重试 ⇒ 每次开窗都在屏外。
    guard = _method_body(body, "if (!viewReady)")
    assert "_layoutRestored = true;" in guard, "超时放弃那一支要置位（否则无限重试）"
    assert guard.index("_layoutRestoreWait >= LayoutRestoreMaxWait") < guard.index("_layoutRestored = true;"), \
        "置位必须在超时判据**之后**（先判超时、再放弃）"
    # 未就绪分支最后必须是 return（否则会掉进下面的"正常应用"路径）
    assert guard.rstrip().endswith("}"), "未就绪分支要提前 return，不许掉进应用路径"
    assert "return;" in guard, "未就绪分支必须 return（下一帧再来）"

    # 异常路径也要落定（否则每帧重试刷日志）
    tail = body[body.index("catch (Exception e)"):]
    assert "_layoutRestored = true;" in tail, "异常路径也要落定，避免每帧重试刷日志"


def test_close_clears_drag_state():
    """★ 复核加固 ★ 关窗要清拖动状态。

    不清的后果：拖到一半关窗，`_dragging` 还是 true；下次开窗鼠标可能正按着，
    `GetMouseButtonUp` 那帧已错过 ⇒ 窗"跟着鼠标跑"。

    ⚠ 11-21 晚些时候**反转**了一条：原先这里钉"关窗不许落盘"，
      现在改成"关窗**才**落盘"（用户定案 + 实测一次拖动写了 4 次盘）。
      详见 `test_layout_saved_on_close_not_on_release`。
    """
    win = _read("UI/ChatWindow.cs")
    close = _method_body(win, "public void Close()")
    assert "_dragging = false;" in close, "关窗必须清 _dragging"
    assert "_dragMoved = false;" in close, "关窗必须清 _dragMoved（残留会让下次误判为动过）"


def test_drag_gated_on_open_window():
    win = _read("UI/ChatWindow.cs")
    drag = _method_body(win, "private void TickDrag()")
    assert "if (!IsOpen || _r == null || _r.Window == null) return;" in drag, \
        "关窗状态不许响应拖动（鼠标事件照样在飞）"
    restore = _method_body(win, "private void TickRestoreLayout()")
    assert "if (!IsOpen || _r == null || _r.Window == null) return;" in restore, \
        "关窗状态不许恢复版式"


def test_clamp_diag_separates_drag_from_restore():
    """★ 复核加固 ★ 「拖动」与「恢复」的钳制域日志必须**各打一行**。

    共用一个 static 标志的话，恢复发生在开窗第 2 帧、早于任何拖动，
    那行日志永远是"恢复"那次的值 —— 而排障要看的正是拖动那次
    （用户报"某个方向拖不动"时，只有拖动那行才有意义）。
    """
    win = _read("UI/ChatWindow.cs")
    assert "_restoreDiagLogged" in win, "恢复要有独立的日志标志"
    body = _method_body(win, "private void ClampWindowToViewport(RectTransform rt, float sf, ref float x, ref float y,")
    assert 'string why = "拖动"' in body, "调用来源要作为参数传进来（默认拖动）"
    # ★ 关键：判据里必须**两个标志都出现**（只出现一个 = 共用一个 = 复现原 bug）★
    #   注意不能只查"符号存在"——变异「共用标志」时 `_restoreDiagLogged` 仍会出现在
    #   赋值行里，只有盯住"判据那一行"才抓得到。
    judge = [ln for ln in body.splitlines() if "bool logged" in ln]
    assert len(judge) == 1, "必须有一行且只有一行在算 logged"
    assert "_restoreDiagLogged" in judge[0] and "_dragDiagLogged" in judge[0], \
        ("判据那一行必须同时出现两个标志（写死成 _dragDiagLogged = 共用 = 恢复那行会挤掉拖动的）"
         f"：实际 {judge[0].strip()}")
    assert "why ==" in judge[0], "判据必须按来源分派"
    # 日志行要带上来源，否则两条一模一样的行分不清谁是谁
    assert '窗口钳制域(" + why + ")' in body, "日志行必须带来源标签"

    # 恢复路径要显式传「恢复」
    restore = _method_body(win, "private void TickRestoreLayout()")
    assert '"恢复"' in restore, "恢复路径必须显式传来源（否则和拖动挤占同一个标志）"


# ---------------------------------------------------------------- 性能（11-21）

def test_window_has_own_canvas_for_cheap_drag():
    """★ 拖动卡顿的根因修复 ★ 给 BG 挂独立子 Canvas。

    Player.log 实证：拖动 10 秒内帧率 184 → **28** fps，而 `TickProfiler` 显示
    我们所有常驻回调合计 < 1ms ⇒ 卡在 UGUI 渲染层。

    根因：**UGUI 的重算单位是 Canvas 不是元素** —— 改任何一个元素的 transform，
    整个 Canvas 的顶点全部重算。`BG` 底下有 N 行消息（头像+气泡+文字）+ 输入区 +
    8 颗按钮 + 侧边栏，拖动每帧写一次 `anchoredPosition` = 每帧重算上千顶点。

    子 Canvas 的内部顶点坐标**相对于它自己**，移动只更新变换矩阵 ⇒ 内部顶点零重算。
    """
    win = _read("UI/ChatWindow.cs")
    init = _method_body(win, "public void Init(ChatWindowRefs refs)")
    # ★ 只看代码行：注释里也会写 `EnsureOwnCanvas();`（说明性引用），
    #   用裸 `in` 会被注释骗过（变异「注释掉调用」时漏报）。
    init_code = [ln.split("//")[0] for ln in init.splitlines()]
    assert any("EnsureOwnCanvas();" in ln for ln in init_code), \
        "Init 里必须真的调用 EnsureOwnCanvas（否则拖动仍然卡）"

    body = _method_body(win, "private void EnsureOwnCanvas()")
    assert "AddComponent<Canvas>()" in body, "必须给 BG 挂 Canvas"
    # ★ 不许碰排序（11-21 第二版修正）★
    #   第一版要求 "overrideSorting = true"，**那条断言是错的**（和 raycaster 那条同一种错）。
    #   运行时事实：游戏 UI 层是 ScreenSpaceCamera order=0，我们实例根是 WorldSpace order=20
    #   （Player.log 实证），两者是两套渲染模式。子 Canvas 一旦 overrideSorting=true
    #   就变成分离排序条目、脱离父级 WorldSpace 上下文 ⇒ 内容可能被压到 BG 背景之下。
    #   而我们本来要修的是**重算粒度**，排序根本没坏 —— 别动它。
    code_all = [ln.split("//")[0] for ln in body.splitlines()]
    for ln in code_all:
        assert "overrideSorting" not in ln, \
            f"不许设置 overrideSorting（继承父级上下文才是安全的）：{ln.strip()}"
        assert "sortingOrder" not in ln, \
            f"不许设置 sortingOrder（继承父级上下文才是安全的）：{ln.strip()}"

    # ★ 禁令（只剩 CanvasScaler）★
    #   11-21 实机事故：「不许加 GraphicRaycaster」这条断言**是错的**，正是它让按钮全失灵。
    #   真相：射线按 Canvas 分表 —— `Graphic.OnEnable` 把图形注册到**最近的** Canvas 祖先，
    #   而 `GraphicRaycaster` 只查自己所属 Canvas 的那张表。挂了子 Canvas 就等于新开一张表，
    #   父级 raycaster 查不到 ⇒ **零拾取**（不是双倍）。必须给子 Canvas 自带一个。
    for line in body.splitlines():
        code = line.split("//")[0]
        assert "CanvasScaler" not in code, \
            "不许加 CanvasScaler（子 Canvas 再缩一次 ⇒ 窗口缩成四分之一）"

    # 幂等：重复 Init（宿主重建）不许挂第二个
    assert "GetComponent<Canvas>()" in body, "必须先判已有 Canvas（宿主重建会重跑 Init）"
    assert "return;" in body, "已有 Canvas 时必须早退"

    # 失败不许拖垮开窗
    assert "catch" in body, "挂不上要兜住（拖动退回卡但可用，窗必须能开）"


def test_drag_throttles_position_writes():
    """拖动写位置要**降频**：位移攒够 `DragStepEpsilon` 才写。

    每写一次都要走一遍 UGUI 的布局/渲染同步（见上一条）。慢速微调时
    "不到 2 单位"的写入全是可以省的。
    """
    win = _read("UI/ChatWindow.cs")
    assert "DragStepEpsilon" in win, "必须有降频阈值常量"
    drag = _method_body(win, "private void TickDrag()")
    assert "DragStepEpsilon" in drag, "TickDrag 必须用它判阈值"
    # ★ 关键：基准必须是"上次真正写入的值"，不是 anchoredPosition ★
    #   后者已被我们自己改过，拿它当基准等于没有阈值（永远差 0）。
    # ★ 盯判据那一行，不是"符号出现过没有" ★
    #   踩过：`_dragAppliedPos` 在赋值行也出现，裸 `in` 断言在
    #   "判据改用 anchoredPosition" 的变异下照样通过。
    gate = [ln for ln in drag.splitlines() if "DragStepEpsilon" in ln and "Mathf.Abs" in ln]
    assert gate, "找不到降频判据行"
    assert all("_dragAppliedPos" in ln for ln in gate), \
        ("降频判据必须拿 _dragAppliedPos 当基准（拿 anchoredPosition = 自己改过的值 = 阈值恒成立"
         f"）：实际 {[ln.strip() for ln in gate]}")
    assert "rt.anchoredPosition = new Vector2(nx, ny);" in drag
    assert "_dragAppliedPos = rt.anchoredPosition;" in drag, "写完要更新基准"

    # 松手要补最后一笔（否则窗停在降频前那一格，"松手弹回去一点点"）
    assert "FlushDragPosition();" in drag, "松手必须补最后一笔"
    flush = _method_body(win, "private void FlushDragPosition()")
    assert "ClampWindowToViewport(" in flush, "补笔也要过钳制"


def test_layout_saved_on_close_not_on_release():
    """写盘从「松手就写」改成「关窗才写」。

    Player.log 实证一次拖动写了 **4 次**盘：
        -74,5 → -214,14 → 245,113 → 5,-28
    说明"松手"在一次连续拖动里被识别了 4 次，每次都是同步文件 IO 阻塞主线程。
    """
    win = _read("UI/ChatWindow.cs")
    drag = _method_body(win, "private void TickDrag()")
    assert "SaveWindowPos()" not in drag, \
        "松手不许再落盘（同步 IO 阻塞主线程；实测一次拖动写了 4 次）"

    close = _method_body(win, "public void Close()")
    assert "SaveWindowPos();" in close, "关窗必须落盘（否则位置记不住）"
    # ★ 顺序断言必须只看**代码行**，不能看注释 ★
    #   踩过：注释里也写着 `_r.Root.SetActive(false)`，`str.index` 命中的是注释，
    #   于是"落盘在失活之前"这条明明是对的却报失败（误报），
    #   反过来也可能因为注释在前而**漏掉**真实顺序错误。
    code_lines = [ln.split("//")[0].strip() for ln in close.splitlines()]

    def _line_idx(frag):
        for i, ln in enumerate(code_lines):
            if frag in ln:
                return i
        raise AssertionError(f"Close 的代码行里找不到 {frag}")

    assert _line_idx("_dragging = false;") < _line_idx("SaveWindowPos();"), \
        "先清拖动状态再落盘"
    assert _line_idx("SaveWindowPos();") < _line_idx("_r.Root.SetActive(false)"), \
        "落盘必须在根节点失活**之前**（失活后 rect 可能不再更新）"


def test_child_canvas_always_gets_a_raycaster():
    """★ 11-21 实机事故的回归 ★ 子 Canvas **必须**自带一个 GraphicRaycaster。

    事故：第一版按"父级已覆盖、再加会双倍拾取"的错误理解**不加** raycaster，
    结果**整个对话窗的按钮、输入框全部点不动**（用户报"点击对话框没反应"）。

    真实机制（Unity）：
        `Graphic.OnEnable` → `GraphicRegistry.RegisterGraphicForCanvas(graphic.canvas, this)`
        其中 `graphic.canvas` = **最近的 Canvas 祖先**
        `GraphicRaycaster.Raycast` → `GraphicRegistry.GetGraphicsForCanvas(自己所属的 canvas)`

    ⇒ 挂子 Canvas = 新开一张注册表；父级 raycaster 挂在 `UIChatAi` 上，
      查的是 `UIChatAi` 那张表 ⇒ 子 Canvas 里的按钮**一个都看不到** = 零拾取。

    为什么拖动当时还能用：`InTitleBand` 走 `RectTransformUtility` 直接算矩形、**不经过射线**。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "private void EnsureOwnCanvas()")
    code = [ln.split("//")[0] for ln in body.splitlines()]

    # ★ 只看**代码行**：注释里也写着 `EnsureOwnRaycaster(`（说明性引用），
    #   裸 `in` 会被注释骗过 —— 本轮变异验证第一版就是在这里漏掉的（第三次犯同一个错）。
    # ★ 两处调用**分别**断言，不能只 `any()` ★
    #   踩过：body 里有两处 `EnsureOwnRaycaster(`（早退分支 + 主路径），
    #   `any()` 会在"主路径被注释掉"时因为早退分支那处仍然通过 ⇒ 漏报。
    main_path = body[body.index("AddComponent<Canvas>()"):]
    main_code = [ln.split("//")[0] for ln in main_path.splitlines()]
    assert any("EnsureOwnRaycaster(" in ln for ln in main_code), \
        "挂完 Canvas 的主路径必须补 raycaster（不补 = 整个窗点不动）"

    # ★ 幂等早退那条分支也必须补 ★
    #   否则"第二次 Init 时已有 Canvas → 直接 return"就会漏掉 raycaster
    head_code = [ln.split("//")[0] for ln in body[: body.index("AddComponent<Canvas>()")].splitlines()]
    assert any("EnsureOwnRaycaster(" in ln for ln in head_code), \
        "已有 Canvas 的早退分支也要补 raycaster（否则重开窗后按钮又失灵）"

    helper = _method_body(win, "private static void EnsureOwnRaycaster(GameObject go)")
    assert "AddComponent<UnityEngine.UI.GraphicRaycaster>()" in helper, "必须真的挂上去"
    assert "GetComponent<UnityEngine.UI.GraphicRaycaster>() != null" in helper, \
        "必须先判已有（多挂 = 同一点拾取两次 = 按钮回调触发两遍）"
    # ★ catch 里必须有日志 ★ 只看"有没有 catch"抓不到"catch 里啥也没干"，
    #   而静默失败恰恰是这个 bug 最难查的形态（按钮不动、日志无痕）。
    tail = helper[helper.index("catch (Exception e)"):]
    assert "ModMain.P(" in tail, \
        "raycaster 挂载失败必须留日志（静默失败 = 按钮不动且无任何痕迹）"


def test_own_canvas_logs_runtime_sorting_facts():
    """子 Canvas 的日志必须把**运行时排序事实**打出来。

    为什么单独钉这一条：子 Canvas 的排序行为取决于"根的 renderMode/order"这套
    **运行时事实**（本仓实测：游戏 UI 层 ScreenSpaceCamera order=0，
    我们实例根 WorldSpace order=20 —— 两套渲染模式）。这类事实**没法从源码推断**，
    只能让它在日志里自己说出来，否则下次改这块又得靠猜（本轮已经靠猜错了两次）。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "private void EnsureOwnCanvas()")
    assert "DescribeCanvas(" in body, "挂载日志必须带上运行时画布参数"
    helper = _method_body(win, "private static string DescribeCanvas(Canvas self)")
    assert "rootCanvas" in helper, "必须报根 Canvas（子 Canvas 的排序行为取决于根）"
    assert "renderMode" in helper and "sortingOrder" in helper and "overrideSorting" in helper, \
        "必须同时报 renderMode / sortingOrder / overrideSorting（三项共同决定排序行为）"
    assert "catch" in helper, "诊断函数自己不许抛（排障日志不该变成新的故障点）"


# ---------------------------------------------------------------- 历史分页接线（11-21）

def test_history_first_screen_is_server_paged():
    """首屏必须是**服务端分页**（limit 改掉），不是本地切片。

    用户原话：「最终的目标还是新协议，新请求，因为每次都拿全部的消息到客户端
    这也要等待很久」。所以 `p["limit"] = 0` 必须消失。
    """
    pre = _read("UI/ChatPresenter.cs")
    code = [ln.split("//")[0] for ln in pre.splitlines()]

    # 单聊 open_chat：不许再传 0
    bad = [ln for ln in code if 'p["limit"] = 0' in ln]
    assert not bad, f"不许再拉全量（limit=0 会让开窗等很久）：{bad}"

    # 必须用常量而不是魔法数字
    assert "HistoryPageTurns" in pre, "单聊首屏页数必须是具名常量"
    assert "RoomHistoryPageRows" in pre, "群聊首屏条数必须是具名常量"
    used = [ln for ln in code if 'p["limit"]' in ln]
    assert used, "找不到 limit 赋值"
    for ln in used:
        assert "HistoryPageTurns" in ln or "RoomHistoryPageRows" in ln, \
            f"limit 必须来自具名常量：{ln.strip()}"


def test_window_no_longer_slices_locally():
    """`ChatWindow` 不许再持有全量历史做本地切片。

    第一版是纯客户端切片（`_fullHistory` + `StartIndex`），但它建立在
    "open_chat 传 limit=0 拿全量"之上 —— 传输一分没省，被用户否决。
    现在真正分页了，本地还留着那套只会造成"服务端给了 N 条、本地又切掉一半"的双重截断。
    """
    win = _read("UI/ChatWindow.cs")
    assert "_fullHistory" not in win, "不许再持有全量历史（服务端已分页）"
    body = _method_body(win, "public void ReplaceHistory(JArray items)")
    assert "HistoryWindowCore.StartIndex" not in body, \
        "ReplaceHistory 不许再本地切片（服务端给多少建多少）"
    assert "_histCursor = 0;" in body, "分帧游标必须从 0 开始（整批都是首屏）"


def test_window_exposes_earlier_request_hook():
    """「加载更早」必须走**网络请求**：窗口抬事件、Presenter 发 RPC。

    窗口自己不许发请求（它不持有 WsClient，见 ChatWindow 文件头"明确不做"）。
    """
    win = _read("UI/ChatWindow.cs")
    assert "public event Action EarlierRequested;" in win, "必须有 EarlierRequested 事件"
    req = _method_body(win, "private void RequestEarlier()")
    assert "EarlierRequested?.Invoke()" in req, "必须触发事件交给 Presenter"
    assert "_moreLoading = true;" in req, "必须先置加载中（网络往返要有反馈）"
    assert "SendRequest" not in win, "窗口不许自己发 RPC"

    pre = _read("UI/ChatPresenter.cs")
    assert "_window.EarlierRequested += OnEarlierRequested;" in pre, "Presenter 必须订阅"
    handler = _method_body(pre, "private void OnEarlierRequested()")
    assert '"get_history"' in handler, "单聊要走 get_history"
    assert '"get_room_history"' in handler, "群聊要走 get_room_history"
    # ★ 必须断言"真的写进了请求"，不是"这个词出现过" ★
    #   变异实测：`before_turn` 在注释里也出现，裸 `in` 在"删掉那行赋值"时照样通过。
    hcode = [ln.split("//")[0] for ln in handler.splitlines()]
    writes_seq = [ln for ln in hcode if 'p["before_seq"]' in ln]
    writes_turn = [ln for ln in hcode if 'p["before_turn"]' in ln]
    assert writes_seq, "必须把 before_seq 写进请求"
    assert writes_turn, "单聊必须把 before_turn 写进请求（无 seq 回合靠它）"
    # 两者都要有 > 0 的条件（0 表示"不限定"，传上去会让服务端按 0 处理 = 没游标）
    for ln in writes_seq + writes_turn:
        assert "> 0" in ln, f"游标只在非 0 时才该传：{ln.strip()}"


def test_insert_earlier_compensates_scroll():
    """插入更早历史必须补偿滚动位置（否则画面往上跳）。

    ⚠ 11-21 更新：补偿**不能在当帧做**（`rect.height` 还没结算，会算成"顶部"
    ⇒ 连环加载）。现在拆成两步：`InsertEarlier` 记参数，`TickEarlierAnchor` 下一帧落位。
    本用例只守"参数记下来了 + 建行顺序对"，落位细节见
    `test_insert_earlier_defers_scroll_anchor`。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "public int InsertEarlier(JArray items)")
    code = [ln.strip() for ln in body.splitlines() if ln.split("//")[0].strip()]

    # 顺序：先记位置 → 再建行
    i_rec = next(i for i, l in enumerate(code) if "oldH = _r.Content.rect.height" in l)
    i_build = next(i for i, l in enumerate(code) if "ApplyHistoryItem(items[i])" in l)
    assert i_rec < i_build, "必须先记位置再建行"

    # 挪到最前必须**倒序**（正序会把顺序写反）
    assert "for (int i = made.Count - 1; i >= 0; i--)" in body, \
        "SetAsFirstSibling 必须倒序（正序会让历史倒着排）"


def test_insert_earlier_defers_scroll_anchor():
    """★ 实机 bug ① ★ 插入历史后**不许当帧补偿**，必须等布局结算。

    症状：用户上划一次，4 秒内自动翻了 2 页（`加载更早：发` 连着出现两次），
    相当于"全送过来了"。

    根因：`Instantiate` 当帧 `Content.rect.height` 还是**旧值** ⇒ `added ≈ 0`
    ⇒ `ScrollAfterPrepend` 把视口算出 **v=1（顶部）**
    ⇒ 手指还在滚轮上，下一格立刻又满足 `AtTop()` ⇒ 连环加载。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "public int InsertEarlier(JArray items)")

    # 当帧不许直接写 verticalNormalizedPosition
    code = [ln.split("//")[0] for ln in body.splitlines()]
    writes = [ln for ln in code if "verticalNormalizedPosition =" in ln]
    assert not writes, f"InsertEarlier 不许当帧写滚动位置（布局未结算，会算成顶部）：{writes}"

    # 必须挂"待锚定"
    assert "_earlierAnchorPending = true;" in body, "必须置待锚定标记"
    assert "_earlierScrollPx = oldScrollPx;" in body, "必须记下插入前的位置"
    assert "_earlierOldH = oldH;" in body, "必须记下插入前的内容高度"

    # 另有 TickEarlierAnchor 在下一帧做真正的补偿
    tick = _method_body(win, "private void TickEarlierAnchor()")
    assert "ScrollAfterPrepend" in tick, "补偿必须放在 TickEarlierAnchor 里"
    assert "_earlierAnchorWait++" in tick, "必须等若干帧（布局结算）"
    assert "_earlierAnchorWait < 10" in tick, "必须有超时兜底（不许永久 pending）"

    # Update 里要真的调
    upd = _method_body(win, "private void Update()")
    assert "TickEarlierAnchor();" in upd, "Update 必须调 TickEarlierAnchor"


def test_auto_load_blocked_while_anchoring():
    """★ 实机 bug ① 的另一半 ★ 锚定未落位时不许再触发自动加载。

    即使补偿延后了，只要中间有一个"还在顶部"的瞬间，`AtTop()` 就会为真。
    必须在触发点就挡住，否则照样连环。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "private void TryAutoLoadEarlier()")
    assert "_earlierAnchorPending" in body, \
        "自动加载必须在锚定期间直接返回（否则连环加载）"
    # 顺序：必须在 AtTop() 之前挡
    code = [ln.strip() for ln in body.splitlines() if ln.split("//")[0].strip()]
    i_block = next(i for i, l in enumerate(code) if "_earlierAnchorPending" in l)
    i_top = next(i for i, l in enumerate(code) if "AtTop()" in l)
    assert i_block < i_top, "必须在 AtTop() **之前**挡（AtTop 那一刻刚好为真）"


def test_insert_earlier_resets_date_separator_state():
    """★ 实机 bug ③ ★ 插入更早历史必须重置日期分隔条的累进状态。

    症状（用户截图）：分隔条出现 `2年8月3日 → 3年7月4日 → 8月19日 → 2年8月3日`
    —— 年份倒退的乱序。

    根因：`_sepHas/_sepY/_sepM/_sepD` 是**跨行累进**状态（记"刚画过的上一条日期"）。
    从新到旧插入时它属于**更晚**的历史，于是首批旧消息每条都被判成"日期变了"。

    要求：插入前清空（让这一批从自己第一条重新累进），插入后复原
    （那个值仍代表"内容区末尾的上一条日期"，实时消息还要用它）。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "public int InsertEarlier(JArray items)")
    code = [ln.split("//")[0] for ln in body.splitlines()]

    clears = [ln for ln in code if "_sepHas = false" in ln]
    assert clears, "插入前必须清空分隔条状态（否则日期乱序）"

    # 必须保存/复原（不复原会让之后的实时消息多画/漏画分隔条）
    assert any("saveSepHas" in ln for ln in code), "必须先保存原值"
    assert any("_sepHas = saveSepHas" in ln for ln in code), "插入后必须复原"

    # 顺序：清空在 ApplyHistoryItem 之前，复原之后
    i_clear = next(i for i, l in enumerate(code) if "_sepHas = false" in l)
    i_build = next(i for i, l in enumerate(code) if "ApplyHistoryItem(items[i])" in l)
    i_restore = next(i for i, l in enumerate(code) if "_sepHas = saveSepHas" in l)
    assert i_clear < i_build < i_restore, "顺序必须是 清空 → 建行 → 复原"


def test_insert_earlier_collects_all_added_nodes():
    """★ 实机 bug ★ 插入更早历史必须收集**整段**新增节点，不是最后一个。

    症状（用户截图）：日期分隔行全部堆在内容**末尾**（末尾连着三行日期），
    而不是各自插在对应消息上方。

    根因：一个 item 可能产出**多个**子节点 —— `AppendUserMessage` 在日期变化时
    会先 `AppendDivider(...)` 再建气泡，`childCount` 一次 +2。
    旧代码只 `made.Add(GetChild(after - 1))`（最后一个 = 气泡），
    **分隔行没进 `made`** ⇒ `SetAsFirstSibling` 只搬气泡 ⇒ 分隔行留在原地。
    """
    win = _read("UI/ChatWindow.cs")
    body = _method_body(win, "public int InsertEarlier(JArray items)")
    code = [ln.split("//")[0] for ln in body.splitlines()]

    # 必须是"遍历区间"，不能只取 after-1
    joined = "\n".join(code)
    assert "for (int k = before; k < after; k++)" in joined, \
        "必须遍历 [before, after) 整段（只取 after-1 会漏掉日期分隔行）"
    assert "GetChild(after - 1)" not in joined, \
        "不许只取最后一个节点（一次可能新增分隔行 + 气泡两个）"

    # 倒序挪动保持不变（正序会让历史倒排）
    assert "for (int i = made.Count - 1; i >= 0; i--)" in body, \
        "SetAsFirstSibling 必须倒序"
