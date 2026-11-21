"""通讯录快捷键（C）：**接线契约**（09-24 用户需求）。

用户需求原文
------------------------------------------------------------------------------------
「我需要加入快捷键，为我的通讯录UI加入快捷键，点击一个按钮直接唤出打开通讯录UI……
首先不能和游戏本身快捷键重叠，其次需要和游戏的快捷键一样，战斗中不会触发，
同时在我的对话UI或者配置UI以及通讯录UI已经打开的情况下不会触发快捷键。」

分层（用户三条要求落在哪）
------------------------------------------------------------------------------------
  · **键位 C**（`AbHotkeys.ContactHotkey`）：游戏 `DefaultKeys.json` 全表 34 条逐条解码后
    已占用 W/S/A/D（移动）、鼠标左/右键、空格、Q/E/R/F、左 Shift、1/2/3/4/5、
    I 人物属性 / X 技能 / B 背包 / L 任务 / H 信件 / N 大事件 / M 小地图 / O 技艺 /
    Z 跳过本月 / J 逆天改命 / F 神器 / T 仙法 —— **C 不在其中**。
  · **规则**：`UI/HotkeyCore.cs`（零 Unity 的判定核，单测在 `csharp/tests/HotkeyCoreTests`）。
  · **判据**（全在 `AbHotkeys.TickContactHotkey` 里接上）：
      我们的界面 = `HotkeyGate.OurPanelOpen()`（= OwnUiProbe 三面板，需求第 3 条）；
      输入框     = `HotkeyGate.InputFocused()`（打字时的字母键属于输入）；
      战斗       = `NpcInitiativeMonitor.BattleActive()`（需求第 2 条）；
      游戏闸     = `FastKeyGate.MapEnabled()`（isEnableMap，与游戏快捷键同款时机）。

本文件钉死的六件事
------------------------------------------------------------------------------------
  ① 同帧去重必须存在、且在**所有判据之前**（F9/F10/F11「按了没反应」的根因是帧回调重复注册）；
  ② 键位是 C、只在一处定义；`_lastHotkeyFrame` 初值必须是 -1（0 会吃掉游戏第 0 帧）；
  ③ 四条判据都接上了，且"记帧与响应解耦"（被拦的按下同样记账，防同帧重复打日志）；
  ④ 语义是"**只开不关**"（`RequestOpen` → `TryOpenNow(toggle: false)`），不是 Toggle；
  ⑤ 失败姿态：`MapEnabled` 读不到要**放行**（快捷键不许因一次反射失败永久静默失效）；
  ⑥ 日志：命中行按用户指定文案打（含 isEnableMap/battle 现场值），拦截原因**同原因只打一次**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """去掉 `//` 与 `/* */` 注释（保留字符串字面量）——按"代码在不在"断言，
    不想被注释里的示例骗过（工程里注释常引用旧实现，直接 in 判断会假绿）。"""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':                       # 字符串整体保留（日志文案就在里面）
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    break
                j += 1
            out.append(src[i:j + 1])
            i = j + 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _body(src: str, signature: str) -> str:
    """取某个方法/函数的完整方法体（按大括号配对），供"方法内有没有接线"类断言。"""
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
    raise AssertionError(f"方法体未闭合：{signature}")


CORE_SRC = _strip_comments(_read("csharp/UI/HotkeyCore.cs"))
HOTKEYS_SRC = _strip_comments(_read("csharp/UI/AbHotkeys.cs"))
GATE_SRC = _strip_comments(_read("csharp/UI/HotkeyGate.cs"))
FASTKEY_SRC = _strip_comments(_read("csharp/UI/FastKeyGate.cs"))
OPENER_SRC = _strip_comments(_read("csharp/UI/ContactPanelOpener.cs"))


# ---------------------------------------------------------------------------
# ① 同帧去重（F9/F10/F11 事件的解药）
# ---------------------------------------------------------------------------


def test_frame_dedup_exists_and_precedes_all_criteria():
    """★核心★ 同帧去重必须存在，且在**任何判据之前**，且"记帧"发生在判据之前。

    为什么这条最贵：`g.timer` 是游戏全局对象、`ModMain.Destroy` 不注销帧回调
    ⇒ 每"回主界面 → 进世界"一轮就多挂一份 `AbHotkeys.OnFrame`；同帧
    `Input.GetKeyDown` 在每份回调里都为真 ⇒ 一次按键判定 N 次。历史上的 F9/F10/F11
    正是因此**开→关互相抵消**（"按了没反应"、零报错）而被全部删除。
    """
    i_dedup = CORE_SRC.index("if (frame == lastFrame)")
    i_mark = CORE_SRC.index("d.NewLastFrame = frame;")
    i_ui = CORE_SRC.index("if (ourUiOpen)")
    assert i_dedup < i_mark < i_ui, (
        "同帧去重/记帧的位置被挪到判据之后 —— 重复注册的回调会让一次按键响应多次"
        "（正是 F9/F10/F11「按了没反应」的复现路径）"
    )
    assert "Reason.SameFrame" in CORE_SRC, "同帧分支被删了"


def test_blocked_press_still_consumes_frame():
    """被拦下的按下**同样**要记账（`d.NewLastFrame = frame;` 在判据之前）——
    否则同帧第二份回调会把同一次按下的日志再打一遍（去重白做、日志翻倍）。"""
    i_mark = CORE_SRC.index("d.NewLastFrame = frame;")
    i_ui = CORE_SRC.index("if (ourUiOpen)")
    assert i_mark < i_ui, "记帧被写在第一条判据之后：被拦的按下不记账，同帧会重复处理"


# ---------------------------------------------------------------------------
# ② 键位：C，且只在一处
# ---------------------------------------------------------------------------


def test_hotkey_is_c_and_defined_once():
    """键位是 C，且**只有一处定义**（改键位只改一行，契约测试跟着改一次）。"""
    assert "private const KeyCode ContactHotkey = KeyCode.C;" in HOTKEYS_SRC, (
        "键位不是 C 或定义被改了 —— 若换键位，请同步更新本测试与 HotkeyCoreTests 的说明"
    )
    assert HOTKEYS_SRC.count("KeyCode ContactHotkey") == 1, "键位定义被复制成多份（改一处会漂移）"
    assert "Input.GetKeyDown(ContactHotkey)" in HOTKEYS_SRC, "检测没有走这份键位定义（写死了别处）"


def test_last_frame_init_is_minus_one():
    """`_lastHotkeyFrame` 初值必须是 -1：`Time.frameCount` 从 0 开始，
    用 0 当初值会把游戏第 0 帧的按下静默吃掉（HotkeyCoreTests 有对应断言）。"""
    assert "_lastHotkeyFrame = -1;" in HOTKEYS_SRC, "初值不是 -1 —— 对齐 Time.frameCount=0 时会吃掉一次按键"


def test_reason_log_is_throttled_by_reason():
    """拦截日志"同一原因只打一次"：输入框里打字（按 c 是正常输入）与战斗中连按
    都不能刷屏；触发过（Fire）再被拦时原因必然变化，会重新打一行。"""
    assert "_lastHotkeyBlock" in HOTKEYS_SRC and "HotkeyCore.Reason.Fire;" in HOTKEYS_SRC, \
        "拦截日志的节流状态不见了"
    assert "if (d.Why == _lastHotkeyBlock) return;" in HOTKEYS_SRC, "同原因没有节流（会刷屏）"


# ---------------------------------------------------------------------------
# ③ 四条判据 + 触发入口的接线
# ---------------------------------------------------------------------------


def _tick_body() -> str:
    return _body(HOTKEYS_SRC, "private static void TickContactHotkey()")


def test_wiring_feeds_all_four_criteria():
    """四判据一个都不能少：界面（需求 3）/ 输入框 / 战斗（需求 2）/ 游戏闸。"""
    body = _tick_body()
    for probe, why in (
        ("HotkeyGate.OurPanelOpen()", "需求 3：我们的对话/配置/通讯录开着时不触发"),
        ("HotkeyGate.InputFocused()", "输入框聚焦时不触发（字母键属于输入）"),
        ("NpcInitiativeMonitor.BattleActive()", "需求 2：战斗中不触发"),
        ("FastKeyGate.MapEnabled()", "游戏闸（isEnableMap）：与游戏快捷键同款时机"),
    ):
        assert probe in body, f"判据没接上：{probe}（{why}）"
    assert "HotkeyCore.Decide(Time.frameCount, _lastHotkeyFrame, true," in body, \
        "没有把帧号与判据喂给判定核（规则被绕过了）"
    assert "ContactPanelOpener.RequestOpen();" in body, "命中了但没有唤出传音簿"


def test_on_frame_calls_the_tick():
    """接线必须挂在**常驻**帧回调 `AbHotkeys.OnFrame` 上（`ModMain` 已注册它；
    它是唯一"面板关着也在跑"的入口 —— 需求 3 要求"面板开着不触发"，
    检测本身就不能依赖任何面板的激活状态）。"""
    assert "TickContactHotkey()" in _body(HOTKEYS_SRC, "public static void OnFrame()"), \
        "OnFrame 没有调快捷键 tick"


def test_hit_log_line_and_scene_values():
    """命中日志按用户指定文案打，且**带现场值**（isEnableMap / battle）——
    真机第一次实验就是靠它核对"战斗里 isEnableMap 到底是不是 False"。"""
    body = _tick_body()
    assert '"[Hotkey] 传音簿快捷键 C 命中"' in body, "命中日志文案被改了（用户按它 grep 现场）"
    assert "isEnableMap=" in body and "battle=" in body, "命中日志没带现场值（核对不了游戏闸）"
    assert "但未触发" in body, "被拦时没有说明原因的行（按了没反应会无法自查）"


# ---------------------------------------------------------------------------
# ④ 语义：只开不关
# ---------------------------------------------------------------------------


def test_hotkey_open_is_open_only_not_toggle():
    """★核心★ 快捷键 = "唤出"，已开着时**不许**把它关掉（需求原文第三条）。
    已开时接线层就已拦下（OurPanelOpen），这里再靠 `toggle: false` 的
    "已开则不动"兜一层 —— 两层都不会误关。"""
    body = _body(OPENER_SRC, "public static void RequestOpen()")
    assert "TryOpenNow(toggle: false)" in body, "快捷键走了开关语义 —— 已开着时会误关"
    assert "toggle: true" not in body, "快捷键不该有开关分支"
    assert "ArmPending()" in body, "未就绪时没有挂起意图（玩家会以为快捷键失效）"


def test_direct_click_still_toggle_and_pending_fulfilment_unchanged():
    """反向钉（原行为一字不改）：点「传」按钮仍是开关语义；
    兑现挂起的意图仍是"只打开、已开则不动"。"""
    assert "TryOpenNow(toggle: true)" in _body(OPENER_SRC, "public static void Toggle()"), \
        "点按钮丢了开关语义"
    assert "TryOpenNow(toggle: false)" in _body(OPENER_SRC, "public static void TickPending()"), \
        "挂起兑现语义被改回开关（会误关玩家刚打开的面板）"


# ---------------------------------------------------------------------------
# ⑤ 失败姿态：游戏闸读不到要放行
# ---------------------------------------------------------------------------


def test_map_enabled_fails_open():
    """`FastKeyGate.MapEnabled()`：属性没探到 / 读取异常 → **返回 true（放行）**。

    与 `PrefixFastKey` 的失败姿态相反（那边宁可漏拦游戏键）：这里服务的是
    "玩家还能不能用我们自己的快捷键"，一次反射失败若判成"不可用"，快捷键就
    **永久静默失效**（无报错面）。战斗中误触的风险由独立的 `BattleActive()` 兜住。
    """
    body = _body(FASTKEY_SRC, "internal static bool MapEnabled()")
    assert "if (p == null) return true;" in body, "读不到属性时没放行 —— 反射失败会让快捷键永久失效"
    assert "return true;" in body.split("catch")[-1], "读取异常时没放行"
    assert "MapWorldMgr" in FASTKEY_SRC and "_isEnableMap" in body, "没有复用既有的 isEnableMap 探针"


def test_hotkey_gate_split_criteria_share_one_source():
    """`OurPanelOpen` / `InputFocused` 是 `Compute` 的**同一实现拆分**（判据只写一处）。
    快捷键日志要区分"被界面拦"与"被输入框拦"，但两条规则不许各写一份（本工程
    在"判据写两处"上栽过：改一处另一处漂移）。"""
    compute = _body(GATE_SRC, "private static bool Compute()")
    assert "if (OurPanelOpen()) return true;" in compute, "Compute 没复用 OurPanelOpen"
    assert "if (InputFocused()) return true;" in compute, "Compute 没复用 InputFocused"
    assert "OwnUiProbe.OpenPanelName() != null" in _body(GATE_SRC, "internal static bool OurPanelOpen()"), \
        "OurPanelOpen 不是 OwnUiProbe 实现（三面板判据漂移）"
    assert GATE_SRC.count("private static bool Compute()") == 1, "Compute 被复制"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
