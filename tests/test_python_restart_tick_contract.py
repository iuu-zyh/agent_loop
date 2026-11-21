"""Python 重启状态机的**每帧驱动**契约（09-20 实机事故）。

事故现场
------------------------------------------------------------------------------------
面板里改 `network.port` → 保存 → `set_config` 应答 `effective=restart` →
BrainLink 挂起 → `shutdown` 应答 `ok=True`（旧进程确实退了）→ **然后就没了**。
Player.log 在该行之后**一行都没有**；agent_loop.log 停在 `shutdown 已受理`。
表现：界面永远停在"正在重启 Python…"，第二次点保存变成"未连接 Python"。

根因（**不是**状态机逻辑错，是它根本没被调用）
------------------------------------------------------------------------------------
重启状态机 `TickPythonRestart` 原由 `ConfigPresenter.Update` 驱动。但配置面板的常驻宿主
**关闭态根节点是 OFF** 的（防游戏把"UI 层有界面"当模态 → 屏蔽世界输入）⇒ `Update` 不再运行
⇒ tick 停摆。当时的修法：搬到 `AbHotkeys.OnFrame`，并在注释里写明"由 ModMain 的
`g.timer.Frame` 每帧回调驱动"——**但那行注册从来没写**。
`AbHotkeys` 在整个工程里只有引用与注释，**没有任何注册调用点** ⇒ 它是一段死代码，
而 `ConfigPresenter.Update` 又已经不再调 tick ⇒ 两边都断了。

本文件钉死的三件事（任何一条单独成立都足以让事故重演）
------------------------------------------------------------------------------------
  ① `AbHotkeys.OnFrame` **必须**在 ModMain 里被 `g.timer.Frame` 注册；
  ② `ConfigPresenter` 不许把 tick 收回 `Update()`（关闭态不运行，等于又断一条）；
  ③ 这条链要能从 `AbHotkeys.OnFrame` 一路走通到 `TickPythonRestart`（中途不许有断点）。

为什么必须用"注册调用点"而不是"函数存在"做判据：本事故里函数一直存在、一直编译通过、
注释一直说它被调用 —— 没有测试的话，删掉那一行注册不会让任何东西变红。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODMAIN = ROOT / "csharp/app/ModMain.cs"
HOTKEYS = ROOT / "csharp/UI/AbHotkeys.cs"
ABCONFIG = ROOT / "csharp/UI/AbConfigPanel.cs"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"
CSPROJ = ROOT / "csharp/AgentLoopBridge.csproj"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_modmain_registers_the_resident_frame_callback():
    """① `AbHotkeys.OnFrame` 必须真的被 g.timer.Frame 注册（事故主因：注册行缺失）。"""
    src = _read(MODMAIN)
    # 11-19 起常驻帧回调统一走 `FrameTicker`（键 `KHotkeys`，内部就是"每帧、常驻、可自愈重挂"）。
    # 判据是"**真的被注册**"+"常驻"，不是"用哪套 API"。
    m = re.search(r'FrameTicker\.Ensure\(\s*FrameTicker\.KHotkeys\s*,\s*"hotkeys"\s*,\s*AbHotkeys\.OnFrame\s*\)', src)
    assert m, (
        "ModMain 里没有注册 AbHotkeys.OnFrame —— 配置面板的 Python 重启状态机将永远不会被 tick，\n"
        "表现：改端口保存后卡在「正在重启 Python…」，新进程从不拉起（09-20 事故原形）"
    )

    # 与 BrainLink 同款：注册必须放在 AB_UI 条件编译内（AbHotkeys 类本身在 #if AB_UI 里）
    before = src[:m.start()]
    assert before.count("#if AB_UI") > before.count("#endif"), \
        "AbHotkeys.OnFrame 的注册落在 #if AB_UI 之外（该类只在 AB_UI 下编译，会编译失败）"


def test_presenter_update_does_not_take_the_tick_back():
    """② `ConfigPresenter.Update` 不许把 tick 收回去：关闭态根节点是 OFF 的，Update 不运行。"""
    src = _read(PRESENTER)
    m = re.search(r'private void Update\(\)\s*\{(.*?)\n        \}', src, re.S)
    assert m, "ConfigPresenter.Update 没找到"
    body = m.group(1)
    assert "TickPythonRestart" not in body and "TickRestart" not in body, (
        "ConfigPresenter.Update 又开始直接驱动重启 tick —— 面板关闭时宿主根节点是 OFF，\n"
        "Update 不运行 ⇒ tick 停摆（这正是 09-20 把 tick 搬去 AbHotkeys 的原因）"
    )


def test_tick_chain_is_complete_from_frame_callback_to_state_machine():
    """③ 整条链必须走得通：OnFrame → AbConfigPanel.Tick → TickRestart → TickPythonRestart。"""
    hot = _read(HOTKEYS)
    m = re.search(r'public static void OnFrame\(\)\s*\{(.*?)\n        \}', hot, re.S)
    assert m, "AbHotkeys.OnFrame 没找到"
    assert "AbConfigPanel.Tick()" in m.group(1), \
        "AbHotkeys.OnFrame 没有调用 AbConfigPanel.Tick()（链在这里断）"

    ab = _read(ABCONFIG)
    m = re.search(r'public static void Tick\(\)\s*\{(.*?)\n        \}', ab, re.S)
    assert m, "AbConfigPanel.Tick 没找到"
    assert "TickRestart()" in m.group(1), \
        "AbConfigPanel.Tick 没有转到 presenter.TickRestart()（链在这里断）"

    pres = _read(PRESENTER)
    m = re.search(r'internal void TickRestart\(\)\s*\{(.*?)\n        \}', pres, re.S)
    assert m, "ConfigPresenter.TickRestart 没找到"
    assert "TickPythonRestart()" in m.group(1), \
        "ConfigPresenter.TickRestart 没有转给 TickPythonRestart()（链在这里断）"


def test_ab_hotkeys_is_compiled_under_ab_ui():
    """AbHotkeys 类身披 #if AB_UI，而注册点也必须在 AB_UI 下 —— 否则一边编一边不编。"""
    hot = _read(HOTKEYS)
    assert re.search(r'#if\s+AB_UI', hot), "AbHotkeys.cs 不再受 #if AB_UI 保护（与注册点的条件编译约定不符）"
    csproj = _read(CSPROJ)
    for cfg in ("Debug", "Release"):
        seg = re.search(r"==\s*'" + cfg + r"\|AnyCPU'\s*\">(.*?)</PropertyGroup>", csproj, re.S)
        assert seg, f"csproj 里没找到 {cfg} 配置"
        assert "AB_UI" in seg.group(1), \
            f"{cfg} 配置没有定义 AB_UI —— 面板代码会整个不被编译（注释明说两个配置都要定义）"


def test_port_change_still_asks_for_a_game_restart():
    """端口变更那条路必须仍然收口成「请重启游戏」（它是设计，不是 bug）。

    本事故容易修歪的地方：把"卡住"误判成"应该自动连新端口"。
    C# 的连接目标在 `WsClient.Start` 时就定了，运行期改不了 ⇒ 只能整机重启。
    """
    src = _read(PRESENTER)
    assert "_portChangedDuringRestart" in src, "端口变更分支被删了"
    assert "PortFieldChanged()" in src, "端口是否变更的判据（PortFieldChanged）没了"
    assert re.search(r'端口已变更[^"]*重启游戏|请重启游戏[^"]*端口', src), \
        "端口变更后没有提示「请重启游戏」（玩家会以为连不上是故障）"
