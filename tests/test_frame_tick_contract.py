"""常驻帧回调的**接线契约**（11-19 立，起因是一次静默瘫痪的真机事故）。

## 事故现场

真机 11-19 01:09：NPC 在同一轮提交了「收取灵果」与「向你传功」两个工具调用。前者先开了
「收下 / 不用了」确认窗，后者的传功因"已有交互占用"被排进「此间事了」队列，并回了一句
"这个动作已受理、此间事了会自动办理、不要重复调用它"。

此后**队列再也没有执行过**。玩家继续催，模型一直按"传功还在等待"的状态答复，
`Player.log` 里一个字的异常都没有。

## 根因

驱动队列的那条**每帧回调**已经不存在了。`g.timer.Frame(...)` 的登记在世界重载
（回主菜单 / 读档 / 换档）时会被清掉，而代码用 `_tickRegistered` 这个**全进程一次**的
布尔去防重复登记 ⇒ 重载之后它仍然"记得挂过" ⇒ 永远不再重挂。

外部的可观测证据（玩家实际日志）：TickProfiler 每 5 秒打一行体检表，含 `deferq` 这个键。
世界重载之后，所有 tick 一起停了一段；重进世界后回来的**只有**在 `ModMain.Init` 里
无条件 `g.timer.Frame(...)` 的那批（`disp.job`/`hotkeys`/`npcmon`/`diag`/`prober`），
而带 `_tickRegistered` 守卫的（`deferq`/`battleflow`/`teamjoin`）**一个都没回来**。

## 这个故障为什么必须用契约测试守

它的后果不是"某件事没办成"，而是**整条动作通道静默瘫痪**：

  · `ShouldQueue()` 的第一句是 `if (_core.Count > 0) return true;` ⇒ 队首卡住之后，
    之后每一个动作工具（送礼 / 改好感 / 邀约 / 传送…）都会被塞进同一张没人翻的本子；
  · "等太久自动作废并告知"（`HardCeilingSeconds`）也跑在同一个 tick 里 ⇒ 连"超时取消"都不会发生；
  · 全程不报错、不提示、不超时。玩家侧只看到"提示条弹出来了，事情永远不发生"。

## 本文件钉三件事

  ① **不许再出现第二个"全进程一次"的注册标志**（`_tickRegistered` 这类）——
     它就是这次故障的载体。常驻回调一律经 `FrameTicker`；
  ② **`Destroy` 必须主动 StopAll**（官方模板与参考实现都这么写）——
     老实现十几处 `g.timer.Frame(...)` 一条都没 Stop 过，全靠"帧回调随场景销毁"，
     而真机日志里 `Destroy ok` 之后体检表还在继续打，说明那个假设从没验证过；
  ③ **六个常驻组件各自经 FrameTicker、键名互不相同** —— 键重了会让两条回调互相顶掉
     （`Once` 按帧号去重是按**键**算的），现象与"某条 tick 停了"一模一样。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """去掉注释 —— 断言"不许出现某实现"时，注释里提到它不算。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//.*", "", src)


#: 六个常驻组件：(文件, 期望的键常量, 期望的日志名)
RESIDENT = [
    (CS / "drama" / "DramaGate.cs", "FrameTicker.KDeferQ", "deferq"),
    (CS / "state" / "BattleFlowGate.cs", "FrameTicker.KBattleFlow", "battleflow"),
    (CS / "drama" / "BattleOutcome.cs", "FrameTicker.KBattleOutcome", "battle.tick"),
    (CS / "drama" / "DramaSegmentRecorder.cs", "FrameTicker.KSegRec", "segrec"),
    (CS / "drama" / "DramaSegmentProbe.cs", "FrameTicker.KSegProbe", "segprobe"),
    # 第七处（真机日志里与 deferq/battleflow 一起消失的 teamjoin）：
    # 决斗/寻仇的「要不要带队友一起上」靠它采样，坏了玩家就再也看不到那扇确认窗。
    (CS / "tools" / "TeamJoinGate.cs", "FrameTicker.KTeamJoin", "teamjoin"),
]


