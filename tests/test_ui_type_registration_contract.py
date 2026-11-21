# -*- coding: utf-8 -*-
"""托管 UI 类型的 Il2Cpp 注册 / 装配失败回滚 —— **接线契约**（Python 侧读源码钉住 C#）。

09-17 实机事故（用户症状：**角色无法移动 + UI 打不开**）：

  1. `UiComposer.Compose` 被当成死代码（"AB_UI 下代码版装配链不生效"，`docs/archive/2026-09-17-群聊编排/设计定稿.md` §13.4），
     连它的**调用点**一起被删 ⇒ 全工程没人注册 ChatWindow/ChatPresenter/ConfigPresenter/
     ContactPresenter/HoverTip；
  2. 三个 Ab*Panel 装配时对 presenter 做 `GetComponent<X>() ?? AddComponent<X>()`，X 是
     **本程序集的托管 MonoBehaviour**——没注册进 Il2Cpp 时 Unhollower 造不出它的
     `Il2CppSystem.Type`（`ClassInjector.ClassFromTypePatch` 里 NRE），于是
     `GameObject+MethodInfoStoreGeneric_GetComponent_Public_T_0`1<T>` 的**静态构造**抛
     `TypeInitializationException`，**且 CLR 缓存该失败 ⇒ 该 T 本次进程内永久不可用**；
  3. `AbContactPanel` 的装配失败回滚当时是 `UnityEngine.Object.Destroy(go)`——绕过 UIMgr
     关窗契约 ⇒ 登记项泄漏 ⇒ **世界输入被永久门控**（人物动不了），死实例还挂在登记表上
     ⇒ 同名 `OpenUI` 返回 null（UI 打不开）。`AbChatPanel` 早已改成走 `CloseUI`，另两个没有。

所以这里钉四件事（每一条都对应一个"只在实机上才炸、日志里只有一行"的静默失败）：

  A. 五个类型**有**注册调用，且注册助手幂等（重复 `RegisterTypeInIl2Cpp` 会抛）、
     单项失败不连累其它、失败的那项下次还会重试；
  B. 注册发生在 `ModMain.Init`（初始化期，纯 MonoBehaviour 不受"UIBase 早期注册毒化"限制）
     **以及**每个面板 `g.ui.OpenUI` **之前**（注册没就绪就别建宿主，免得留下要回滚的登记）；
  C. 装配失败的回滚必须走 `g.ui.CloseUI`（关窗契约），**不得** `Destroy(go)`；
  D. 凡是本仓库自己定义的组件类型，被任何 `GetComponent<X>` / `AddComponent<X>` 用到时
     都必须在注册清单里（C 是兜底，D 是根治：漏注册在编译期完全无声）。

反例检查过：把 `EnsureCoreTypesRegistered` 的调用点、或注册清单里任一项、或回滚那两行
改回去，本文件对应的用例即红（B/C/D 三条各自独立触发）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
MODMAIN = CS / "app" / "ModMain.cs"
COMPOSER = CS / "UI" / "UiComposer.cs"
PANELS = [
    CS / "UI" / "AbChatPanel.cs",
    CS / "UI" / "AbConfigPanel.cs",
    CS / "UI" / "AbContactPanel.cs",
]

# 宿主显式注册的五个托管类型（UiComposer._coreTypeNames 的镜像；D 用例会再做包含性检查）
CORE_TYPES = ["ChatWindow", "ChatPresenter", "ConfigPresenter", "ContactPresenter", "HoverTip"]

# 面板自己的注册（各自文件里的 _typeRegistered 分支）
PANEL_SELF = {
    "AbChatPanel.cs": "AbChatPanel",
    "AbConfigPanel.cs": "AbConfigPanel",
    "AbContactPanel.cs": "AbContactPanel",
}

# Unity / 游戏自带的组件类型：不是本程序集的托管类型，不需要 ClassInjector 注册
FOREIGN_TYPES = {
    "Canvas", "CanvasGroup", "CanvasScaler", "GraphicRaycaster", "RectTransform", "Transform",
    "Image", "RawImage", "Text", "Button", "ScrollRect", "InputField", "Mask", "RectMask2D",
    "Animator", "Toggle", "Slider", "Outline", "Shadow", "EventTrigger", "LayoutElement",
    "VerticalLayoutGroup", "HorizontalLayoutGroup", "ContentSizeFitter", "CanvasRenderer",
    "EventSystem", "StandaloneInputModule", "UINPCInfo", "TextMeshProUGUI", "Camera",
    "AudioSource", "SpriteRenderer", "BoxCollider",
}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


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


def _block(src: str, anchor: str) -> str:
    """取 anchor 之后第一个 `{...}`（含花括号）——**必须按配对切**：
    切片写成"往后 400 字符"会滑进下一个分支，断言就变成空的（本文件自身踩过一次）。"""
    src = _strip_comments(src)
    assert src.count(anchor) == 1, "锚点不唯一（%d 次）：%s" % (src.count(anchor), anchor)
    i = src.index(anchor)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + anchor)


def _project_mono_types() -> set[str]:
    """本仓库 csharp/ 里定义的类名（跨文件收集；用来判 `Component<T>` 的 T 是不是自家的）。"""
    names = set()
    for p in CS.rglob("*.cs"):
        if "tests" in p.parts or "obj" in p.parts or "bin" in p.parts:
            continue
        for m in re.finditer(r"\bclass\s+([A-Za-z_]\w*)", _strip_comments(_read(p))):
            names.add(m.group(1))
    return names


# ----------------------------------------------------------------------
# A. 注册调用存在 / 助手本身健壮
# ----------------------------------------------------------------------

def test_every_core_type_has_a_registration_call():
    src = _strip_comments(_read(COMPOSER))
    for t in CORE_TYPES:
        assert "RegisterTypeInIl2Cpp<%s>()" % t in src, (
            "%s 没有 RegisterTypeInIl2Cpp 调用 —— 面板装配时 GetComponent<%s>() 会抛 "
            "TypeInitializationException 且被 CLR 永久缓存（09-17 事故根因）" % (t, t))


def test_registration_helper_is_idempotent_and_per_type_isolated():
    body = _method(_read(COMPOSER), "public static bool EnsureCoreTypesRegistered()")
    # 幂等：已注册的必须跳过（重复 RegisterTypeInIl2Cpp 会抛）
    assert "if (_coreTypeDone[i]) continue;" in body
    assert "_coreTypeDone[i] = true;" in body
    # 单项隔离：逐个 try/catch，失败不抛出去（否则第一个失败会连累后面四个）
    assert body.count("catch (Exception e)") == 1
    assert "return all;" in body
    # 清单与断言表一一对应（漏一个类型 = 漏一个注册）
    names = _read(COMPOSER)
    for t in CORE_TYPES:
        assert '"%s"' % t in names, "注册清单 _coreTypeNames 里缺 " + t


def test_registration_helper_does_not_mark_failed_types_done():
    body = _method(_read(COMPOSER), "public static bool EnsureCoreTypesRegistered()")
    catch = body[body.index("catch (Exception e)"):]
    assert "_coreTypeDone[i] = true" not in catch, (
        "注册失败也记成已完成 ⇒ 该类型永久缺失且不再重试")


# ----------------------------------------------------------------------
# B. 调用点：ModMain.Init + 每个面板 OpenUI 之前
# ----------------------------------------------------------------------

def test_modmain_registers_core_types_in_init():
    init = _method(_read(MODMAIN), "public void Init()")
    assert "UiComposer.EnsureCoreTypesRegistered()" in init, (
        "ModMain.Init 不再注册核心 UI 类型 —— 这正是 09-17 事故（调用点被当死代码删掉）")


def test_each_panel_registers_before_openui():
    """每个面板在 `OpenUI` **之前**完成前置检查（11-20 起那段收进 `AbPanelPreflight.Check<T>`）。

    判据不变（注册必须早于 OpenUI、面板自己必须被注册），只把"在哪注册"从
    "面板里直接调 `UiComposer` / `RegisterTypeInIl2Cpp<T>()`"改成"调 preflight"——
    那四项检查的顺序与理由现在只有一处（`UI/AbPanelPreflight.cs`）。
    """
    preflight = _strip_comments(_read(CS / "UI" / "AbPanelPreflight.cs"))
    # preflight 内部：先注册宿主类型，再注册核心 UI 类型
    assert "RegisterTypeInIl2Cpp<T>()" in preflight, \
        "preflight 没注册宿主类型（IL2CPP 下泛型静态构造会抛且被永久缓存）"
    assert "UiComposer.EnsureCoreTypesRegistered()" in preflight, \
        "preflight 没注册核心 UI 类型"
    i_self = preflight.index("RegisterTypeInIl2Cpp<T>()")
    i_core = preflight.index("UiComposer.EnsureCoreTypesRegistered()")
    assert i_self < i_core, "preflight 里核心类型注册排在宿主类型之前 —— 顺序反了"

    for p in PANELS:
        src = _strip_comments(_read(p))
        assert "AbPanelPreflight.Check<" in src, "%s 没有走前置检查" % p.name
        open_ui = src.index("g.ui.OpenUI(new UIType.UITypeBase(PrefabKey")
        call = src.index("AbPanelPreflight.Check<")
        assert call < open_ui, (
            "%s：前置检查排在 OpenUI 之后 —— 宿主已进 UIMgr 登记表，装配失败还得回滚一次" % p.name)
        # 面板自己必须被注册（现在由 preflight 的泛型参数指名）
        self_type = PANEL_SELF[p.name]
        assert "AbPanelPreflight.Check<%s>(" % self_type in src, \
            "%s 没有把自己交给 preflight 注册" % p.name



def _gives_up(branch: str) -> bool:
    """这个分支是否**放弃了本轮创建**。

    两种合法写法（都表示放弃）：
      · `return false;`                          —— 原始写法
      · `return Fail("…");`                      —— 09-20 起：失败时一并记下**原因**，
                                                     供「传」按钮把"为什么没打开"如实告诉玩家

    ★ 判据刻意写成"二者之一"，而不是把测试改成只看新的那种：
      本测试守护的是**语义**（"注册失败就不许继续往下建宿主"），不是某个字面量。
      但也不能放宽成"只要出现 return 就算"——那会让 `return true;`（继续建）也通过。
    """
    return bool(re.search(r"return\s+false\s*;", branch)
                or re.search(r"return\s+Fail\s*\(", branch))

def test_panel_gives_up_without_creating_a_host_when_registration_failed():
    """前置检查失败 ⇒ **不许**继续 `OpenUI`。

    两种合法形态（都是"失败就放弃"，只是写法随重构变过）：
      · `if (AbPanelPreflight.Check<T>(…) != Ok) return false;`   —— 一句话早退
      · `switch (pre) { … return Fail(…); }`                        —— 要按原因给不同文案
    判据是"从检查失败到 OpenUI 之间确实放弃了"，不锁某一种排版。
    """
    for p in PANELS:
        src = _strip_comments(_read(p))
        i_check = src.index("AbPanelPreflight.Check<")
        i_open = src.index("g.ui.OpenUI(")
        assert i_check < i_open, "%s：前置检查排在 OpenUI 之后" % p.name
        between = src[i_check:i_open]
        assert _gives_up(between), (
            "%s：前置检查失败却没有放弃（会照样建宿主，回滚还得 CloseUI 白来一次）" % p.name)


# ----------------------------------------------------------------------
# C. 装配失败的回滚：必须走 UIMgr 关窗契约
# ----------------------------------------------------------------------

def test_assembly_failure_rolls_back_through_uimgr_close():
    for p in PANELS:
        src = _strip_comments(_read(p))
        assert "Object.Destroy(go)" not in src and "Destroy(go)" not in src, (
            "%s：装配失败用 Object.Destroy 绕过关窗契约 ⇒ UIMgr 登记项泄漏 ⇒ "
            "世界输入被永久门控（人物动不了 + UI 打不开）" % p.name)
        rollback = _block(src, "if (!panel.AssembleResident")
        assert "g.ui.CloseUI(new UIType.UITypeBase(PrefabKey, (UILayer)0), false)" in rollback, (
            "%s：装配失败分支没有 CloseUI 回滚" % p.name)
        assert _gives_up(rollback), "%s：回滚后没有放弃本轮创建" % p.name


# ----------------------------------------------------------------------
# D. 根治：自家组件类型一律要有注册（漏注册编译期无声）
# ----------------------------------------------------------------------

def _all_cs_files() -> list[Path]:
    """csharp/ 下全部参与编译的 .cs（排除 tests/obj/bin）——D 用例的扫描面。"""
    return [p for p in CS.rglob("*.cs")
            if not ({"tests", "obj", "bin"} & set(p.parts))]


def _registered_types() -> set[str]:
    """源码里**实际**出现的 `RegisterTypeInIl2Cpp<X>()`（D 用例不依赖上面那份镜像清单）。"""
    out: set[str] = set()
    for p in _all_cs_files():
        code = _strip_comments(_read(p))
        out |= set(re.findall(r"RegisterTypeInIl2Cpp<([A-Za-z_]\w*)>\s*\(", code))
        # 11-20 起宿主类型统一由 `AbPanelPreflight.Check<T>` 注册（泛型形参就是那个 T）
        out |= set(re.findall(r"AbPanelPreflight\.Check<([A-Za-z_]\w*)>\s*\(", code))
    return out


def test_every_project_component_type_used_by_panels_is_registered():
    registered = _registered_types()
    own = _project_mono_types()
    for p in _all_cs_files():
        src = _strip_comments(_read(p))
        used = {m.group(1) for m in
                re.finditer(r"\b(?:Get|Add)Component<([A-Za-z_]\w*)>", src)}
        for t in sorted(used - FOREIGN_TYPES):
            if t not in own:
                # 泛型形参（`private static T FindComp<T>(...) => go.GetComponent<T>()` 这类
                # 透传）不是组件类型 —— 但必须真是本文件声明过的泛型形参，免得成了绕过判据的后门。
                assert re.search(r"\w+\s+[A-Za-z_]\w*\s*<\s*%s\s*[,>]" % re.escape(t), src), (
                    "%s：%s 既不是自家类型、也不是本文件声明的泛型形参（判据要更新）"
                    % (p.relative_to(CS), t))
                continue
            assert t in registered, (
                "%s 用了 Get/AddComponent<%s>() 但 %s 没有任何注册调用" % (p.relative_to(CS), t, t))


def test_ignored_foreign_types_are_not_project_types():
    """忽略表不能把自家类型悄悄放行（否则 D 用例会被绕过）。"""
    own = _project_mono_types()
    leaked = sorted(FOREIGN_TYPES & own)
    assert leaked == [], "忽略表里混进了本仓库自己的类型：%s" % leaked


# ----------------------------------------------------------------------
# E. 日志文案不许提"不存在的热键"（排障时的假线索）
# ----------------------------------------------------------------------

HOTKEY_NAMES = ["F9", "F10", "F11"]


def test_log_text_never_promises_a_hotkey_that_does_not_exist():
    """09-17 排障实录：`[ContactPanelOpener] ... F10 本次不响应` 这句文案让我把
    "点了 HUD「传」按钮"读成了"按了 F10"，而全 mod 早已没有任何热键（唯一的按键读取
    只剩输入框的 Esc / Ctrl+V / Enter）。文案与实现分家 = 排障时最贵的一种谎。

    允许将来恢复热键：那时源码里必须真有 `KeyCode.Fxx` 读取，本用例自然放行。
    """
    files = _all_cs_files()
    live = set()
    for p in files:
        live |= set(re.findall(r"KeyCode\.([A-Za-z0-9_]+)", _strip_comments(_read(p))))
    for p in files:
        src = _strip_comments(_read(p))
        for lit in re.findall(r'P\(\s*"([^"]*)"', src):
            for k in HOTKEY_NAMES:
                if k in lit:
                    assert k in live, (
                        "%s 的日志文案提到 %s，但全工程没有任何 KeyCode.%s 读取 —— "
                        "文案陈旧会把人引到错的方向：%r" % (p.relative_to(CS), k, k, lit))
