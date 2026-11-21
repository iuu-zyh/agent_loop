"""★ 面板的共用件只有一份（11-20 批次 5）★

## 这次收掉的是什么（以及没收什么）

原计划是"四个面板合并成一个基类"。读完代码后否掉了，理由写在复核文档里，关键是两条：

1. **继承槽位已经被游戏的 `UIBase` 占了**。再插一层 `AbPanelBase : UIBase` 意味着往 Il2Cpp
   类型系统里注入"派生自原生类型的中间层"——本仓注释里记着这条的后果（毒化类型系统 → 登录 UI
   原生崩溃）。为组织代码付这个价不值。
2. **四份"同构"不等于同一段代码**。真正逐字相同的只有：`NormalizeLayout`（4 份）、
   "创建前四项检查"（3 份）、存活判定（3 份，两种写法）。其余是各面板自己的节点收集与失败文案。

所以只抽三件小事：

| 件 | 在哪 | 原来 |
|---|---|---|
| `UiKit.NormalizeLayout(go, tag)` | `UI/UiKit.cs` | 四个面板各一份（逐字相同） |
| `AbPanelPreflight.Check<T>(key, requireWs)` | `UI/AbPanelPreflight.cs` | 三份四项检查 |
| `AbPanelLifecycle.Alive(ref T)` | `UI/AbPanelLifecycle.cs` | 两个 `IsAlive` 属性 + 一份手写 try/catch |

## 钉什么

· 这三件各只有一份实现（别处不许再写一遍）；
· 三个面板确实走统一前置检查，且**不再是各自注册**（`_typeRegistered` 那种记账已删）；
· 存活判定统一（面板里不许再出现手写的 `gameObject == null` 清理块）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
UI = CS / "UI"
KIT = UI / "UiKit.cs"
PREFLIGHT = UI / "AbPanelPreflight.cs"
LIFECYCLE = UI / "AbPanelLifecycle.cs"

PANELS = {
    "AbChatPanel.cs": "AbChatPanel",
    "AbConfigPanel.cs": "AbConfigPanel",
    "AbContactPanel.cs": "AbContactPanel",
}
NOTICE = UI / "NoticePanel.cs"


def _code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"///.*", "", text)
    text = re.sub(r"//.*", "", text)
    return text


def _ui_files():
    return [p for p in UI.rglob("*.cs") if p.name not in
            {"UiKit.cs", "AbPanelPreflight.cs", "AbPanelLifecycle.cs"}]


def test_three_helpers_exist_and_are_unique():
    assert "public static void NormalizeLayout(GameObject go, string tag)" in _code(KIT.read_text(encoding="utf-8"))
    assert "public static string Check<T>(string prefabKey, bool requireWs)" in _code(PREFLIGHT.read_text(encoding="utf-8"))
    assert "public static bool Alive<T>(ref T resident)" in _code(LIFECYCLE.read_text(encoding="utf-8"))

    for p in _ui_files():
        src = _code(p.read_text(encoding="utf-8"))
        assert "static void NormalizeLayout(" not in src, "%s 又写了一份 NormalizeLayout" % p.name
        # 允许"一行委托"（`public static bool IsAlive => AbPanelLifecycle.Alive(ref _resident);`），
        # 禁止带方法体的第二份实现（原来那 11 行 try/catch 的形态）
        assert not re.search(r"static\s+bool\s+IsAlive\s*\{", src), \
            "%s 又写了一份带方法体的 IsAlive（应走 AbPanelLifecycle）" % p.name
        assert "RegisterTypeInIl2Cpp<" not in src or p.name not in PANELS, (
            "%s 自己注册宿主类型（应走 AbPanelPreflight）" % p.name)


def test_panels_delegate_their_preflight():
    for name, t in PANELS.items():
        src = _code((UI / name).read_text(encoding="utf-8"))
        assert "AbPanelPreflight.Check<%s>(" % t in src, "%s 没走统一前置检查" % name
        for gone in ("_typeRegistered", "UiComposer.EnsureCoreTypesRegistered()", "ModAbRes.EnsureInjected("):
            assert gone not in src, "%s 里还残留 %s（前置检查已收拢）" % (name, gone)


def test_configure_panel_does_not_require_connection():
    """配置面板是纯本地读写：断线时也必须能打开改设置。

    12-…（11-20）收拢时最容易搞错的一处：把"在线连接"当成三面板共同前置。
    聊天/传音簿需要（订阅 UiEvent），配置面板不需要。
    """
    src = _code((UI / "AbConfigPanel.cs").read_text(encoding="utf-8"))
    assert "AbPanelPreflight.Check<AbConfigPanel>(PrefabKey, false)" in src, \
        "配置面板把 requireWs 传成了 true —— 断线时玩家将无法修改设置"
    for name in ("AbChatPanel.cs", "AbContactPanel.cs"):
        s = _code((UI / name).read_text(encoding="utf-8"))
        assert "(PrefabKey, true)" in s, "%s 应在 preflight 里要求在线连接" % name


def test_notice_panel_uses_shared_normalize():
    src = _code(NOTICE.read_text(encoding="utf-8"))
    assert "UiKit.NormalizeLayout(" in src, "公告栏没有走共用归一化"