# ---------- ① 不许再有全进程一次的注册标志 ----------


def test_no_process_wide_registration_flag_survives():
    """`_tickRegistered` 这类布尔必须全部消失。

    它是这次故障的**载体**：记的是"我有没有调用过登记"，答不了"那条登记现在还在吗"。
    重载把登记清掉之后它还是 true，于是再来多少次 Init 都不重挂。
    判据换成句柄 + 帧号心跳（`FrameTicker`）之后，这个标志没有任何存在理由。
    """
    offenders = []
    for p in sorted(CS.rglob("*.cs")):
        if "tests" in p.parts:
            continue
        body = _code_only(_read(p))
        if "_tickRegistered" in body:
            offenders.append(p.relative_to(ROOT).as_posix())
    assert not offenders, (
        "这些文件里还有 `_tickRegistered`（全进程一次的注册标志）：" + "、".join(offenders)
        + "\n它会在世界重载后继续撒谎（登记已被游戏清掉、它还记得挂过），"
          "表现是常驻 tick 永久停止且日志无痕 —— 见本文件头注释。改用 FrameTicker。"
    )


def test_frame_ticker_owns_the_handle():
    """句柄必须真的被存下来（这是"能主动 Stop / 能判断在不在"的前提）。

    老实现全仓十几处 `g.timer.Frame(...)` **一处的返回值都没接**，所以才需要那个布尔。
    官方模组模板与参考实现都是存起来的（`corUpdate = g.timer.Frame(...)`）。
    """
    src = _read(CS / "app" / "FrameTicker.cs")
    assert re.search(r"TimerCoroutine\s+c\s*=\s*g\.timer\.Frame\(", src), \
        "FrameTicker 没有接住 g.timer.Frame 的返回值（TimerCoroutine 句柄）"
    assert "_cor[k] = c;" in src or "_cor[key] = c;" in src, \
        "句柄没有存进表里 —— 那就既 Stop 不了、也判断不了在不在"
    assert "g.timer.Stop(" in src, "FrameTicker 没有 Stop 过任何句柄（撤旧挂新/收摊都要它）"


# ---------- ② Destroy 必须主动收摊 ----------


def test_destroy_stops_all_resident_ticks():
    """`ModMain.Destroy`（回主界面）必须主动 StopAll。

    为什么不能指望"帧回调随场景销毁"：老实现十几处注册**一条都没 Stop**，
    而真机日志里 `Destroy ok` 之后体检表还在继续打（3098 行之后仍有采样），
    说明那个假设从来没有被验证过。官方模板的 Destroy 正是 `g.timer.Stop(corUpdate)`。

    主动停还有第二个作用：让"句柄在册 = 确实在跑"这句话成立，
    于是 `Init` 里的重挂不可能重复（见 `tests/test_frame_tick_contract.py` 第 ③ 条）。
    """
    mm = _read(CS / "app" / "ModMain.cs")
    body = _code_only(mm)
    i = body.find("public void Destroy()")
    assert i >= 0, "ModMain.Destroy 不见了（官方桥靠它收摊）"
    # 截到方法结束（花括号配对）
    j = body.index("{", i)
    depth = 0
    end = -1
    for k in range(j, len(body)):
        if body[k] == "{":
            depth += 1
        elif body[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    assert end > j, "ModMain.Destroy 方法体没闭合"
    destroy = body[j:end]
    assert "FrameTicker.StopAll(" in destroy, (
        "Destroy 里没有 FrameTicker.StopAll —— 常驻帧回调会留到下个世界，"
        "而且『句柄在册 = 确实在跑』这句话不再成立（Init 重挂时可能变成两份）"
    )


def test_init_reports_resident_state():
    """`Init` 要把在册情况打一行 —— 这个故障以前完全静默，必须留下现场证据。

    判读方式（写进日志的行长这样）：`常驻帧回调：在册 5 条：deferq(idle=0) …`
      · 某条的 `idle` 持续变大 ⇒ 它停了（会同时出现 `[FrameTicker] … 停摆 … → 已重挂`）；
      · 在册条数少于预期 ⇒ 有键压根没接线。
    """
    mm = _read(CS / "app" / "ModMain.cs")
    assert "FrameTicker.Describe()" in mm, \
        "Init 没有打常驻帧回调的在册情况 —— 下次再出现『动作排了队不执行』又只能靠猜"


# ---------- ③ 六个组件各自经 FrameTicker，键互不相同 ----------


def test_each_resident_component_goes_through_frame_ticker():
    """每个常驻组件都必须经 `FrameTicker.Ensure(键, 名, 体)`，且键名互不相同。

    ★为什么键必须唯一★ `Once` 的"本帧只跑一份"是**按键**判的。两个组件共用一个键，
    后注册的那个会永远被前一个挡掉（现象与"某条 tick 停了"一模一样，而且更难查：
    日志里键名还在、idle 也在动，只是那个组件的逻辑从来没执行）。
    """
    seen_keys = {}
    for path, key, name in RESIDENT:
        assert path.exists(), f"{path} 不见了（常驻组件被搬走/删掉？）"
        body = _code_only(_read(path))
        assert key in body, f"{path.name} 没有用 {key} 注册帧回调"
        assert name in body, f"{path.name} 的日志名 '{name}' 不见了（改名要同步体检表对账口径）"
        assert "FrameTicker.Ensure(" in body, \
            f"{path.name} 没有经 FrameTicker 注册（常驻/去重/重载自愈都在那一处）"
        assert key not in seen_keys, \
            f"键 {key} 被 {seen_keys.get(key)} 和 {path.name} 共用 —— 后注册的会被 Once 永久挡住"
        seen_keys[key] = path.name

    # `DramaGate.cs` 里住着两个类（DramaGate + DeferredActionQueue），要分别确认
    dg = _code_only(_read(CS / "drama" / "DramaGate.cs"))
    assert "FrameTicker.KDramaGate" in dg, "DramaGate 自身的槽位过期 tick 没走 FrameTicker"
    assert "FrameTicker.KDeferQ" in dg, "DeferredActionQueue 的 tick 没走 FrameTicker"


def test_frame_ticker_keys_are_declared_once():
    """键常量只在 `FrameTicker` 里定义一处，且值互不相同（重复值等于共用键）。"""
    src = _read(CS / "app" / "FrameTicker.cs")
    pairs = re.findall(r"internal const int (K[A-Za-z]+)\s*=\s*(\d+)\s*;", src)
    assert len(pairs) >= 6, f"FrameTicker 的键常量少于 6 个（实际 {len(pairs)}）"
    vals = [v for _, v in pairs]
    assert len(set(vals)) == len(vals), f"键值重复：{pairs}"
    names = [n for n, _ in pairs]
    assert len(set(names)) == len(names), f"键名重复：{pairs}"


# ---------- ④ 回调体必须先去重 ----------


def test_callback_body_dedupes_by_frame():
    """包装回调的第一句必须是 `Once` —— 否则重复登记会让同一次事件被处理两遍。

    历史事故形状：F9/F10/F11 的帧回调被重复注册 ⇒ 同一次按键触发两次开关、开→关自己抵消，
    玩家以为"这键按不动"。那三个热键后来整个删掉了。
    """
    src = _read(CS / "app" / "FrameTicker.cs")
    body = _code_only(src)
    assert "FrameTicker.Once(" in body or "Once(k)" in body, "回调包装里没有 Once 去重"
    # `Attach` 里那个包装 lambda 必须先 Once 再跑实体
    m = re.search(r"Action\s+wrapped\s*=\s*\(\)\s*=>\s*\{(.*?)\};", body, re.S)
    assert m, "找不到 Attach 里的包装回调"
    inner = m.group(1)
    assert "Once(" in inner, "包装回调里没有 Once —— 同帧会跑两遍"
    assert inner.index("Once(") < inner.index("body()"), "Once 必须在调用实体**之前**"
